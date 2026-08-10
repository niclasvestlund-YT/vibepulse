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

    def load_document(self, name, value):
        documents = {
            "hardware-sources.yaml": {
                "schema_version": 1,
                "sources": [VALID_SOURCE],
            },
            "hardware-capabilities.yaml": {
                "schema_version": 1,
                "board": "waveshare-esp32-s3-touch-amoled-2.16",
                "capabilities": [VALID_CAPABILITY],
            },
            "device-units.yaml": {
                "schema_version": 1,
                "units": [VALID_UNIT],
            },
        }
        documents[name] = value
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename, document in documents.items():
                self.write_yaml(root, filename, document)
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

    def test_nonstring_state_is_rejected_as_a_schema_error(self):
        value = dict(VALID_CAPABILITY)
        value["states"] = dict(VALID_CAPABILITY["states"], board_wired=[])
        with self.assertRaisesRegex(RegistryError, "invalid board_wired"):
            self.load(capability=value)

    def test_nonstring_confidence_is_rejected_as_a_schema_error(self):
        value = dict(VALID_CAPABILITY, confidence=[])
        with self.assertRaisesRegex(RegistryError, "invalid confidence"):
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

    def test_equal_rank_conflict_is_rejected_in_forward_order(self):
        sources = {
            "vendor-a": {"rank": 3},
            "vendor-b": {"rank": 3},
        }
        with self.assertRaisesRegex(RegistryError, "ambiguous claim at rank 3"):
            resolve_claim([
                {"source": "vendor-a", "value": "CST9220"},
                {"source": "vendor-b", "value": "CST9217"},
            ], sources)

    def test_equal_rank_conflict_is_rejected_in_reverse_order(self):
        sources = {
            "vendor-a": {"rank": 3},
            "vendor-b": {"rank": 3},
        }
        with self.assertRaisesRegex(RegistryError, "ambiguous claim at rank 3"):
            resolve_claim([
                {"source": "vendor-b", "value": "CST9217"},
                {"source": "vendor-a", "value": "CST9220"},
            ], sources)

    def test_top_level_collections_are_required(self):
        documents = {
            "hardware-sources.yaml": "sources",
            "hardware-capabilities.yaml": "capabilities",
            "device-units.yaml": "units",
        }
        for filename, collection in documents.items():
            with self.subTest(collection=collection):
                with self.assertRaisesRegex(RegistryError,
                                            f"missing {collection}"):
                    self.load_document(filename, {"schema_version": 1})

    def test_top_level_collections_must_be_lists(self):
        documents = {
            "hardware-sources.yaml": "sources",
            "hardware-capabilities.yaml": "capabilities",
            "device-units.yaml": "units",
        }
        for filename, collection in documents.items():
            with self.subTest(collection=collection):
                with self.assertRaisesRegex(
                        RegistryError, f"{collection} must be a list"):
                    self.load_document(filename, {
                        "schema_version": 1,
                        collection: {"id": "not-a-list"},
                    })

    def test_ids_must_be_nonempty_strings(self):
        for bad_id in (12, "  "):
            with self.subTest(bad_id=bad_id):
                source = dict(VALID_SOURCE, id=bad_id)
                with self.assertRaisesRegex(
                        RegistryError, "id must be a nonempty string"):
                    self.load(sources=[source])

    def test_source_rank_must_be_an_integer(self):
        for bad_rank in ("1", True):
            with self.subTest(bad_rank=bad_rank):
                source = dict(VALID_SOURCE, rank=bad_rank)
                with self.assertRaisesRegex(
                        RegistryError, "rank must be an integer"):
                    self.load(sources=[source])

    def test_evidence_entries_must_be_mappings(self):
        value = dict(VALID_CAPABILITY, evidence=["not-a-mapping"])
        with self.assertRaisesRegex(RegistryError,
                                    "evidence entry must be a mapping"):
            self.load(capability=value)

    def test_evidence_requires_field_value_and_source(self):
        evidence = [dict(finding) for finding in VALID_CAPABILITY["evidence"]]
        evidence[0].pop("value")
        value = dict(VALID_CAPABILITY, evidence=evidence)
        with self.assertRaisesRegex(RegistryError,
                                    "evidence missing.*value"):
            self.load(capability=value)

    def test_evidence_values_must_be_states(self):
        evidence = [dict(finding) for finding in VALID_CAPABILITY["evidence"]]
        evidence[0]["value"] = "maybe"
        value = dict(VALID_CAPABILITY, evidence=evidence)
        with self.assertRaisesRegex(RegistryError,
                                    "invalid evidence value maybe"):
            self.load(capability=value)

    def test_verification_must_be_a_mapping_when_present(self):
        value = dict(VALID_CAPABILITY, verification="not-a-mapping")
        with self.assertRaisesRegex(RegistryError,
                                    "verification must be a mapping"):
            self.load(capability=value)

    def test_capability_list_fields_must_be_lists(self):
        for field in ("resources", "constraints", "conflicts",
                      "opportunities", "sources", "evidence"):
            with self.subTest(field=field):
                value = dict(VALID_CAPABILITY, **{field: "not-a-list"})
                with self.assertRaisesRegex(
                        RegistryError, f"{field} must be a list"):
                    self.load(capability=value)

    def test_unit_records_cannot_contain_secret_fields(self):
        bad_unit = dict(VALID_UNIT, wifi_password="bad")
        with self.assertRaisesRegex(RegistryError, "secret field wifi_password"):
            self.load(unit=bad_unit)

    def test_unit_records_cannot_contain_nested_secret_fields(self):
        bad_unit = dict(VALID_UNIT, credentials={"wifi_password": "bad"})
        with self.assertRaisesRegex(RegistryError, "secret field wifi_password"):
            self.load(unit=bad_unit)
