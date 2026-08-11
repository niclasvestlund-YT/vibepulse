#!/usr/bin/env python3
"""Validate VibePulse Studio tokens and export deterministic C constants."""

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.hardware_registry import load_registry


class DesignError(ValueError):
    """Raised when a Studio design violates the versioned contract."""


LOCKED_COLORS = {
    "background": "#000000",
    "claude": "#D97757",
    "codex": "#6F78FF",
}

PALETTE_NAMES = (
    "background",
    "text",
    "muted",
    "track",
    "hairline",
    "claude",
    "codex",
)

TOKEN_NAMES = {
    "safeX": "VP_SAFE_X",
    "contentWidth": "VP_CONTENT_W",
    "providerY": "VP_PROVIDER_Y",
    "quotaY": "VP_QUOTA_Y",
    "percentY": "VP_PERCENT_Y",
    "percentFontPx": "VP_PERCENT_FONT_PX",
    "barY": "VP_BAR_Y",
    "barHeight": "VP_BAR_H",
    "resetY": "VP_RESET_Y",
    "statusY": "VP_STATUS_Y",
    "statusHeight": "VP_STATUS_H",
}

DOCUMENT_KEYS = {
    "schemaVersion", "deviceCapability", "palette", "hero", "fixtures",
}
FIXTURE_KEYS = {"claude", "codex"}
FIXTURE_FIELDS = {
    "provider", "model", "effort", "quota", "percent", "today", "reset",
}
RGB_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")


def _pixel_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _display_dimensions(display):
    if not isinstance(display, dict) or set(display) != {"width", "height"}:
        raise DesignError("display must contain width and height")
    width = display["width"]
    height = display["height"]
    if (not _pixel_integer(width) or not _pixel_integer(height)
            or width <= 0 or height <= 0):
        raise DesignError("display dimensions must be positive integers")
    return width, height


def load_display(spec_dir):
    """Read immutable panel dimensions from the hardware registry."""
    registry = load_registry(spec_dir)
    capability = registry.capabilities.get("display.amoled")
    if not isinstance(capability, dict):
        raise DesignError("display.amoled is missing from the hardware registry")
    display = {
        "width": capability.get("width"),
        "height": capability.get("height"),
    }
    _display_dimensions(display)
    return display


def _validate_palette(palette):
    if not isinstance(palette, dict) or set(palette) != set(PALETTE_NAMES):
        raise DesignError("palette keys do not match the v1 contract")
    for name in PALETTE_NAMES:
        color = palette[name]
        if not isinstance(color, str) or RGB_COLOR.fullmatch(color) is None:
            raise DesignError(f"palette {name} must be a #RRGGBB color")
    for name, expected in LOCKED_COLORS.items():
        if palette[name] != expected:
            label = name.title()
            raise DesignError(f"{label} color must remain {expected}")


def _validate_hero(hero, width, height):
    if not isinstance(hero, dict) or set(hero) != set(TOKEN_NAMES):
        raise DesignError("hero keys do not match the v1 contract")
    if not all(_pixel_integer(value) for value in hero.values()):
        raise DesignError("hero values must be integer panel pixels")

    if not 16 <= hero["safeX"] <= 40:
        raise DesignError("safeX outside reviewed range")
    if hero["contentWidth"] != width - 2 * hero["safeX"]:
        raise DesignError("contentWidth must match safeX")
    if not 12 <= hero["barHeight"] <= 24:
        raise DesignError("barHeight outside readable range")

    positions = (
        "providerY", "quotaY", "percentY", "barY", "resetY", "statusY",
    )
    if any(not 0 <= hero[name] < height for name in positions):
        raise DesignError("hero geometry must remain on screen")
    if (not 1 <= hero["contentWidth"] <= width
            or not 1 <= hero["percentFontPx"] <= height
            or not 1 <= hero["statusHeight"] <= height
            or hero["barY"] + hero["barHeight"] > height
            or hero["statusY"] + hero["statusHeight"] > height):
        raise DesignError("hero geometry must remain on screen")
    if tuple(hero[name] for name in positions) != tuple(
            sorted(hero[name] for name in positions)):
        raise DesignError("hero geometry must follow visual reading order")


