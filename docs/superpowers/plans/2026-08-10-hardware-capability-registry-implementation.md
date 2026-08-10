# Torget Hardware Capability Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give Codex and Claude Code one validated, source-backed record of what the ESP32-S3 silicon can do, what the Waveshare board wires up, what Torget enables, and what the physical unit has actually verified.

**Architecture:** Keep human guidance in `spec/hardware.md`, canonical facts in three YAML registries, and validation in a small Python module using PyYAML. Agent instruction files contain only routing and behavior rules; they never duplicate hardware facts. Physical-unit evidence and product opportunities remain distinct from vendor claims.

**Tech Stack:** YAML 1.2, Python 3.11+, PyYAML 6.0.3, `unittest`, Markdown, existing Torget host-test runner.

---

## File responsibility map

- `requirements-dev.txt`: pin the non-runtime Python tools used by repository validation and Studio.
- `tools/hardware_registry.py`: load and validate sources, capabilities, and unit records.
- `tools/test_hardware_registry.py`: test schema failures, provenance, conflicts, and repository data.
- `spec/hardware-sources.yaml`: stable IDs and metadata for every external or local evidence source.
- `spec/hardware-capabilities.yaml`: canonical capability truth states and resource constraints.
- `spec/device-units.yaml`: non-secret inventory and physical verification state for each unit.
- `spec/hardware-opportunities.md`: ideas enabled by known hardware, with prerequisites and status.
- `spec/hardware.md`: concise human entry point and known traps.
- `AGENTS.md`: route Codex and other compatible agents to the registry.
- `CLAUDE.md`: route Claude Code to the same files and rules.
- `test/run.sh`: run registry validation with the existing host suite.
- `README.md`: explain where hardware truth lives and how to validate it.

### Task 1: Add a pinned registry parser and failing schema tests

**Files:**
- Create: `requirements-dev.txt`
- Create: `tools/hardware_registry.py`
- Create: `tools/test_hardware_registry.py`

- [ ] **Step 1: Pin the shared development dependencies**

Create `requirements-dev.txt` with:

```text
PyYAML==6.0.3
Pillow==12.3.0
zeroconf==0.150.0
```

Only PyYAML is used in this plan. Pillow and zeroconf are pinned now because the approved Studio plans consume the same development environment.

- [ ] **Step 2: Write failing tests for the truth model**

Create `tools/test_hardware_registry.py` with temporary YAML fixtures and these test cases:

