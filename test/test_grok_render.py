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
FRAMES = ("startup", "empty", "xai", "openrouter", "plan", "plan-est", "plan-bots", "bots", "stale", "disabled", "live",
          "offline", "retained", "bots-live", "bots-retained",
          "plan-live", "plan-retained", "plan-unknown-age-live",
          "plan-unknown-age-retained", "wide", "wide-left", "huge-percent", "unlimited", "zero-limit", "dollar-thousands", "plan-nowindow")


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

    def test_retained_reading_dims_all_derived_values(self):
        regions = ((22, 232, 458, 252), (22, 258, 140, 280),
                   (320, 254, 329, 282), (22, 286, 230, 338),
                   (250, 286, 458, 338))
        with self.frame("live") as live, self.frame("retained") as retained:
            for region in regions:
                with self.subTest(region=region):
                    self.assertGreater(brightness(retained, region), 0)
                    self.assertLess(brightness(retained, region),
                                    .8 * brightness(live, region))

    def test_fresh_reading_restores_every_value(self):
        with self.frame("live") as live, self.frame("restored") as restored:
            self.assertIsNone(ImageChops.difference(
                live.crop((16, 105, 464, 338)).convert("RGB"),
                restored.crop((16, 105, 464, 338)).convert("RGB")).getbbox())

    def test_native_quota_bar_uses_silver_fill_and_white_pace_tick(self):
        with self.frame("live") as live, self.frame("plan") as plan:
            self.assertEqual(live.convert("RGB").getpixel((40, 270)), (201, 212, 232))
            self.assertEqual(live.convert("RGB").getpixel((200, 270)), (48, 50, 56))
            self.assertEqual(live.convert("RGB").getpixel((324, 256)), (255, 255, 255))
            self.assertEqual(plan.convert("RGB").getpixel((180, 270)), (201, 212, 232))

    def test_plan_without_window_has_neutral_caption_and_no_pace_tick(self):
        with self.frame("plan-nowindow") as neutral, self.frame("plan") as plan:
            caption = (22, 75, 400, 100)
            self.assertIsNotNone(ImageChops.difference(
                neutral.crop(caption).convert("RGB"),
                plan.crop(caption).convert("RGB")).getbbox())
            image = neutral.convert("RGB")
            self.assertTrue(all(image.getpixel((x, 256)) != (255, 255, 255)
                                for x in range(22, 458)))

    def test_est_tag_is_only_on_estimated_plan(self):
        tag = (410, 75, 444, 93)
        with self.frame("plan-est") as estimate, self.frame("plan") as copied:
            self.assertGreater(brightness(estimate, tag), 0)
            self.assertEqual(brightness(copied, tag), 0)

    def test_budget_details_and_unlimited_fallback(self):
        detail = (22, 232, 458, 252)
        with self.frame("live") as budget, self.frame("unlimited") as unlimited, self.frame("plan") as plan:
            self.assertGreater(brightness(budget, detail), 0)
            self.assertEqual(brightness(unlimited, detail), 0)
            self.assertEqual(brightness(plan, detail), 0)
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

    def test_retained_bot_and_plan_readings_show_cached_provenance(self):
        for source in ("bots", "plan", "plan-unknown-age"):
            with self.subTest(source=source), self.frame(source + "-live") as live, self.frame(source + "-retained") as retained:
                # The source prefix stays in place; CACHED adds visible suffix ink.
                footer = (22, 429, 458, 447)
                self.assertGreater(brightness(retained, footer), brightness(live, footer))
                self.assertLess(brightness(retained, (16, 112, 464, 242)),
                                0.8 * brightness(live, (16, 112, 464, 242)))


