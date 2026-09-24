#!/usr/bin/env python3
"""Native round landmarks plus exact pins for the public documentation images."""
import shutil
import subprocess
import sys
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageChops
from test_docs_frame_drift import BOARD_175_FRAMES, ROOT


class RoundPreviewTests(unittest.TestCase):
    def test_round_needs_you_hands_decisions_to_computer(self):
        """The real renderer's approve attempts must never send a round verdict."""
        build = ROOT / 'sim/build-175/torget-sim'
        subprocess.run(['cmake', '-S', 'sim', '-B', 'sim/build-175', '-G', 'Ninja',
                        '-DTORGET_BOARD=waveshare_175',
                        f'-DTORGET_SOLELKOLLEN_DIR={ROOT}/no-companion'],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run(['cmake', '--build', 'sim/build-175', '--parallel', '2'],
                       cwd=ROOT, check=True, capture_output=True)
        with tempfile.TemporaryDirectory(prefix='vibepulse-round-needs-you.') as directory:
            result = subprocess.run([str(build), '--vibepulse-needs-you-qa'],
                                    cwd=ROOT,
                                    env={**os.environ, 'TORGET_CAPTURE_DIR': directory},
                                    text=True, capture_output=True, check=True)
        self.assertNotIn('needs-you verdict:', result.stdout)

    def test_full_app_surfaces_stay_inside_round_glass(self):
        """Exercise the real shared LVGL surfaces, including attention and OTA."""
        build = ROOT / 'sim/build-175/torget-sim'
        subprocess.run(['cmake', '-S', 'sim', '-B', 'sim/build-175', '-G', 'Ninja',
                        '-DTORGET_BOARD=waveshare_175',
                        f'-DTORGET_SOLELKOLLEN_DIR={ROOT}/no-companion'],
                       cwd=ROOT, check=True, capture_output=True)
        subprocess.run(['cmake', '--build', 'sim/build-175', '--parallel', '2'],
                       cwd=ROOT, check=True, capture_output=True)
        with tempfile.TemporaryDirectory(prefix='vibepulse-round-app.') as directory:
            subprocess.run([str(build), '--vibepulse-static-qa'], cwd=ROOT,
                           env={**os.environ, 'TORGET_CAPTURE_DIR': directory,
                                'TORGET_LABS_MASK': '0'},
                           check=True, capture_output=True)
            captures = list(Path(directory).glob('*.bmp'))
            self.assertGreaterEqual(len(captures), 100)
            outside = Image.new('L', (466, 466))
            outside.putdata([255 if (x - 232.5) ** 2 + (y - 232.5) ** 2 > 230 ** 2 else 0
                             for y in range(466) for x in range(466)])
            for capture in captures:
                with self.subTest(capture=capture.name), Image.open(capture) as source:
                    self.assertEqual(source.size, (466, 466))
                    leaked = ImageChops.multiply(source.convert('L'), outside)
                    self.assertLessEqual(leaked.getextrema()[1], 24)

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
