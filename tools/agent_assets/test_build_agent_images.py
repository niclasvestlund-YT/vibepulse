import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("build-agent-images.py")
SPEC = importlib.util.spec_from_file_location("build_agent_images", SCRIPT)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


def decode_i4(data, size):
    palette = [tuple(data[i:i + 4]) for i in range(0, 16 * 4, 4)]
    indices = []
    for byte in data[16 * 4:]:
        indices.extend((byte >> 4, byte & 0x0F))
    return palette, indices[:size * size]


class AgentAssetTests(unittest.TestCase):
    def test_checked_in_generated_sources_match_exactly(self):
        header, source = build.render_generated_sources()

        self.assertEqual(
            header,
            (build.ROOT / "components/app_tokens/agent_assets.h").read_text(
                encoding="utf-8"
            ),
        )
        self.assertEqual(
            source,
            (build.ROOT / "components/app_tokens/agent_assets.c").read_text(
                encoding="utf-8"
            ),
        )

    def test_generated_source_drift_is_detected(self):
        header, source = build.render_generated_sources()
        with tempfile.TemporaryDirectory() as directory:
            generated = Path(directory) / "agent_assets.c"
            generated.write_text(source + "/* hand edit */\n", encoding="utf-8")

            self.assertNotEqual(source, generated.read_text(encoding="utf-8"))

    def test_claude_small_asset_is_exactly_32_by_32_a8(self):
        data = build.build_claude(32)

        self.assertEqual(len(data), 32 * 32)
        self.assertGreater(sum(byte > 0 for byte in data), 64)

    def test_codex_assets_are_native_deterministic_i4_composites(self):
        for size in (112, 32):
            with self.subTest(size=size):
                data = build.build_codex(size)
                self.assertIsInstance(data, bytes)
                self.assertEqual(len(data), 16 * 4 + size * size // 2)
                self.assertEqual(data, build.build_codex(size))

    def test_codex_palette_reserves_transparency_and_one_exact_white(self):
        for size in (112, 32):
            palette, indices = decode_i4(build.build_codex(size), size)
            used = {palette[index] for index in indices}

            self.assertEqual(palette[0], (0, 0, 0, 0))
            self.assertEqual(sum(color == (255, 255, 255, 255)
                                 for color in palette), 1)
            self.assertIn((255, 255, 255, 255), used)
            self.assertGreater(sum(index == 15 for index in indices),
                               140 if size == 112 else 8)
            self.assertGreater(sum(index not in (0, 15) for index in indices),
                               size * size // 5)

    def test_codex_silhouette_has_clean_transparent_corners(self):
        for size in (112, 32):
            palette, indices = decode_i4(build.build_codex(size), size)
            visible = [(x, y) for y in range(size) for x in range(size)
                       if indices[y * size + x] != 0]
            xs = [x for x, _ in visible]
            ys = [y for _, y in visible]
            x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
            bbox_area = (x1 - x0 + 1) * (y1 - y0 + 1)

            self.assertTrue(all(indices[y * size + x] == 0
                                for x, y in ((0, 0), (size - 1, 0),
                                             (0, size - 1),
                                             (size - 1, size - 1))))
            self.assertLess(len(visible), bbox_area * 0.85)
            for color in palette[1:15]:
                b, g, r, a = color
                if a:
                    self.assertGreater(b, r)
                    self.assertGreater(b, g)
                    self.assertFalse(r > 205 and b > 225,
                                     f"lavender fringe color {color}")

    def test_descriptor_uses_requested_canvas_size(self):
        source = build.descriptor(
            "small", "small_data", "LV_COLOR_FORMAT_A8",
            stride=32, size=1024, canvas=32)

        self.assertIn(".w = 32", source)
        self.assertIn(".h = 32", source)


if __name__ == "__main__":
    unittest.main()