class GrokFixtureTests(unittest.TestCase):
    def test_175_fresh_percent_unit_is_brighter_than_retained(self):
        build = ROOT / "sim/build-grok-waveshare_175"
        configure = ["cmake", "-S", "sim", "-B", str(build), "-G", "Ninja",
                     "-DTORGET_BOARD=waveshare_175", "-DTORGET_GROK=ON",
                     "-DTORGET_SOLELKOLLEN_DIR=/nonexistent", "-DTORGET_WITH_BUDDY=OFF"]
        lvgl = ROOT / "sim/build/_deps/lvgl-src"
        if lvgl.is_dir():
            configure.append(f"-DFETCHCONTENT_SOURCE_DIR_LVGL={lvgl}")
        for command in (configure, ["cmake", "--build", str(build)]):
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([str(build / "torget-sim"), "--vibepulse-grok-qa"],
                           cwd=ROOT, check=True, capture_output=True,
                           env={**os.environ, "TORGET_CAPTURE_DIR": directory})
            with Image.open(Path(directory) / "torget-vibepulse-grok-live.bmp") as live, Image.open(
                Path(directory) / "torget-vibepulse-grok-retained.bmp"
            ) as retained:
                self.assertEqual(live.size, (466, 466))
                self.assertEqual(retained.size, live.size)
                # Same 28% reading and glyph position; only freshness changes.
                unit = (286, 222, 323, 254)
                self.assertGreater(brightness(retained, unit), 0)
                self.assertGreater(brightness(live, unit),
                                   1.5 * brightness(retained, unit))
                for region in ((100, 305, 380, 365), (70, 280, 410, 304), (0, 0, 466, 35)):
                    with self.subTest(region=region):
                        self.assertGreater(brightness(retained, region), 0)
                        self.assertLess(brightness(retained, region), .85 * brightness(live, region))
                with Image.open(Path(directory) / "torget-vibepulse-grok-restored.bmp") as restored:
                    self.assertIsNone(ImageChops.difference(
                        live.convert("RGB"), restored.convert("RGB")).getbbox())
                with Image.open(Path(directory) / "torget-vibepulse-grok-first-live.bmp") as first:
                    for region in (unit, (100, 305, 380, 365), (70, 280, 410, 304), (0, 0, 466, 35)):
                        self.assertEqual(brightness(first, region), brightness(live, region))

    def test_191_dollar_thousands_font_and_capture(self):
        build = ROOT / "sim/build-grok-waveshare_191_touch"
        configure = ["cmake", "-S", "sim", "-B", str(build), "-G", "Ninja",
                     "-DTORGET_BOARD=waveshare_191_touch", "-DTORGET_GROK=ON",
                     "-DTORGET_SOLELKOLLEN_DIR=/nonexistent", "-DTORGET_WITH_BUDDY=OFF"]
        lvgl = ROOT / "sim/build/_deps/lvgl-src"
        if lvgl.is_dir():
            configure.append(f"-DFETCHCONTENT_SOURCE_DIR_LVGL={lvgl}")
        for command in (configure, ["cmake", "--build", str(build)]):
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        with tempfile.TemporaryDirectory() as directory:
            # QA also checks the actual hero font's dollar/comma glyphs and height.
            subprocess.run([str(build / "torget-sim"), "--vibepulse-grok-qa"],
                           cwd=ROOT, check=True, capture_output=True,
                           env={**os.environ, "TORGET_CAPTURE_DIR": directory})
            with Image.open(Path(directory) / "torget-vibepulse-grok-dollar-thousands.bmp") as image:
                self.assertEqual(image.size, (536, 240))
                self.assertGreater(brightness(image, (16, 40, 282, 130)), 0)

    def test_18_money_and_caption_fit(self):
        build = ROOT / "sim/build-grok-waveshare_18_v2"
        configure = ["cmake", "-S", "sim", "-B", str(build), "-G", "Ninja",
                     "-DTORGET_BOARD=waveshare_18_v2", "-DTORGET_GROK=ON",
                     "-DTORGET_SOLELKOLLEN_DIR=/nonexistent", "-DTORGET_WITH_BUDDY=OFF"]
        lvgl = ROOT / "sim/build/_deps/lvgl-src"
        if lvgl.is_dir():
            configure.append(f"-DFETCHCONTENT_SOURCE_DIR_LVGL={lvgl}")
        for command in (configure, ["cmake", "--build", str(build)]):
            subprocess.run(command, cwd=ROOT, check=True, capture_output=True)
        with tempfile.TemporaryDirectory() as directory:
            subprocess.run([str(build / "torget-sim"), "--vibepulse-grok-qa"],
                           cwd=ROOT, check=True, capture_output=True,
                           env={**os.environ, "TORGET_CAPTURE_DIR": directory})
            with Image.open(Path(directory) / "torget-vibepulse-grok-dollar-millions.bmp") as image:
                self.assertEqual(image.size, (368, 448))

    def test_active_includes_working_and_waiting(self):
        import json

        for path in sorted((ROOT / "sim-fixtures").glob("*.json")):
            payload = json.loads(path.read_text())
            if "active" in payload:
                with self.subTest(fixture=path.name):
                    self.assertEqual(payload["active"], sum(
                        bot["state"] in ("working", "waiting")
                        for bot in payload.get("bots", [])))


class GrokReviewWiringTests(unittest.TestCase):
    def test_round_bot_fields_follow_feed_and_styles_only_change_on_transition(self):
        source = (ROOT / "components/app_tokens/usage_screen.c").read_text()
        self.assertIn('left_stale=feed_stale;', source)
        self.assertIn('if (feed->bot_count) reset_stale=feed_stale;', source)
        self.assertIn('if (lv_obj_get_style_text_opa(obj,0)!=opa)', source)
        self.assertIn('if (stale!=grok_page.rendered_stale)', source)
        self.assertIn('tick.opa=lv_obj_get_style_arc_opa(page->track,0)', source)

    def test_docs_describe_postpaid_credits_and_no_window(self):
        docs = (ROOT / "docs/grok-pulse.md").read_text()
        self.assertIn('postpaid invoice amount due this billing month', docs)
        self.assertIn('not drawn on the panel page', docs)
        self.assertIn('Without `windowSeconds` no pace marker', docs)
        self.assertNotIn('xAI uses 30 days', docs)
        self.assertNotIn('shown only with', docs)


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
