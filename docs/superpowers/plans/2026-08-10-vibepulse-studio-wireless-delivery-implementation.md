# VibePulse Studio Wireless Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let VibePulse Studio discover the named Torget unit, show trustworthy diagnostics, build firmware, install it over home Wi-Fi or a phone hotspot, and report verified success or rollback without unplugging USB.

**Architecture:** Extend the physically verified OTA service with a versioned device-information contract and mDNS advertisement. The localhost Studio backend owns discovery, Keychain credentials, fixed-command builds, streaming upload, and post-reboot verification as explicit jobs. A ten-minute WPA2 maintenance SoftAP provides the documented fallback when hotspot or multicast discovery fails.

**Tech Stack:** Python 3.11+, `zeroconf`, macOS Keychain (`security`), standard-library `http.client`/`subprocess`, HTML/CSS/ES modules, ESP-IDF mDNS, `esp_http_server`, Wi-Fi STA+AP, C11 host policies and Python `unittest`.

---

## Prerequisites

- Complete and physically verify `2026-08-10-safe-ota-foundation-implementation.md`.
- Complete the Studio preview service through its static AMOLED gate.
- Do not expose build/install endpoints on a LAN bind; Studio's control server remains localhost-only.
- Discovery, preview, build, and install are separate actions. Only the explicit install action may mutate a device.

## File responsibility map

- `components/torget_ota/device_info.h/.c`: collect bounded device identity and diagnostics.
- `components/torget_ota/device_info_json.h/.c`: encode versioned JSON without credentials.
- `test/test_device_info_json.c`: host-test complete, missing, and bounded diagnostics.
- `components/torget_ota/ota_service.c`: serve authenticated `GET /api/device` and OTA routes.
- `components/torget_ota/ota_discovery.h/.c`: advertise `_vibepulse._tcp.local`.
- `components/torget_ota/maintenance_ap.h/.c`: ten-minute WPA2 fallback network.
- `tools/vibepulse_studio/devices.py`: merge mDNS, remembered, manual, and fallback candidates.
- `tools/vibepulse_studio/credentials.py`: keep device secrets in macOS Keychain.
- `tools/vibepulse_studio/build_jobs.py`: fixed incremental builds and immutable artifacts.
- `tools/vibepulse_studio/installer.py`: preflight, upload, reboot wait, and result classification.
- `tools/vibepulse_studio/jobs.py`: thread-safe job state and bounded public logs.
- `tools/vibepulse_studio/server.py`: expose discovery, build, install, credential, and job APIs.
- `tools/vibepulse_studio/web/`: explicit device/build/install interface outside the 480 x 480 canvas.
- `tools/vibepulse_studio/test_*.py`: host tests for each Studio boundary.
- `test/test_wireless_delivery_wiring.py`: target/source safety checks.
- `docs/wireless-delivery.md`: daily workflow, hotspot/AP fallback, and recovery.

### Task 1: Define a stable device diagnostic contract

**Files:**
- Create: `components/torget_ota/device_info.h`
- Create: `components/torget_ota/device_info.c`
- Create: `components/torget_ota/device_info_json.h`
- Create: `components/torget_ota/device_info_json.c`
- Create: `test/test_device_info_json.c`
- Modify: `components/torget_ota/CMakeLists.txt`
- Modify: `test/run.sh`

- [ ] **Step 1: Write the failing JSON contract tests**

Use this bounded data model:

```c
#define TG_DEVICE_ID_CAP 32
#define TG_VERSION_CAP 40
#define TG_PARTITION_CAP 16
#define TG_RESET_REASON_CAP 24

typedef struct {
  char device_id[TG_DEVICE_ID_CAP];
  char project[16];
  char chip[16];
  char version[TG_VERSION_CAP];
  char active_partition[TG_PARTITION_CAP];
  char reset_reason[TG_RESET_REASON_CAP];
  char previous_rollback[TG_VERSION_CAP];
  char ipv4[16];
  int rssi_dbm;
  uint64_t uptime_ms;
  uint32_t free_internal_heap;
  uint32_t largest_dma_block;
  uint32_t free_psram;
  uint32_t ota_max_image;
  bool maintenance_open;
  unsigned maintenance_seconds;
} tg_device_info;

bool tg_device_info_json(const tg_device_info *info,
                         char *output, size_t capacity);
```

