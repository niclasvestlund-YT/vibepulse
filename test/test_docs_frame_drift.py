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

THE QUARANTINE, AND WHAT IT ACTUALLY GUARANTEES. STALE_CHROME maps each
frame that fails CHROME today to a digest of its file, and the test asserts
both that the failures are exactly those frames and that each is still the
file that was reviewed. A NEW stale frame fails, and re-capturing an old one
fails until its entry is struck off.

The first version of this list held filenames only, and the honest reading —
Codex's, on the PR — is that it did not enforce the rule it advertised: add
the new frame's name and everything passes again. Nothing in a repository
can stop someone editing a constant, and claiming otherwise is the mistake
AGENTS.md records about a merge block that was never there. What the digest
changes is the price: quarantining a new frame now costs a filename AND a
64-character hash of that exact file, which a reviewer sees. And it freezes
what is already here — a quarantined frame cannot quietly become different
stale art, and re-capturing one has exactly one correct resolution, which is
to delete its entry rather than update its hash.

Deliberately NOT covered: docs/img/mockups/, which are concept art from
tools/mockups/gen_concept_mockups.py and never claimed to be captures, and
docs/img/github/glass-live.png, a photograph of the physical panel.
"""

from __future__ import annotations

import hashlib
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

# Frames still showing the pre-redraw Wi-Fi indicator, each frozen at the
# exact bytes that were reviewed.
#
# A NAME ALONE WOULD NOT BE A RATCHET. This started as a set of filenames,
# and Codex was right to call that out: a contributor who adds a new stale
# frame could add its name here and every test would pass again, so the
# "may shrink, never grow" rule was a comment, not a check — the same shape
# of mistake AGENTS.md records about promising a merge block that did not
# exist. Nothing in a repository can stop someone editing a constant. What a
# digest buys is that quarantining a NEW frame now costs a filename *and* a
# 64-character hash of that specific file, which is a visible, deliberate
# act in a diff rather than a one-word addition to a list.
#
# It also freezes what is quarantined. A frame here cannot be swapped for
# different stale art, and re-capturing one breaks its digest — which is the
# intended exit: the fix is to delete the entry, not to update the hash.
#
# Re-capturing is a documentation change with a visible result — a different
# fixture tells a different story on the glass — so which capture replaces
# which is the maintainer's call, not this test's.
STALE_CHROME = {
    "github/sim-cached.png":
        "371cfc2168bf7d6f85a3e8a83ac6848cdb12ce1a79a2b19a2d634087696ad7dc",
    "github/sim-live.png":
        "19a106e5ff938dbbe1314ef7e93208f1d12c384b78cd441dc7c05d38d202f351",
    "github/sim-missing.png":
        "709d96c2c720510ceced9bca4dd41d615c21bfbd26e217605dbe6db934ada342",
    "needs-you/vibepulse-needs-you-none.png":
        "ba8b6cae7e893909b5fefb38029133a314ad9e5c68534a96957de5992fd1805a",
    "vibepulse-agent-working.png":
        "3f2adbdf5020050c307120ce26d4af0a94af06defcc630fe91b57efeae6841b4",
    "vibepulse-burn-rate.png":
        "ced77198dbb1086e5fb68d04a85b980c08e7e71a9ad2f97ead524ccf70dfc0b3",
    "vibepulse-claude-week.png":
        "f5e0f17b4d437b864c79e0674e17f35860699c9e7ff8465e28328873ddd4bc65",
    "vibepulse-codex-week.png":
        "91be52589635e467cad6dbc62266676e958c94e81d891dedb99ba9ff946080c4",
    "vibepulse-max-tracker-claude.png":
        "fc07fcd17951e668d6d6014878e65367f7c38e58a9393ba6a250732b389e3c84",
    "vibepulse-max-tracker.png":
        "bbc2f49fb7a7f7afd4e8fafe96dadd881df6168ae34bc765da9a6719ab48d637",
    "vibepulse-needs-you-codex-approval.png":
        "7f661a322aca5cea0f2645feffed6deba6a326936af9072ff2c97196105f78c0",
    "vibepulse-needs-you-codex-question.png":
        "4fd9fd20759c51bf29a8e6fb336033f43db27cd6b1ca0532df712c41f96e2406",
    "vibepulse-no-data.png":
        "a70d4683b44819d3c3a40f34a74ef2ec69b125b668c254a2e469fc7794e241fb",
    "vibepulse-value-ahead.png":
        "5e82b9c09c39f03bddcd1091b19bba91f092ac25acf8f8155a4764698b5dfc91",
    "vibepulse-wifi-searching.png":
        "7f250af04f810ae0966f27be9a29e0bfdb275d097a3757b6f30360aa9aaa532c",
    "vibepulse-wifi-signal.png":
        "fd1fff6f8e42157a2143cd3c3fb66ae4ed125ffc72426e0d21f01dfc365ea66b",
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

    def test_no_frame_smuggles_transparency(self):
        """Both comparisons below call convert("RGB"), which silently DROPS an
        alpha channel. Codex caught the consequence: an RGBA frame whose
        hidden RGB still matched would pass as identical to the opaque
        simulator BMP while the README rendered it differently — the exact
        check advertised as exact, looking through the thing that changed.

        The sound rule is not to work around the conversion but to reject its
        precondition failing. These are captures of an AMOLED panel; the glass
        has no transparency, the simulator writes opaque BMPs, and a frame
        carrying alpha did not come from where it claims to. Asserting that
        first makes convert("RGB") provably lossless for everything after it.

        Checked as fully-opaque rather than as mode == "RGB": LA, PA and a
        palette image with a `transparency` key all carry alpha too, and mode
        alone would wave those past.
        """
        for frame, path in self.frames:
            with self.subTest(frame=frame):
                with Image.open(path) as im:
                    has_alpha = ("A" in im.getbands()
                                 or "transparency" in im.info)
                    if not has_alpha:
                        continue
                    low, _ = im.convert("RGBA").getchannel("A").getextrema()
                    self.assertEqual(
                        low, 255,
                        f"{frame} has transparent pixels. A panel capture is "
                        f"opaque; alpha here would be dropped unnoticed by "
                        f"the comparisons below.")

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

        new = sorted(stale - set(STALE_CHROME))
        fixed = sorted(set(STALE_CHROME) - stale)
        self.assertEqual(stale, set(STALE_CHROME), "\n".join(
            [""]
            + [f"  NEW stale frame: {n} — it shows a Wi-Fi indicator this "
               f"build does not draw. Re-capture it." for n in new]
            + [f"  no longer stale: {f} — remove it from STALE_CHROME."
               for f in fixed]))

    def test_each_quarantined_frame_is_frozen_at_the_reviewed_bytes(self):
        """The half that makes the quarantine cost something.

        Without it, STALE_CHROME is a list of filenames and adding one entry
        is enough to wave a brand-new stale frame past every check — the rule
        would live in a comment instead of in the run. With it, quarantining
        a frame costs its digest too, and a quarantined frame is pinned as
        hard as a PINNED one: it cannot be swapped for different stale art,
        and re-capturing it fails here. That failure has ONE correct answer —
        delete the entry — because a re-captured frame is no longer stale.
        Updating the hash to make this pass puts the frame back in quarantine
        it no longer belongs in.
        """
        for frame, path in self.frames:
            expected = STALE_CHROME.get(frame)
            if expected is None:
                continue
            with self.subTest(frame=frame):
                actual = hashlib.sha256(path.read_bytes()).hexdigest()
                self.assertEqual(
                    actual, expected,
                    f"{frame} changed while quarantined. If you re-captured "
                    f"it, delete its STALE_CHROME entry — do not update the "
                    f"digest.")

    def test_the_quarantine_names_only_real_frames(self):
        """A typo, or a frame someone deleted, would park a name in
        STALE_CHROME forever and quietly shrink what the guard covers."""
        known = {frame for frame, _ in self.frames}
        self.assertEqual(sorted(set(STALE_CHROME) - known), [])
        self.assertEqual(sorted(set(PINNED) - known), [])

    def test_every_digest_is_a_digest(self):
        """A truncated or placeholder value would make the freeze check pass
        on nothing while looking like it was doing its job."""
        for frame, digest in sorted(STALE_CHROME.items()):
            with self.subTest(frame=frame):
                self.assertRegex(digest, r"\A[0-9a-f]{64}\Z")
        self.assertEqual(len(set(STALE_CHROME.values())), len(STALE_CHROME),
                         "two quarantined frames share a digest")

    def test_pinned_and_quarantined_do_not_overlap(self):
        """A frame cannot be both reproduced exactly and drawing obsolete
        chrome; if it ever is, one of the two lists is lying."""
        self.assertEqual(sorted(set(PINNED) & set(STALE_CHROME)), [])


if __name__ == "__main__":
    unittest.main()
