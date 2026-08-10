# Torget Safe OTA Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Install a local, authenticated A/B firmware update path on Torget that survives interrupted uploads and bad first boots, while preserving USB as the final recovery route.

**Architecture:** Replace the factory-only partition table with two equal OTA application slots and ESP-IDF rollback metadata. A target-only `torget_ota` component exposes a maintenance-window HTTP upload endpoint, validates metadata and SHA-256 while streaming to the inactive slot, and reboots only after `esp_ota_end` succeeds. A small boot-health state machine validates display/UI/scheduler/memory independently of Wi-Fi and external services before accepting a new image.

**Tech Stack:** ESP-IDF 5.5.2, `esp_http_server`, `esp_ota_ops`, mbedTLS SHA-256, NVS, FreeRTOS, LVGL 9.5, C11 host tests, Python `unittest`, ESP32-S3 16 MB flash.

---

## Prerequisites and stop conditions

- Complete the hardware capability registry plan first.
- The Studio preview plan may be complete or in progress; OTA does not depend on its browser UI.
- Do not burn secure-boot or flash-encryption eFuses in this plan.
- Do not change the partition table on the physical unit until Tasks 1-7 pass and the user explicitly approves the one-time destructive USB migration in Task 8.

## File responsibility map

- `partitions.csv`: NVS, OTA data, PHY, and two equal application slots.
- `sdkconfig.defaults`: enable rollback.
- `test/test_ota_partition.py`: prove both slots fit measured and permitted images.
- `components/torget_ota/ota_policy.h/.c`: pure request validation and progress math.
- `test/test_ota_policy.c`: host-test every accept/reject boundary.
- `components/torget_ota/button_policy.h/.c`: distinguish KEY3 short press from maintenance hold.
- `test/test_ota_button_policy.c`: lock gesture timing.
- `components/torget_ota/boot_health_policy.h/.c`: pure first-boot decision state machine.
- `test/test_boot_health_policy.c`: prove external services are not health requirements.
- `components/torget_ota/boot_health.h/.c`: ESP-IDF rollback, NVS, heap, and timing adapter.
- `components/torget_ota/ota_ui.h/.c`: large update/maintenance overlay.
- `components/torget_ota/ota_service.h/.c`: authenticated streaming inactive-slot writer.
- `components/torget_ota/CMakeLists.txt` and `idf_component.yml`: component wiring.
- `main/main.c` and `main/CMakeLists.txt`: health marks, KEY3 routing, and service startup.
- `main/Kconfig.projbuild`: development-only failure injection.
- `secrets.h.example`: personal-development OTA token contract.
- `tools/bootstrap-ota.sh`: guarded one-time USB migration.
- `docs/ota-recovery.md`: update, rollback, and USB recovery runbook.
- `test/run.sh`: pure OTA tests and partition checks.

### Task 1: Lock a safe A/B partition budget before changing firmware

**Files:**
- Modify: `partitions.csv`
- Modify: `sdkconfig.defaults`
- Create: `test/test_ota_partition.py`
- Modify: `test/run.sh`

- [ ] **Step 1: Write the failing partition test**

Create `test/test_ota_partition.py`:

```python
import csv
import re
from pathlib import Path

root = Path(__file__).resolve().parents[1]
rows = []
for raw in (root / "partitions.csv").read_text().splitlines():
    raw = raw.strip()
    if raw and not raw.startswith("#"):
        rows.append(next(csv.reader([raw], skipinitialspace=True)))

parts = {row[0].strip(): row for row in rows}
assert "factory" not in parts, "normal updates must not target a factory slot"
assert {"nvs", "otadata", "phy_init", "ota_0", "ota_1"} <= set(parts)

def size(text):
    match = re.fullmatch(r"(\d+)([KkMm]?)", text.strip())
    assert match, text
    value = int(match.group(1))
    return value * {"": 1, "k": 1024, "m": 1024 * 1024}[match.group(2).lower()]

slot0 = size(parts["ota_0"][4])
slot1 = size(parts["ota_1"][4])
assert slot0 == slot1 == 5 * 1024 * 1024
assert parts["ota_0"][2].strip() == "ota_0"
assert parts["ota_1"][2].strip() == "ota_1"
assert size(parts["otadata"][4]) == 8 * 1024
maximum_permitted = 4 * 1024 * 1024
assert maximum_permitted <= slot0
binary = root / "build/torget.bin"
if binary.exists():
    assert binary.stat().st_size <= maximum_permitted
print("OK: two 5 MiB OTA slots; 4 MiB maximum image gate")
```

- [ ] **Step 2: Run it and verify the factory-only table fails**