Assert exact `v: 1`, device/project/chip/version, active slot, reset reason, previous rollback, IP/RSSI, uptime, internal heap, DMA block, PSRAM, maximum image, and maintenance fields. Prove a too-small output returns false with no partial JSON and that no token, SSID, password, or `secrets.h` field exists in the model.

- [ ] **Step 2: Run and verify compile failure**

Add the C compile/run command to `test/run.sh` and run it. Expected: missing files.

- [ ] **Step 3: Implement collection and encoding separately**

`device_info.c` uses `esp_app_get_description()`, `esp_ota_get_running_partition()`, `esp_reset_reason()`, `esp_timer_get_time()`, `heap_caps_*`, `esp_wifi_sta_get_ap_info()`, and the active netif address. Derive `device_id` as `torget-` plus the final six lowercase MAC hex digits; do not expose the full MAC. Read `previous_rollback` from the OTA adapter's bounded NVS evidence; return `none` when no rollback has occurred.

`device_info_json.c` encodes only the struct through cJSON, prints unformatted into a temporary allocation, copies only when the entire string fits, and always NUL-terminates success. The pure encoder must compile on the host without ESP-IDF.

- [ ] **Step 4: Run and commit the contract**

```bash
./test/run.sh
git add components/torget_ota/device_info.h components/torget_ota/device_info.c components/torget_ota/device_info_json.h components/torget_ota/device_info_json.c components/torget_ota/CMakeLists.txt test/test_device_info_json.c test/run.sh
git commit -m "Expose bounded Torget device diagnostics"
```

### Task 2: Serve diagnostics and advertise the named unit

**Files:**
- Create: `components/torget_ota/ota_discovery.h`
- Create: `components/torget_ota/ota_discovery.c`
- Modify: `components/torget_ota/ota_service.c`
- Modify: `components/torget_ota/CMakeLists.txt`
- Modify: `components/torget_ota/idf_component.yml`
- Modify: `main/idf_component.yml`
- Create: `test/test_wireless_delivery_wiring.py`

- [ ] **Step 1: Add failing target wiring checks**

Create `test/test_wireless_delivery_wiring.py` and assert:

```python
assert '"/api/device"' in ota_service
assert "tg_device_info_collect" in ota_service
assert "mdns_service_add" in discovery
assert '"_vibepulse"' in discovery
assert '"_tcp"' in discovery
for forbidden in ("TG_OTA_TOKEN", "TG_WIFI_PASS", "TG_OTA_AP_PASS"):
    assert forbidden not in device_info_json
```

- [ ] **Step 2: Add authenticated diagnostics**

Register `GET /api/device`. Require the same constant-time bearer-token check as firmware upload. Return `401` without a valid token, `503` if collection fails, otherwise JSON with `Cache-Control: no-store`. Keep `GET /api/ota/status` as the smaller unauthenticated preflight route.

- [ ] **Step 3: Advertise minimal mDNS identity**

After station IP, initialize mDNS, set hostname to the derived device ID and instance to `TG_DEVICE_NAME` (default `Torget`), then add `_vibepulse._tcp` on `TG_OTA_HTTP_PORT`. Publish only:

```text
protocol=1
project=torget
chip=esp32s3
device=<derived device ID>
```

Do not publish version, IP, token, SSID, or usage data; mDNS already supplies addresses.

- [ ] **Step 4: Wire official mDNS and build**

Add `espressif/mdns: "^1.11.3"` to the component manifest and requirements. Run `./test/run.sh`, source ESP-IDF, then run `idf.py reconfigure build`. Expected: all pass.

- [ ] **Step 5: Commit discovery on the device**

```bash
git add components/torget_ota main/idf_component.yml test/test_wireless_delivery_wiring.py
git commit -m "Advertise Torget diagnostics over mDNS"
```

### Task 3: Discover devices with deterministic fallback precedence

