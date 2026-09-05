#!/usr/bin/env python3
"""The frames in docs/img must still be frames this firmware renders.

README.md, docs/wifi.md and the release bodies show 480x480 simulator
captures. Nothing checked that they still resemble the panel. They drifted,
and the drift is the invisible kind: the picture stays plausible, so nobody
re-reads it, and a reader ends up trusting a screenshot of a build that no
longer exists.

WHAT ACTUALLY HAPPENED. The global Wi-Fi indicator was redrawn in d5be82d
(2026-08-22), which deleted the Font Awesome glyph font
platform/fonts/torget_wifi_22.c and replaced it with the generated
platform/wifi_status_assets.c: a thick bright-white fan became a thin
muted-grey one at a different offset. The two Wi-Fi onboarding frames date
from 1b6ba3a, the day before, so they were stale within twenty-four hours of
being committed. test/test_wifi_setup_wiring.py "pinned" them the whole time
— it asserts they exist, are PNG, are 480x480 and are linked from the README,
and all four stayed true. A picture's dimensions are not its content.

THE RESULT IS BLUNT: of twenty-nine checked-in frames, this guard can verify
FOUR. That is not the guard being weak; it is the guard refusing to call
unverified frames verified, which is what the first three versions of this
file did in three different ways.

PINNED — byte for byte, and that is total. Four frames are reproduced exactly
by a capture --vibepulse-static-qa still produces, so any change to the page
shell, fonts, palette or layout breaks them. The mapping was found by
comparing every frame against every capture, not guessed from filenames.

UNVERIFIED — everything else. Those frames came from ad-hoc simulator states
that no pinned capture set reproduces, so there is no image to compare them
with. Each is quarantined by name AND by a digest of its file. Most are
positively stale: their Wi-Fi indicator is the pre-redraw glyph. The rest
cannot be judged either way, and are quarantined for that reason rather than
waved through — see the next paragraph, which is the whole reason this file
was rewritten.

WHY A BLANK RECTANGLE PROVES NOTHING. The check compares the indicator's own
20x18 box at (426, 28) — the position wifi_status_create() gives it in
platform/torget_ui.c — against the renderings the simulator produces. An
earlier version accepted ANY rendering the QA sets emitted, including a
blank box, and Codex caught what that let through: docs/img/launcher.png has
an all-black box and sailed past, even though every launcher capture draws
the indicator. Blankness is ambiguous by construction — a full-screen
takeover hides the header, and so does a frame captured before the indicator
existed — so it can never be evidence for an unpinned frame.

Worse, and found while checking that: --vibepulse-pulse-qa and
--vibepulse-completion-qa never call torget_wifi_status_set_mode(NORMAL), so
the indicator stays HIDDEN for every capture they write. The SAME tag proves
it — torget-vibepulse-claude-done-static.bmp has 56 indicator pixels under
--vibepulse-static-qa and 0 under --vibepulse-completion-qa. Same surface,
same firmware; the difference is the harness. Feeding those modes into the
allowed set imported blanks that the panel never draws, which is how a
frame's pin could rest on an artifact. This file now reads ONE mode, the one
that exercises the page shell.

THE QUARANTINE, AND WHAT IT ACTUALLY GUARANTEES. STALE_CHROME maps each
unverified frame to a digest of its file, and the test asserts both that the
unverified set is exactly those frames and that each is still the file that
was reviewed. Its first version held filenames only, and Codex was right
that this enforced nothing it advertised: add the new frame's name and
everything passes. Nothing in a repository can stop someone editing a
constant, and claiming otherwise is the mistake AGENTS.md records about a
merge block that was never there. What the digest changes is the price —
a filename AND a 64-character hash a reviewer sees — and it freezes what is
already here: re-capturing a quarantined frame fails, with one correct
answer, delete the entry rather than update the hash.

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

# ONE mode, deliberately. --vibepulse-static-qa is the only QA flow that runs
# capture_global_wifi_matrix() and therefore the only one whose captures show
# the page shell the panel actually draws. See the docstring: the others
# leave the indicator HIDDEN and their blanks are harness artifacts.
QA_MODE = "--vibepulse-static-qa"

# platform/torget_ui.c: wifi_status_create() places the group at (426, 28)
# and sizes it 20x18. The rectangle holds the glyph and nothing else — the
# ordinary app captures render it identically — so a mismatch is the
# indicator, not the page behind it.
WIFI_BOX = (426, 28, 426 + 20, 28 + 18)

# Frames the simulator reproduces exactly. Byte-for-byte or the test fails.
PINNED = {
    "vibepulse-settings-menu.png": "torget-settings-menu.bmp",
    "vibepulse-settings-about.png": "torget-settings-about-found.bmp",
    "vibepulse-settings-no-address.png": "torget-settings-menu-address-lost.bmp",
    "vibepulse-wifi-setup.png": "torget-wifi-setup-open.bmp",
}

# Frames whose chrome this guard cannot confirm is current, each frozen at
# the exact bytes that were reviewed. Two populations, deliberately in one
# list because the remedy is identical — re-capture from a named state:
#
#   * positively stale: the box holds the pre-redraw glyph.
#   * unjudgeable: the box is blank, which proves nothing on an unpinned
#     frame (a takeover hides the header, and so does a capture predating
#     the indicator). These are the eight Codex's review surfaced.
#
# Strike a name off when its frame is re-captured; never add one to turn a
# red run green. Re-capturing is a documentation change with a visible
# result — a different fixture tells a different story on the glass — so
# which capture replaces which is the maintainer's call, not this test's.
STALE_CHROME = {
    "github/sim-cached.png":
        "371cfc2168bf7d6f85a3e8a83ac6848cdb12ce1a79a2b19a2d634087696ad7dc",
    "github/sim-live.png":
        "19a106e5ff938dbbe1314ef7e93208f1d12c384b78cd441dc7c05d38d202f351",
    "github/sim-missing.png":
        "709d96c2c720510ceced9bca4dd41d615c21bfbd26e217605dbe6db934ada342",
    "github/sim-star-popup.png":
        "9683ea79d93d13dfba0ae0efa8505db7b7d5fa2320f5d8af71df5ae07e30676a",
    "launcher.png":
        "a8aad4b591a1152a8d9b0aa48628a11b95b1b5cc497f2385d7c7730392b60166",
    "needs-you/vibepulse-needs-you-approval.png":
        "704164c54756b6712cb9086016487eaba40a65e7b5c60504ef3157168991c921",
    "needs-you/vibepulse-needs-you-attract.png":
        "046155bfb4115f101a356cdd5ea2fcf3ec758d62c084f615af9641299fefc576",
    "needs-you/vibepulse-needs-you-none.png":
        "ba8b6cae7e893909b5fefb38029133a314ad9e5c68534a96957de5992fd1805a",
    "needs-you/vibepulse-needs-you-payoff.png":
        "02e2c8c430367db23e3ef167f9a9f8ee9b7409bca42ff50037f8ec4050950531",
    "needs-you/vibepulse-needs-you-private.png":
        "e5a5c439bf8c6db505003ddfbddbd510df89a1129f53c9d2d810146f4ead0fea",
    "needs-you/vibepulse-needs-you-question.png":
        "101ac6d584f551e764c8556be3364e5757842abb0db722bd77fecef3db212dc4",
    "vibepulse-agent-working.png":
        "3f2adbdf5020050c307120ce26d4af0a94af06defcc630fe91b57efeae6841b4",
    "vibepulse-burn-rate.png":
        "ced77198dbb1086e5fb68d04a85b980c08e7e71a9ad2f97ead524ccf70dfc0b3",
    "vibepulse-claude-week.png":
        "f5e0f17b4d437b864c79e0674e17f35860699c9e7ff8465e28328873ddd4bc65",
    "vibepulse-codex-needs-you.png":
        "21ea74df103b943e2efc0443e8558bc4bd5751edafb84f4f49e124b28bc090b9",
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
    "vibepulse-needs-you.png":
        "10a1fd51f5b56538a4f8c9557ee0fc9e3c81fad4b1418a2370651bdc8081292e",
    "vibepulse-no-data.png":
        "a70d4683b44819d3c3a40f34a74ef2ec69b125b668c254a2e469fc7794e241fb",
    "vibepulse-value-ahead.png":
        "5e82b9c09c39f03bddcd1091b19bba91f092ac25acf8f8155a4764698b5dfc91",
    "vibepulse-wifi-searching.png":
        "7f250af04f810ae0966f27be9a29e0bfdb275d097a3757b6f30360aa9aaa532c",
    "vibepulse-wifi-signal.png":
        "fd1fff6f8e42157a2143cd3c3fb66ae4ed125ffc72426e0d21f01dfc365ea66b",
}

# PNG chunks that change what a browser paints without changing a single
# sample value, so a comparison of sample values alone looks straight
# through them. Codex's second finding: an EXIF orientation or a colour
# profile arriving on a pinned frame would leave this test green while the
# README rendered the picture differently.
DISPLAY_METADATA = ("icc_profile", "exif", "gamma", "srgb", "chromaticity")


def docs_frames():
    """Every checked-in 480x480 simulator capture, by path under docs/img.

    Discovered rather than listed: a frame added to the README tomorrow must
    face this guard without anyone remembering to enrol it. That is how
    needs-you/vibepulse-needs-you-none.png joined the quarantine after being
    missed by hand.
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