Run `python3 test/test_ota_partition.py`. Expected: FAIL because the current table contains `factory` and no OTA slots.

- [ ] **Step 3: Replace the partition table**

Use:

```csv
# Torget 16 MB A/B OTA layout. Normal OTA writes only the inactive app slot.
# Name,    Type, SubType, Offset, Size
nvs,       data, nvs,     ,       64K
otadata,   data, ota,     ,       8K
phy_init,  data, phy,     ,       4K
ota_0,     app,  ota_0,   ,       5M
ota_1,     app,  ota_1,   ,       5M
```

Leave the remaining flash unallocated; do not create storage without an owner.

- [ ] **Step 4: Enable rollback and wire the partition test**

Add to `sdkconfig.defaults`:

```text
# A new OTA image must pass Torget's local first-boot health gate.
CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y
```

Append `python3 test_ota_partition.py` to `test/run.sh`. Run `./test/run.sh`; expected: all tests pass.

- [ ] **Step 5: Commit the unflashed partition change**

```bash
git add partitions.csv sdkconfig.defaults test/test_ota_partition.py test/run.sh
git commit -m "Add safe A/B OTA partition layout"
```

Do not flash this commit yet.

### Task 2: Define pure request and image policy before opening a socket

**Files:**
- Create: `components/torget_ota/ota_policy.h`
- Create: `components/torget_ota/ota_policy.c`
- Create: `test/test_ota_policy.c`
- Modify: `test/run.sh`

- [ ] **Step 1: Write failing policy tests**

Test this public contract:

```c
typedef struct {
  bool maintenance_open;
  bool authorized;
  const char *project;
  const char *chip;
  size_t content_length;
  size_t slot_size;
} tg_ota_request;

typedef enum {
  TG_OTA_ACCEPT,
  TG_OTA_REJECT_CLOSED,
  TG_OTA_REJECT_AUTH,
  TG_OTA_REJECT_PROJECT,
  TG_OTA_REJECT_CHIP,
  TG_OTA_REJECT_SIZE,
} tg_ota_decision;

tg_ota_decision tg_ota_request_check(const tg_ota_request *request);
unsigned tg_ota_progress_percent(size_t received, size_t total);
```

Assert closed window, absent/wrong auth, wrong project, wrong chip, zero length, image over 4 MiB, image over slot size, valid image, zero-total progress, monotonic progress, and clamping at 100.

- [ ] **Step 2: Run and verify the module is missing**

Add its compile/run command to `test/run.sh`, then run `./test/run.sh`. Expected: compile FAIL.

- [ ] **Step 3: Implement the minimal policy**

```c
#define TG_OTA_MAX_IMAGE_BYTES (4U * 1024U * 1024U)

tg_ota_decision tg_ota_request_check(const tg_ota_request *r) {
  if (!r || !r->maintenance_open) return TG_OTA_REJECT_CLOSED;
  if (!r->authorized) return TG_OTA_REJECT_AUTH;
  if (!r->project || strcmp(r->project, "torget") != 0)
    return TG_OTA_REJECT_PROJECT;
  if (!r->chip || strcmp(r->chip, "esp32s3") != 0)
    return TG_OTA_REJECT_CHIP;
  if (r->content_length == 0 || r->content_length > TG_OTA_MAX_IMAGE_BYTES ||
      r->content_length > r->slot_size)
    return TG_OTA_REJECT_SIZE;
  return TG_OTA_ACCEPT;
}

unsigned tg_ota_progress_percent(size_t received, size_t total) {
  if (!total) return 0;
  if (received >= total) return 100;
  return (unsigned)((received * 100U) / total);
}
```

- [ ] **Step 4: Run and commit**

Run `./test/run.sh`; expected: all pass.

```bash
git add components/torget_ota/ota_policy.h components/torget_ota/ota_policy.c test/test_ota_policy.c test/run.sh
git commit -m "Define OTA request safety policy"
```

### Task 3: Make KEY3 maintenance activation deterministic

**Files:**
- Create: `components/torget_ota/button_policy.h`
- Create: `components/torget_ota/button_policy.c`
- Create: `test/test_ota_button_policy.c`
- Modify: `test/run.sh`

- [ ] **Step 1: Write failing short-press/hold tests**

Use:

```c
typedef enum { TG_BUTTON_NONE, TG_BUTTON_NEXT_APP,
               TG_BUTTON_OPEN_MAINTENANCE } tg_button_action;
typedef struct { bool was_down; bool hold_fired; int64_t pressed_at_us; }
    tg_button_policy;
tg_button_action tg_button_update(tg_button_policy *policy,
                                  bool down, int64_t now_us);
```