**Files:**
- Create: `tools/vibepulse_studio/devices.py`
- Create: `tools/vibepulse_studio/test_devices.py`
- Modify: `tools/vibepulse_studio/server.py`

- [ ] **Step 1: Write failing discovery tests**

Use:

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class DeviceCandidate:
    device_id: str
    host: str
    port: int
    source: str  # mdns, remembered, manual, maintenance-ap

def merge_candidates(mdns, remembered, manual):
    precedence = {"remembered": 1, "mdns": 2, "manual": 3}
    merged = {}
    for candidate in [*remembered, *mdns, *manual]:
        current = merged.get(candidate.device_id)
        if current is None or precedence[candidate.source] > precedence[current.source]:
            merged[candidate.device_id] = candidate
    return sorted(merged.values(), key=lambda item: item.device_id), None
```

Tests must prove candidates deduplicate by device ID; manual address wins only for an explicitly selected device; live mDNS beats stale remembered IP; remembered IP remains when mDNS is absent; `192.168.4.1` appears only after fallback is requested; malformed ports/addresses are rejected; and multiple devices never cause implicit selection.

- [ ] **Step 2: Run and verify module failure**

Run `python3 -m unittest tools.vibepulse_studio.test_devices -v`. Expected: import failure.

- [ ] **Step 3: Implement discovery and remembered addresses**

Use `zeroconf.ServiceBrowser` for `_vibepulse._tcp.local.` with a bounded two-second scan. Accept only TXT `protocol=1`, project `torget`, chip `esp32s3`, and a bounded device ID. Persist only device ID, friendly name, last host, port, and last-seen timestamp under `~/Library/Application Support/VibePulse Studio/devices.json`, atomically with mode `0600`. This file contains no token or AP password.

Normalize candidates before calling this function; the pure merge groups by device ID, prefers `manual`, then `mdns`, then `remembered`, and deliberately returns `selected=None`.

- [ ] **Step 4: Add read-only discovery routes**

```text
GET  /api/devices
POST /api/devices/manual   {"deviceId":"torget-a1b2c3","host":"192.168.1.42","port":80}
```

Manual registration only updates the remembered address. It never builds, installs, opens maintenance, or selects a unit.

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest tools.vibepulse_studio.test_devices -v
python3 -m unittest discover -s tools/vibepulse_studio -p 'test_*.py' -v
git add tools/vibepulse_studio/devices.py tools/vibepulse_studio/test_devices.py tools/vibepulse_studio/server.py
git commit -m "Discover Torget devices with network fallbacks"
```

### Task 4: Keep device credentials out of project files and logs

**Files:**
- Create: `tools/vibepulse_studio/credentials.py`
- Create: `tools/vibepulse_studio/test_credentials.py`
- Modify: `tools/vibepulse_studio/server.py`

- [ ] **Step 1: Write failing fake-Keychain tests**

Define a runner-injected `MacOSKeychain` and prove:

```python
store.set("torget-a1b2c3", "ota", "secret-value")
self.assertEqual(store.get("torget-a1b2c3", "ota"), "secret-value")
self.assertNotIn("secret-value", " ".join(fake.commands))
self.assertNotIn("secret-value", json.dumps(public_device_record))
store.delete("torget-a1b2c3", "ota")
self.assertIsNone(store.get("torget-a1b2c3", "ota"))
```

The fake runner accepts secret bytes on stdin, never as an argv element.

- [ ] **Step 2: Implement Keychain storage**

Use service `com.vibepulse.studio.device`, account `<device-id>:ota` or `<device-id>:ap`, and the argv list `[/usr/bin/security, add-generic-password, -U, -a, <account>, -s, com.vibepulse.studio.device, -w]` with the secret supplied on stdin. Reads use `find-generic-password -w`; deletes use `delete-generic-password`. Redact stderr before raising. Reject empty secrets and device IDs outside `[a-z0-9-]{1,31}`.

- [ ] **Step 3: Add credential routes that never echo values**

```text
PUT    /api/devices/{id}/credentials  {"otaToken":"64-hex-character-token","apPassword":"unique-12-char-pass"}
GET    /api/devices/{id}/credentials  {"hasOtaToken":true,"hasApPassword":true}
DELETE /api/devices/{id}/credentials
```

