#!/usr/bin/env python3
"""The three permanent overlays must report what they cost, on every boot.

Background, and it is a real gap rather than a hypothesis. #72 shipped a
SETTINGS overlay built once at boot and kept for the whole run. The AMOLED
skill forbids adding a persistent UI layer "without approval and a measured
memory budget", and that PR had no measurement — it could not have one from
a container, only from the panel. The question then sat open, which is the
worst outcome: a rule everyone agrees with and nobody can answer.

So the measurement is automatic instead of remembered. Each of the three
top-layer overlays is bracketed at creation, and the delta is logged. Any
flash answers the question; nobody has to think to ask it.

WHY TWO NUMBERS. LVGL's allocator pool is TLSF in PSRAM here — see
`LV_MEM_POOL_ALLOC` in `main/lv_psram_pool.h`, moved there as the 2026-08-16
freeze fix because the pool in internal BSS pushed the largest contiguous
DMA-capable block under the panel flush's 11 520 B. Object trees therefore
come out of that 256 KiB PSRAM pool, not internal RAM. The internal figure is
the *control*: it shows whether a create takes internal memory anyway. A zero
delta retires the starvation worry for that layer with evidence; a non-zero
one puts the cost in the log. Either way nobody guesses.

This test cannot run the firmware. It pins the instrumentation's presence and
shape, which is what a host test can honestly do.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN = ROOT / "main/main.c"
POOL_HEADER = ROOT / "main/lv_psram_pool.h"

# Every overlay that is created once and kept for the run.
PERSISTENT_OVERLAYS = ("wifi-setup", "settings", "ota")


def without_comments(source):
    source = re.sub(r"/\*.*?\*/", "", source, flags=re.S)
    return re.sub(r"//[^\n]*", "", source)


class OverlayMemoryBudgetTests(unittest.TestCase):
    def setUp(self):
        self.source = without_comments(MAIN.read_text(encoding="utf-8"))

    def test_every_persistent_overlay_is_bracketed(self):
        """Each create sits between a mark and a report, and reports under
        its own name — otherwise three costs arrive as one number and the
        menu's share is unknowable again."""
        for overlay, create in (
            ("wifi-setup", "torget_wifi_ui_create()"),
            ("settings", "torget_settings_create()"),
            ("ota", "torget_ota_ui_create()"),
        ):
            with self.subTest(overlay=overlay):
                idx = self.source.index(create)
                before = self.source[:idx]
                after = self.source[idx:]
                self.assertTrue(
                    before.rstrip().endswith("overlay_cost_mark();"),
                    f"{create} must be preceded by overlay_cost_mark()")
                report = f'overlay_cost_report("{overlay}")'
                # The report must be the next cost call, not a later one.
                nxt = after.index("overlay_cost_report(")
                self.assertTrue(after[nxt:].startswith(report),
                                f"{create} must report as {overlay!r}")

    def test_both_pools_are_measured(self):
        """The PSRAM pool answers 'what did it cost', the internal heap
        answers 'did it touch the memory the rule is actually about'. One
        without the other cannot settle the question."""
        self.assertIn("lv_mem_monitor(&mon)", self.source)
        self.assertIn("heap_caps_get_free_size(MALLOC_CAP_INTERNAL)",
                      self.source)

    def test_the_internal_delta_is_signed(self):
        """Internal free can move either way around a create, and a negative
        number is data rather than an error. An unsigned subtraction would
        wrap and print a plausible-looking enormous value — a fabricated
        figure, which is exactly what this repository refuses to display."""
        self.assertIn("long internal_delta", self.source)
        self.assertIn("%+ld", MAIN.read_text(encoding="utf-8"))

    def test_the_pool_is_still_in_psram(self):
        """The whole reading of these numbers depends on it. If someone
        moves the pool back to internal BSS, the interpretation written into
        main.c's comment silently becomes wrong — and so does the 2026-08-16
        freeze fix."""
        pool = POOL_HEADER.read_text(encoding="utf-8")
        self.assertIn("MALLOC_CAP_SPIRAM", pool)
        self.assertNotIn("MALLOC_CAP_DMA", pool.split("#define")[-1])


if __name__ == "__main__":
    unittest.main()
