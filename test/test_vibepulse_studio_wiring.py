#!/usr/bin/env python3
"""Static contract checks for the dependency-free VibePulse Studio UI."""

import hashlib
import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tools/vibepulse_studio/web"
HTML_PATH = WEB / "index.html"
CSS_PATH = WEB / "studio.css"
JS_PATH = WEB / "studio.js"
DESIGN_PATH = ROOT / "design/vibepulse/studio-design.json"

FONT_DIGESTS = {
    "IBMPlexSans-Bold.woff2": (
        "fa7130d854a660b39a7fc9e6e0f2dc23dba5f1346e2adea3e1fe37b6d884133d"
    ),
    "IBMPlexSans-SemiBold.woff2": (
        "f78048030eab62e860efa39a0df79e2e5581bf122eb95b9bc42c0b8a4988d205"
    ),
    "OFL.txt": (
        "7e6b2818edbd8f6a01ae80641cc8f16a51080d08fb4e532be3a0b6f74adb07da"
    ),
}

EXPORT_NAMES = {
    "claude-hero",
    "codex-hero",
    "claude-details",
    "overview",
    "claude-hero-stale",
    "codex-hero-stale",
    "claude-hero-missing",
    "codex-hero-missing",
}


class MarkupInventory(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attributes):
        self.elements.append((tag, dict(attributes)))


class StudioWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        cls.css = CSS_PATH.read_text(encoding="utf-8")
        cls.js = JS_PATH.read_text(encoding="utf-8")
        cls.design = json.loads(DESIGN_PATH.read_text(encoding="utf-8"))
        cls.inventory = MarkupInventory()
        cls.inventory.feed(cls.html)

    def test_canvas_dimensions_come_from_hardware_api(self):
        self.assertNotRegex(self.html, r'(?i)(?:width|height|viewbox)=["\'][^"\']*480')
        self.assertNotIn("480", self.js)
        self.assertIn('loadJson("/api/hardware")', self.js)
        self.assertIn("const response = await fetch(path)", self.js)
        self.assertIn('setAttribute("viewBox"', self.js)
        self.assertIn('setAttribute("width"', self.js)
        self.assertIn('setAttribute("height"', self.js)
        self.assertIn("Preview:", self.js)
        self.assertRegex(self.js, r"hardware\.display")

    def test_preview_root_and_controls_are_accessible(self):
        ids = {
            attributes.get("id")
            for _, attributes in self.inventory.elements
            if attributes.get("id")
        }
        for required in (
            "preview-title",
            "preview-frame",
            "device-preview",
            "screen-background",
            "hero-content",
            "zoom-warning",
            "operation-status",
        ):
            self.assertIn(required, ids)

        svg = next(
            attributes
            for tag, attributes in self.inventory.elements
            if tag == "svg" and attributes.get("id") == "device-preview"
        )
        self.assertEqual(svg.get("role"), "img")
        self.assertEqual(svg.get("aria-label"), "VibePulse AMOLED preview")
        self.assertNotIn('setAttribute("aria-label"', self.js)
        self.assertIn('querySelector("#preview-svg-title")', self.js)
        self.assertIn('querySelector("#preview-svg-description")', self.js)

        hierarchy = re.search(
            r'<section class="preview-panel" aria-labelledby="preview-title">\s*'
            r'<header\b.*?</header>\s*'
            r'<div id="preview-frame" class="scale-1">\s*'
            r'<svg id="device-preview" role="img"\s*'
            r'aria-label="VibePulse AMOLED preview">',
            self.html,
            re.DOTALL,
        )
        self.assertIsNotNone(hierarchy)
        self.assertNotIn('id="preview-viewport"', self.html)
        self.assertNotIn('id="preview-space"', self.html)

        buttons = [
            attributes for tag, attributes in self.inventory.elements
            if tag == "button"
        ]
        self.assertGreaterEqual(len(buttons), 8)
        self.assertTrue(all(button.get("type") == "button" for button in buttons))
        for state_name in ("claude", "codex", "missing", "stale"):
            self.assertIn(f'data-state="{state_name}"', self.html)
        self.assertIn('data-scale="1"', self.html)
        self.assertIn('data-scale="2"', self.html)

    def test_true_size_and_inspection_zoom_are_visibly_distinct(self):
        self.assertIn("scale(1)", self.css)
        self.assertIn("scale(2)", self.css)
        self.assertIn("INSPECTION ZOOM — NOT PHYSICAL SIZE", self.html)
        self.assertRegex(
            self.css,
            r"#preview-frame\.scale-2\s+#device-preview\s*\{[^}]*"
            r"transform:\s*scale\(2\)",
        )
        svg_rule = re.search(r"#device-preview\s*\{([^}]*)\}", self.css, re.DOTALL)
        self.assertIsNotNone(svg_rule)
        self.assertNotIn("max-width", svg_rule.group(1))
        self.assertNotRegex(svg_rule.group(1), r"width:\s*100%")

    def test_ui_uses_one_fixture_driven_render_path(self):
        self.assertEqual(len(re.findall(r"\bfunction\s+render\s*\(", self.js)), 1)
        self.assertRegex(self.js, r"function\s+render\s*\(\s*design\s*,\s*selection\s*\)")
        self.assertRegex(self.js, r"design\.fixtures\s*\[\s*selection\.provider\s*\]")
        for field in ("provider", "model", "effort", "quota", "percent", "today", "reset"):
            self.assertIn(f"fixture.{field}", self.js)
        for palette_name in ("background", "text", "muted", "track", "hairline"):
            self.assertIn(f"design.palette.{palette_name}", self.js)
        self.assertRegex(self.js, r"design\.palette\s*\[\s*selection\.provider\s*\]")
        for hero_name in (
            "safeX", "contentWidth", "providerY", "quotaY", "percentY",
            "percentFontPx", "barY", "barHeight", "resetY", "statusY",
            "statusHeight",
        ):
            self.assertIn(f"hero.{hero_name}", self.js)

    @unittest.skipUnless(shutil.which("osascript"), "JXA is unavailable")
    def test_semantic_copy_matches_firmware_for_every_data_condition(self):
        fixture = {
            "provider": "CLAUDE",
            "quota": "FABLE · WEEK",
            "percent": 73,
            "today": 12,
            "reset": "RESET IN 2D 4H",
        }
        result = self.evaluate_javascript(f"""
          (() => {{
            const fixture = {json.dumps(fixture)};
            return {{
              live: heroCopy(fixture, "live"),
              stale: heroCopy(fixture, "stale"),
              missing: heroCopy(fixture, "missing")
            }};
          }})()
        """)
        self.assertEqual(result["live"], {
            "quotaText": "FABLE · WEEK",
            "percentageText": "73%",
            "todayText": "+12% TODAY",
            "resetText": "RESET IN 2D 4H",
            "statusText": "LIVE",
        })
        self.assertEqual(result["stale"], {
            "quotaText": "FABLE · WEEK",
            "percentageText": "73%",
            "todayText": "+12% TODAY",
            "resetText": "RESET IN 2D 4H",
            "statusText": "STALE",
        })
        self.assertEqual(result["missing"], {
            "quotaText": "WEEKLY",
            "percentageText": "–",
            "todayText": "– TODAY",
            "resetText": "USAGE UNAVAILABLE",
            "statusText": "NO DATA",
        })
        self.assertIn("const percentSize = isMissing ? labelSize : hero.percentFontPx", self.js)
        self.assertIn("textStyle(percentSize", self.js)

    def test_only_reviewed_numeric_tokens_are_editable_and_bounded(self):
        expected = {
            "safeX", "providerY", "quotaY", "percentY", "barY",
            "barHeight", "resetY", "statusY", "statusHeight",
        }
        configured = set(re.findall(r'\{\s*name:\s*"([A-Za-z]+)"', self.js))
        self.assertEqual(configured, expected)
        self.assertIn('input.type = "number"', self.js)
        self.assertIn('input.min = String(bounds.min)', self.js)
        self.assertIn('input.max = String(bounds.max)', self.js)
        self.assertIn("applyHeroChange(", self.js)
        self.assertIn("refreshGeometryControls()", self.js)
        self.assertIn("heroIsServerValid(", self.js)
        self.assertIn("const MIN_TEXT_ROW_STEP = 26", self.js)
        self.assertIn("const MIN_SECTION_GAP = 8", self.js)
        self.assertIn("const MIN_QUOTA_TO_PERCENT_STEP = 28", self.js)
        self.assertIn("const PERCENT_RENDERED_LINE_HEIGHT = 119", self.js)
        self.assertRegex(self.js, r"contentWidth\s*=\s*width\s*-\s*2\s*\*")

    def test_svg_text_uses_named_lvgl_metric_offsets(self):
        self.assertIn("const LVGL_WEB_FONT_METRIC_Y = Object.freeze", self.js)
        for role in (
            "provider", "model", "quota", "today", "percent", "reset",
            "status",
        ):
            self.assertRegex(
                self.js,
                rf"lvglTextY\([^,]+,\s*\"{role}\"\)",
            )

    @unittest.skipUnless(shutil.which("osascript"), "JXA is unavailable")
    def test_svg_text_coordinates_match_lvgl_raster_authority(self):
        result = self.evaluate_javascript("""
          (() => ({
            provider: lvglTextY(23, "provider"),
            model: lvglTextY(23, "model"),
            quota: lvglTextY(86, "quota"),
            today: lvglTextY(86, "today"),
            percent: lvglTextY(112, "percent"),
            reset: lvglTextY(312, "reset"),
            status: lvglTextY(421, "status")
          }))()
        """)
        self.assertEqual(result, {
            "provider": 28,
            "model": 30,
            "quota": 91,
            "today": 94,
            "percent": 98,
            "reset": 317,
            "status": 423,
        })

    @unittest.skipUnless(shutil.which("osascript"), "JXA is unavailable")
    def test_repository_geometry_matches_browser_contract_and_stays_editable(self):
        hero = self.design["hero"]
        self.assertEqual(hero["percentY"], 150)
        self.assertEqual(hero["barY"], 304)
        self.assertEqual(hero["barHeight"], 24)
        expression = f"""
          (() => {{
            const hero = {json.dumps(hero)};
            const requested = {{
              safeX: 23, providerY: 23, quotaY: 73, percentY: 151,
              barY: 305, barHeight: 23, resetY: 353,
              statusY: 391, statusHeight: 65
            }};
            const changes = {{}};
            for (const [name, value] of Object.entries(requested)) {{
              const result = applyHeroChange(hero, name, value, 480, 480);
              changes[name] = {{
                value: result.hero[name],
                valid: heroIsServerValid(result.hero, 480, 480),
                accepted: result.accepted
              }};
            }}
            const bounds = {{}};
            for (const name of Object.keys(requested)) {{
              bounds[name] = heroBounds(hero, name, 480, 480);
            }}
            const badType = applyHeroChange(hero, "statusY", true, 480, 480);
            return {{
              valid: heroIsServerValid(hero, 480, 480),
              changes, bounds, badType
            }};
          }})()
        """
        result = self.evaluate_javascript(expression)
        self.assertTrue(result["valid"])
        self.assertEqual(
            {name: item["value"] for name, item in result["changes"].items()},
            {
                "safeX": 23,
                "providerY": 23,
                "quotaY": 73,
                "percentY": 151,
                "barY": 305,
                "barHeight": 23,
                "resetY": 353,
                "statusY": 391,
                "statusHeight": 65,
            },
        )
        self.assertTrue(all(
            item["valid"] and item["accepted"]
            for item in result["changes"].values()
        ))
        self.assertTrue(all(
            bounds["min"] <= bounds["max"]
            for bounds in result["bounds"].values()
        ))
        self.assertEqual(result["bounds"]["safeX"], {"min": 16, "max": 40})
        self.assertEqual(result["bounds"]["providerY"], {"min": 0, "max": 46})
        self.assertEqual(result["bounds"]["quotaY"], {"min": 48, "max": 122})
        self.assertEqual(result["bounds"]["percentY"], {"min": 100, "max": 177})
        self.assertEqual(result["bounds"]["barY"], {"min": 277, "max": 320})
        self.assertEqual(result["bounds"]["barHeight"], {"min": 12, "max": 24})
        self.assertEqual(result["bounds"]["resetY"], {"min": 336, "max": 364})
        self.assertEqual(result["bounds"]["statusY"], {"min": 378, "max": 414})
        self.assertEqual(result["bounds"]["statusHeight"], {"min": 1, "max": 90})
        self.assertFalse(result["badType"]["accepted"])
        self.assertEqual(result["badType"]["hero"], hero)

        save = self.js[self.js.index("async function saveDesign"):
                       self.js.index("async function exportPng")]
        self.assertIn(
            "if (!heroIsServerValid(state.design.hero, width, height))",
            save,
        )

    @unittest.skipUnless(shutil.which("osascript"), "JXA is unavailable")
    def test_browser_contract_rejects_rendered_and_quota_overlap(self):
        rendered_overlap = {
            **self.design["hero"],
            "barY": 276,
        }
        quota_overlap = {
            "safeX": 22,
            "contentWidth": 436,
            "providerY": 22,
            "quotaY": 77,
            "percentY": 104,
            "percentFontPx": 164,
            "barY": 276,
            "barHeight": 20,
            "resetY": 352,
            "statusY": 390,
            "statusHeight": 66,
        }
        result = self.evaluate_javascript(f"""
          (() => ({{
            renderedOverlap: heroIsServerValid(
              {json.dumps(rendered_overlap)}, 480, 480
            ),
            quotaOverlap: heroIsServerValid(
              {json.dumps(quota_overlap)}, 480, 480
            )
          }}))()
        """)
        self.assertFalse(result["renderedOverlap"])
        self.assertFalse(result["quotaOverlap"])

    @unittest.skipUnless(shutil.which("osascript"), "JXA is unavailable")
    def test_operation_lock_covers_every_mutator_and_snapshots_export_name(self):
        prelude = """
          var __controls = [
            {disabled: false}, {disabled: false}, {disabled: false},
            {disabled: false}, {disabled: false}, {disabled: false}
          ];
          var document = {
            querySelectorAll: function(_selector) { return __controls; }
          };
        """
        expression = """
          (() => {
            const first = beginOperation();
            const locked = __controls.every((control) => control.disabled);
            const second = beginOperation();
            const claudeStale = currentExportName({
              provider: "claude", condition: "stale"
            });
            const codexMissing = currentExportName({
              provider: "codex", condition: "missing"
            });
            finishOperation();
            return {
              first, second, locked,
              unlocked: __controls.every((control) => !control.disabled),
              active: state.operationActive,
              claudeStale, codexMissing
            };
          })()
        """
        result = self.evaluate_javascript(expression, prelude=prelude)
        self.assertEqual(result, {
            "first": True,
            "second": False,
            "locked": True,
            "unlocked": True,
            "active": False,
            "claudeStale": "claude-hero-stale",
            "codexMissing": "codex-hero-missing",
        })
        self.assertIn(
            '"[data-state], [data-scale], #geometry-controls input, "',
            self.js,
        )
        self.assertGreaterEqual(self.js.count("if (state.operationActive)"), 3)

    @unittest.skipUnless(shutil.which("osascript"), "JXA is unavailable")
    def test_status_halo_stays_inside_the_safe_inset(self):
        result = self.evaluate_javascript("""
          (() => {
            const geometry = statusDotGeometry({safeX: 22, barHeight: 18});
            return {
              left: geometry.centerX - geometry.haloRadius,
              centerX: geometry.centerX,
              haloRadius: geometry.haloRadius,
              dotRadius: geometry.dotRadius
            };
          })()
        """)
        self.assertEqual(result, {
            "left": 22,
            "centerX": 28,
            "haloRadius": 6,
            "dotRadius": 4,
        })

    @classmethod
    def evaluate_javascript(cls, expression, prelude=""):
        script = prelude + "\n" + cls.js + "\nJSON.stringify(" + expression + ");\n"
        completed = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", script],
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            raise AssertionError(completed.stderr.strip() or completed.stdout.strip())
        return json.loads(completed.stdout)

    def test_disallowed_preview_features_and_copy_are_absent(self):
        combined = "\n".join((self.html, self.css, self.js))
        for forbidden in (
            "5-HOUR", "5 HOUR", "5-hour", "VIBEPULSE", "linear-gradient",
            "radial-gradient", "box-shadow", "text-shadow", "draggable",
            "free drag", "working rail",
        ):
            self.assertNotIn(forbidden, combined)
        self.assertNotIn("alert(", self.js)
        self.assertNotIn("#D97757", self.js)
        self.assertNotIn("#6F78FF", self.js)
        self.assertNotIn("#D97757", self.html)
        self.assertNotIn("#6F78FF", self.html)

    def test_save_and_export_wait_for_server_responses(self):
        save = self.js[self.js.index("async function saveDesign"):
                       self.js.index("async function exportPng")]
        self.assertLess(save.index("await fetch"), save.index("requireSuccessful"))
        self.assertLess(save.index("requireSuccessful"), save.index("headerDigest"))
        self.assertIn('"Content-Type": "application/json"', save)
        self.assertIn("setOperationStatus", save)

        export = self.js[self.js.index("async function exportPng"):
                         self.js.index("function bindControls")]
        self.assertLess(export.index("document.fonts.ready"), export.index("XMLSerializer"))
        self.assertLess(export.index("await fetch"), export.index("requireSuccessful"))
        self.assertLess(export.index("requireSuccessful"), export.rindex("setOperationStatus"))
        self.assertIn('"Content-Type": "image/png"', export)
        self.assertIn("state.hardware.display", export)
        self.assertIn("canvas.toBlob", export)
        self.assertIn("if (!response.ok)", self.js)
        for required in (
            'setAttribute("xmlns", SVG_NS)',
            'setAttribute("viewBox"',
            "resolvePreviewStyles(",
            "createImageBitmap",
            "new Image()",
            "URL.createObjectURL",
            "URL.revokeObjectURL",
        ):
            self.assertIn(required, export)
        self.assertIn("loadExportFontCssSafely()", self.js)
        self.assertIn('fontSignature !== "wOF2"', self.js)
        self.assertIn("fontResult.ok", export)
        self.assertIn("Local export fonts unavailable", export)

    def test_export_names_match_the_server_allowlist(self):
        block = re.search(
            r"const\s+EXPORT_NAMES\s*=\s*Object\.freeze\(\[([^]]+)]\)",
            self.js,
            re.DOTALL,
        )
        self.assertIsNotNone(block)
        names = set(re.findall(r'"([a-z-]+)"', block.group(1)))
        self.assertEqual(names, EXPORT_NAMES)
        self.assertIn("EXPORT_NAMES.includes(name)", self.js)
        self.assertIn("`/api/export/${name}`", self.js)

    def test_lan_mutation_token_stays_only_in_memory(self):
        self.assertIn('params.get("mutation-token")', self.js)
        self.assertIn("history.replaceState", self.js)
        self.assertIn('"X-VibePulse-Studio-Token"', self.js)
        self.assertRegex(self.js, r"mutationHeaders\([^)]*\)")
        for forbidden in (
            "localStorage", "sessionStorage", "document.cookie", "console.log",
            "console.debug", "console.info",
        ):
            self.assertNotIn(forbidden, self.js)
        self.assertNotIn("mutation-token", self.html)

    def test_fonts_are_local_pinned_assets_with_matching_weights(self):
        for name, digest in FONT_DIGESTS.items():
            payload = (WEB / "fonts" / name).read_bytes()
            self.assertEqual(hashlib.sha256(payload).hexdigest(), digest)
            if name.endswith(".woff2"):
                self.assertEqual(payload[:4], b"wOF2")
                self.assertIn(f"fonts/{name}", self.css)
        self.assertRegex(self.css, r"font-weight:\s*700")
        self.assertRegex(self.css, r"font-weight:\s*600")
        self.assertIn("SIL OPEN FONT LICENSE", (WEB / "fonts/OFL.txt").read_text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