Limit the body to 4 KiB, return booleans only, and ensure request logs never include bodies or authorization headers.

- [ ] **Step 4: Run and commit**

```bash
python3 -m unittest tools.vibepulse_studio.test_credentials -v
git add tools/vibepulse_studio/credentials.py tools/vibepulse_studio/test_credentials.py tools/vibepulse_studio/server.py
git commit -m "Store OTA credentials in macOS Keychain"
```

### Task 5: Add fixed-command incremental build jobs

**Files:**
- Create: `tools/build-target.sh`
- Create: `tools/vibepulse_studio/jobs.py`
- Create: `tools/vibepulse_studio/build_jobs.py`
- Create: `tools/vibepulse_studio/test_build_jobs.py`
- Modify: `tools/vibepulse_studio/server.py`

- [ ] **Step 1: Write failing job tests**

Tests must prove builds use an argv list and never `shell=True`; callers cannot supply a command/path; output identity comes from `build/project_description.json`; the artifact is `build/torget.bin`; SHA-256 is recorded; files over 4 MiB fail; public logs retain only the latest 50 redacted lines; and jobs expose `queued/running/succeeded/failed/cancelled`.

- [ ] **Step 2: Implement a fixed build wrapper**

Create:

```sh
#!/bin/sh
set -eu
. "$HOME/esp/esp-idf/export.sh" >/dev/null
repo=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
cd "$repo"
idf.py build
```

`build_jobs.py` invokes only this resolved file with `subprocess.Popen([path], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)`. It reads `project_name`, `project_version`, and app path from `project_description.json`, hashes the binary, and stores an immutable artifact record. A `-dirty` version is allowed but visibly labeled.

- [ ] **Step 3: Add build and job routes**

```text
POST /api/build
GET  /api/jobs/{id}
POST /api/jobs/{id}/cancel
```

Only one build/install mutation job may run at once. Preview and discovery remain available during it.

- [ ] **Step 4: Run and commit**

```bash
python3 -m unittest tools.vibepulse_studio.test_build_jobs -v
git add tools/build-target.sh tools/vibepulse_studio/jobs.py tools/vibepulse_studio/build_jobs.py tools/vibepulse_studio/test_build_jobs.py tools/vibepulse_studio/server.py
git commit -m "Add explicit incremental firmware build jobs"
```

### Task 6: Upload, reconnect, and classify success truthfully

**Files:**
- Create: `tools/vibepulse_studio/installer.py`
- Create: `tools/vibepulse_studio/test_installer.py`
- Modify: `tools/vibepulse_studio/server.py`

- [ ] **Step 1: Write failing installer state-machine tests**

Use:

```python
from enum import Enum

class InstallResult(Enum):
    SUCCEEDED = "succeeded"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    UNKNOWN = "unknown"

def install(device, artifact, token, transport, progress, clock):
    info = transport.device_info(device, token)
    if info["deviceId"] != device.device_id:
        return InstallResult.FAILED
    if info["project"] != "torget" or info["chip"] != "esp32s3":
        return InstallResult.FAILED
    if not info["maintenance"]["open"] or artifact.size > info["ota"]["maxImage"]:
        return InstallResult.FAILED
    previous = info["version"]
    if transport.upload(device, artifact, token, progress) != 202:
        return InstallResult.FAILED
    deadline = clock.now() + 45
    while clock.now() < deadline:
        current = transport.try_device_info(device, token)
        if current and current["version"] == artifact.version:
            return InstallResult.SUCCEEDED
        if current and current["version"] == previous and current["previousRollback"] != "none":
            return InstallResult.ROLLED_BACK
        clock.wait(1)
    return InstallResult.UNKNOWN
```

Tests cover wrong device/project/chip, closed maintenance, insufficient capacity, missing token, 401, midstream disconnect, 422, digest mismatch, expected version after reboot, previous version with rollback evidence, and no reconnect. Prove `202` alone never returns success.

- [ ] **Step 2: Implement preflight and chunked upload**