```python
import tempfile
import unittest
from pathlib import Path

from tools.hardware_registry import RegistryError, load_registry, resolve_claim


VALID_SOURCE = {
    "id": "physical",
    "kind": "physical-test",
    "rank": 1,
    "title": "Display smoke test",
    "publisher": "Torget",
    "locator": "test://torget-home-01/display-smoke",
    "revision": "2026-08-06",
    "accessed": "2026-08-10",
}

VALID_UNIT = {
    "id": "torget-home-01",
    "friendly_name": "Torget hemma",
    "board": "waveshare-esp32-s3-touch-amoled-2.16",
    "sku_evidence": "physical-device-and-working-bsp",
    "board_revision": "unknown",
    "enclosure": "white-square-enclosure",
    "speaker": "unknown",
    "battery": "not_fitted",
    "microsd": "unknown",
    "antenna": "onboard",
    "installed_firmware": "unknown",
    "last_physical_verification": "2026-08-06",
    "secrets": False,
}

VALID_CAPABILITY = {
    "id": "display.amoled",
    "name": "480 x 480 AMOLED",
    "states": {
        "soc_capable": "yes",
        "board_wired": "yes",
        "bsp_support": "yes",
        "firmware_enabled": "yes",
        "unit_verified": "yes",
    },
    "confidence": "measured",
    "resources": ["SPI2_HOST"],
    "constraints": ["40 MHz QSPI"],
    "conflicts": [],
    "opportunities": ["amoled-ui"],
    "sources": ["physical"],
    "evidence": [
        {"field": "soc_capable", "value": "yes", "source": "physical"},
        {"field": "board_wired", "value": "yes", "source": "physical"},
        {"field": "bsp_support", "value": "yes", "source": "physical"},
        {"field": "firmware_enabled", "value": "yes", "source": "physical"},
        {"field": "unit_verified", "value": "yes", "source": "physical"},
    ],
    "last_verified": "2026-08-06",
    "verification": {"unit": "torget-home-01", "test": "display-smoke"},
}


class RegistryValidationTests(unittest.TestCase):
    def write_yaml(self, root, name, value):
        import yaml
        path = Path(root) / name
        path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")
        return path

    def load(self, capability=None, sources=None, unit=None):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            raw_capabilities = capability or VALID_CAPABILITY
            if not isinstance(raw_capabilities, list):
                raw_capabilities = [raw_capabilities]
            self.write_yaml(root, "hardware-sources.yaml", {
                "schema_version": 1,
                "sources": sources or [VALID_SOURCE],
            })
            self.write_yaml(root, "hardware-capabilities.yaml", {
                "schema_version": 1,
                "board": "waveshare-esp32-s3-touch-amoled-2.16",
                "capabilities": raw_capabilities,
            })
            self.write_yaml(root, "device-units.yaml", {
                "schema_version": 1,
                "units": [unit or VALID_UNIT],
            })
            return load_registry(root)

    def test_valid_registry_loads(self):
        registry = self.load()
        self.assertEqual(registry.capabilities["display.amoled"]["confidence"],
                         "measured")

    def test_unknown_source_is_rejected(self):
        value = dict(VALID_CAPABILITY, sources=["missing"])
        with self.assertRaisesRegex(RegistryError, "unknown source missing"):
            self.load(capability=value)

    def test_invalid_state_is_rejected(self):
        value = dict(VALID_CAPABILITY)
        value["states"] = dict(VALID_CAPABILITY["states"], board_wired="maybe")
        with self.assertRaisesRegex(RegistryError, "board_wired"):
            self.load(capability=value)

    def test_measured_claim_requires_unit_and_test(self):
        value = dict(VALID_CAPABILITY)
        value.pop("verification")
        with self.assertRaisesRegex(RegistryError, "verification"):
            self.load(capability=value)

    def test_duplicate_capability_is_rejected(self):
        with self.assertRaisesRegex(RegistryError, "duplicate capability"):
            self.load(capability=[VALID_CAPABILITY, VALID_CAPABILITY])

    def test_higher_ranked_physical_evidence_wins_a_vendor_conflict(self):
        sources = {
            "physical": {"rank": 1},
            "vendor": {"rank": 4},
        }
        value, conflicts = resolve_claim([
            {"source": "vendor", "value": "CST9220"},
            {"source": "physical", "value": "CST9217"},
        ], sources)
        self.assertEqual(value, "CST9217")
        self.assertEqual(conflicts, ["vendor"])

    def test_unit_records_cannot_contain_secret_fields(self):
        bad_unit = dict(VALID_UNIT, wifi_password="bad")
        with self.assertRaisesRegex(RegistryError, "secret field wifi_password"):
            self.load(unit=bad_unit)
```

- [ ] **Step 3: Run the focused tests and verify the import fails**

Run:

```bash
python3 -m unittest tools.test_hardware_registry -v
```

Expected: FAIL because `tools.hardware_registry` does not exist.

- [ ] **Step 4: Implement the minimal validator**

Create `tools/hardware_registry.py` with these public types and checks:

