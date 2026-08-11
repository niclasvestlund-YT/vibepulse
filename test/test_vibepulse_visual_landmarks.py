#!/usr/bin/env python3
"""Exact-size raster checks against the shared LVGL simulator renderer."""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
LAYOUT_HEADER = ROOT / "components/app_tokens/vibepulse_layout.generated.h"


def layout_token(name):
    content = LAYOUT_HEADER.read_text(encoding="utf-8")
    match = re.search(rf"^#define {re.escape(name)} (\d+)$", content, re.MULTILINE)
    if match is None:
        raise AssertionError(f"missing generated layout token {name}")
    return int(match.group(1))


BAR_SOLID_CENTER_Y = layout_token("VP_BAR_Y") + layout_token("VP_BAR_H") // 2

EXPECTED = {
    "torget-vibepulse-claude-fable.bmp",
    "torget-vibepulse-claude-all.bmp",
    "torget-vibepulse-codex-weekly.bmp",
    "torget-vibepulse-burn-speed-up.bmp",
    "torget-vibepulse-burn-on-pace.bmp",
    "torget-vibepulse-burn-early.bmp",
    "torget-vibepulse-burn-learning.bmp",
    "torget-vibepulse-burn-unavailable.bmp",
    "torget-vibepulse-volume.bmp",
    "torget-vibepulse-claude-stale.bmp",
    "torget-vibepulse-claude-missing.bmp",
    "torget-vibepulse-codex-missing.bmp",
}


class VibePulseVisualLandmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix="vibepulse-raster-")
        cls.capture_dir = Path(cls.temp.name)
        subprocess.run(
            ["cmake", "-S", "sim", "-B", "sim/build", "-G", "Ninja"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            ["cmake", "--build", "sim/build"],
            cwd=ROOT,
            check=True,
            text=True,
            capture_output=True,
        )
        subprocess.run(
            [str(ROOT / "sim/build/torget-sim"), "--vibepulse-static-qa"],
            cwd=ROOT,
            env={**os.environ, "TORGET_CAPTURE_DIR": str(cls.capture_dir)},
            check=True,
            text=True,
            capture_output=True,
        )

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def image(self, name):
        return Image.open(self.capture_dir / name).convert("RGB")

    def test_capture_matrix_is_complete_and_true_size(self):
        actual = {path.name for path in self.capture_dir.iterdir()}
        self.assertEqual(actual, EXPECTED)
        for name in sorted(EXPECTED):
            with self.subTest(name=name):
                self.assertEqual(self.image(name).size, (480, 480))

    def test_provider_bars_use_locked_colors_and_full_track(self):
        cases = (
            ("torget-vibepulse-claude-fable.bmp", (217, 119, 87)),
            ("torget-vibepulse-claude-all.bmp", (217, 119, 87)),
            ("torget-vibepulse-codex-weekly.bmp", (111, 120, 255)),
        )
        for name, accent in cases:
            with self.subTest(name=name):
                image = self.image(name)
                row = [
                    image.getpixel((x, BAR_SOLID_CENTER_Y))
                    for x in range(480)
                ]
                colored = [x for x, pixel in enumerate(row) if pixel == accent]
                self.assertTrue(colored)
                self.assertEqual(colored[0], 22)
                self.assertEqual(row[457], (48, 50, 56))

    def test_burn_rate_is_unboxed_with_one_shared_separator(self):
        image = self.image("torget-vibepulse-burn-speed-up.bmp")
        hairline = (32, 35, 40)
        separator = [x for x in range(480)
                     if image.getpixel((x, 251)) == hairline]
        self.assertEqual((separator[0], separator[-1]), (22, 457))

    def test_missing_pages_keep_identity_and_empty_progress(self):
        for name in (
            "torget-vibepulse-claude-missing.bmp",
            "torget-vibepulse-codex-missing.bmp",
        ):
            with self.subTest(name=name):
                image = self.image(name)
                row = [
                    image.getpixel((x, BAR_SOLID_CENTER_Y))
                    for x in range(22, 458)
                ]
                self.assertEqual(set(row), {(48, 50, 56)})


if __name__ == "__main__":
    unittest.main()
