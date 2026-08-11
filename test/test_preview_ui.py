#!/usr/bin/env python3
"""Wiring guard for the exact-size VibePulse preview command."""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from PIL import Image


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
assert re.search(
    r'^"\$PYTHON_BIN" - "\$repo" "\$output_dir" "\$manifest" '
    r'<<\'PREVIEW_CONVERTER_PY\'$',
    script,
    re.MULTILINE,
), (
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
assert "captures-before.json" in script
assert "stat.st_ino" in script
assert "stat.st_size" in script
assert "before.get(str(capture)) != fingerprint(capture)" in script
assert re.search(
    r'"\$PYTHON_BIN" - "\$manifest" <<\'PREVIEW_MANIFEST_PY\'',
    script,
), "the selected interpreter must snapshot the pre-run manifest"
assert script.index("PREVIEW_MANIFEST_PY") < script.index(
    '"$repo/sim/build/torget-sim" --vibepulse-static-qa'
)
assert "image.size != expected" in script
assert "no fresh VibePulse captures" in script
assert "rm -f /tmp/torget-vibepulse" not in script
assert "Preview directory:" in script
assert re.search(r"print\([^\n]*output", script), (
    "conversion must print every generated PNG path"
)

assert "usage:" in script and "vibepulse" in script
assert "exit 2" in script

heredocs = list(re.finditer(
    r'^"\$PYTHON_BIN"[^\n]*<<\'(?P<delimiter>[A-Z_]+|PY)\'\n'
    r'(?P<source>.*?)\n(?P=delimiter)$',
    script,
    re.MULTILINE | re.DOTALL,
))
assert heredocs, "preview-ui.sh must contain an extractable converter heredoc"
converter = heredocs[-1]

fixture_id = uuid.uuid4().hex
old_source = Path(f"/tmp/torget-vibepulse-test-old-{fixture_id}.bmp")
fresh_source = Path(f"/tmp/torget-vibepulse-test-fresh-{fixture_id}.bmp")


def fingerprint(path):
    stat = path.stat()
    return {
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


try:
    Image.new("RGB", (480, 480), "navy").save(old_source)
    future_ns = time.time_ns() + 3_600_000_000_000
    os.utime(old_source, ns=(future_ns, future_ns))

    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        manifest = output_dir / "captures-before.json"
        before = {
            str(path): fingerprint(path)
            for path in Path("/tmp").glob("torget-vibepulse-*.bmp")
        }
        manifest.write_text(json.dumps(before), encoding="utf-8")

        Image.new("RGB", (480, 480), "green").save(fresh_source)
        result = subprocess.run(
            [
                sys.executable,
                "-",
                str(root),
                str(output_dir),
                str(manifest),
            ],
            input=converter.group("source"),
            text=True,
            capture_output=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr

        old_output = output_dir / f"{old_source.stem.removeprefix('torget-')}.png"
        fresh_output = (
            output_dir / f"{fresh_source.stem.removeprefix('torget-')}.png"
        )
        assert fresh_output.is_file(), "new capture was not exported"
        assert not old_output.exists(), (
            "unchanged future-dated pre-existing capture was exported"
        )
        assert old_source.is_file() and fresh_source.is_file(), (
            "preview conversion must preserve source BMPs"
        )
finally:
    old_source.unlink(missing_ok=True)
    fresh_source.unlink(missing_ok=True)

assert converter.group("delimiter") == "PREVIEW_CONVERTER_PY", (
    "converter heredoc needs a stable extraction delimiter"
)

print("OK: exact-size VibePulse preview workflow")