```python
from dataclasses import dataclass
from pathlib import Path

import yaml

STATE_VALUES = {"yes", "no", "unknown", "not_applicable"}
STATE_KEYS = {
    "soc_capable", "board_wired", "bsp_support",
    "firmware_enabled", "unit_verified",
}
CONFIDENCE_VALUES = {
    "measured", "schematic", "source_inspected",
    "vendor_claimed", "unverified",
}
SECRET_FIELD_PARTS = {"secret", "password", "pass", "token", "ssid", "key"}


class RegistryError(ValueError):
    pass


@dataclass(frozen=True)
class HardwareRegistry:
    sources: dict
    capabilities: dict
    units: dict


def _read(path):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise RegistryError(f"{path}: schema_version must be 1")
    return value


def _unique(items, label):
    result = {}
    for item in items:
        item_id = item.get("id") if isinstance(item, dict) else None
        if not item_id:
            raise RegistryError(f"{label} entry has no id")
        if item_id in result:
            raise RegistryError(f"duplicate {label} {item_id}")
        result[item_id] = item
    return result


def resolve_claim(evidence, sources):
    ordered = sorted(evidence, key=lambda item: sources[item["source"]]["rank"])
    if not ordered:
        raise RegistryError("claim has no evidence")
    winner = ordered[0]
    conflicts = [item["source"] for item in ordered[1:]
                 if item["value"] != winner["value"]]
    return winner["value"], conflicts


def load_registry(root):
    root = Path(root)
    source_doc = _read(root / "hardware-sources.yaml")
    capability_doc = _read(root / "hardware-capabilities.yaml")
    unit_doc = _read(root / "device-units.yaml")
    sources = _unique(source_doc.get("sources", []), "source")
    units = _unique(unit_doc.get("units", []), "unit")
    raw_capabilities = capability_doc.get("capabilities", [])
    if isinstance(raw_capabilities, dict):
        raw_capabilities = [raw_capabilities]
    capabilities = _unique(raw_capabilities, "capability")

    for source_id, source in sources.items():
        for key in ("kind", "rank", "title", "publisher", "locator",
                    "revision", "accessed"):
            if source.get(key) in (None, ""):
                raise RegistryError(f"source {source_id}: missing {key}")

    required_unit_keys = {
        "friendly_name", "board", "sku_evidence", "board_revision",
        "enclosure", "speaker", "battery", "microsd", "antenna",
        "installed_firmware", "last_physical_verification", "secrets",
    }
    for unit_id, unit in units.items():
        missing = required_unit_keys - set(unit)
        if missing:
            raise RegistryError(f"unit {unit_id}: missing {sorted(missing)}")
        if unit["secrets"] is not False:
            raise RegistryError(f"unit {unit_id}: secrets must be false")
        for key in unit:
            if key != "secrets" and any(part in key.lower()
                                        for part in SECRET_FIELD_PARTS):
                raise RegistryError(f"unit {unit_id}: secret field {key}")

    for capability_id, capability in capabilities.items():
        states = capability.get("states")
        if not isinstance(states, dict) or set(states) != STATE_KEYS:
            raise RegistryError(f"{capability_id}: states must be {sorted(STATE_KEYS)}")
        for key, value in states.items():
            if value not in STATE_VALUES:
                raise RegistryError(f"{capability_id}: invalid {key}={value}")
        if capability.get("confidence") not in CONFIDENCE_VALUES:
            raise RegistryError(f"{capability_id}: invalid confidence")
        for source_id in capability.get("sources", []):
            if source_id not in sources:
                raise RegistryError(f"{capability_id}: unknown source {source_id}")
        for finding in capability.get("evidence", []):
            field = finding.get("field")
            source_id = finding.get("source")
            if field not in STATE_KEYS or source_id not in sources:
                raise RegistryError(f"{capability_id}: invalid evidence")
        for field in STATE_KEYS:
            evidence = [item for item in capability.get("evidence", [])
                        if item.get("field") == field]
            if states[field] != "unknown" and not evidence:
                raise RegistryError(
                    f"{capability_id}: {field} has no evidence")
            if evidence:
                resolved, _ = resolve_claim(evidence, sources)
                if resolved != states[field]:
                    raise RegistryError(
                        f"{capability_id}: {field} contradicts ranked evidence")
        if states["unit_verified"] == "yes":
            verification = capability.get("verification") or {}
            if verification.get("unit") not in units or not verification.get("test"):
                raise RegistryError(f"{capability_id}: verification needs known unit and test")
        for key in ("name", "resources", "constraints", "conflicts",
                    "opportunities", "sources", "evidence", "last_verified"):
            if key not in capability:
                raise RegistryError(f"{capability_id}: missing {key}")

    return HardwareRegistry(sources=sources, capabilities=capabilities,
                            units=units)
```

- [ ] **Step 5: Run the tests and commit the validator**

Run:

```bash
python3 -m unittest tools.test_hardware_registry -v
```

Expected: all registry unit tests pass.

Commit:

```bash
git add requirements-dev.txt tools/hardware_registry.py tools/test_hardware_registry.py
git commit -m "Add hardware registry validator"
```

### Task 2: Add the canonical source ledger and device inventory

**Files:**
- Create: `spec/hardware-sources.yaml`
- Create: `spec/hardware-capabilities.yaml`
- Create: `spec/device-units.yaml`
- Modify: `tools/test_hardware_registry.py`

- [ ] **Step 1: Add a failing repository-data test**

Add:

```python
class RepositoryRegistryTests(unittest.TestCase):
    def test_repository_registry_loads(self):
        root = Path(__file__).resolve().parents[1] / "spec"
        registry = load_registry(root)
        self.assertIn("waveshare-schematic-2026-08-10", registry.sources)
        self.assertIn("torget-home-01", registry.units)
```

- [ ] **Step 2: Run it and verify the canonical files are missing**

Run:

```bash
python3 -m unittest tools.test_hardware_registry.RepositoryRegistryTests -v
```

Expected: FAIL with missing `spec/hardware-sources.yaml`.

- [ ] **Step 3: Create the source ledger**

Create `spec/hardware-sources.yaml` with `schema_version: 1` and these stable IDs:

| ID | Kind | Rank | Locator/revision |
|---|---:|---:|---|
| `torget-physical-2026-08-06` | physical-test | 1 | `spec/hardware.md`, unit `torget-home-01`, findings dated 2026-08-06 |
| `waveshare-schematic-2026-08-10` | schematic | 2 | official schematic PDF, downloaded 2026-08-10 |
| `waveshare-bsp-2.0.1` | source-code | 3 | locked component `waveshare/esp32_s3_touch_amoled_2_16` 2.0.1 |
| `waveshare-cst9217-driver-1.0.0` | source-code | 3 | locked CST9217 component revision from `dependencies.lock` |
| `torget-main-1fad449` | source-code | 3 | local Torget commit `1fad449` |
| `waveshare-board-docs-2026-08-10` | vendor-doc | 4 | official board documentation accessed 2026-08-10 |
| `esp32s3-datasheet-2026-08-10` | silicon-doc | 5 | official ESP32-S3 datasheet accessed 2026-08-10 |
| `esp-idf-5.5-ota` | framework-doc | 5 | official ESP-IDF 5.5 OTA/rollback documentation |
| `esp-idf-5.5-usb` | framework-doc | 5 | official ESP-IDF 5.5 USB device documentation |

Each YAML record must include all fields required by the validator. Add `board_revision: unknown` where the source is board-specific but no silkscreen revision has been physically captured; do not invent a revision.

- [ ] **Step 4: Create the non-secret unit inventory**

Create `spec/device-units.yaml` with:

```yaml
schema_version: 1
units:
  - id: torget-home-01
    friendly_name: Torget hemma
    board: waveshare-esp32-s3-touch-amoled-2.16
    sku_evidence: physical-device-and-working-bsp
    board_revision: unknown
    enclosure: white-square-enclosure
    speaker: unknown
    battery: not_fitted
    microsd: unknown
    antenna: onboard
    installed_firmware: unknown-after-next-flash
    last_physical_verification: "2026-08-06"
    secrets: false
```

Do not add SSIDs, local IPs, bearer tokens, API tokens, or values copied from `secrets.h`.

- [ ] **Step 5: Add an empty but valid capability document**

Create the registry shell that Task 3 will populate:

```yaml
schema_version: 1
board: waveshare-esp32-s3-touch-amoled-2.16
capabilities: []
```

- [ ] **Step 6: Run the test and commit the evidence foundation**

Run:

```bash
python3 -m unittest tools.test_hardware_registry -v
```

Expected: all tests pass against the empty capability document and populated source/unit files. Commit the independent evidence foundation:

```bash
git add spec/hardware-sources.yaml spec/hardware-capabilities.yaml spec/device-units.yaml tools/test_hardware_registry.py
git commit -m "Record Torget hardware sources and physical unit"
```

### Task 3: Populate the capability registry without collapsing uncertainty

**Files:**
- Modify: `spec/hardware-capabilities.yaml`
- Modify: `tools/test_hardware_registry.py`

- [ ] **Step 1: Require the critical distinctions in the repository test**

Add assertions:

```python
registry = load_registry(Path(__file__).resolve().parents[1] / "spec")
self.assertEqual(registry.capabilities["radio.bluetooth-le"]["states"]["firmware_enabled"], "no")
self.assertEqual(registry.capabilities["touch.controller"]["states"]["unit_verified"], "unknown")
self.assertEqual(registry.capabilities["audio.speaker-output"]["states"]["unit_verified"], "unknown")
self.assertEqual(registry.capabilities["sensors.ambient-light"]["states"]["board_wired"], "no")
self.assertEqual(registry.capabilities["rtc.pcf85063atl"]["constraints"][0],
                 "battery backup is not physically verified")
self.assertEqual(registry.capabilities["usb.host"]["states"]["soc_capable"], "yes")
self.assertEqual(registry.capabilities["usb.host"]["states"]["board_wired"], "unknown")
```

- [ ] **Step 2: Run the test and verify the required capabilities are missing**

Run:

```bash
python3 -m unittest tools.test_hardware_registry.RepositoryRegistryTests -v
```

Expected: FAIL because the empty registry has no `radio.bluetooth-le` entry.