def _validate_fixtures(fixtures):
    if not isinstance(fixtures, dict) or set(fixtures) != FIXTURE_KEYS:
        raise DesignError("fixture keys do not match the v1 contract")
    expected_providers = {"claude": "CLAUDE", "codex": "CODEX"}
    for name in sorted(FIXTURE_KEYS):
        fixture = fixtures[name]
        if not isinstance(fixture, dict) or set(fixture) != FIXTURE_FIELDS:
            raise DesignError(f"{name} fixture fields do not match v1")
        for field in ("provider", "model", "effort", "quota", "reset"):
            if not isinstance(fixture[field], str) or not fixture[field].strip():
                raise DesignError(f"{name} fixture {field} must be text")
        if fixture["provider"] != expected_providers[name]:
            raise DesignError(f"{name} fixture provider is fixed")
        for field in ("percent", "today"):
            value = fixture[field]
            if not _pixel_integer(value) or not 0 <= value <= 100:
                raise DesignError(
                    f"{name} fixture {field} must be an integer percentage",
                )


def validate_design(value, display):
    """Return *value* when it satisfies the complete schema-v1 contract."""
    width, height = _display_dimensions(display)
    if not isinstance(value, dict):
        raise DesignError("document keys do not match the v1 contract")
    if "canvas" in value or value.get("deviceCapability") != "display.amoled":
        raise DesignError("device facts belong only in the hardware registry")
    if set(value) != DOCUMENT_KEYS:
        raise DesignError("document keys do not match the v1 contract")
    if (not _pixel_integer(value.get("schemaVersion"))
            or value["schemaVersion"] != 1):
        raise DesignError("schemaVersion must be 1")
    _validate_palette(value.get("palette"))
    _validate_hero(value.get("hero"), width, height)
    _validate_fixtures(value.get("fixtures"))
    return value


def load_design(path, display):
    """Load and validate a UTF-8 JSON design document."""
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise DesignError(f"cannot load design {path}: {error}") from error
    return validate_design(value, display)


def _atomic_write_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as file:
            os.fchmod(file.fileno(), 0o644)
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def save_design(path, value, display):
    """Validate and atomically save canonical UTF-8 JSON."""
    validate_design(value, display)
    content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
    _atomic_write_text(path, content)


def generate_header(value, display):
    """Generate deterministic preprocessor constants for LVGL code."""
    width, height = _display_dimensions(display)
    validate_design(value, display)
    lines = [
        "/* Generated by tools/vibepulse_studio/design.py; do not hand-edit. */",
        "#ifndef VIBEPULSE_LAYOUT_GENERATED_H",
        "#define VIBEPULSE_LAYOUT_GENERATED_H",
        "",
        f"#define VP_SCREEN_W {width}",
        f"#define VP_SCREEN_H {height}",
    ]
    for source, target in TOKEN_NAMES.items():
        lines.append(f"#define {target} {value['hero'][source]}")
    lines.append("")
    for name in PALETTE_NAMES:
        color = value["palette"][name]
        lines.append(f"#define VP_COLOR_{name.upper()} 0x{color[1:].upper()}")
    lines.extend(["", "#endif", ""])
    return "\n".join(lines)


def save_header(path, content):
    """Atomically save a generated header."""
    if not isinstance(content, str):
        raise TypeError("header content must be text")
    _atomic_write_text(path, content)


def _paths(arguments):
    repo = Path(arguments.repo).resolve()
    return (
        repo,
        repo / arguments.spec,
        repo / arguments.design,
        repo / arguments.header,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--write", action="store_true")
    parser.add_argument("--repo", default=str(REPOSITORY_ROOT))
    parser.add_argument("--spec", default="spec")
    parser.add_argument(
        "--design", default="design/vibepulse/studio-design.json",
    )
    parser.add_argument(
        "--header",
        default="components/app_tokens/vibepulse_layout.generated.h",
    )
    arguments = parser.parse_args(argv)
    _, spec_path, design_path, header_path = _paths(arguments)

    try:
        display = load_display(spec_path)
        design = load_design(design_path, display)
        header = generate_header(design, display)
        if arguments.write:
            save_design(design_path, design, display)
            save_header(header_path, header)
            return 0
        if not header_path.is_file() or header_path.read_text(
                encoding="utf-8") != header:
            print(
                f"generated header is stale: {header_path}; run --write",
                file=sys.stderr,
            )
            return 1
        return 0
    except (DesignError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
