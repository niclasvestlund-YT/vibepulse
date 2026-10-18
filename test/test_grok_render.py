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
FRAMES = ("empty", "xai", "manual", "bots", "stale", "disabled", "live",
          "offline", "retained")


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

    def test_states_are_distinct(self):
        with self.frame("empty") as empty, self.frame("live") as live:
            self.assertIsNotNone(ImageChops.difference(
                empty.convert("RGB"), live.convert("RGB")).getbbox())

    def test_outage_dims_bot_rows_and_hero(self):
        rows = (16, 284, 464, 440)
        hero = (16, 112, 464, 242)
        with self.frame("live") as live, self.frame("offline") as offline:
            self.assertLess(brightness(offline, rows), 0.7 * brightness(live, rows))
            self.assertLess(brightness(offline, hero), 0.8 * brightness(live, hero))

    def test_retained_reading_stays_dimmed(self):
        hero = (16, 112, 464, 242)
        with self.frame("live") as live, self.frame("retained") as retained:
            self.assertLess(brightness(retained, hero), 0.8 * brightness(live, hero))
            self.assertGreater(brightness(retained, hero), 0)


if __name__ == "__main__":
    unittest.main()
