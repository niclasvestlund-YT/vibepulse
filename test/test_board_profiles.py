#!/usr/bin/env python3
"""Board selection and actual native V2 rasters, not a scaled square mockup."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

from test_docs_frame_drift import BOARD_241_FRAMES

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.hardware_registry import load_registry  # noqa: E402


def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, text=True, capture_output=True,
                          check=True, **kwargs)


class BoardSelectionTests(unittest.TestCase):
    def test_cmake_profiles_and_unsupported_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp)
            (source / "CMakeLists.txt").write_text(
                'cmake_minimum_required(VERSION 3.24)\nproject(check NONE)\n'
                f'include("{ROOT}/cmake/torget_board.cmake")\n'
                'get_directory_property(defs COMPILE_DEFINITIONS)\n'
                'message(STATUS "profile=${TORGET_BOARD};defs=${defs}")\n'
            )
            for board, expected in [(None, "waveshare_216;defs="),
                                    ("waveshare_216", "waveshare_216;defs="),
                                    ("waveshare_241_v2", "waveshare_241_v2;defs=TORGET_BOARD_241_V2=1")]:
                with self.subTest(board=board):
                    args = ["cmake", "-S", str(source), "-B", str(source / str(board))]
                    if board:
                        args.append(f"-DTORGET_BOARD={board}")
                    self.assertIn("profile=" + expected, run(args).stdout)
            result = subprocess.run(
                ["cmake", "-S", str(source), "-B", str(source / "invalid"),
                 "-DTORGET_BOARD=waveshare_241_v1"], text=True, capture_output=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("V1 is not supported", result.stderr)

    def test_registries_do_not_share_units_or_display_geometry(self):
        square = load_registry(ROOT / "spec")
        landscape = load_registry(ROOT / "spec/boards/waveshare_241_v2")
        self.assertEqual(square.capabilities["display.amoled"]["width"], 480)
        self.assertEqual(landscape.capabilities["display.amoled"]["width"], 600)
        self.assertEqual(landscape.capabilities["display.amoled"]["height"], 450)
        self.assertFalse(set(square.units) & set(landscape.units))
        self.assertEqual(landscape.capabilities["controls.settings"]["states"]["unit_verified"], "unknown")


class NativeV2RasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Deliberately exclude any companion that happens to exist at HOME.
        # The converter must follow this build's cache rather than that folder.
        run(["cmake", "-S", "sim", "-B", "sim/build-241", "-G", "Ninja",
             "-DTORGET_BOARD=waveshare_241_v2",
             f"-DTORGET_SOLELKOLLEN_DIR={ROOT}/no-companion"])
        result = run(["tools/preview-ui.sh", "vibepulse", "waveshare_241_v2"],
                     env={**os.environ, "PYTHON_BIN": sys.executable,
                          "CMAKE_BUILD_PARALLEL_LEVEL": "2"})
        cls.output = Path(result.stdout.split("Preview directory: ")[-1].strip())
        if not cls.output.is_dir() or not cls.output.name.startswith("vibepulse-preview."):
            raise AssertionError("preview did not return its private capture directory")
        cls.addClassCleanup(shutil.rmtree, cls.output)

    def frame(self, name):
        with Image.open(self.output / (name + ".png")) as image:
            return image.convert("RGB")

    def test_complete_native_matrix_and_footer_clearance(self):
        frames = list(self.output.glob("*.png"))
        self.assertGreater(len(frames), 100)
        for path in frames:
            with self.subTest(frame=path.name), Image.open(path) as image:
                self.assertEqual(image.size, (600, 450))
        for name in ["settings-menu", "settings-labs-analytics", "settings-labs-github",
                     "wifi-setup-qr", "wifi-setup-manual"]:
            image = self.frame(name)
            self.assertIsNone(image.crop((0, 447, 600, 450)).getbbox(), name)
            # A blank footer would also satisfy clearance: require actual text.
            self.assertIsNotNone(image.crop((160, 416, 440, 443)).getbbox(), name)

    def test_provider_accents_and_native_qr_size(self):
        for name, accent in [("vibepulse-claude-all", (217, 119, 87)),
                             ("vibepulse-codex-weekly-live-46", (111, 120, 255))]:
            count = sum(pixel == accent for pixel in self.frame(name).get_flattened_data())
            self.assertGreater(count, 100, name)
        qr = self.frame("wifi-setup-qr")
        # 196px source QR translated by (60,-15), never stretched to fit.
        self.assertEqual(qr.getpixel((202, 93)), (255, 255, 255))
        self.assertEqual(qr.getpixel((397, 288)), (255, 255, 255))
        self.assertEqual(qr.getpixel((201, 93)), (0, 0, 0))
        self.assertEqual(qr.getpixel((398, 288)), (0, 0, 0))

    def test_documented_frames_are_exact_current_renders(self):
        for document, capture in BOARD_241_FRAMES.items():
            with self.subTest(document=document), Image.open(ROOT / "docs/img" / document) as image:
                self.assertEqual(image.size, (600, 450))
                self.assertEqual(image.mode, "RGB")
                self.assertEqual(getattr(image, "n_frames", 1), 1)
                self.assertFalse(set(image.info) & {"transparency", "gamma", "icc_profile"})
                with Image.open(self.output / capture) as current:
                    self.assertEqual(image.tobytes(), current.convert("RGB").tobytes())
        self.assertIn('"$PYTHON_BIN" test/test_board_profiles.py',
                      (ROOT / "test/run.sh").read_text())


if __name__ == "__main__":
    unittest.main()
