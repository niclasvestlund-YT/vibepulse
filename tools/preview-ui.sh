#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ "$1" != "vibepulse" ]; then
  printf 'usage: %s vibepulse\n' "$0" >&2
  exit 2
fi

repo=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

if ! "$PYTHON_BIN" - "$repo" <<'PREVIEW_PREFLIGHT_PY'
import sys

sys.path.insert(0, sys.argv[1])

from PIL import Image  # noqa: F401
from tools.hardware_registry import load_registry  # noqa: F401
PREVIEW_PREFLIGHT_PY
then
  printf 'ERROR: selected Python cannot import Pillow and the hardware registry: %s\n' \
    "$PYTHON_BIN" >&2
  exit 1
fi

output_dir=$(mktemp -d "${TMPDIR:-/tmp}/vibepulse-preview.XXXXXX")
manifest=$output_dir/captures-before.json

"$PYTHON_BIN" - "$manifest" <<'PREVIEW_MANIFEST_PY'
import json
import sys
from pathlib import Path


def fingerprint(path):
    stat = path.stat()
    return {
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


manifest = Path(sys.argv[1])
before = {
    str(path): fingerprint(path)
    for path in Path("/tmp").glob("torget-vibepulse-*.bmp")
}
manifest.write_text(json.dumps(before, sort_keys=True), encoding="utf-8")
PREVIEW_MANIFEST_PY

cmake -S "$repo/sim" -B "$repo/sim/build" -G Ninja
cmake --build "$repo/sim/build"
"$repo/sim/build/torget-sim" --vibepulse-static-qa

"$PYTHON_BIN" - "$repo" "$output_dir" "$manifest" <<'PREVIEW_CONVERTER_PY'
import json
import sys
from pathlib import Path

repo = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
manifest = Path(sys.argv[3])
sys.path.insert(0, str(repo))

from PIL import Image
from tools.hardware_registry import load_registry

registry = load_registry(repo / "spec")
display = registry.capabilities["display.amoled"]
expected = (display["width"], display["height"])
before = json.loads(manifest.read_text(encoding="utf-8"))


def fingerprint(path):
    stat = path.stat()
    return {
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


captures = sorted(
    capture
    for capture in Path("/tmp").glob("torget-vibepulse-*.bmp")
    if before.get(str(capture)) != fingerprint(capture)
)

if not captures:
    raise SystemExit("no fresh VibePulse captures")

for capture in captures:
    with Image.open(capture) as image:
        if image.size != expected:
            raise SystemExit(
                f"{capture}: expected {expected[0]}x{expected[1]}, "
                f"got {image.size[0]}x{image.size[1]}"
            )
        output = output_dir / f"{capture.stem.removeprefix('torget-')}.png"
        image.convert("RGB").save(output)
    print(output)

print(f"Preview directory: {output_dir}")
PREVIEW_CONVERTER_PY
