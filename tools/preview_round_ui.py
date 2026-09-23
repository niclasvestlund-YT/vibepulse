#!/usr/bin/env python3
"""Native round quota/diagnostic preview, not a complete board-support claim."""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
NAMES = (
    "codex-live", "codex-stale", "codex-to-empty", "codex-long-reset", "codex-today-missing",
    "codex-today-invalid", "codex-full", "codex-zero", "codex-missing",
    "claude-live", "claude-wide-label", "claude-reset-missing", "diagnostic",
)


def run(args, **kwargs):
    result = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, **kwargs)
    if result.returncode:
        raise RuntimeError(f"{args[0]} exited {result.returncode}\n"
                           + result.stdout[-4000:] + result.stderr[-4000:])


def main():
    from hardware_registry import load_registry

    registry = load_registry(ROOT / "spec/boards/waveshare_175")
    display = registry.capabilities["display.amoled"]
    build = ROOT / "sim/build-175"
    run(["cmake", "-S", "sim", "-B", str(build), "-G", "Ninja",
         "-DTORGET_BOARD=waveshare_175",
         f"-DTORGET_SOLELKOLLEN_DIR={ROOT}/no-companion"])
    run(["cmake", "--build", str(build), "--parallel", "2"])
    output = Path(tempfile.mkdtemp(prefix="vibepulse-preview."))
    captures = output / "captures"
    captures.mkdir(mode=0o700)
    try:
        run([str(build / "torget-sim"), "--vibepulse-round-qa"],
            env={**os.environ, "TORGET_CAPTURE_DIR": str(captures),
                 "TORGET_LABS_MASK": "0"})
        expected = {f"torget-round-{name}.bmp" for name in NAMES}
        actual = {p.name for p in captures.iterdir()}
        if actual != expected:
            raise RuntimeError(f"Round capture set mismatch: {actual ^ expected}")
        for name in NAMES:
            with Image.open(captures / f"torget-round-{name}.bmp") as image:
                if image.size != (display["width"], display["height"]):
                    raise RuntimeError(f"Wrong native raster: {image.size}")
                image.convert("RGB").save(output / f"round-{name}.png")
        from validate_round_preview import validate
        validate(output)
    except BaseException:
        shutil.rmtree(output)
        raise
    print("Preview directory: " + str(output))


if __name__ == "__main__":
    main()