- [ ] **Step 3: Create every required capability entry**

Create `spec/hardware-capabilities.yaml` using the schema from Task 1. Populate these IDs with evidence from the approved design and `spec/hardware.md`:

```text
compute.esp32s3r8             display.amoled
memory.psram                  touch.controller
radio.wifi-24                 radio.bluetooth-le
audio.microphones             audio.speaker-output
sensors.imu-qmi8658           sensors.ambient-light
power.axp2101                 power.battery-connector
rtc.pcf85063atl               storage.microsd
usb.device                    usb.host
input.key3                    input.boot-button
antenna.onboard               antenna.ipex-mod
expansion.shared-i2c          security.secure-boot-v2
security.flash-encryption     update.ota-ab-rollback
soc.ulp                       soc.hardware-crypto-rng
soc.adc                       soc.die-temperature
soc.capacitive-touch          soc.pwm-rmt-twai
```

Every non-`unknown` state gets at least one `evidence` record naming the state
field, value, and source ID. If sources disagree, retain both findings; the
lowest source-rank number must resolve to the stored state and the disagreement
must also be described under `conflicts`. Add immutable display properties to
`display.amoled`: `width: 480`, `height: 480`, `color_format: RGB565`,
`byte_order: big_endian`, `bus: QSPI`, and `bus_mhz: 40`. Studio will read
these values instead of copying the resolution into another config.

Use `yes/no/unknown/not_applicable` for all five states. In particular:

- Wi-Fi, AMOLED, PSRAM, IMU rotation, touch interaction, native USB device, and KEY3 may be `unit_verified: yes` only when linked to `torget-home-01` and a named finding in `spec/hardware.md`.
- Bluetooth LE is silicon/board/BSP capable but `firmware_enabled: no` and `unit_verified: unknown`.
- Touch keeps the CST9220-vs-CST9217 conflict in `conflicts`; it does not erase either claim.
- Speaker output and microphones remain physically unknown until separate tests run.
- Ambient light has `board_wired: no`; buzzer and haptic absence belong in its constraints or audio constraints rather than fabricated capability entries.
- RTC starts with the exact first constraint `battery backup is not physically verified`.
- USB host has `soc_capable: yes`, `board_wired: unknown`, and names VBUS/current-limit/PHY-sharing constraints.
- IPEX is a hardware modification requiring resistor changes, not a firmware toggle.
- Secure boot, flash encryption, and OTA have `firmware_enabled: no` at this commit.
- Die temperature explicitly says it is not room temperature.

- [ ] **Step 4: Add cross-field tests for impossible claims**

Extend the validator and tests so:

```python
if states["unit_verified"] == "yes" and states["board_wired"] != "yes":
    raise RegistryError(f"{capability_id}: verified capability must be board_wired")
if states["firmware_enabled"] == "yes" and states["bsp_support"] == "no":
    raise RegistryError(f"{capability_id}: enabled capability has no software support")
```

Add one failing test for each rule before adding the implementation.

- [ ] **Step 5: Validate and commit the canonical facts**

Run:

```bash
python3 -m unittest tools.test_hardware_registry -v
python3 tools/hardware_registry.py spec
```

Add a CLI footer to `hardware_registry.py` that loads the provided path and prints `OK: N capabilities, N sources, N units`. Expected: both commands pass and the CLI reports all 30 capability entries.

Commit:

```bash
git add spec/hardware-capabilities.yaml tools/hardware_registry.py tools/test_hardware_registry.py
git commit -m "Add source-backed Torget capability registry"
```

### Task 4: Add the opportunity radar and agent routing

**Files:**
- Create: `spec/hardware-opportunities.md`
- Create: `CLAUDE.md`
- Modify: `AGENTS.md`
- Modify: `tools/test_hardware_registry.py`

- [ ] **Step 1: Add failing routing tests**

Add a repository test that reads both instruction files and asserts that each contains all four canonical paths:

```python
for name in ("AGENTS.md", "CLAUDE.md"):
    text = (repo / name).read_text(encoding="utf-8")
    for path in ("spec/hardware.md", "spec/hardware-capabilities.yaml",
                 "spec/hardware-sources.yaml", "spec/hardware-opportunities.md"):
        self.assertIn(path, text, f"{name} does not route to {path}")
    self.assertIn("silicon-capable", text)
    self.assertIn("physically verified", text)
```

Run the focused test. Expected: FAIL because `CLAUDE.md` is absent and `AGENTS.md` lacks the routing rule.

