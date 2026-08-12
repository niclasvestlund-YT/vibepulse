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
    "torget-vibepulse-codex-weekly-live-46.bmp",
    "torget-vibepulse-claude-fable-cached-stale.bmp",
    "torget-vibepulse-codex-weekly-cached-stale.bmp",
    "torget-vibepulse-claude-fable-no-data.bmp",
    "torget-vibepulse-burn-speed-up.bmp",
    "torget-vibepulse-burn-on-pace.bmp",
    "torget-vibepulse-burn-early.bmp",
    "torget-vibepulse-burn-learning.bmp",
    "torget-vibepulse-burn-unavailable.bmp",
    "torget-vibepulse-claude-stale.bmp",
    "torget-vibepulse-claude-missing.bmp",
    "torget-vibepulse-codex-missing.bmp",
    "torget-vibepulse-claude-single-working.bmp",
    "torget-vibepulse-claude-lease-expired.bmp",
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
    "torget-vibepulse-claude-needs-you.bmp",
    "torget-vibepulse-codex-needs-you.bmp",
    "torget-vibepulse-claude-error.bmp",
    "torget-vibepulse-codex-error.bmp",
    "torget-vibepulse-two-waiting-queued.bmp",
    "torget-vibepulse-claude-done-static.bmp",
    "torget-vibepulse-codex-done-static.bmp",
    "torget-vibepulse-claude-swedish-project.bmp",
    "torget-vibepulse-tracker-claude.bmp",
    "torget-vibepulse-tracker-codex.bmp",
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
        path = self.capture_dir / name
        self.assertTrue(path.is_file(), f"missing expected capture: {name}")
        return Image.open(path).convert("RGB")

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

    def test_corrected_quota_provenance_fixtures_are_visually_distinct(self):
        live = self.image("torget-vibepulse-codex-weekly-live-46.bmp")
        stale = self.image("torget-vibepulse-codex-weekly-cached-stale.bmp")
        no_data = self.image("torget-vibepulse-claude-fable-no-data.bmp")
        claude_stale = self.image(
            "torget-vibepulse-claude-fable-cached-stale.bmp"
        )

        # Source-stale changes only the reserved status and halo treatment,
        # while retaining the exact cached quota geometry.
        content = (18, 56, 458, 390)
        self.assertEqual(live.crop(content).tobytes(),
                         stale.crop(content).tobytes())
        self.assertNotEqual(live.crop((200, 14, 458, 56)).tobytes(),
                            stale.crop((200, 14, 458, 56)).tobytes())
        self.assertNotEqual(claude_stale.crop(content).tobytes(),
                            no_data.crop(content).tobytes())
        self.assertEqual(live.getpixel((37, 14)), (111, 120, 255))
        self.assertEqual(stale.getpixel((37, 14)), (0, 0, 0))

        # Missing data keeps the fixed page identity but never paints quota
        # progress, reset or today's delta.
        row = [no_data.getpixel((x, BAR_SOLID_CENTER_Y))
               for x in range(22, 458)]
        self.assertEqual(set(row), {(48, 50, 56)})
        self.assertEqual(
            no_data.crop((22, 72, 458, 118)).tobytes(),
            claude_stale.crop((22, 72, 458, 118)).tobytes(),
            "no-data must retain the fixed FABLE · WEEK page identity",
        )
        status = [(x, y) for y in range(18, 56) for x in range(300, 458)
                  if no_data.getpixel((x, y)) != (0, 0, 0)]
        self.assertEqual(
            (min(x for x, _ in status), max(x for x, _ in status),
             min(y for _, y in status), max(y for _, y in status)),
            (395, 457, 32, 41),
            "NO DATA must occupy the one reserved status slot",
        )
        hero = [(x, y) for y in range(140, 280) for x in range(22, 458)
                if no_data.getpixel((x, y)) == (255, 255, 255)]
        self.assertEqual(
            (min(x for x, _ in hero), max(x for x, _ in hero),
             min(y for _, y in hero), max(y for _, y in hero), len(hero)),
            (22, 105, 208, 226, 1596),
            "no-data hero must remain the deterministic large dash mask",
        )
        for color, expected_bounds in (
            ((217, 119, 87), (24, 40, 364, 367)),
            ((255, 255, 255), (439, 455, 364, 367)),
        ):
            glyphs = [(x, y) for y in range(345, 390)
                      for x in range(22, 458)
                      if no_data.getpixel((x, y)) == color]
            self.assertEqual(
                (min(x for x, _ in glyphs), max(x for x, _ in glyphs),
                 min(y for _, y in glyphs), max(y for _, y in glyphs)),
                expected_bounds,
                "no-data lower values must be dash glyphs, not numbers",
            )

    def test_native_codex_icons_are_transparent_non_rectangular_assets(self):
        quota = self.image("torget-vibepulse-codex-weekly-live-46.bmp")
        attention = self.image("torget-vibepulse-codex-needs-you.bmp")
        cases = (
            (quota, (22, 20, 54, 52), 40),
            (attention, (184, 89, 296, 201), 1000),
        )
        for image, box, minimum_blue in cases:
            with self.subTest(box=box):
                crop = image.crop(box)
                pixels = list(crop.get_flattened_data())
                self.assertEqual(crop.getpixel((0, 0)), (0, 0, 0))
                self.assertEqual(crop.getpixel((crop.width - 1, 0)),
                                 (0, 0, 0))
                self.assertEqual(crop.getpixel((0, crop.height - 1)),
                                 (0, 0, 0))
                self.assertEqual(crop.getpixel(
                    (crop.width - 1, crop.height - 1)), (0, 0, 0))
                self.assertIn((255, 255, 255), pixels)
                self.assertGreater(
                    sum(1 for red, green, blue in pixels
                        if blue > red + 30 and blue > green + 20),
                    minimum_blue,
                )
                non_black = {(x, y) for y in range(crop.height)
                             for x in range(crop.width)
                             if crop.getpixel((x, y)) != (0, 0, 0)}
                xs = [x for x, _ in non_black]
                ys = [y for _, y in non_black]
                bounds_area = ((max(xs) - min(xs) + 1) *
                               (max(ys) - min(ys) + 1))
                self.assertLess(len(non_black), bounds_area * 0.9)

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
            "torget-vibepulse-claude-lease-expired.bmp",
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

    def test_missing_and_stale_headers_show_only_quota_truth_status(self):
        cases = (
            ("torget-vibepulse-claude-stale.bmp", 414),
            ("torget-vibepulse-claude-missing.bmp", 395),
            ("torget-vibepulse-codex-stale.bmp", 414),
            ("torget-vibepulse-codex-missing.bmp", 395),
        )
        for name, expected_left in cases:
            with self.subTest(name=name):
                image = self.image(name)
                status_pixels = [
                    (x, y)
                    for y in range(18, 56)
                    for x in range(200, 458)
                    if image.getpixel((x, y)) != (0, 0, 0)
                ]
                self.assertTrue(status_pixels)
                self.assertEqual(min(x for x, _ in status_pixels),
                                 expected_left)
                self.assertEqual(max(x for x, _ in status_pixels), 457)
                self.assertEqual(
                    (min(y for _, y in status_pixels),
                     max(y for _, y in status_pixels)),
                    (32, 41),
                )

    def test_working_lease_expiry_matches_idle_header_and_halo(self):
        expired = self.image("torget-vibepulse-claude-lease-expired.bmp")
        idle = self.image("torget-vibepulse-claude-idle.bmp")
        header = (18, 14, 458, 56)
        self.assertEqual(expired.crop(header).tobytes(),
                         idle.crop(header).tobytes())

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
        accent = (217, 119, 87)
        daily_pixels = [
            (x, y)
            for y in range(345, 390)
            for x in range(22, 220)
            if image.getpixel((x, y)) == accent
        ]
        self.assertEqual(
            (min(x for x, _ in daily_pixels),
             max(x for x, _ in daily_pixels),
             min(y for _, y in daily_pixels),
             max(y for _, y in daily_pixels),
             len(daily_pixels)),
            (24, 40, 364, 367, 68),
        )

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

    def test_attention_outline_is_six_pixels_at_exact_inset_and_color(self):
        cases = (
            ("torget-vibepulse-claude-needs-you.bmp", (217, 119, 87)),
            ("torget-vibepulse-codex-needs-you.bmp", (111, 120, 255)),
            ("torget-vibepulse-claude-error.bmp", (217, 119, 87)),
            ("torget-vibepulse-codex-error.bmp", (111, 120, 255)),
        )
        for name, accent in cases:
            with self.subTest(name=name):
                image = self.image(name)
                self.assertEqual(
                    [image.getpixel((240, y)) for y in range(8, 14)],
                    [accent] * 6,
                )
                self.assertEqual(image.getpixel((240, 7)), (0, 0, 0))
                self.assertEqual(image.getpixel((240, 14)), (0, 0, 0))
                self.assertEqual(
                    [image.getpixel((x, 240)) for x in range(8, 14)],
                    [accent] * 6,
                )
                self.assertEqual(
                    [image.getpixel((x, 240)) for x in range(466, 472)],
                    [accent] * 6,
                )
                self.assertEqual(
                    [image.getpixel((240, y)) for y in range(466, 472)],
                    [accent] * 6,
                )

    def test_attention_icons_use_real_provider_assets_inside_exact_box(self):
        claude = self.image("torget-vibepulse-claude-needs-you.bmp")
        codex = self.image("torget-vibepulse-codex-needs-you.bmp")
        box = (184, 89, 296, 201)
        claude_pixels = list(claude.crop(box).get_flattened_data())
        codex_pixels = list(codex.crop(box).get_flattened_data())
        self.assertIn((217, 119, 87), claude_pixels)
        self.assertIn((255, 255, 255), codex_pixels)
        self.assertGreater(
            sum(1 for red, green, blue in codex_pixels
                if blue > red + 30 and blue > green + 20),
            1000,
            "Codex cloud must retain a substantial source-colored area",
        )

    def test_attention_ring_is_static_at_reviewed_midpoint(self):
        cases = (
            ("torget-vibepulse-claude-needs-you.bmp", (217, 119, 87)),
            ("torget-vibepulse-codex-needs-you.bmp", (111, 120, 255)),
        )
        for name, accent in cases:
            with self.subTest(name=name):
                image = self.image(name)
                self.assertEqual(image.getpixel((240, 77)), accent)
                self.assertEqual(image.getpixel((240, 78)), accent)

    def test_attention_copy_occupies_reviewed_rows(self):
        cases = (
            "torget-vibepulse-claude-needs-you.bmp",
            "torget-vibepulse-codex-needs-you.bmp",
            "torget-vibepulse-claude-error.bmp",
            "torget-vibepulse-codex-error.bmp",
            "torget-vibepulse-two-waiting-queued.bmp",
            "torget-vibepulse-claude-done-static.bmp",
            "torget-vibepulse-codex-done-static.bmp",
        )
        regions = ((31, 56), (246, 314), (321, 355),
                   (365, 390), (430, 456))
        for name in cases:
            with self.subTest(name=name):
                image = self.image(name)
                for top, bottom in regions:
                    pixels = [
                        image.getpixel((x, y))
                        for y in range(top, bottom)
                        for x in range(30, 450)
                    ]
                    self.assertTrue(any(pixel != (0, 0, 0)
                                        for pixel in pixels))

        waiting = self.image("torget-vibepulse-claude-needs-you.bmp")
        done = self.image("torget-vibepulse-claude-done-static.bmp")
        waiting_white = [
            (x, y) for y in range(246, 314) for x in range(14, 466)
            if waiting.getpixel((x, y)) == (255, 255, 255)
        ]
        done_white = [
            (x, y) for y in range(246, 314) for x in range(14, 466)
            if done.getpixel((x, y)) == (255, 255, 255)
        ]
        self.assertGreater(max(x for x, _ in waiting_white) -
                           min(x for x, _ in waiting_white), 260)
        self.assertLess(max(x for x, _ in done_white) -
                        min(x for x, _ in done_white), 180)

    def test_attention_count_and_error_states_have_distinct_rasters(self):
        one = self.image("torget-vibepulse-claude-needs-you.bmp")
        two = self.image("torget-vibepulse-two-waiting-queued.bmp")
        claude_error = self.image("torget-vibepulse-claude-error.bmp")
        codex_error = self.image("torget-vibepulse-codex-error.bmp")
        detail = (14, 365, 466, 390)
        title = (14, 246, 466, 314)
        self.assertNotEqual(one.crop(detail).tobytes(),
                            two.crop(detail).tobytes())
        self.assertNotEqual(one.crop(title).tobytes(),
                            claude_error.crop(title).tobytes())
        self.assertNotEqual(claude_error.crop((14, 31, 466, 390)).tobytes(),
                            codex_error.crop((14, 31, 466, 390)).tobytes())

    def test_attention_project_renders_uppercase_swedish_glyphs(self):
        image = self.image("torget-vibepulse-claude-swedish-project.bmp")
        accent = (217, 119, 87)
        project_pixels = [
            (x, y) for y in range(321, 355) for x in range(20, 460)
            if image.getpixel((x, y)) == accent
        ]
        self.assertGreater(len(project_pixels), 900)
        self.assertEqual(min(y for _, y in project_pixels), 321)
        self.assertEqual(max(y for _, y in project_pixels), 344)


if __name__ == "__main__":
    unittest.main()
