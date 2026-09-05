#!/usr/bin/env python3
"""The frames in docs/img must still be frames this firmware renders.

README.md, docs/wifi.md and the release bodies show 480x480 simulator
captures. Nothing checked that they still resemble the panel. They drifted,
and the drift is the invisible kind: the picture stays plausible, so nobody
re-reads it, and a reader ends up trusting a screenshot of a build that no
longer exists.

WHAT ACTUALLY HAPPENED, since a guard written from a hypothesis is worth
little. The global Wi-Fi indicator (platform/torget_ui.c, `wifi_status_*`)
was redrawn: a thick bright-white fan became a thin muted-grey one, drawn
from a different asset at a different offset. Fifteen of the twenty-three
checked-in simulator frames still show the old glyph — including the three
Wi-Fi onboarding images that test_wifi_setup_wiring.py already pins by size
and by their presence in the README. Pinning a picture's dimensions does not
pin its content, and the redraw walked straight through that gap.

TWO CHECKS, because one is exact and the other is possible.

PINNED — a frame the current simulator reproduces byte for byte is compared
byte for byte. That is the whole guard for those, and it is total: any
future change to the page shell, the fonts, the palette or the layout breaks
it. Only five frames qualify today.

CHROME — the other eighteen were captured from ad-hoc simulator states (a
different fixture, a different tile, a different signal strength) that no
pinned capture set reproduces, so there is no image to compare them against.
What CAN be compared is the one rectangle whose content is decided by the
firmware alone and not by the fixture: the Wi-Fi indicator's own 20x18 box.
Across the 153 captures the QA sets produce, that rectangle takes exactly
six renderings; a checked-in frame showing something else is showing a panel
this build cannot draw.

THE QUARANTINE SHRINKS, NEVER GROWS. STALE_CHROME lists the frames that fail
CHROME today, and the test asserts the failures are exactly that list. So a
NEW stale frame fails, and re-capturing an old one also fails until its name
is struck off — which is the point: the list is the outstanding work, and it
has to be spent deliberately rather than left to rot quietly.

Deliberately NOT covered: docs/img/mockups/, which are concept art from
tools/mockups/gen_concept_mockups.py and never claimed to be captures, and
docs/img/github/glass-live.png, a photograph of the physical panel.
"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from PIL import Image, ImageChops


ROOT = Path(__file__).resolve().parents[1]

# The QA modes that between them produce every capture a docs frame is
# compared against. --vibepulse-pulse-qa is here for one frame only
# (vibepulse-needs-you.png is its t0 bright tick), which is exactly why it
# has to be named: without it that frame silently drops to "unpinned".
QA_MODES = ("--vibepulse-static-qa", "--vibepulse-pulse-qa")

# platform/torget_ui.c: wifi_status_create() places the group at (426, 28)
# and sizes it 20x18. The rectangle holds the glyph and nothing else — all
# 114 ordinary app captures render it identically — so a mismatch is the
# indicator, not the page behind it.
WIFI_BOX = (426, 28, 426 + 20, 28 + 18)

# Frames the simulator reproduces exactly. Byte-for-byte or the test fails.
PINNED = {
    "vibepulse-settings-menu.png": "torget-settings-menu.bmp",
    "vibepulse-settings-about.png": "torget-settings-about-found.bmp",
    "vibepulse-settings-no-address.png": "torget-settings-menu-no-address.bmp",
    "vibepulse-wifi-setup.png": "torget-wifi-setup-open.bmp",
    "vibepulse-needs-you.png": "torget-vibepulse-pulse-t0-bright.bmp",
}

# Frames still showing the pre-redraw Wi-Fi indicator. Re-capturing one is a
# documentation change with a visible result — a different fixture tells a
# different story on the glass — so it is the maintainer's call which capture
# replaces which, not this test's. Strike a name off when its frame is
# re-captured; never add one to make a red run go green.
STALE_CHROME = {
    "github/sim-cached.png",
    "github/sim-live.png",
    "github/sim-missing.png",
    "needs-you/vibepulse-needs-you-none.png",
    "vibepulse-agent-working.png",
    "vibepulse-burn-rate.png",
    "vibepulse-claude-week.png",
    "vibepulse-codex-week.png",
    "vibepulse-max-tracker-claude.png",
    "vibepulse-max-tracker.png",
    "vibepulse-needs-you-codex-approval.png",
    "vibepulse-needs-you-codex-question.png",
    "vibepulse-no-data.png",
    "vibepulse-value-ahead.png",
    "vibepulse-wifi-searching.png",
    "vibepulse-wifi-signal.png",
}


def docs_frames():
    """Every checked-in 480x480 simulator capture, by path under docs/img.

    Discovered rather than listed: a frame added to the README tomorrow must
    face this guard without anyone remembering to enrol it.
    """
    img = ROOT / "docs/img"
    for path in sorted(img.rglob("*.png")):
        rel = path.relative_to(img)
        if rel.parts[0] == "mockups":
            continue
        with Image.open(path) as im:
            if im.size != (480, 480):
                continue
        yield rel.as_posix(), path


class DocsFrameDriftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vibepulse-docs-frames-")
        cls.capture_dir = Path(cls.temp.name)
        for argv in (["cmake", "-S", "sim", "-B", "sim/build", "-G", "Ninja"],
                     ["cmake", "--build", "sim/build"]):
            subprocess.run(argv, cwd=ROOT, check=True, text=True,
                           capture_output=True)
        for mode in QA_MODES:
            subprocess.run(
                [str(ROOT / "sim/build/torget-sim"), mode],
                cwd=ROOT,
                env={**os.environ, "TORGET_CAPTURE_DIR": str(cls.capture_dir)},
                check=True, text=True, capture_output=True)
        cls.captures = sorted(cls.capture_dir.glob("*.bmp"))
        cls.frames = list(docs_frames())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_capture_sets_actually_ran(self):
        """Both QA modes writing nothing would make every other test in this
        file pass vacuously — an empty allowed-set rejects nothing only if
        the tests below are written to notice, so notice here instead."""
        self.assertGreater(len(self.captures), 100,
                           "the QA modes produced almost no captures")
        self.assertTrue(self.frames, "found no 480x480 frames under docs/img")

    def test_pinned_frames_are_byte_identical_to_their_capture(self):
        names = {path.name for path in self.captures}
        for frame, capture in sorted(PINNED.items()):
            with self.subTest(frame=frame):
                self.assertIn(capture, names,
                              f"{frame} is pinned to a capture the QA modes "
                              f"no longer produce: {capture}")
                with Image.open(ROOT / "docs/img" / frame) as a, \
                        Image.open(self.capture_dir / capture) as b:
                    diff = ImageChops.difference(a.convert("RGB"),
                                                 b.convert("RGB"))
                    self.assertIsNone(
                        diff.getbbox(),
                        f"{frame} no longer matches {capture}. The panel "
                        f"changed and the documentation did not: re-capture "
                        f"the frame in the same commit as the change.")

    def test_every_frame_shows_a_wifi_indicator_this_build_can_draw(self):
        allowed = set()
        for path in self.captures:
            with Image.open(path) as im:
                allowed.add(im.convert("RGB").crop(WIFI_BOX).tobytes())
        self.assertTrue(allowed)

        stale = set()
        for frame, path in self.frames:
            with Image.open(path) as im:
                if im.convert("RGB").crop(WIFI_BOX).tobytes() not in allowed:
                    stale.add(frame)

        new = sorted(stale - STALE_CHROME)
        fixed = sorted(STALE_CHROME - stale)
        self.assertEqual(stale, STALE_CHROME, "\n".join(
            [""]
            + [f"  NEW stale frame: {n} — it shows a Wi-Fi indicator this "
               f"build does not draw. Re-capture it." for n in new]
            + [f"  no longer stale: {f} — remove it from STALE_CHROME."
               for f in fixed]))

    def test_the_quarantine_names_only_real_frames(self):
        """A typo, or a frame someone deleted, would park a name in
        STALE_CHROME forever and quietly shrink what the guard covers."""
        known = {frame for frame, _ in self.frames}
        self.assertEqual(sorted(STALE_CHROME - known), [])
        self.assertEqual(sorted(set(PINNED) - known), [])

    def test_pinned_and_quarantined_do_not_overlap(self):
        """A frame cannot be both reproduced exactly and drawing obsolete
        chrome; if it ever is, one of the two lists is lying."""
        self.assertEqual(sorted(set(PINNED) & STALE_CHROME), [])


if __name__ == "__main__":
    unittest.main()
