#!/usr/bin/env python3
"""Regression guard for LVGL's small private heap on the ESP32 target."""

from pathlib import Path


root = Path(__file__).resolve().parents[1]
usage_screen = (root / "components/app_tokens/usage_screen.c").read_text(
    encoding="utf-8"
)

# Scaling a dynamic label makes LVGL render the complete label into an
# unsliceable ARGB transform layer.  The 436 px hero labels can then request
# more contiguous memory than Torget's 96 KiB LVGL pool has available and the
# draw dispatcher spins until the task watchdog fires.
assert "lv_obj_set_style_transform_scale_" not in usage_screen, (
    "VibePulse labels must use a native-size font, never an LVGL transform layer"
)
assert "SUMMARY_LABEL_SCALE_Y" not in usage_screen
assert "extern const lv_font_t plex_ui_21;" in usage_screen
assert "text(parent, &plex_ui_21, color)" in usage_screen

print("OK: VibePulse dynamic labels do not allocate transform layers")