def indicator(image):
    return image.convert("RGB").crop(WIFI_BOX).tobytes()


def is_blank(box):
    """The indicator box with nothing drawn in it."""
    return all(box[i] + box[i + 1] + box[i + 2] <= 75
               for i in range(0, len(box), 3))


class DocsFrameDriftTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vibepulse-docs-frames-")
        cls.capture_dir = Path(cls.temp.name)
        for argv in (["cmake", "-S", "sim", "-B", "sim/build", "-G", "Ninja"],
                     ["cmake", "--build", "sim/build"]):
            subprocess.run(argv, cwd=ROOT, check=True, text=True,
                           capture_output=True)
        subprocess.run(
            [str(ROOT / "sim/build/torget-sim"), QA_MODE],
            cwd=ROOT,
            env={**os.environ, "TORGET_CAPTURE_DIR": str(cls.capture_dir)},
            check=True, text=True, capture_output=True)
        cls.captures = sorted(cls.capture_dir.glob("*.bmp"))
        cls.frames = list(docs_frames())

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_the_capture_set_actually_ran(self):
        """The QA mode writing nothing would make every other test in this
        file pass vacuously — an empty allowed-set rejects nothing unless
        something notices, so notice here."""
        self.assertGreater(len(self.captures), 100,
                           "the QA mode produced almost no captures")
        self.assertTrue(self.frames, "found no 480x480 frames under docs/img")

    def test_the_capture_set_really_exercises_the_indicator(self):
        """The assumption the whole CHROME check rests on, asserted rather
        than trusted. If someone changes QA_MODE to a flow that leaves the
        indicator HIDDEN, every capture goes blank, the allowed set empties,
        and the check would start rejecting correct frames — or, in the
        version this replaced, accepting anything blank."""
        drawn = [p for p in self.captures
                 if not is_blank(indicator(Image.open(p)))]
        self.assertGreater(
            len(drawn), 50,
            f"{QA_MODE} left the Wi-Fi indicator hidden in almost every "
            f"capture; its blanks are a harness artifact, not the panel.")

    def test_no_frame_smuggles_transparency(self):
        """Both comparisons call convert("RGB"), which silently DROPS an
        alpha channel: an RGBA frame whose hidden RGB still matched would
        pass as identical to the opaque simulator BMP while the README
        rendered it differently.

        The sound move is to reject the precondition rather than work around
        the conversion. These are captures of an AMOLED panel; the glass has
        no transparency and the simulator writes opaque BMPs, so a frame
        carrying alpha did not come from where it claims to. Asserting it
        first makes convert("RGB") provably lossless everywhere after.

        Checked as fully-opaque rather than as mode == "RGB": LA, PA and a
        palette image with a `transparency` key all carry alpha while
        passing a mode test, and the palette case hides best — its mode is
        "P" and "A" is not among its bands.
        """
        for frame, path in self.frames:
            with self.subTest(frame=frame):
                with Image.open(path) as im:
                    if not ("A" in im.getbands() or "transparency" in im.info):
                        continue
                    low, _ = im.convert("RGBA").getchannel("A").getextrema()
                    self.assertEqual(
                        low, 255,
                        f"{frame} has transparent pixels. A panel capture is "
                        f"opaque; alpha here would be dropped unnoticed by "
                        f"the comparisons below.")

    def test_pinned_frames_carry_no_display_affecting_metadata(self):
        """Scoped to PINNED because that is where the word "exact" is used.
        An ICC profile or an EXIF orientation changes what a browser paints
        without touching one sample value, so the comparison below would stay
        green while the README showed something else. All four are bare PNGs
        today; this keeps them that way rather than trusting they stay."""
        for frame in sorted(PINNED):
            with self.subTest(frame=frame):
                with Image.open(ROOT / "docs/img" / frame) as im:
                    present = sorted(k for k in DISPLAY_METADATA
                                     if k in im.info)
                self.assertEqual(
                    present, [],
                    f"{frame} gained {present}, which changes how a browser "
                    f"renders it while leaving its pixels equal. Re-save it "
                    f"without the profile, or stop calling the check exact.")

    def test_pinned_frames_are_byte_identical_to_their_capture(self):
        names = {path.name for path in self.captures}
        for frame, capture in sorted(PINNED.items()):
            with self.subTest(frame=frame):
                self.assertIn(capture, names,
                              f"{frame} is pinned to a capture {QA_MODE} no "
                              f"longer produces: {capture}")
                with Image.open(ROOT / "docs/img" / frame) as a, \
                        Image.open(self.capture_dir / capture) as b:
                    diff = ImageChops.difference(a.convert("RGB"),
                                                 b.convert("RGB"))
                    self.assertIsNone(
                        diff.getbbox(),
                        f"{frame} no longer matches {capture}. The panel "
                        f"changed and the documentation did not: re-capture "
                        f"the frame in the same commit as the change.")

    def test_every_unpinned_frame_shows_an_indicator_this_build_draws(self):
        """A blank box is never evidence here. See the docstring: it means
        either a takeover hiding the header or a capture that predates the
        indicator, and nothing distinguishes them from outside."""
        allowed = set()
        for path in self.captures:
            with Image.open(path) as im:
                box = indicator(im)
            if not is_blank(box):
                allowed.add(box)
        self.assertTrue(allowed, "no capture drew the indicator at all")

        unverified = set()
        for frame, path in self.frames:
            if frame in PINNED:
                continue
            with Image.open(path) as im:
                if indicator(im) not in allowed:
                    unverified.add(frame)

        new = sorted(unverified - set(STALE_CHROME))
        fixed = sorted(set(STALE_CHROME) - unverified)
        self.assertEqual(unverified, set(STALE_CHROME), "\n".join(
            [""]
            + [f"  NEW unverified frame: {n} — its Wi-Fi indicator is not "
               f"one this build draws (or is blank, which proves nothing). "
               f"Re-capture it from a named simulator state." for n in new]
            + [f"  now verifiable: {f} — remove it from STALE_CHROME."
               for f in fixed]))

    def test_each_quarantined_frame_is_frozen_at_the_reviewed_bytes(self):
        """The half that makes the quarantine cost something.

        Without it STALE_CHROME is a list of filenames, and adding one entry
        waves a brand-new unverified frame past every check — the rule would
        live in a comment instead of in the run. With it, quarantining costs
        a digest too, and a quarantined frame is pinned as hard as a PINNED
        one. Re-capturing one fails here, and that failure has ONE correct
        answer: delete the entry. Updating the hash puts the frame back into
        a quarantine it has just left.
        """
        for frame, path in self.frames:
            expected = STALE_CHROME.get(frame)
            if expected is None:
                continue
            with self.subTest(frame=frame):
                self.assertEqual(
                    hashlib.sha256(path.read_bytes()).hexdigest(), expected,
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
        """A frame cannot be both reproduced exactly and unverifiable; if it
        ever is, one of the two lists is lying."""
        self.assertEqual(sorted(set(PINNED) & set(STALE_CHROME)), [])

    def test_every_frame_is_accounted_for(self):
        """No third category. A frame that is neither pinned nor quarantined
        would be one this file silently ignores, which is how the blank-box
        hole stayed open."""
        unaccounted = sorted(frame for frame, _ in self.frames
                             if frame not in PINNED
                             and frame not in STALE_CHROME)
        self.assertEqual(unaccounted, [])


if __name__ == "__main__":
    unittest.main()
