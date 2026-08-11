#!/usr/bin/env python3
"""Raster landmark checks calibrated from the LVGL simulator authority."""

import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
EXPORTS = ROOT / "design/vibepulse/exports"

# Visible-ink row starts measured from the 480x480 LVGL hero captures. We lock
# only structural landmarks; browser and LVGL antialiasing are intentionally
# allowed to differ.
LVGL_LANDMARK_TOPS = (30, 93, 113, 276, 319, 415)


def nonblack_row_runs(path):
    image = Image.open(path).convert("RGB")
    occupied = []
    for y in range(image.height):
        occupied.append(any(image.getpixel((x, y)) != (0, 0, 0)
                            for x in range(image.width)))

    runs = []
    start = None
    for y, present in enumerate(occupied + [False]):
        if present and start is None:
            start = y
        elif not present and start is not None:
            runs.append((start, y - 1))
            start = None
    return image.size, runs


class VibePulseVisualLandmarkTests(unittest.TestCase):
    def test_browser_exports_follow_lvgl_vertical_landmarks(self):
        for name in ("claude-hero.png", "codex-hero.png"):
            with self.subTest(name=name):
                size, runs = nonblack_row_runs(EXPORTS / name)
                self.assertEqual(size, (480, 480))
                self.assertEqual(len(runs), len(LVGL_LANDMARK_TOPS))
                self.assertEqual(tuple(run[0] for run in runs),
                                 LVGL_LANDMARK_TOPS)
                self.assertEqual(runs[3], (276, 293))


if __name__ == "__main__":
    unittest.main()
