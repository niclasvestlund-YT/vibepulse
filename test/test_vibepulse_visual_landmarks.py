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
    "torget-vibepulse-claude-single-working.bmp",
    "torget-vibepulse-claude-multi-chat.bmp",
    "torget-vibepulse-claude-idle.bmp",
    "torget-vibepulse-codex-single-working.bmp",
    "torget-vibepulse-codex-multi-chat.bmp",
    "torget-vibepulse-codex-idle.bmp",
    "torget-vibepulse-codex-stale.bmp",
    "torget-vibepulse-claude-today-missing.bmp",
    "torget-vibepulse-claude-today-contradictory.bmp",
    "torget-vibepulse-claude-zero-total.bmp",
    "torget-vibepulse-codex-full-total.bmp",
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

    def test_provider_bars_are_segmented_with_locked_colors_and_marker(self):
        cases = (
            ("torget-vibepulse-claude-fable.bmp", (138, 79, 66),
             (217, 119, 87), 287),
            ("torget-vibepulse-claude-all.bmp", (138, 79, 66),
             (217, 119, 87), 191),
            ("torget-vibepulse-codex-weekly.bmp", (69, 75, 138),
             (111, 120, 255), 152),
        )
        for name, baseline, accent, marker_start in cases:
            with self.subTest(name=name):
                image = self.image(name)
                row = [
                    image.getpixel((x, BAR_SOLID_CENTER_Y))
                    for x in range(480)
                ]
                self.assertIn(baseline, row)
                self.assertIn(accent, row)
                self.assertEqual(
                    row[marker_start:marker_start + 3],
                    [(255, 255, 255)] * 3,
                )
                self.assertEqual(row[457], (48, 50, 56))

                for y in range(layout_token("VP_BAR_Y") - 4,
                               layout_token("VP_BAR_Y") +
                               layout_token("VP_BAR_H") + 4):
                    self.assertEqual(
                        image.getpixel((marker_start + 1, y)),
                        (255, 255, 255),
                    )

    def test_working_halo_is_static_and_provider_colored(self):
        cases = (
            ("torget-vibepulse-claude-single-working.bmp", (217, 119, 87)),
            ("torget-vibepulse-claude-multi-chat.bmp", (217, 119, 87)),
            ("torget-vibepulse-codex-single-working.bmp", (111, 120, 255)),
            ("torget-vibepulse-codex-multi-chat.bmp", (111, 120, 255)),
        )
        halo_only_point = (37, 14)
        for name, accent in cases:
            with self.subTest(name=name):
                image = self.image(name)
                self.assertEqual(image.getpixel(halo_only_point), accent)

        inactive = (
            "torget-vibepulse-claude-idle.bmp",
            "torget-vibepulse-claude-stale.bmp",
            "torget-vibepulse-claude-missing.bmp",
            "torget-vibepulse-codex-idle.bmp",
            "torget-vibepulse-codex-stale.bmp",
            "torget-vibepulse-codex-missing.bmp",
        )
        for name in inactive:
            with self.subTest(name=name):
                self.assertEqual(
                    self.image(name).getpixel(halo_only_point),
                    (0, 0, 0),
                )

    def test_missing_and_stale_headers_have_empty_context_region(self):
        names = (
            "torget-vibepulse-claude-stale.bmp",
            "torget-vibepulse-claude-missing.bmp",
            "torget-vibepulse-codex-stale.bmp",
            "torget-vibepulse-codex-missing.bmp",
        )
        for name in names:
            with self.subTest(name=name):
                image = self.image(name)
                context_region = {
                    image.getpixel((x, y))
                    for y in range(18, 56)
                    for x in range(200, 458)
                }
                self.assertEqual(context_region, {(0, 0, 0)})

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

    def test_missing_today_uses_one_accent_fill_without_marker(self):
        image = self.image("torget-vibepulse-claude-today-missing.bmp")
        row = [image.getpixel((x, BAR_SOLID_CENTER_Y)) for x in range(480)]
        self.assertEqual(set(row[22:340]), {(217, 119, 87)})
        self.assertEqual(set(row[340:458]), {(48, 50, 56)})
        self.assertNotIn((255, 255, 255), row[22:458])

    def test_contradictory_today_never_fabricates_progress(self):
        image = self.image("torget-vibepulse-claude-today-contradictory.bmp")
        row = [
            image.getpixel((x, BAR_SOLID_CENTER_Y))
            for x in range(22, 458)
        ]
        self.assertEqual(set(row), {(48, 50, 56)})

    def test_endpoint_markers_are_clamped_inside_track(self):
        zero = self.image("torget-vibepulse-claude-zero-total.bmp")
        full = self.image("torget-vibepulse-codex-full-total.bmp")
        zero_row = [
            zero.getpixel((x, BAR_SOLID_CENTER_Y)) for x in range(22, 458)
        ]
        full_row = [
            full.getpixel((x, BAR_SOLID_CENTER_Y)) for x in range(22, 458)
        ]
        self.assertEqual(zero_row[:3], [(255, 255, 255)] * 3)
        self.assertEqual(zero_row[3:], [(48, 50, 56)] * 433)
        self.assertEqual(full_row[-3:], [(255, 255, 255)] * 3)
        self.assertIn((69, 75, 138), full_row[:-3])


if __name__ == "__main__":
    unittest.main()
