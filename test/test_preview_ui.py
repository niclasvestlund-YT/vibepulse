#!/usr/bin/env python3
"""Wiring guard for the exact-size VibePulse preview command."""

import os
import re
from pathlib import Path


root = Path(__file__).resolve().parents[1]
script_path = root / "tools/preview-ui.sh"
script = script_path.read_text(encoding="utf-8")

assert os.access(script_path, os.X_OK), "preview-ui.sh must be executable"
assert re.search(r"^set -eu$", script, re.MULTILINE)
assert 'PYTHON_BIN=${PYTHON_BIN:-python3}' in script
assert 'mktemp -d "${TMPDIR:-/tmp}/vibepulse-preview.XXXXXX"' in script
assert "cmake -S" in script
assert "cmake --build" in script
assert "--vibepulse-static-qa" in script
assert re.search(r'"\$PYTHON_BIN"[^\n]*<<[\'\"]?PY', script), (
    "conversion must run with the selected PYTHON_BIN interpreter"
)

assert "from PIL import Image" in script
assert "from tools.hardware_registry import load_registry" in script
assert 'registry.capabilities["display.amoled"]' in script
assert 'display["width"]' in script
assert 'display["height"]' in script
assert '["properties"]' not in script

assert 'glob("torget-vibepulse-*.bmp")' in script
assert "/tmp" in script
assert "st_mtime_ns" in script
assert "image.size != expected" in script
assert "no fresh VibePulse captures" in script
assert "rm -f /tmp/torget-vibepulse" not in script
assert "Preview directory:" in script
assert re.search(r"print\([^\n]*output", script), (
    "conversion must print every generated PNG path"
)

assert "usage:" in script and "vibepulse" in script
assert "exit 2" in script

print("OK: exact-size VibePulse preview workflow")
