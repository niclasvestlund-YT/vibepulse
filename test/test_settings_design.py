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
# The KEY3 arbitration left main/main.c for shared, pure platform code, so
# the structural assertions below follow it there. What each rule DOES is
# pinned as a table of cases in test/test_key3_arbitration.c; what is checked
# here is that the rule still lives in one shared place and in the right
# order, and that the hosts only apply it.
ARBITRATION_PATH = ROOT / "platform/button_arbitration.c"

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

    def test_menu_asserts_its_own_z_order_every_tick(self):
        """Creation order is NOT precedence here, and assuming it was is the
        mistake this test replaces. Both the Wi-Fi overlay and the OTA
        overlay call lv_obj_move_foreground() inside their own set(), so
        whoever drew last sits on top. NO NETWORK is the case that bites:
        its countdown changes every second, so its dedup lets a redraw
        through once a second and lifts the Wi-Fi layer again — burying a
        menu that had only been lifted once at open, in exactly the state
        where the user needs the WIFI row."""
        source = without_comments(SOURCE_PATH.read_text(encoding="utf-8"))
        self.assertIn("lv_obj_move_foreground(ui.overlay)", source)
        # Re-asserted while open, not once at open: the guard is the open
        # check, so a closed menu can never claim the top layer.
        keep = source.split("void torget_settings_keep_foreground(void) {", 1)
        self.assertEqual(len(keep), 2, "keep_foreground moved")
        keep = keep[1].split("\n}", 1)[0]
        self.assertIn("!ui.open", keep)

    def test_the_menu_yields_to_every_window_that_owns_the_glass(self):
        """The menu is a signpost, never a competitor: any real window on
        the glass closes it. That — not layer order — is what gives the
        windows precedence, and it has to cover the two paths no key branch
        can catch, both triggered from other tasks while the menu is up:

        * the notice, announced by maintenance_ui_task, which otherwise
          covered a menu still holding open=true and revealed it on LATER;
        * the setup window, which opens ITSELF after 90 s without an
          address. owns_input then went true, the branch below ate the key
          and closed a window nobody could see, while the menu stayed on
          top promising "KEY3 CLOSES". The visible thing did not answer and
          the invisible one died."""
        arb = without_comments(ARBITRATION_PATH.read_text(encoding="utf-8"))
        owners = arb.split("const bool window_owns_glass =", 1)
        self.assertEqual(len(owners), 2, "the window-ownership test moved")
        owners = owners[1].split(";", 1)[0]
        for owner in ("in->notice_visible",
                      "in->setup_owns_input",
                      "in->maintenance_open"):
            self.assertIn(owner, owners, owner)
        block = arb.split("if (menu_open) {", 1)
        self.assertEqual(len(block), 2, "the exclusion block moved")
        block = block[1].split("\n  }", 1)[0]
        self.assertIn("window_owns_glass", block)
        self.assertIn("out->close_menu = true;", block)
        self.assertIn("out->menu_foreground = true;", block)
        # Both hosts feed the same function; neither keeps a copy of the rule.
        for host in (MAIN_PATH, ROOT / "sim/main.c"):
            self.assertIn("tg_button_arbitrate(",
                          host.read_text(encoding="utf-8"), str(host))

    def test_the_address_stays_live_while_the_menu_is_open(self):
        """A snapshot taken at open goes stale, and the honesty rules bite
        both ways when it does: ABOUT shows an address the panel no longer
        has, and UPDATE stays selectable so a press opens a maintenance
        window that can never receive an upload — the exact thing the muting
        exists to prevent. Nothing rescues it quickly either: the setup
        window does not take over until 90 s without an address, so the
        stale menu can stand for over a minute."""
        source = SOURCE_PATH.read_text(encoding="utf-8")
        setter = source.split("void torget_settings_set_address(const char *ip) {", 1)
        self.assertEqual(len(setter), 2, "the live address setter moved")
        setter = setter[1].split("\n}", 1)[0]
        # Only while open, deduplicated, and it must re-render on a change.
        self.assertIn("!ui.open", setter)
        self.assertIn("strcmp(next, ui.ip) == 0", setter)
        self.assertIn("ui.about_dirty = true;", setter)
        self.assertIn("render();", setter)
        # Fed every tick from the locked copy, in the same branch that keeps
        # the menu on top — i.e. exactly when no window owns the glass.
        main = without_comments(MAIN_PATH.read_text(encoding="utf-8"))
        block = main.split("torget_settings_keep_foreground();", 1)[0]
        block = block.rsplit("if (key3_out.menu_foreground) {", 1)[1]
        self.assertIn("ip_text_copy(ip, sizeof ip)", block)
        self.assertIn("torget_settings_set_address(have_ip ? ip : NULL)", block)

    def test_the_exclusion_runs_before_the_key_chain(self):
        """Order matters, not just presence. The close must land on the same
        tick the window takes over, so the next press acts on what the user
        actually sees. Behind the chain instead, that press would still be
        eaten by the setup branch."""
        arb = without_comments(ARBITRATION_PATH.read_text(encoding="utf-8"))
        exclusion = arb.index("if (menu_open) {")
        chain = arb.index("if (in->setup_owns_input) {")
        self.assertLess(exclusion, chain,
                        "the exclusion must run before the KEY3 chain")
        # And the chain must read the CLOSED menu, not the input: the local is
        # cleared by the exclusion, which is what makes the ordering visible.
        self.assertIn("menu_open = false;", arb)
        self.assertIn("} else if (menu_open) {", arb)

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
        arb = without_comments(ARBITRATION_PATH.read_text(encoding="utf-8"))
        # The hold reaches a MENU and nothing else. The arbitration cannot
        # reach past it even in principle: it has no output that opens the
        # maintenance window, and it calls no service at all.
        block = arb.split(
            "} else if (in->key == TG_BUTTON_OPEN_MAINTENANCE &&", 1)
        self.assertEqual(len(block), 2, "the KEY3 hold branch moved")
        opened = block[1].split("\n  }", 1)[0]
        self.assertIn("out->open_menu = true;", opened)
        self.assertNotIn("torget_ota_service_open_maintenance", arb)
        # The host applies that output by opening the menu, and the window is
        # opened in exactly one place: the menu's own intent.
        self.assertIn("if (key3_out.open_menu) {", main)
        self.assertEqual(main.count("torget_ota_service_open_maintenance();"), 1)
        self.assertIn("torget_settings_take_intent()", main)
        # Any release closes the menu: the same escape the two windows have,
        # found on hardware when a ~2 s press once left a window stuck.
        escape = arb.split("} else if (menu_open) {", 1)[1]
        escape = escape.split("} else if", 1)[0]
        self.assertIn("out->close_menu = true;", escape)
        self.assertIn("TG_BUTTON_NONE", escape)

    def test_the_hold_does_nothing_while_update_ready_owns_the_glass(self):
        """The takeover is a UI state, not an open maintenance window, so
        torget_ota_service_maintenance_open() answers no while it shows and
        the hold fell through to the branch that opens the menu. Without this
        guard SETTINGS opened BEHIND the notice: open=true, nothing on the
        glass, and the menu surfacing later when the notice went away. The
        guard belongs on that one branch — a short tap and the panic hold
        must behave exactly as before."""
        arb = without_comments(ARBITRATION_PATH.read_text(encoding="utf-8"))
        self.assertIn(
            "in->key == TG_BUTTON_OPEN_MAINTENANCE && !in->notice_visible",
            arb)
        # Not a block of its own: NEXT_APP and PANIC keep their own branches.
        self.assertNotIn("} else if (in->notice_visible) {", arb)

    def test_the_address_is_copied_under_the_lock_not_aliased(self):
        """Writing the string before publishing the event bit only ordered
        the FIRST publication. A renewed DHCP lease or an address change with
        no disconnect in between rewrites it from the event loop while the
        LVGL task may be copying it, on another core."""
        main = without_comments(MAIN_PATH.read_text(encoding="utf-8"))
        self.assertIn("portMUX_TYPE s_ip_text_mux", main)
        # Every writer goes through the helper; nobody touches the buffer.
        self.assertEqual(main.count("s_ip_text["), 3,
                         "s_ip_text may only be touched inside its helpers")
        self.assertIn("ip_text_copy(ip, sizeof ip)", main)

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