Fetch authenticated `/api/device`, verify exact selection and artifact compatibility, then use `http.client.HTTPConnection`, `putrequest`, fixed headers, and 64 KiB `send()` chunks. Map upload progress to 0-90 and reserve 90-100 for reboot verification. Never put the token in URL, logs, exceptions, or job JSON.

- [ ] **Step 3: Verify rebooted identity through all known addresses**

After `202`, poll the remembered address, refreshed mDNS address, and explicit manual/fallback address for at most 45 seconds. Authenticate detailed queries. Return only:

```text
succeeded   expected project_version is running
rolled_back previous project_version is running with rollback evidence
failed      explicit rejection before reboot
unknown     no compatible authenticated identity could be proven
```

- [ ] **Step 4: Add explicit install job creation**

Add `POST /api/install` accepting exactly `{"deviceId":"torget-a1b2c3","buildJobId":"build-20260810-001"}`. Require a succeeded build and an exact selected device; return a new job ID immediately. Never choose the only visible device automatically.

- [ ] **Step 5: Run and commit**

```bash
python3 -m unittest tools.vibepulse_studio.test_installer -v
python3 -m unittest discover -s tools/vibepulse_studio -p 'test_*.py' -v
git add tools/vibepulse_studio/installer.py tools/vibepulse_studio/test_installer.py tools/vibepulse_studio/server.py
git commit -m "Verify wireless installs after device reboot"
```

### Task 7: Add the temporary hotspot-safe maintenance AP

**Files:**
- Create: `components/torget_ota/maintenance_ap.h`
- Create: `components/torget_ota/maintenance_ap.c`
- Modify: `components/torget_ota/ota_service.c`
- Modify: `components/torget_ota/CMakeLists.txt`
- Modify: `main/main.c`
- Modify: `secrets.h.example`
- Modify: `test/test_wireless_delivery_wiring.py`

- [ ] **Step 1: Add source safety assertions**

Require `WIFI_MODE_APSTA`, `WIFI_AUTH_WPA2_PSK`, derived `VibePulse-Update-` SSID, documented `192.168.4.1`, shutdown at maintenance timeout, and absence of AP password from logs and JSON.

- [ ] **Step 2: Define the AP secret separately**

Add:

```c
/* Optional WPA2 fallback network, active only during the ten-minute physical
 * maintenance window. Minimum 12 characters; counterpart stored in Keychain. */
/* #define TG_OTA_AP_PASS "replace-with-a-unique-password" */
```

If absent, do not start an open AP; station/manual-IP OTA remains available.

- [ ] **Step 3: Implement bounded APSTA maintenance**

On the three-second hold, retain STA, create the AP netif once, use APSTA mode, configure `VibePulse-Update-<last6>`, WPA2, maximum two clients, and no persistent config. Serve OTA on both interfaces. At ten minutes or explicit close, stop AP, restore station-only mode, hide maintenance UI, and clear transient state. Reopening resets the timer but never changes credentials.

- [ ] **Step 4: Show fallback facts without crowding**

The maintenance overlay may alternate its one detail line every four seconds between device ID and `AP 192.168.4.1`; do not add another row. Studio shows manual fallback instructions only after `Use maintenance AP` and never attempts privileged Wi-Fi switching.

- [ ] **Step 5: Build and commit**

```bash
./test/run.sh
. ~/esp/esp-idf/export.sh
idf.py build
git add components/torget_ota/maintenance_ap.h components/torget_ota/maintenance_ap.c components/torget_ota/ota_service.c components/torget_ota/CMakeLists.txt main/main.c secrets.h.example test/test_wireless_delivery_wiring.py
git commit -m "Add temporary WPA2 OTA maintenance network"
```

### Task 8: Add the Studio device and install interface

**Files:**
- Modify: `tools/vibepulse_studio/web/index.html`
- Modify: `tools/vibepulse_studio/web/studio.css`
- Modify: `tools/vibepulse_studio/web/studio.js`
- Modify: `test/test_vibepulse_studio_wiring.py`

- [ ] **Step 1: Add failing interface assertions**

