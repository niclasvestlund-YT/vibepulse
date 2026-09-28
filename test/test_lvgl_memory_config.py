#!/usr/bin/env python3
"""Regression tests for Torget's configure-time LVGL pool floor."""

from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
GUARD = ROOT / "cmake" / "torget_lvgl_memory_guard.cmake"
ROOT_CMAKE = ROOT / "CMakeLists.txt"
SDKCONFIG_DEFAULTS = ROOT / "sdkconfig.defaults"


def run_guard(actual_kib: int, board: str = "waveshare_216") -> subprocess.CompletedProcess[str]:
    with tempfile.TemporaryDirectory() as tmp:
        script = Path(tmp) / "check.cmake"
        script.write_text(
            f'set(TORGET_BOARD "{board}")\n'
            f'include("{GUARD.as_posix()}")\n'
            f"torget_require_lvgl_pool({actual_kib})\n",
            encoding="utf-8",
        )
        return subprocess.run(
            ["cmake", "-P", str(script)],
            capture_output=True,
            check=False,
            text=True,
        )


class LvglMemoryConfigTests(unittest.TestCase):
    def test_stale_96_kib_generated_config_is_rejected(self) -> None:
        result = run_guard(96)
        self.assertNotEqual(result.returncode, 0)
        diagnostic = result.stdout + result.stderr
        self.assertIn("LVGL pool is 96 KiB", diagnostic)
        self.assertIn("at least 256 KiB", diagnostic)
        self.assertIn("sdkconfig is stale", diagnostic)
        self.assertIn("CONFIG_LV_MEM_SIZE_KILOBYTES=256", diagnostic)
        self.assertIn("idf.py reconfigure && idf.py build", diagnostic)

    def test_intended_256_kib_config_is_accepted(self) -> None:
        result = run_guard(256)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_18_v2_rejects_an_existing_square_board_pool(self) -> None:
        result = run_guard(256, "waveshare_18_v2")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("at least 768 KiB", result.stdout + result.stderr)

    def test_18_v2_accepts_its_development_pool(self) -> None:
        result = run_guard(768, "waveshare_18_v2")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_root_build_invokes_the_guard(self) -> None:
        root_cmake = ROOT_CMAKE.read_text(encoding="utf-8")
        self.assertIn(
            'include("${CMAKE_CURRENT_SOURCE_DIR}/cmake/'
            'torget_lvgl_memory_guard.cmake")',
            root_cmake,
        )
        self.assertIn(
            'torget_require_lvgl_pool("${CONFIG_LV_MEM_SIZE_KILOBYTES}")',
            root_cmake,
        )

    def test_checked_in_default_matches_the_guard_floor(self) -> None:
        defaults = SDKCONFIG_DEFAULTS.read_text(encoding="utf-8").splitlines()
        self.assertIn("CONFIG_LV_MEM_SIZE_KILOBYTES=256", defaults)


if __name__ == "__main__":
    unittest.main(verbosity=2)
