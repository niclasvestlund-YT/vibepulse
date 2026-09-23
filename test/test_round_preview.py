#!/usr/bin/env python3
"""Native round landmarks plus exact pins for the public documentation images."""
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from PIL import Image, ImageChops
from test_docs_frame_drift import BOARD_175_FRAMES, ROOT


class RoundPreviewTests(unittest.TestCase):
    def test_native_preview_and_public_image_pins(self):
        # The command independently checks circle, spacing and data landmarks.
        result = subprocess.run([sys.executable, str(ROOT / 'tools/preview_round_ui.py')],
                                cwd=ROOT, text=True, capture_output=True, check=True)
        prefix = 'Preview directory: '
        self.assertTrue(result.stdout.strip().startswith(prefix), result.stdout)
        directory = Path(result.stdout.strip()[len(prefix):])
        try:
            for document, capture in BOARD_175_FRAMES.items():
                with self.subTest(document=document):
                    with Image.open(ROOT / 'docs/img' / document) as actual, Image.open(directory / capture) as expected:
                        self.assertEqual(actual.size, (466, 466))
                        self.assertEqual(actual.format, 'PNG')
                        self.assertEqual(actual.mode, 'RGB')
                        self.assertEqual(getattr(actual, 'n_frames', 1), 1)
                        self.assertIsNone(ImageChops.difference(actual, expected.convert('RGB')).getbbox())
        finally:
            shutil.rmtree(directory)


if __name__ == '__main__':
    unittest.main()
