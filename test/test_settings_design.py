#!/usr/bin/env python3
"""Exact-size contract for the 480x480 SETTINGS menu and its ABOUT view.

Same method as the Wi-Fi screen's validator: the saved design tokens are the
authority, the C file must agree with them digit for digit, and the geometry
assertions are the ones that would have caught the mistakes actually made
here — the first ABOUT layout put the COMPUTER value about five pixels from
the BACK control, which looked fine in code and wrong on the glass.
"""

import json
import re
import unittest
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESIGN_PATH = ROOT / "design/vibepulse/settings-design.json"
SOURCE_PATH = ROOT / "platform/settings_menu.c"
HEADER_PATH = ROOT / "platform/settings_menu.h"
MAIN_PATH = ROOT / "main/main.c"

CANVAS = 480
# Clipped corners: content that reaches the edge loses characters.
SAFE_MARGIN = 8


def without_comments(source):
    """Strip C comments so an assertion about code is not tripped by prose
    explaining why that code is absent."""
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


class SettingsDesignTests(unittest.TestCase):
    def setUp(self):
        self.design = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))

    def validate(self, design):
        self.assertEqual(
            set(design),
            {"schemaVersion", "deviceCapability", "canvas", "menu", "rows",
             "aboutRows", "about", "fonts"},
        )
        self.assertEqual(design["schemaVersion"], 1)
        self.assertEqual(design["deviceCapability"], "display.amoled")
        self.assertEqual(design["canvas"], {"width": CANVAS, "height": CANVAS})

        menu = design["menu"]
        self.assertEqual(
            set(menu),
            {"wordY", "rowX", "rowWidth", "rowHeight", "rowGap", "firstRowY",
             "rowRadius", "rowBorderWidth", "footerY"},
        )

        rows = design["rows"]
        # Three rows that work, not the spec's five. FEATURES needs the
        # on-unit RAM re-measurement and PAIR belongs to step 4; a row that
        # does nothing is a promise the screen cannot keep.
        self.assertEqual(rows, ["UPDATE", "WIFI", "ABOUT"])

        # The row geometry is deliberately the Wi-Fi window's proven MANUAL
        # SETUP control. If either moves, they should move together.
        self.assertEqual(menu["rowX"], 74)
        self.assertEqual(menu["rowWidth"], 332)
        self.assertGreaterEqual(menu["rowX"], SAFE_MARGIN)
        self.assertLessEqual(menu["rowX"] + menu["rowWidth"],
                             CANVAS - SAFE_MARGIN)

        # Every row lands on the glass, and the last one clears the footer.
        last_bottom = (menu["firstRowY"]
                       + (len(rows) - 1) * (menu["rowHeight"] + menu["rowGap"])
                       + menu["rowHeight"])
        self.assertGreater(menu["firstRowY"], menu["wordY"] + design["fonts"]["word"])
        self.assertLessEqual(last_bottom, menu["footerY"] - SAFE_MARGIN)
        self.assertLess(menu["footerY"] + design["fonts"]["footer"], CANVAS)
        # A finger needs a real target: the panel is touch-driven here.
        self.assertGreaterEqual(menu["rowHeight"], 60)
        self.assertGreaterEqual(menu["rowGap"], 8)

        about = design["about"]
        self.assertEqual(
            set(about),
            {"wordY", "labelX", "firstLineY", "lineGap", "backY", "footerY"},
        )
        # No COMPUTER row: the only signal available for it is a boot latch
        # that never clears, so it would have read FOUND forever after one
        # successful fetch. A row that says something false is worse than a
        # row that is not there.
        about_rows = design["aboutRows"]
        self.assertEqual(about_rows, ["FIRMWARE", "ADDRESS"])

        # The regression this file exists for: the last label/value pair must
        # clear the BACK control, not merely avoid overlapping it. Counted
        # from the declared rows so adding one cannot silently re-create the
        # five-pixel collision.
        pair_height = design["fonts"]["aboutLabel"] + design["fonts"]["aboutValue"]
        last_value_bottom = (about["firstLineY"]
                             + (len(about_rows) - 1) * about["lineGap"]
                             + pair_height)
        self.assertLessEqual(last_value_bottom, about["backY"] - SAFE_MARGIN)
        self.assertLessEqual(about["backY"] + menu["rowHeight"],
                             about["footerY"] - SAFE_MARGIN)
        self.assertGreater(about["firstLineY"],
                           about["wordY"] + design["fonts"]["word"])

        for name, size in design["fonts"].items():
            self.assertIs(type(size), int, name)
            self.assertGreaterEqual(size, 14, name)

    def test_contract_is_safe_and_exact_size(self):
        self.validate(self.design)

    def test_validator_rejects_a_drifted_contract(self):
        """The assertions must actually bite; a validator that passes
        everything proves nothing."""
        cases = [
            (("about", "backY"), 200),        # BACK back under the values
            (("menu", "firstRowY"), 430),     # rows off the bottom
            (("menu", "rowX"), 2),            # into the clipped corner
            (("menu", "rowHeight"), 20),      # too small to hit
        ]
        for path, value in cases:
            changed = deepcopy(self.design)
            changed[path[0]][path[1]] = value
            with self.subTest(path=path), self.assertRaises(AssertionError):
                self.validate(changed)

    def test_source_matches_saved_tokens(self):
        self.validate(self.design)
        source = SOURCE_PATH.read_text(encoding="utf-8")
        macros = {
            name: int(value)
            for name, value in re.findall(
                r"^#define SETTINGS_([A-Z0-9_]+)\s+(\d+)$", source, re.M
            )
        }
        menu, about = self.design["menu"], self.design["about"]
        self.assertEqual(macros, {
            "WORD_Y": menu["wordY"],
            "ROW_X": menu["rowX"],
            "ROW_WIDTH": menu["rowWidth"],
            "ROW_HEIGHT": menu["rowHeight"],
            "ROW_GAP": menu["rowGap"],
            "FIRST_ROW_Y": menu["firstRowY"],
            "ROW_RADIUS": menu["rowRadius"],
            "ROW_BORDER_W": menu["rowBorderWidth"],
            "FOOTER_Y": menu["footerY"],
            "ABOUT_FIRST_LINE_Y": about["firstLineY"],
            "ABOUT_LINE_GAP": about["lineGap"],
            "ABOUT_BACK_Y": about["backY"],
        })

    def test_row_labels_match_the_contract(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        block = source.split("ROW_TEXT[TG_SETTINGS_ROW_COUNT] = {", 1)[1]
        block = block.split("};", 1)[0]
        self.assertEqual(re.findall(r'"([A-Z]+)"', block), self.design["rows"])

    def test_about_labels_match_the_contract(self):
        source = SOURCE_PATH.read_text(encoding="utf-8")
        block = source.split("ABOUT_LABEL[ABOUT_ROWS] = {", 1)[1]
        block = block.split("};", 1)[0]
        self.assertEqual(re.findall(r'"([A-Z]+)"', block),
                         self.design["aboutRows"])
        self.assertNotIn("COMPUTER", without_comments(source),
                         "the COMPUTER row had no signal that meant it")

    def test_menu_never_lifts_itself_above_the_update_takeover(self):
        """UPDATE READY is not an open maintenance window, so a hold reaches
        the menu while it shows. Creation order keeps the ring on top only
        while the menu does not foreground itself, and the OTA renderer
        deduplicates an unchanged state so it would never lift back."""
        source = without_comments(SOURCE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("lv_obj_move_foreground", source)

    def test_update_is_refused_without_an_address(self):
        """An OTA window with no address can never receive an upload, so the
        row is toned down AND the press is ignored — one truth in two
        expressions, neither of them alone."""
        source = SOURCE_PATH.read_text(encoding="utf-8")
        press = source.split("case TG_SETTINGS_ROW_UPDATE:", 1)[1]
        press = press.split("case TG_SETTINGS_ROW_WIFI:", 1)[0]
        self.assertIn("if (!ui.ip[0]) break;", press)
        self.assertIn("can_update ? lv_color_white() : COL_MUTED", source)

    def test_menu_never_locks_the_ui_itself(self):
        """The Wi-Fi and OTA overlays take torget_ui_try_lock because other
        tasks call them. The menu is LVGL-task-only, so the same call here
        would stall the tick for 200 ms and then silently do nothing — the
        exact trap main.c's KEY3 block warns about."""
        source = without_comments(SOURCE_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("torget_ui_try_lock", source)
        self.assertNotIn("torget_ui_lock", source)
        self.assertIn("torget_ui_lock()", HEADER_PATH.read_text(encoding="utf-8"))

    def test_main_keeps_the_consent_model_and_the_escape(self):
        main = MAIN_PATH.read_text(encoding="utf-8")
        # The hold opens the menu; it must not reach past it into a window.
        # Two branches carry this action: the hold-hold path inside the OTA
        # window (unchanged, and still the way to reach Wi-Fi from there) and
        # the one that opens the menu. rsplit takes the latter.
        block = main.rsplit(
            "} else if (key3_action == TG_BUTTON_OPEN_MAINTENANCE) {", 1)
        self.assertEqual(len(block), 2, "the KEY3 hold branch moved")
        opened = block[1].split("\n  }", 1)[0]
        self.assertIn("torget_settings_open(", opened)
        self.assertNotIn("torget_ota_service_open_maintenance", opened)
        # Any release closes the menu: the same escape the two windows have,
        # found on hardware when a ~2 s press once left a window stuck.
        escape = main.split("} else if (torget_settings_open_p()) {", 1)[1]
        escape = escape.split("} else if", 1)[0]
        self.assertIn("torget_settings_close();", escape)
        self.assertIn("TG_BUTTON_NONE", escape)
        # The menu's choice is executed by whoever owns the window order.
        self.assertIn("torget_settings_take_intent()", main)

    def test_readiness_takeover_still_wins_the_top_layer(self):
        """Creation order is z-order on the shared top layer. A pending
        update matters more than a menu, so the ring is still created last."""
        main = MAIN_PATH.read_text(encoding="utf-8")
        self.assertLess(main.index("torget_settings_create()"),
                        main.index("torget_ota_ui_create()"))
        self.assertLess(main.index("torget_wifi_ui_create()"),
                        main.index("torget_settings_create()"))


if __name__ == "__main__":
    unittest.main()
    print("OK: exact-size SETTINGS contract")
