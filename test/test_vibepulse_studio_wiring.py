#!/usr/bin/env python3
"""Static contract checks for the dependency-free VibePulse Studio UI."""

import hashlib
import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "tools/vibepulse_studio/web"
HTML_PATH = WEB / "index.html"
CSS_PATH = WEB / "studio.css"
JS_PATH = WEB / "studio.js"

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
        self.assertTrue(svg.get("aria-label"))

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
        self.assertRegex(self.css, r"\.scale-2\s*\{[^}]*transform:\s*scale\(2\)")
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
        self.assertIn("clamp(", self.js)
        self.assertRegex(self.js, r"contentWidth\s*=\s*width\s*-\s*2\s*\*")

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
