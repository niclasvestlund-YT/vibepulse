#!/usr/bin/env python3
"""Behavioral and wiring tests for the secure VibePulse preview command."""

import re
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "tools/preview-ui.sh"
SIM_PATH = ROOT / "sim/main.c"
RUNNER_PATH = ROOT / "test/run.sh"

EXPECTED_BMPS = {
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
    "torget-vibepulse-claude-today-missing.bmp",
    "torget-vibepulse-claude-today-contradictory.bmp",
    "torget-vibepulse-claude-zero-total.bmp",
    "torget-vibepulse-codex-full-total.bmp",
}
EXPECTED_PNGS = {
    f"{Path(name).stem.removeprefix('torget-')}.png"
    for name in EXPECTED_BMPS
}


def extract_heredoc(script, delimiter):
    match = re.search(
        rf"<<'{re.escape(delimiter)}'\n(?P<source>.*?)\n{re.escape(delimiter)}$",
        script,
        re.MULTILINE | re.DOTALL,
    )
    if match is None:
        raise ValueError(f"missing {delimiter} heredoc")
    return match.group("source")


def write_bmp(path, size=(480, 480), color="navy"):
    Image.new("RGB", size, color).save(path)


class PreviewWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = SCRIPT_PATH.read_text(encoding="utf-8")
        cls.sim = SIM_PATH.read_text(encoding="utf-8")
        cls.runner = RUNNER_PATH.read_text(encoding="utf-8")

    def test_private_capture_command_wiring(self):
        self.assertEqual(stat.S_IMODE(SCRIPT_PATH.stat().st_mode), 0o755)
        self.assertRegex(self.script, r"(?m)^set -eu$")
        self.assertIn("PYTHON_BIN=${PYTHON_BIN:-python3}", self.script)
        self.assertIn(
            'mktemp -d "${TMPDIR:-/tmp}/vibepulse-preview.XXXXXX"',
            self.script,
        )
        self.assertIn('chmod 0700 "$output_dir"', self.script)
        self.assertIn('capture_dir="$output_dir/captures"', self.script)
        self.assertIn('mkdir -m 0700 "$capture_dir"', self.script)
        self.assertIn(
            'TORGET_CAPTURE_DIR="$capture_dir" '
            '"$repo/sim/build/torget-sim" --vibepulse-static-qa',
            self.script,
        )
        self.assertIn('cmake -S "$repo/sim"', self.script)
        self.assertIn('cmake --build "$repo/sim/build"', self.script)
        self.assertNotIn('Path("/tmp").glob', self.script)
        self.assertNotIn("captures-before.json", self.script)
        self.assertNotIn("rm -f /tmp/torget-vibepulse", self.script)
        self.assertIn("shutil.rmtree", self.script)

    def test_converter_and_registry_wiring(self):
        self.assertRegex(
            self.script,
            r'(?m)^"\$PYTHON_BIN" - "\$repo" "\$output_dir" '
            r'"\$capture_dir" <<\'PREVIEW_CONVERTER_PY\'$',
        )
        self.assertIn("from PIL import Image", self.script)
        self.assertIn(
            "from tools.hardware_registry import load_registry",
            self.script,
        )
        self.assertIn('registry.capabilities["display.amoled"]', self.script)
        self.assertIn('display["width"]', self.script)
        self.assertIn('display["height"]', self.script)
        self.assertNotIn('["properties"]', self.script)
        self.assertIn("missing", self.script)
        self.assertIn("unexpected", self.script)
        self.assertIn("lstat()", self.script)
        self.assertIn("stat.S_ISREG", self.script)
        self.assertIn("os.O_NOFOLLOW", self.script)
        self.assertIn("image.size != expected", self.script)
        self.assertIn("Preview directory:", self.script)
        for name in EXPECTED_BMPS:
            with self.subTest(name=name):
                self.assertIn(name, self.script)

    def test_simulator_uses_private_dir_nofollow_and_propagates_failures(self):
        self.assertIn('getenv("TORGET_CAPTURE_DIR")', self.sim)
        self.assertIn("O_WRONLY | O_CREAT | O_TRUNC | O_NOFOLLOW", self.sim)
        self.assertIn("0600", self.sim)
        self.assertNotIn('fopen(path, "wb")', self.sim)
        self.assertIn("capture_failures", self.sim)
        self.assertIn("return capture_failures == 0 ? 0 : 1;", self.sim)
        self.assertIn("return run_vibepulse_static_qa();", self.sim)
        self.assertIn("fwrite", self.sim)
        self.assertIn("fclose", self.sim)

    def test_runner_preflights_exact_pillow_pin(self):
        self.assertIn("import PIL", self.runner)
        self.assertRegex(
            self.runner,
            r"PIL\.__version__\s*==\s*['\"]12\.3\.0['\"]",
        )
        self.assertIn('"$PYTHON_BIN" test_preview_ui.py', self.runner)

    def test_usage_gate_remains_exact(self):
        self.assertIn("usage:", self.script)
        self.assertIn("vibepulse", self.script)
        self.assertIn("exit 2", self.script)


class ConverterBehaviorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        script = SCRIPT_PATH.read_text(encoding="utf-8")
        cls.converter = extract_heredoc(script, "PREVIEW_CONVERTER_PY")

    def create_case(self, root, omitted=None):
        capture_dir = root / "captures"
        output_dir = root / "output"
        capture_dir.mkdir(mode=0o700)
        output_dir.mkdir(mode=0o700)
        for name in EXPECTED_BMPS - {omitted}:
            write_bmp(capture_dir / name)
        return capture_dir, output_dir

    def run_converter(self, capture_dir, output_dir):
        return subprocess.run(
            [
                sys.executable,
                "-",
                str(ROOT),
                str(output_dir),
                str(capture_dir),
            ],
            input=self.converter,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_exact_capture_matrix_exports_matching_pngs(self):
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir, output_dir = self.create_case(Path(tmp))
            result = self.run_converter(capture_dir, output_dir)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                {path.name for path in output_dir.glob("*.png")},
                EXPECTED_PNGS,
            )
            self.assertEqual(
                len(result.stdout.splitlines()), len(EXPECTED_BMPS) + 1
            )
            for output in output_dir.glob("*.png"):
                with Image.open(output) as image:
                    self.assertEqual(image.size, (480, 480))
                    self.assertEqual(image.mode, "RGB")
            self.assertEqual(
                {path.name for path in capture_dir.iterdir()},
                EXPECTED_BMPS,
            )

    def test_missing_expected_capture_is_rejected_with_exact_set_error(self):
        missing = "torget-vibepulse-burn-on-pace.bmp"
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir, output_dir = self.create_case(Path(tmp), missing)
            result = self.run_converter(capture_dir, output_dir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(f"missing: {missing}", result.stderr)
            self.assertIn("unexpected: (none)", result.stderr)
            self.assertEqual(
                {path.name for path in capture_dir.iterdir()},
                EXPECTED_BMPS - {missing},
            )

    def test_unexpected_capture_is_rejected_with_exact_set_error(self):
        unexpected = "torget-vibepulse-unexpected.bmp"
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir, output_dir = self.create_case(Path(tmp))
            write_bmp(capture_dir / unexpected)
            result = self.run_converter(capture_dir, output_dir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing: (none)", result.stderr)
            self.assertIn(f"unexpected: {unexpected}", result.stderr)
            self.assertTrue((capture_dir / unexpected).is_file())

    def test_symlink_capture_is_rejected_without_touching_victim(self):
        linked_name = "torget-vibepulse-claude-fable.bmp"
        with tempfile.TemporaryDirectory() as tmp:
            case_root = Path(tmp)
            capture_dir, output_dir = self.create_case(case_root, linked_name)
            victim = case_root / "victim.bmp"
            write_bmp(victim, color="red")
            victim_before = victim.read_bytes()
            linked_capture = capture_dir / linked_name
            linked_capture.symlink_to(victim)
            result = self.run_converter(capture_dir, output_dir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("not a regular non-symlink capture", result.stderr)
            self.assertEqual(victim.read_bytes(), victim_before)
            self.assertTrue(linked_capture.is_symlink())

    def test_corrupt_expected_capture_is_rejected(self):
        corrupt_name = "torget-vibepulse-codex-weekly.bmp"
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir, output_dir = self.create_case(Path(tmp))
            corrupt = capture_dir / corrupt_name
            corrupt.write_bytes(b"not a BMP")
            result = self.run_converter(capture_dir, output_dir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(corrupt), result.stderr)
            self.assertEqual(corrupt.read_bytes(), b"not a BMP")

    def test_wrong_size_expected_capture_is_rejected(self):
        wrong_name = "torget-vibepulse-burn-learning.bmp"
        with tempfile.TemporaryDirectory() as tmp:
            capture_dir, output_dir = self.create_case(Path(tmp))
            wrong = capture_dir / wrong_name
            write_bmp(wrong, size=(32, 32), color="green")
            result = self.run_converter(capture_dir, output_dir)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(str(wrong), result.stderr)
            self.assertIn("expected 480x480, got 32x32", result.stderr)
            self.assertTrue(wrong.is_file())


if __name__ == "__main__":
    program = unittest.main(verbosity=2, exit=False)
    if program.result.wasSuccessful():
        print("OK: exact-size VibePulse preview workflow")
    raise SystemExit(0 if program.result.wasSuccessful() else 1)
