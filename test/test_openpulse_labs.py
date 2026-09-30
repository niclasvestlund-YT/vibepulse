"""Exercise OpenRouter Labs on/off through the shared round LVGL UI."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


class OpenPulseLabsTests(unittest.TestCase):
    def test_unsupported_boards_rejected_before_rendering(self):
        for board in ("waveshare_241_v2", "waveshare_191_touch", "waveshare_18_v2"):
            for option in ("TORGET_OPENPULSE", "TORGET_BUILD_OPENPULSE_SIM"):
                with self.subTest(board=board, option=option), tempfile.TemporaryDirectory() as build:
                    result = subprocess.run(
                        ["cmake", "-S", "sim", "-B", build, "-G", "Ninja",
                         f"-DTORGET_BOARD={board}", f"-D{option}=ON"],
                        cwd=ROOT, text=True, capture_output=True, timeout=30)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn("OpenPulse v0.1 supports only", result.stderr)
                    self.assertFalse((Path(build) / "_deps").exists())

    def test_round_labs_toggle_and_preserved_provider_pages(self):
        build = "sim/build-openpulse-labs"
        commands = [
            ["cmake", "-S", "sim", "-B", build, "-G", "Ninja",
             "-DTORGET_BOARD=waveshare_175", "-DTORGET_OPENPULSE=ON",
             "-DTORGET_SOLELKOLLEN_DIR=/nonexistent", "-DTORGET_WITH_BUDDY=OFF",
             f"-DFETCHCONTENT_SOURCE_DIR_LVGL={ROOT / 'sim/build/_deps/lvgl-src'}"],
            ["cmake", "--build", build, "--parallel", "2"],
        ]
        for command in commands:
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        with tempfile.TemporaryDirectory(prefix="openpulse-labs-") as directory:
            env = dict(os.environ, TORGET_LABS_MASK="511", TORGET_CAPTURE_DIR=directory)
            if sys.platform == "linux":
                env.setdefault("SDL_VIDEODRIVER", "offscreen")
            subprocess.run([str(ROOT / build / "torget-sim"), "--openpulse-labs-qa"],
                           cwd=ROOT, env=env, check=True, capture_output=True, timeout=30)
            frames = list(Path(directory).glob("*.bmp"))
            self.assertEqual(len(frames), 9)
            for frame in frames:
                with Image.open(frame) as image:
                    self.assertEqual(image.size, (466, 466))
                    rgb = image.convert("RGB")
                    for y in range(466):
                        for x in range(466):
                            if (x - 232.5)**2 + (y - 232.5)**2 > 232.5**2:
                                self.assertLess(max(rgb.getpixel((x, y))), 16, str(frame))
            for severity, color in ((1, (238, 184, 97)), (2, (255, 105, 122))):
                with self.subTest(severity=severity), \
                     Image.open(Path(directory) / f"torget-openpulse-key-{severity}-before.bmp") as before, \
                     Image.open(Path(directory) / f"torget-openpulse-key-{severity}-after.bmp") as after:
                    before_budget = before.convert("RGB").crop((70, 268, 390, 300))
                    after_budget = after.convert("RGB").crop((70, 268, 390, 300))
                    self.assertIn(color, before_budget.get_flattened_data())
                    self.assertNotIn(color, after_budget.get_flattened_data())
                    self.assertIn((99, 214, 197), after_budget.get_flattened_data())
            with Image.open(Path(directory) / "torget-openpulse-labs-on.bmp") as enabled, \
                 Image.open(Path(directory) / "torget-openpulse-labs-off-pending.bmp") as pending:
                self.assertNotEqual(enabled.crop((90, 244, 380, 300)).tobytes(),
                                    pending.crop((90, 244, 380, 300)).tobytes())
                self.assertNotEqual(enabled.crop((90, 307, 380, 368)).tobytes(),
                                    pending.crop((90, 307, 380, 368)).tobytes())


if __name__ == "__main__":
    unittest.main()