- [ ] **Step 2: Create the opportunity radar**

Create `spec/hardware-opportunities.md` with one concise table containing these exact candidates:

| ID | Status | Required capabilities | Physical prerequisite |
|---|---|---|---|
| `local-ota` | designed | Wi-Fi, OTA A/B | one final USB bootstrap |
| `completion-audio` | candidate | ES8311, speaker output | confirm attached speaker and safe volume |
| `ble-provisioning` | candidate | BLE, Wi-Fi | radio/memory measurement |
| `motion-gestures` | candidate | QMI8658 | enclosure false-positive test |
| `battery-mode` | idea | AXP2101, battery connector | cell/polarity/current/thermal verification |
| `rtc-wake` | idea | PCF85063ATL, PMU | verify backup supply and wake path |
| `microsd-history` | idea | one-bit SDMMC | insertion/removal/write-interruption test |
| `native-usb-modes` | idea | USB device | reconcile Buddy and USB debug ownership |
| `voice-controls` | idea | microphones, speaker, network | privacy UI and full-duplex audio test |

For each candidate add user value, conflicts, privacy implications, and why it is not automatically authorized work.

- [ ] **Step 3: Route both agents to one source of truth**

Append this block to `AGENTS.md` and create `CLAUDE.md` with the same block plus a one-line pointer to `README.md`:

```markdown
## Hardware-aware work

Before proposing external hardware, declaring a device limitation, or designing
a hardware-dependent feature, read `spec/hardware.md`,
`spec/hardware-capabilities.yaml`, `spec/hardware-sources.yaml`, and
`spec/hardware-opportunities.md`. State whether the idea is only
silicon-capable, board-wired, firmware-enabled, and physically verified on the
named unit. Mention a relevant unused onboard capability when it materially
improves the request. Never copy secrets or turn an opportunity into authorized
implementation work.
```

- [ ] **Step 4: Run routing tests and commit**

Run:

```bash
python3 -m unittest tools.test_hardware_registry -v
```

Expected: all tests pass.

Commit:

```bash
git add AGENTS.md CLAUDE.md spec/hardware-opportunities.md tools/test_hardware_registry.py
git commit -m "Route agents through Torget hardware knowledge"
```

### Task 5: Make validation part of the normal Torget workflow

**Files:**
- Modify: `test/run.sh`
- Modify: `spec/hardware.md`
- Modify: `README.md`

- [ ] **Step 1: Add the registry test to the host suite**

Append to `test/run.sh`:

```sh
python3 -m unittest tools.test_hardware_registry -v
```

Because the script currently changes into `test/`, first add `cd ..` immediately before the Python command, then return with `cd test` only if more test commands follow.

- [ ] **Step 2: Replace the stale hardware introduction**

At the top of `spec/hardware.md`, add:

```markdown
## How to read hardware truth

This file explains verified traps and context. Machine-readable state lives in
`hardware-capabilities.yaml`, source metadata in `hardware-sources.yaml`, and
per-device physical evidence in `device-units.yaml`. A feature of ESP32-S3 is
not automatically wired on this board, enabled in Torget, or verified on the
physical unit. Run `python3 tools/hardware_registry.py spec` after editing any
registry file.
```

Replace the stale RTC and remaining-gap wording with links to the corresponding capability IDs; keep the original dated findings as evidence.

- [ ] **Step 3: Document setup and lifecycle checks**

Add to `README.md`:

```markdown
## Hardware knowledge

Install development tools with `python3 -m pip install -r requirements-dev.txt`.
Both Codex and Claude Code read the validated registries under `spec/` before
hardware-dependent work. Run `python3 tools/hardware_registry.py spec` for a
focused check or `./test/run.sh` for the full host gate. Update the registry
when the board, BSP, ESP-IDF, partition table, enclosure, or physical evidence
changes; never store secrets in it.
```

- [ ] **Step 4: Run the full validation**

Run:

```bash
python3 -m pip install -r requirements-dev.txt
./test/run.sh
git diff --check
```

Expected: existing C/Python wiring tests and the registry suite all pass.

- [ ] **Step 5: Commit the workflow integration**

```bash
git add test/run.sh spec/hardware.md README.md
git commit -m "Validate hardware knowledge in the host gate"
```

## Completion gate

The plan is complete only when the registry CLI reports all entries, both agent files route to the same canonical data, the repository contains no secrets, and `./test/run.sh` passes. Do not enable Bluetooth, audio, battery operation, OTA, or irreversible security settings as part of this plan.
