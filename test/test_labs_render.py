"""All optional-page combinations through the actual shared LVGL renderer."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]

class LabsRenderTests(unittest.TestCase):
    def test_all_masks_have_dense_tiles_and_complete_menu_controls(self):
        subprocess.run(["cmake", "-S", "sim", "-B", "sim/build", "-G", "Ninja"],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run(["cmake", "--build", "sim/build"], cwd=ROOT,
                       check=True, capture_output=True)
        with tempfile.TemporaryDirectory(prefix="vp-labs-") as temporary:
            for mask in range(32):
                with self.subTest(mask=mask):
                    env = dict(os.environ, TORGET_LABS_MASK=str(mask),
                               TORGET_CAPTURE_DIR=temporary)
                    run = subprocess.run([str(ROOT / "sim/build/torget-sim"),
                                          "--vibepulse-labs-qa"],
                                         env=env, capture_output=True, text=True,
                                         timeout=30)
                    self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                    for tag in ("labs-menu", "labs-pending", "labs-github"):
                        with Image.open(Path(temporary) / f"torget-{tag}.bmp") as im:
                            self.assertEqual(im.size, (480, 480))
                            # Independent evidence of all four touch controls:
                            # checking only text missed disappearing borders.
                            for top in (108, 186, 264, 342):
                                self.assertEqual(im.getpixel((74, top + 32)),
                                                 (148, 154, 165))
                                self.assertEqual(im.getpixel((405, top + 32)),
                                                 (148, 154, 165))
                            header = im.crop((140, 24, 340, 80))
                            self.assertGreater(sum(p == (255,255,255) for p in
                                                   header.get_flattened_data()), 200)
                            footer = im.crop((80, 442, 400, 470))
                            self.assertGreater(sum(p != (0,0,0) for p in
                                                   footer.get_flattened_data()), 100)

if __name__ == "__main__":
    unittest.main()
