import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.vibepulse_studio.design import (
    DesignError,
    generate_header,
    load_design,
    load_display,
    save_design,
    save_header,
    validate_design,
)


class DesignTests(unittest.TestCase):
    def setUp(self):
        self.repo = Path(__file__).resolve().parents[2]
        self.path = self.repo / "design/vibepulse/studio-design.json"
        self.display = load_display(self.repo / "spec")
        self.design = json.loads(self.path.read_text(encoding="utf-8"))

    def test_repository_design_is_exact_and_provider_colors_are_locked(self):
        design = load_design(self.path, self.display)
        self.assertEqual(self.display, {"width": 480, "height": 480})
        self.assertNotIn("canvas", design)
        self.assertEqual(design["palette"]["claude"], "#D97757")
        self.assertEqual(design["palette"]["codex"], "#6F78FF")
        self.assertEqual(design["palette"]["background"], "#000000")
        self.assertNotIn("5-hour", json.dumps(design))

    def test_device_facts_cannot_be_changed(self):
        design = copy.deepcopy(self.design)
        design["canvas"] = {"width": 481, "height": 480}
        with self.assertRaisesRegex(DesignError, "device facts"):
            generate_header(design, self.display)

        design = copy.deepcopy(self.design)
        design["deviceCapability"] = "display.other"
        with self.assertRaisesRegex(DesignError, "device facts"):
            generate_header(design, self.display)

    def test_locked_colors_cannot_be_changed(self):
        for key, label in (("background", "Background"),
                           ("claude", "Claude"), ("codex", "Codex")):
            with self.subTest(key=key):
                design = copy.deepcopy(self.design)
                design["palette"][key] = "#123456"
                with self.assertRaisesRegex(DesignError, f"{label} color"):
                    generate_header(design, self.display)

    def test_palette_requires_exact_named_rgb_colors(self):
        design = copy.deepcopy(self.design)
        design["palette"]["text"] = "white"
        with self.assertRaisesRegex(DesignError, "#RRGGBB"):
            validate_design(design, self.display)

        design = copy.deepcopy(self.design)
        design["palette"].pop("hairline")
        with self.assertRaisesRegex(DesignError, "palette keys"):
            validate_design(design, self.display)

        design = copy.deepcopy(self.design)
        design["palette"]["extra"] = "#123456"
        with self.assertRaisesRegex(DesignError, "palette keys"):
            validate_design(design, self.display)

    def test_hero_contract_rejects_unknown_missing_and_boolean_values(self):
        design = copy.deepcopy(self.design)
        design["hero"]["unknown"] = 1
        with self.assertRaisesRegex(DesignError, "hero keys"):
            validate_design(design, self.display)

        design = copy.deepcopy(self.design)
        design["hero"]["safeX"] = True
        with self.assertRaisesRegex(DesignError, "integer panel pixels"):
            validate_design(design, self.display)

    def test_reviewed_geometry_bounds_are_enforced(self):
        cases = (
            ("safeX", 15, "safeX"),
            ("safeX", 41, "safeX"),
            ("contentWidth", 435, "contentWidth"),
            ("barHeight", 11, "barHeight"),
            ("barHeight", 25, "barHeight"),
            ("providerY", -1, "screen"),
            ("statusHeight", 100, "screen"),
            ("percentFontPx", 481, "screen"),
        )
        for key, value, message in cases:
            with self.subTest(key=key, value=value):
                design = copy.deepcopy(self.design)
                design["hero"][key] = value
                with self.assertRaisesRegex(DesignError, message):
                    validate_design(design, self.display)

    def test_schema_and_document_shape_are_versioned(self):
        design = copy.deepcopy(self.design)
        design["schemaVersion"] = True
        with self.assertRaisesRegex(DesignError, "schemaVersion"):
            validate_design(design, self.display)

        design = copy.deepcopy(self.design)
        design["unexpected"] = 1
        with self.assertRaisesRegex(DesignError, "document keys"):
            validate_design(design, self.display)

    def test_header_generation_is_deterministic_and_complete(self):
        design = load_design(self.path, self.display)
        first = generate_header(design, self.display)
        second = generate_header(design, self.display)
        self.assertEqual(first, second)
        self.assertIn("#define VP_SCREEN_W 480", first)
        self.assertIn("#define VP_SCREEN_H 480", first)
        self.assertIn("#define VP_SAFE_X 22", first)
        self.assertIn("#define VP_STATUS_H 66", first)
        self.assertIn("#define VP_COLOR_CLAUDE 0xD97757", first)
        self.assertIn("#define VP_COLOR_CODEX 0x6F78FF", first)
        self.assertEqual(first.count("#define VP_COLOR_"), 7)

    def test_save_is_atomic_and_reloads(self):
        design = load_design(self.path, self.display)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "design.json"
            save_design(target, design, self.display)
            self.assertEqual(load_design(target, self.display), design)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertFalse((Path(tmp) / "design.json.tmp").exists())

    def test_failed_design_replace_cleans_temp_and_keeps_original(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "design.json"
            target.write_text("original\n", encoding="utf-8")
            with mock.patch("tools.vibepulse_studio.design.os.replace",
                            side_effect=OSError("blocked")):
                with self.assertRaisesRegex(OSError, "blocked"):
                    save_design(target, self.design, self.display)
            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])

    def test_header_write_is_atomic_and_cleans_temp_on_failure(self):
        header = generate_header(self.design, self.display)
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "generated.h"
            save_header(target, header)
            self.assertEqual(target.read_text(encoding="utf-8"), header)
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertFalse((Path(tmp) / "generated.h.tmp").exists())

            target.write_text("original\n", encoding="utf-8")
            with mock.patch("tools.vibepulse_studio.design.os.replace",
                            side_effect=OSError("blocked")):
                with self.assertRaisesRegex(OSError, "blocked"):
                    save_header(target, header)
            self.assertEqual(target.read_text(encoding="utf-8"), "original\n")
            self.assertEqual(list(Path(tmp).glob(".*.tmp")), [])

    def test_display_dimensions_must_be_positive_integers(self):
        for display in ({"width": True, "height": 480},
                        {"width": 0, "height": 480},
                        {"width": 480, "height": -1}):
            with self.subTest(display=display):
                with self.assertRaisesRegex(DesignError, "display"):
                    validate_design(self.design, display)


if __name__ == "__main__":
    unittest.main()