Prove press alone does nothing, release before three seconds emits one `NEXT_APP`, crossing three seconds emits one `OPEN_MAINTENANCE`, release after a hold does not switch app, and time before the press cannot trigger a hold.

- [ ] **Step 2: Run and verify compile failure**

Add the host command and run `./test/run.sh`. Expected: missing module failure.

- [ ] **Step 3: Implement the three-second policy**

Use `TG_MAINTENANCE_HOLD_US = 3000000LL`. Record time only on the up-to-down edge. Emit the hold once at the threshold. Emit next-app only on a release whose hold did not fire. Reset the hold flag after release.

- [ ] **Step 4: Run and commit**

```bash
./test/run.sh
git add components/torget_ota/button_policy.h components/torget_ota/button_policy.c test/test_ota_button_policy.c test/run.sh
git commit -m "Reserve KEY3 hold for OTA maintenance"
```

### Task 4: Define a service-independent first-boot health gate

**Files:**
- Create: `components/torget_ota/boot_health_policy.h`
- Create: `components/torget_ota/boot_health_policy.c`
- Create: `test/test_boot_health_policy.c`
- Modify: `test/run.sh`

- [ ] **Step 1: Write failing health tests**

Use:

```c
enum {
  TG_HEALTH_NVS = 1 << 0, TG_HEALTH_PSRAM = 1 << 1,
  TG_HEALTH_DISPLAY = 1 << 2, TG_HEALTH_UI = 1 << 3,
  TG_HEALTH_SCHEDULER = 1 << 4,
};
#define TG_HEALTH_REQUIRED (TG_HEALTH_NVS | TG_HEALTH_PSRAM | \
                            TG_HEALTH_DISPLAY | TG_HEALTH_UI | \
                            TG_HEALTH_SCHEDULER)
typedef struct {
  uint32_t passed;
  int64_t started_at_us;
  bool pending_verify;
  bool forced_failure;
} tg_boot_health;
typedef enum { TG_HEALTH_WAIT, TG_HEALTH_ACCEPT, TG_HEALTH_ROLLBACK }
    tg_health_decision;
tg_health_decision tg_boot_health_decide(const tg_boot_health *health,
                                         int64_t now_us);
```

Assert: non-pending images are ignored; pending images wait at seven seconds; all bits accept at eight seconds; any missing bit rolls back at 15 seconds; forced failure rolls back at eight seconds; and Wi-Fi, SNTP, tokenserver, and Solelkollen are absent from the required mask.

- [ ] **Step 2: Run and verify failure**

Add the host command and run `./test/run.sh`. Expected: missing module failure.

- [ ] **Step 3: Implement deterministic timing**

Use an eight-second minimum and 15-second deadline. Before eight seconds return `WAIT`; from eight seconds accept only when all bits passed; at 15 seconds return `ROLLBACK`. Forced failure rolls back at eight seconds. Non-pending images always return `WAIT` because no rollback call is required.

- [ ] **Step 4: Run and commit**

```bash
./test/run.sh
git add components/torget_ota/boot_health_policy.h components/torget_ota/boot_health_policy.c test/test_boot_health_policy.c test/run.sh
git commit -m "Define OTA first-boot health gate"
```

### Task 5: Adapt boot health to ESP-IDF rollback

**Files:**
- Create: `components/torget_ota/boot_health.h`
- Create: `components/torget_ota/boot_health.c`
- Create: `components/torget_ota/CMakeLists.txt`
- Create: `components/torget_ota/idf_component.yml`
- Create: `main/Kconfig.projbuild`
- Modify: `main/CMakeLists.txt`
- Modify: `main/main.c`
- Modify: `test/test_ota_partition.py`

- [ ] **Step 1: Add failing target wiring checks**

Assert target source contains `torget_boot_health_start`, marks `TG_HEALTH_DISPLAY`, `TG_HEALTH_UI`, and `TG_HEALTH_SCHEDULER`, and the adapter contains both `esp_ota_mark_app_valid_cancel_rollback` and `esp_ota_mark_app_invalid_rollback_and_reboot`.

- [ ] **Step 2: Implement the target adapter**

`torget_boot_health_start()` must get the running partition/state, enable policy only for `ESP_OTA_IMG_PENDING_VERIFY`, prove NVS by reading or writing one `boot_probe` byte in namespace `torget_health`, require nonzero SPIRAM plus 32 KiB free internal RAM and a 12 KiB largest DMA block, then evaluate the policy every 500 ms. Accept only through `esp_ota_mark_app_valid_cancel_rollback`; on rollback store the rejected version in bounded NVS key `rollback_from`, log the missing bitmask, and call `esp_ota_mark_app_invalid_rollback_and_reboot`. On every stable boot, inspect the other OTA slot for `ESP_OTA_IMG_ABORTED` so bootloader-triggered crash rollback also updates this evidence without accepting an invalid image.

