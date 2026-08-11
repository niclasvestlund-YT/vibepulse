#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ "$1" != "vibepulse" ]; then
  printf 'usage: %s vibepulse\n' "$0" >&2
  exit 2
fi

repo=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}

if ! "$PYTHON_BIN" - "$repo" <<'PY'
import sys

sys.path.insert(0, sys.argv[1])

from PIL import Image  # noqa: F401
from tools.hardware_registry import load_registry  # noqa: F401
PY
then
  printf 'ERROR: selected Python cannot import Pillow and the hardware registry: %s\n' \
    "$PYTHON_BIN" >&2
  exit 1
fi

output_dir=$(mktemp -d "${TMPDIR:-/tmp}/vibepulse-preview.XXXXXX")
run_marker=$output_dir/.run-start
touch "$run_marker"

cmake -S "$repo/sim" -B "$repo/sim/build" -G Ninja
cmake --build "$repo/sim/build"
"$repo/sim/build/torget-sim" --vibepulse-static-qa

"$PYTHON_BIN" - "$repo" "$output_dir" "$run_marker" <<'PY'
import sys
from pathlib import Path

repo = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
run_marker = Path(sys.argv[3])
sys.path.insert(0, str(repo))

from PIL import Image
from tools.hardware_registry import load_registry

registry = load_registry(repo / "spec")
display = registry.capabilities["display.amoled"]
expected = (display["width"], display["height"])
started_at_ns = run_marker.stat().st_mtime_ns
captures = sorted(
    capture
    for capture in Path("/tmp").glob("torget-vibepulse-*.bmp")
    if capture.stat().st_mtime_ns >= started_at_ns
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
PY
