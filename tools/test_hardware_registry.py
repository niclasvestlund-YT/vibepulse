import subprocess
import sys
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

    def test_verified_capability_must_be_board_wired(self):
        value = dict(VALID_CAPABILITY)
        value["states"] = dict(
            VALID_CAPABILITY["states"], board_wired="unknown",
        )
        value["evidence"] = [
            dict(finding) for finding in VALID_CAPABILITY["evidence"]
            if finding["field"] != "board_wired"
        ]
        with self.assertRaisesRegex(
                RegistryError, "verified capability must be board_wired"):
            self.load(capability=value)

    def test_enabled_capability_must_have_software_support(self):
        value = dict(VALID_CAPABILITY)
        value["states"] = dict(
            VALID_CAPABILITY["states"], bsp_support="no",
        )
        value["evidence"] = [
            dict(finding, value="no")
            if finding["field"] == "bsp_support" else dict(finding)
            for finding in VALID_CAPABILITY["evidence"]
        ]
        with self.assertRaisesRegex(
                RegistryError, "enabled capability has no software support"):
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

    def test_cli_returns_nonzero_for_invalid_registry_path(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(repo / "tools/hardware_registry.py"),
             str(repo / "spec/does-not-exist")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("OK:", result.stdout)


class RepositoryRegistryTests(unittest.TestCase):
    @staticmethod
    def load_repository_registry():
        root = Path(__file__).resolve().parents[1] / "spec"
        return load_registry(root)

    def test_imu_is_not_claimed_as_physically_verified(self):
        registry = self.load_repository_registry()
        capability = registry.capabilities["sensors.imu-qmi8658"]
        self.assertEqual(
            capability["states"]["unit_verified"],
            "unknown",
        )
        self.assertNotIn("verification", capability)
        self.assertFalse(any(
            finding["field"] == "unit_verified"
            for finding in capability["evidence"]
        ))

    def test_usb_device_is_not_claimed_as_physically_verified(self):
        registry = self.load_repository_registry()
        capability = registry.capabilities["usb.device"]
        self.assertEqual(
            capability["states"]["unit_verified"],
            "unknown",
        )
        self.assertNotIn("verification", capability)
        self.assertFalse(any(
            finding["field"] == "unit_verified"
            for finding in capability["evidence"]
        ))

    def test_only_dated_physical_findings_are_unit_verified(self):
        registry = self.load_repository_registry()
        verified_ids = {
            capability_id
            for capability_id, capability in registry.capabilities.items()
            if capability["states"]["unit_verified"] == "yes"
        }
        self.assertEqual(
            verified_ids,
            {"display.amoled", "radio.wifi-24"},
        )
        for capability_id in verified_ids:
            with self.subTest(capability=capability_id):
                capability = registry.capabilities[capability_id]
                self.assertEqual(
                    capability["verification"]["unit"],
                    "torget-home-01",
                )
                self.assertTrue(capability["verification"]["test"].strip())
                sources = {
                    finding["source"] for finding in capability["evidence"]
                    if finding["field"] == "unit_verified"
                }
                self.assertEqual(sources, {"torget-physical-2026-08-06"})

    def test_repository_required_truth_distinctions(self):
        registry = self.load_repository_registry()

        for capability_id in (
                "audio.microphones", "audio.speaker-output"):
            with self.subTest(capability=capability_id):
                self.assertEqual(
                    registry.capabilities[capability_id]["states"][
                        "unit_verified"
                    ],
                    "unknown",
                )

        self.assertEqual(
            registry.capabilities["radio.bluetooth-le"]["states"],
            {
                "soc_capable": "yes",
                "board_wired": "yes",
                "bsp_support": "yes",
                "firmware_enabled": "no",
                "unit_verified": "unknown",
            },
        )

        touch = registry.capabilities["touch.controller"]
        touch_conflicts = " ".join(touch["conflicts"])
        self.assertIn("CST9220", touch_conflicts)
        self.assertIn("CST9217", touch_conflicts)
        self.assertTrue({
            "waveshare-board-docs-2026-08-10",
            "waveshare-cst9217-driver-1.0.0",
        }.issubset(touch["sources"]))

        usb_host = registry.capabilities["usb.host"]
        self.assertEqual(usb_host["states"]["soc_capable"], "yes")
        self.assertEqual(usb_host["states"]["board_wired"], "unknown")
        usb_host_constraints = " ".join(usb_host["constraints"]).lower()
        self.assertIn("vbus", usb_host_constraints)
        self.assertRegex(usb_host_constraints, r"current[- ]limit")
        self.assertIn("phy", usb_host_constraints)
        self.assertIn("shared", usb_host_constraints)

        ipex = registry.capabilities["antenna.ipex-mod"]
        self.assertEqual(ipex["states"]["board_wired"], "no")
        self.assertEqual(
            ipex["states"]["firmware_enabled"],
            "not_applicable",
        )
        self.assertTrue(any(
            "resistor" in constraint.lower()
            for constraint in ipex["constraints"]
        ))

        for capability_id in (
                "security.secure-boot-v2",
                "security.flash-encryption",
                "update.ota-ab-rollback"):
            with self.subTest(capability=capability_id):
                self.assertEqual(
                    registry.capabilities[capability_id]["states"][
                        "firmware_enabled"
                    ],
                    "no",
                )

        die_temperature = registry.capabilities["soc.die-temperature"]
        self.assertTrue(any(
            "not room temperature" in constraint.lower()
            for constraint in die_temperature["constraints"]
        ))

    def test_repository_cli_counts_records(self):
        repo = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(repo / "tools/hardware_registry.py"),
             str(repo / "spec")],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout,
            "OK: 30 capabilities, 9 sources, 1 units\n",
        )

    def test_repository_registry_loads(self):
        root = Path(__file__).resolve().parents[1] / "spec"
        registry = load_registry(root)
        expected_capabilities = {
            "compute.esp32s3r8",
            "display.amoled",
            "memory.psram",
            "touch.controller",
            "radio.wifi-24",
            "radio.bluetooth-le",
            "audio.microphones",
            "audio.speaker-output",
            "sensors.imu-qmi8658",
            "sensors.ambient-light",
            "power.axp2101",
            "power.battery-connector",
            "rtc.pcf85063atl",
            "storage.microsd",
            "usb.device",
            "usb.host",
            "input.key3",
            "input.boot-button",
            "antenna.onboard",
            "antenna.ipex-mod",
            "expansion.shared-i2c",
            "security.secure-boot-v2",
            "security.flash-encryption",
            "update.ota-ab-rollback",
            "soc.ulp",
            "soc.hardware-crypto-rng",
            "soc.adc",
            "soc.die-temperature",
            "soc.capacitive-touch",
            "soc.pwm-rmt-twai",
        }
        self.assertEqual(set(registry.capabilities), expected_capabilities)
        self.assertEqual(len(registry.capabilities), 30)

        display = registry.capabilities["display.amoled"]
        self.assertEqual(
            {key: display[key] for key in (
                "width", "height", "color_format", "byte_order", "bus",
                "bus_mhz",
            )},
            {
                "width": 480,
                "height": 480,
                "color_format": "RGB565",
                "byte_order": "big_endian",
                "bus": "QSPI",
                "bus_mhz": 40,
            },
        )
        for capability_id, capability in registry.capabilities.items():
            evidenced_fields = {
                finding["field"] for finding in capability["evidence"]
            }
            expected_fields = {
                field for field, value in capability["states"].items()
                if value != "unknown"
            }
            with self.subTest(capability=capability_id):
                self.assertTrue(expected_fields.issubset(evidenced_fields))

        expected_sources = {
            "torget-physical-2026-08-06": (
                "physical-test", 1,
                "findings-2026-08-06; unit=torget-home-01",
            ),
            "waveshare-schematic-2026-08-10": (
                "schematic", 2, "downloaded-2026-08-10",
            ),
            "waveshare-bsp-2.0.1": ("source-code", 3, "2.0.1"),
            "waveshare-cst9217-driver-1.0.0": (
                "source-code", 3, "2.0.0",
            ),
            "torget-main-1fad449": ("source-code", 3, "1fad449"),
            "waveshare-board-docs-2026-08-10": (
                "vendor-doc", 4, "accessed-2026-08-10",
            ),
            "esp32s3-datasheet-2026-08-10": (
                "silicon-doc", 5, "accessed-2026-08-10",
            ),
            "esp-idf-5.5-ota": ("framework-doc", 5, "ESP-IDF-v5.5"),
            "esp-idf-5.5-usb": ("framework-doc", 5, "ESP-IDF-v5.5"),
        }
        self.assertEqual(set(expected_sources), set(registry.sources))
        for source_id, expected in expected_sources.items():
            with self.subTest(source=source_id):
                source = registry.sources[source_id]
                self.assertEqual(
                    (source["kind"], source["rank"], source["revision"]),
                    expected,
                )

        datasheet = registry.sources["esp32s3-datasheet-2026-08-10"]
        self.assertEqual(
            datasheet["locator"],
            "https://documentation.espressif.com/esp32-s3_datasheet_en.pdf",
        )
        cst9217 = registry.sources["waveshare-cst9217-driver-1.0.0"]
        self.assertEqual(cst9217["revision"], "2.0.0")
        self.assertEqual(
            cst9217.get("note"),
            "Legacy stable ID; dependencies.lock pins 2.0.0.",
        )

        self.assertEqual(
            registry.capabilities["radio.bluetooth-le"]["states"][
                "firmware_enabled"
            ],
            "no",
        )
        self.assertEqual(
            registry.capabilities["touch.controller"]["states"][
                "unit_verified"
            ],
            "unknown",
        )
        self.assertEqual(
            registry.capabilities["audio.speaker-output"]["states"][
                "unit_verified"
            ],
            "unknown",
        )
        self.assertEqual(
            registry.capabilities["sensors.ambient-light"]["states"][
                "board_wired"
            ],
            "no",
        )
        self.assertEqual(
            registry.capabilities["rtc.pcf85063atl"]["constraints"][0],
            "battery backup is not physically verified",
        )
        self.assertEqual(
            registry.capabilities["usb.host"]["states"]["soc_capable"],
            "yes",
        )
        self.assertEqual(
            registry.capabilities["usb.host"]["states"]["board_wired"],
            "unknown",
        )

        self.assertEqual(registry.units["torget-home-01"], {
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
            "installed_firmware": "unknown-after-next-flash",
            "last_physical_verification": "2026-08-06",
            "secrets": False,
        })
