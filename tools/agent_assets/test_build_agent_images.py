import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).with_name("build-agent-images.py")
SPEC = importlib.util.spec_from_file_location("build_agent_images", SCRIPT)
build = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(build)


class SmallAgentAssetTests(unittest.TestCase):
    def test_claude_small_asset_is_exactly_32_by_32_a8(self):
        data = build.build_claude(32)

        self.assertEqual(len(data), 32 * 32)
        self.assertGreater(sum(byte > 0 for byte in data), 64)

    def test_codex_small_assets_use_real_cloud_and_two_glyph_masks(self):
        cloud, chevron, underscore = build.build_codex(32)

        self.assertEqual(len(cloud), 16 * 4 + 32 * 32 // 2)
        self.assertEqual(len(chevron), 32 * 32)
        self.assertEqual(len(underscore), 32 * 32)
        self.assertNotEqual(chevron, underscore)

    def test_descriptor_uses_requested_canvas_size(self):
        source = build.descriptor(
            "small", "small_data", "LV_COLOR_FORMAT_A8",
            stride=32, size=1024, canvas=32)

        self.assertIn(".w = 32", source)
        self.assertIn(".h = 32", source)


if __name__ == "__main__":
    unittest.main()
