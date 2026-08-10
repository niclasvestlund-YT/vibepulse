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
