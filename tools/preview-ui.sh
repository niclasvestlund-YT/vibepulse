#!/bin/sh
set -eu

if [ "$#" -ne 1 ] || [ "$1" != "vibepulse" ]; then
  printf 'usage: %s vibepulse\n' "$0" >&2
  exit 2
fi

repo=$(CDPATH= cd -P "$(dirname "$0")/.." && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
output_dir=

cleanup_preview() {
  cleanup_status=$?
  trap - 0 HUP INT TERM
  if [ "$cleanup_status" -ne 0 ] && [ -n "${output_dir:-}" ]; then
    if ! "$PYTHON_BIN" - "$output_dir" "${TMPDIR:-/tmp}" <<'PREVIEW_CLEANUP_PY'
import shutil
import sys
from pathlib import Path

candidate = Path(sys.argv[1])
expected_parent = Path(sys.argv[2]).resolve()
safe = (
    candidate.name.startswith("vibepulse-preview.")
    and candidate.parent.resolve() == expected_parent
    and not candidate.is_symlink()
)
if not safe:
    raise SystemExit(f"refusing unsafe preview cleanup: {candidate}")
if candidate.exists():
    if not candidate.is_dir():
        raise SystemExit(f"refusing non-directory preview cleanup: {candidate}")
    shutil.rmtree(candidate)
PREVIEW_CLEANUP_PY
    then
      printf 'WARNING: failed to clean private preview directory: %s\n' \
        "$output_dir" >&2
    fi
  fi
  exit "$cleanup_status"
}

trap cleanup_preview 0
trap 'exit 1' HUP INT TERM

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
chmod 0700 "$output_dir"
capture_dir="$output_dir/captures"
mkdir -m 0700 "$capture_dir"

cmake -S "$repo/sim" -B "$repo/sim/build" -G Ninja
cmake --build "$repo/sim/build"
TORGET_CAPTURE_DIR="$capture_dir" "$repo/sim/build/torget-sim" --vibepulse-static-qa

"$PYTHON_BIN" - "$repo" "$output_dir" "$capture_dir" <<'PREVIEW_CONVERTER_PY'
import os
import stat
import sys
from pathlib import Path

repo = Path(sys.argv[1])
output_dir = Path(sys.argv[2])
capture_dir = Path(sys.argv[3])
sys.path.insert(0, str(repo))

from PIL import Image
from tools.hardware_registry import load_registry

registry = load_registry(repo / "spec")
display = registry.capabilities["display.amoled"]
expected = (display["width"], display["height"])
expected_names = {
    "torget-vibepulse-claude-fable.bmp",
    "torget-vibepulse-claude-all.bmp",
    "torget-vibepulse-codex-weekly.bmp",
    "torget-vibepulse-codex-weekly-live-46.bmp",
    "torget-vibepulse-claude-fable-cached-stale.bmp",
    "torget-vibepulse-codex-weekly-cached-stale.bmp",
    "torget-vibepulse-claude-fable-no-data.bmp",
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
    "torget-vibepulse-claude-lease-expired.bmp",
    "torget-vibepulse-claude-multi-chat.bmp",
    "torget-vibepulse-claude-idle.bmp",
    "torget-vibepulse-codex-single-working.bmp",
    "torget-vibepulse-codex-multi-chat.bmp",
    "torget-vibepulse-codex-idle.bmp",
    "torget-vibepulse-codex-stale.bmp",
    "torget-vibepulse-claude-today-missing.bmp",
    "torget-vibepulse-claude-today-contradictory.bmp",
    "torget-vibepulse-claude-zero-total.bmp",
    "torget-vibepulse-codex-full-total.bmp",
    "torget-vibepulse-claude-needs-you.bmp",
    "torget-vibepulse-codex-needs-you.bmp",
    "torget-vibepulse-claude-error.bmp",
    "torget-vibepulse-codex-error.bmp",
    "torget-vibepulse-two-waiting-queued.bmp",
    "torget-vibepulse-claude-done-static.bmp",
    "torget-vibepulse-codex-done-static.bmp",
    "torget-vibepulse-claude-swedish-project.bmp",
}
actual_names = {path.name for path in capture_dir.iterdir()}
missing = sorted(expected_names - actual_names)
unexpected = sorted(actual_names - expected_names)


def listed(names):
    return ", ".join(names) if names else "(none)"


if missing or unexpected:
    raise SystemExit(
        f"capture set mismatch; missing: {listed(missing)}; "
        f"unexpected: {listed(unexpected)}"
    )

captures = []
for name in sorted(expected_names):
    capture = capture_dir / name
    mode = capture.lstat().st_mode
    if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
        raise SystemExit(f"{capture}: not a regular non-symlink capture")
    captures.append(capture)

for capture in captures:
    try:
        descriptor = os.open(capture, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            with os.fdopen(descriptor, "rb") as source:
                descriptor = -1
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise SystemExit(
                        f"{capture}: not a regular non-symlink capture"
                    )
                with Image.open(source) as image:
                    if image.size != expected:
                        raise SystemExit(
                            f"{capture}: expected {expected[0]}x{expected[1]}, "
                            f"got {image.size[0]}x{image.size[1]}"
                        )
                    output = (
                        output_dir
                        / f"{capture.stem.removeprefix('torget-')}.png"
                    )
                    image.convert("RGB").save(output)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
    except OSError as error:
        raise SystemExit(f"{capture}: invalid capture: {error}") from error
    print(output)

print(f"Preview directory: {output_dir}")
PREVIEW_CONVERTER_PY