Require `Devices`, `Add IP`, `Build firmware`, `Install wirelessly`, `Use maintenance AP`, and diagnostics `Version`, `Wi-Fi`, `Slot`, `Last rollback`, `Reset`, `Uptime`, `Internal heap`, `PSRAM`. Assert install begins disabled and build/install use different routes.

- [ ] **Step 2: Render selection outside the device canvas**

List discovery source and last-seen status in a Studio side panel, never inside the 480 x 480 AMOLED preview. Require a row click before credentials, diagnostics, build, or install.

- [ ] **Step 3: Implement the safe action sequence**

Use this exact flow:

```text
select exact device -> fetch authenticated diagnostics -> build -> review
version/SHA/size -> hold KEY3 -> refresh maintenance -> install -> upload
progress -> reconnect -> succeeded/rolled back/unknown
```

Disable install unless compatibility, credential presence, maintenance-open state, and a succeeded artifact are true. Final confirmation names the device and from/to versions in an in-page dialog.

- [ ] **Step 4: Poll bounded job status**

Poll no faster than twice per second and stop on a terminal state. Show only redacted bounded logs. `UNKNOWN` must say the update was not proven and offer discovery/manual IP/USB recovery, never a green success state.

- [ ] **Step 5: Run browser and host verification**

```bash
python3 -m unittest discover -s tools/vibepulse_studio -p 'test_*.py' -v
python3 test/test_vibepulse_studio_wiring.py
python3 test/test_wireless_delivery_wiring.py
./test/run.sh
python3 tools/vibepulse_studio/server.py --no-open
```

Verify preview/discovery do not build, build does not install, selection is never implicit, saved secrets are not shown, and terminal states stay distinct.

- [ ] **Step 6: Commit the interface**

```bash
git add tools/vibepulse_studio/web test/test_vibepulse_studio_wiring.py
git commit -m "Add safe wireless install workflow to Studio"
```

### Task 9: Verify home Wi-Fi, phone hotspot, fallback AP, and rollback

**Files:**
- Create: `docs/wireless-delivery.md`
- Modify: `README.md`
- Modify: `spec/hardware-capabilities.yaml`
- Modify: `spec/device-units.yaml`

- [ ] **Step 1: Document the daily workflow**

Document Studio startup, exact device selection, manual IP, Keychain setup, build, maintenance activation, install, result meanings, hotspot 2.4 GHz requirement, multicast limitations, fallback AP, and USB recovery. State that later bootloader/partition migrations still require separately reviewed USB work.

- [ ] **Step 2: Execute the home Wi-Fi matrix**

Verify mDNS, remembered and manual IP, diagnostics, successful install, cancellation, rollback classification, maintenance timeout, and absence of tokens from URLs/logs.

- [ ] **Step 3: Execute the phone-hotspot matrix**

With iPhone `Maximize Compatibility`, verify remembered/manual IP even if mDNS fails, complete update/reconnect, and truthful unknown status if client isolation blocks verification.

- [ ] **Step 4: Execute the maintenance-AP matrix**

Verify WPA2 join, `192.168.4.1`, authenticated install, timed AP shutdown and STA restoration, plus USB recovery after deliberately losing both network paths.

- [ ] **Step 5: Record evidence and run final checks**

Update registry/unit evidence only with observations and dates. Run:

```bash
python3 -m unittest discover -s tools/vibepulse_studio -p 'test_*.py' -v
./test/run.sh
. ~/esp/esp-idf/export.sh
idf.py build
python3 tools/hardware_registry.py spec
git diff --check
```

- [ ] **Step 6: Commit documentation and physical evidence**

```bash
git add docs/wireless-delivery.md README.md spec/hardware-capabilities.yaml spec/device-units.yaml
git commit -m "Verify VibePulse Studio wireless delivery"
```

## Completion gate

Wireless delivery is complete only when Studio explicitly targets the named unit, shows all required diagnostics, verifies the expected build on home Wi-Fi, works through manual IP on the phone hotspot, recovers through the temporary AP, distinguishes rollback/unknown from success, and leaves USB recovery functional. Production signing, encrypted provisioning, and irreversible eFuse security remain a separate reviewed hardening project.
