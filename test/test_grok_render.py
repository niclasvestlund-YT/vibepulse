"""The opt-in GROK page renders on the 480x480 composition and stays honest.

Builds a separate Grok-enabled simulator (sim/build-grok, reusing the LVGL
source already fetched for sim/build) and checks the QA captures: every frame
exists at native size, a feed outage visibly dims the bot rows, and a later
payload without numbers keeps the retained spend dimmed instead of live.
The default sim/build stays Grok-free, which the pager landmark test covers.
"""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ("startup", "empty", "xai", "openrouter", "manual", "bots", "stale", "disabled", "live",
          "offline", "retained", "bots-live", "bots-retained",
          "manual-live", "manual-retained", "manual-unknown-age-live",
          "manual-unknown-age-retained", "wide", "wide-left", "huge-percent", "unlimited")


def brightness(image, box):
    return sum(image.convert("L").crop(box).getdata())


class GrokRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vibepulse-grok-")
        cls.capture_dir = Path(cls.temp.name)
        configure = ["cmake", "-S", "sim", "-B", "sim/build-grok", "-G", "Ninja",
                     "-DTORGET_GROK=ON", "-DTORGET_BOARD=waveshare_216",
                     "-DTORGET_SOLELKOLLEN_DIR=/nonexistent",
                     "-DTORGET_WITH_BUDDY=OFF"]
        lvgl = ROOT / "sim/build/_deps/lvgl-src"
        if lvgl.is_dir():
            configure.append(f"-DFETCHCONTENT_SOURCE_DIR_LVGL={lvgl}")
        subprocess.run(configure, cwd=ROOT, check=True, text=True,
                       capture_output=True)
        subprocess.run(["cmake", "--build", "sim/build-grok"], cwd=ROOT,
                       check=True, text=True, capture_output=True)
        subprocess.run([str(ROOT / "sim/build-grok/torget-sim"),
                        "--vibepulse-grok-qa"], cwd=ROOT,
                       env={**os.environ, "TORGET_CAPTURE_DIR": str(cls.capture_dir)},
                       check=True, text=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def frame(self, name):
        return Image.open(self.capture_dir / f"torget-vibepulse-grok-{name}.bmp")

    def test_every_state_is_captured_at_native_size(self):
        for name in FRAMES:
            with self.subTest(name=name), self.frame(name) as image:
                self.assertEqual(image.size, (480, 480))

    def test_startup_has_no_bar_or_bot_rows(self):
        with self.frame("startup") as startup:
            self.assertEqual(brightness(startup, (22, 254, 458, 282)), 0)
            self.assertEqual(brightness(startup, (22, 375, 458, 425)), 0)
            self.assertEqual(brightness(startup, (220, 24, 408, 48)), 0)
            self.assertGreater(brightness(startup, (16, 105, 464, 235)), 0)
            self.assertGreater(brightness(startup, (22, 429, 200, 447)), 0)

    def test_states_are_distinct(self):
        with self.frame("empty") as empty, self.frame("live") as live:
            self.assertIsNotNone(ImageChops.difference(
                empty.convert("RGB"), live.convert("RGB")).getbbox())

    def test_outage_dims_bot_rows_and_hero(self):
        rows = (16, 348, 464, 425)
        hero = (16, 112, 464, 242)
        with self.frame("live") as live, self.frame("offline") as offline:
            self.assertLess(brightness(offline, rows), 0.7 * brightness(live, rows))
            self.assertLess(brightness(offline, hero), 0.8 * brightness(live, hero))

    def test_retained_reading_stays_dimmed(self):
        hero = (16, 112, 464, 242)
        with self.frame("live") as live, self.frame("retained") as retained:
            self.assertLess(brightness(retained, hero), 0.8 * brightness(live, hero))
            self.assertGreater(brightness(retained, hero), 0)

    def test_native_quota_bar_uses_silver_fill_and_white_pace_tick(self):
        with self.frame("live") as live, self.frame("manual") as manual:
            self.assertEqual(live.convert("RGB").getpixel((40, 270)), (201, 212, 232))
            self.assertEqual(live.convert("RGB").getpixel((200, 270)), (48, 50, 56))
            self.assertEqual(live.convert("RGB").getpixel((268, 256)), (255, 255, 255))
            self.assertEqual(manual.convert("RGB").getpixel((180, 270)), (201, 212, 232))

    def test_budget_details_and_unlimited_fallback(self):
        detail = (22, 232, 458, 252)
        with self.frame("live") as budget, self.frame("unlimited") as unlimited, self.frame("manual") as manual:
            self.assertGreater(brightness(budget, detail), 0)
            self.assertEqual(brightness(unlimited, detail), 0)
            self.assertEqual(brightness(manual, detail), 0)
            self.assertEqual(brightness(unlimited, (22, 254, 458, 282)), 0)
            self.assertGreater(brightness(unlimited, (16, 105, 464, 232)), 0)

    def test_maximum_percent_fits_and_bar_stays_clamped(self):
        with self.frame("huge-percent") as huge, self.frame("wide") as full:
            self.assertGreater(brightness(huge, (16, 105, 464, 160)), 0)
            self.assertEqual(brightness(huge, (16, 160, 464, 232)), 0)
            self.assertIsNone(ImageChops.difference(
                huge.crop((22, 258, 458, 282)).convert("RGB"),
                full.crop((22, 258, 458, 282)).convert("RGB")).getbbox())

    def test_bot_divider_and_status_dot_landmarks(self):
        with self.frame("live") as live:
            image = live.convert("RGB")
            self.assertTrue(all(image.getpixel((x, 340)) == (32, 35, 40)
                                for x in range(22, 458)))
            # The circle spans eight pixels, with its centre on the name caps.
            for y in (354, 379, 404):
                ink = [(x, row) for x in range(22, 30) for row in range(y, y + 8)
                       if image.getpixel((x, row)) != (0, 0, 0)]
                self.assertEqual(max(x for x, _ in ink) - min(x for x, _ in ink), 7)
                self.assertEqual(max(row for _, row in ink) - min(row for _, row in ink), 7)

    def test_retained_bot_and_manual_readings_show_cached_provenance(self):
        for source in ("bots", "manual", "manual-unknown-age"):
            with self.subTest(source=source), self.frame(source + "-live") as live, self.frame(source + "-retained") as retained:
                # The source prefix stays in place; CACHED adds visible suffix ink.
                footer = (22, 429, 458, 447)
                self.assertGreater(brightness(retained, footer), brightness(live, footer))
                self.assertLess(brightness(retained, (16, 112, 464, 242)),
                                0.8 * brightness(live, (16, 112, 464, 242)))


class GrokDocsTests(unittest.TestCase):
    def test_documented_native_frames_match_grok_qa(self):
        from test_docs_frame_drift import GROK_FRAMES

        for name, (board, state, size) in GROK_FRAMES.items():
            with self.subTest(board=board), tempfile.TemporaryDirectory() as directory:
                build = ROOT / ("sim/build-grok" if board == "waveshare_216" else "sim/build-grok-241")
                configure = ["cmake", "-S", "sim", "-B", str(build), "-G", "Ninja",
                             f"-DTORGET_BOARD={board}", "-DTORGET_GROK=ON",
                             "-DTORGET_SOLELKOLLEN_DIR=/nonexistent", "-DTORGET_WITH_BUDDY=OFF"]
                lvgl = ROOT / "sim/build/_deps/lvgl-src"
                if lvgl.is_dir():
                    configure.append(f"-DFETCHCONTENT_SOURCE_DIR_LVGL={lvgl}")
                subprocess.run(configure, cwd=ROOT, check=True, capture_output=True)
                subprocess.run(["cmake", "--build", str(build), "--parallel", "4"],
                               check=True, capture_output=True)
                subprocess.run([str(build / "torget-sim"), "--vibepulse-grok-qa"],
                               cwd=ROOT, check=True, capture_output=True,
                               env={**os.environ, "TORGET_CAPTURE_DIR": directory})
                with Image.open(ROOT / "docs/img" / name) as documented, Image.open(
                    Path(directory) / f"torget-vibepulse-grok-{state}.bmp"
                ) as rendered:
                    self.assertEqual(documented.size, size)
                    self.assertIsNone(ImageChops.difference(
                        documented.convert("RGB"), rendered.convert("RGB")).getbbox())


if __name__ == "__main__":
    unittest.main()