- [ ] **Step 3: Wire explicit marks into existing boot order**

Call start after NVS initialization, mark display after `display_start()`, UI after `torget_ui_create()`, and scheduler on the first `tick_cb`. Preserve the existing event-group-before-app-task ordering.

- [ ] **Step 4: Add deliberate failure injection**

Create `main/Kconfig.projbuild`:

```text
menu "Torget development diagnostics"
config TORGET_BOOT_HEALTH_FORCE_FAIL
    bool "Force pending OTA image to fail its health gate"
    default n
    help
        Development-only failure injection for the physical rollback test.
endmenu
```

Map it to `forced_failure` only for a pending image.

- [ ] **Step 5: Build and commit**

Run `./test/run.sh`, source ESP-IDF, and run `idf.py build`. Expected: both pass.

```bash
git add components/torget_ota main/CMakeLists.txt main/main.c main/Kconfig.projbuild test/test_ota_partition.py
git commit -m "Validate new firmware before accepting OTA"
```

### Task 6: Add the maintenance overlay without changing app layouts

**Files:**
- Create: `components/torget_ota/ota_ui.h`
- Create: `components/torget_ota/ota_ui.c`
- Modify: `components/torget_ota/CMakeLists.txt`
- Modify: `main/main.c`
- Modify: `test/test_ota_partition.py`

- [ ] **Step 1: Add a source-level safety check**

Assert `ota_ui.c` uses `lv_layer_top()`, `torget_ui_lock()`, native fonts, and a black background; it must contain no transform API or canvas object.

- [ ] **Step 2: Implement four large static states**

Create one hidden 480 x 480 overlay with:

```text
UPDATES ON       09:59
RECEIVING        42%
VERIFYING
RESTARTING
```

Use `plex_text_32`, `plex_num_50`, a 20 px neutral progress bar, and detail no smaller than `plex_ui_21`. No cards, logos, animation, opacity layers, or app data. `torget_ota_ui_set(state, percent, seconds_left)` locks the UI and updates existing widgets.

- [ ] **Step 3: Create the overlay after the shared UI**

Call `torget_ota_ui_create()` after `torget_ui_create()` while the UI lock is held. Keep it hidden until maintenance opens.

- [ ] **Step 4: Build and commit**

```bash
./test/run.sh
. ~/esp/esp-idf/export.sh
idf.py build
git add components/torget_ota/ota_ui.c components/torget_ota/ota_ui.h components/torget_ota/CMakeLists.txt main/main.c test/test_ota_partition.py
git commit -m "Add minimal OTA maintenance overlay"
```

### Task 7: Stream authenticated firmware into the inactive slot

**Files:**
- Create: `components/torget_ota/ota_service.h`
- Create: `components/torget_ota/ota_service.c`
- Modify: `components/torget_ota/CMakeLists.txt`
- Modify: `components/torget_ota/idf_component.yml`
- Modify: `main/CMakeLists.txt`
- Modify: `main/main.c`
- Modify: `secrets.h.example`
- Modify: `test/test_ota_partition.py`

- [ ] **Step 1: Add target source assertions**

Require `esp_ota_get_next_update_partition`, `esp_ota_begin`, `esp_ota_write`, `esp_ota_end`, `esp_ota_set_boot_partition`, `esp_ota_abort`, incremental SHA-256, constant-time token comparison, and no authorization-header logging.

- [ ] **Step 2: Define the development token**

Add:

```c
/* Optional local OTA maintenance token. Generate at least 32 random bytes,
 * keep it out of git, and store its counterpart outside Studio project data.
 * Without this macro the upload endpoint remains disabled. */
/* #define TG_OTA_TOKEN "replace-with-64-hex-characters" */
```

When defined, require exactly 64 lowercase hexadecimal characters with
`_Static_assert(sizeof(TG_OTA_TOKEN) == 65, "TG_OTA_TOKEN must be 64 hex characters")`
and validate the character set once during service initialization. Never print
or return it.

- [ ] **Step 3: Implement the HTTP boundary**

Define `TG_OTA_HTTP_PORT 80` in `ota_service.h`. Start `esp_http_server`
idempotently immediately after `wifi_start()`, even when the station has no IP;
the listener then becomes reachable on whichever station or maintenance-AP
interface comes up. Register `GET /api/ota/status` and
`POST /api/ota/firmware`. Status exposes project, chip, maintenance state,
maximum size, running version, and active partition, never the token. Upload
requires:

```text
Authorization: Bearer <TG_OTA_TOKEN>
X-VibePulse-Project: torget
X-VibePulse-Chip: esp32s3
X-VibePulse-SHA256: 64 lowercase hex characters
Content-Length: exact binary length
```

Use constant-time comparison and `tg_ota_request_check()` before beginning OTA.

- [ ] **Step 4: Validate and stream the image**

Read at most 4096 bytes per `httpd_req_recv` into one fixed internal buffer. Validate the first chunk's ESP image magic, segment count, chip ID, app-description magic, and project name. Increment SHA and UI progress per chunk. Timeout, disconnect, short body, bad metadata, write error, invalid image, digest mismatch, or `esp_ota_end` failure must abort and retain the current boot partition.

Only after `esp_ota_end` and digest comparison may the service select the inactive partition. Respond `202` with expected SHA/version, flush the response, then reboot through a short timer. Never write bootloader, partition table, NVS, PHY, or the active app slot.

- [ ] **Step 5: Route KEY3 and timeout**

Replace the immediate KEY3 edge with `tg_button_update()`: short release changes app; three-second hold opens OTA for ten minutes and suppresses app switching. Timeout hides the overlay and returns uploads to 403.

- [ ] **Step 6: Build, size, and commit the unflashed service**

Run:

```bash
./test/run.sh
. ~/esp/esp-idf/export.sh
idf.py fullclean build
idf.py size
python3 test/test_ota_partition.py
```

Expected: all pass and `torget.bin` is below 4 MiB.

```bash
git add components/torget_ota main/CMakeLists.txt main/main.c secrets.h.example test/test_ota_partition.py
git commit -m "Add authenticated inactive-slot OTA service"
```

### Task 8: Perform the one-time USB bootstrap and failure-injection gate

**Files:**
- Create: `tools/bootstrap-ota.sh`
- Create: `docs/ota-recovery.md`
- Modify: `README.md`
- Modify: `spec/hardware-capabilities.yaml`
- Modify: `spec/device-units.yaml`

- [ ] **Step 1: Create a guarded migration script**

Require:

```text
./tools/bootstrap-ota.sh --port /dev/cu.usbmodem101 --confirm-erase
```

Refuse unresolved globs, missing `secrets.h`, undefined/short `TG_OTA_TOKEN`, build failure, or image over 4 MiB. Print partitions and SHA-256, require the user to type `torget-home-01`, then run `idf.py -p "$port" erase-flash flash monitor`. Never auto-select a port.

- [ ] **Step 2: Write the recovery runbook first**

Document short vs. long KEY3; direct-IP status/upload using a token environment variable; screen/log states; BOOT+RESET mode; full USB recovery from the known-good commit; disabling forced failure; and the explicit absence of secure-boot/eFuse changes.

- [ ] **Step 3: Request the destructive physical approval**

Before running the script, show port, unit, partition table, commit, image SHA, and that flash/NVS will be erased. Continue only after explicit approval.

- [ ] **Step 4: Bootstrap and verify the healthy image**

Verify display/UI, Solelkollen/VibePulse, short app switching, long maintenance hold, status endpoint, active `ota_0`, and image validation without external data services.

- [ ] **Step 5: Exercise physical failures**

Record normal slot-to-slot update, cancellation near 40 percent, power removal during upload before slot selection, bad SHA, oversized length, wrong project/chip, forced health failure with automatic rollback, healthy update without Internet/tokenserver, and BOOT+RESET USB recovery. Never power-cut bootloader/partition-table writes.

- [ ] **Step 6: Record verified state and commit**

Only after all tests pass, set `update.ota-ab-rollback` to firmware-enabled and physically verified for `torget-home-01`. Record installed commit/slot without IP/token.

```bash
./test/run.sh
python3 tools/hardware_registry.py spec
git diff --check
git add tools/bootstrap-ota.sh docs/ota-recovery.md README.md spec/hardware-capabilities.yaml spec/device-units.yaml
git commit -m "Verify Torget A/B OTA recovery on hardware"
```

## Completion gate

The foundation is complete only after both slots boot, interrupted/corrupt/unauthorized uploads are rejected, a forced-bad image rolls back, a healthy image validates without Internet or tokenserver, and USB recovery succeeds. Discovery, hotspot maintenance AP, rich diagnostics, and Studio's install button remain in the wireless-delivery plan.
