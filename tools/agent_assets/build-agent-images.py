#!/usr/bin/env python3
"""Build flash-only LVGL images for VibePulse's Claude and Codex pets."""

from __future__ import annotations

from collections import deque
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "components/app_tokens/assets/source"
OUT_C = ROOT / "components/app_tokens/agent_assets.c"
OUT_H = ROOT / "components/app_tokens/agent_assets.h"
CANVAS = 180


def fit_canvas(image: Image.Image, crop: tuple[int, int, int, int],
               canvas_size: int) -> Image.Image:
    cropped = image.crop(crop)
    scale = min(canvas_size / cropped.width, canvas_size / cropped.height)
    size = (max(1, round(cropped.width * scale)),
            max(1, round(cropped.height * scale)))
    resized = cropped.resize(size, Image.Resampling.NEAREST)
    canvas = Image.new(image.mode, (canvas_size, canvas_size), 0)
    canvas.paste(resized, ((canvas_size - size[0]) // 2,
                           (canvas_size - size[1]) // 2))
    return canvas


def components(mask: Image.Image, bounds: tuple[int, int, int, int]):
    pixels = mask.load()
    x0, y0, x1, y1 = bounds
    remaining = {(x, y) for y in range(y0, y1) for x in range(x0, x1)
                 if pixels[x, y]}
    found = []
    while remaining:
        start = remaining.pop()
        queue = deque([start])
        part = [start]
        while queue:
            x, y = queue.popleft()
            for point in ((x - 1, y), (x + 1, y),
                          (x, y - 1), (x, y + 1)):
                if point in remaining:
                    remaining.remove(point)
                    queue.append(point)
                    part.append(point)
        found.append(part)
    return found


def build_claude(canvas_size: int = CANVAS) -> bytes:
    source = Image.open(SOURCE / "claude-pet-white.png").convert("RGBA")
    alpha = source.getchannel("A")
    crop = alpha.getbbox()
    if crop is None:
        raise ValueError("Claude source has no visible pixels")
    return bytes(fit_canvas(alpha, crop, canvas_size).getdata())


def build_codex(canvas_size: int = CANVAS) -> tuple[bytes, bytes, bytes]:
    source = Image.open(SOURCE / "codex-icon.png").convert("RGBA")
    src = source.load()
    colored = Image.new("L", source.size, 0)
    colored_px = colored.load()
    for y in range(source.height):
        for x in range(source.width):
            r, g, b, a = src[x, y]
            if a and max(r, g, b) - min(r, g, b) > 8:
                colored_px[x, y] = 255

    crop = colored.getbbox()
    if crop is None:
        raise ValueError("Codex source has no colored cloud")

    # The white app plate touches the crop edges; the two enclosed white
    # components are the real terminal glyphs. This derives > and _ from the
    # supplied artwork instead of redrawing approximations.
    white = Image.new("L", source.size, 0)
    white_px = white.load()
    x0, y0, x1, y1 = crop
    for y in range(y0, y1):
        for x in range(x0, x1):
            r, g, b, a = src[x, y]
            if a > 200 and min(r, g, b) >= 235:
                white_px[x, y] = 255

    glyph_parts = []
    for part in components(white, crop):
        if len(part) < 100:
            continue
        if any(x in (x0, x1 - 1) or y in (y0, y1 - 1) for x, y in part):
            continue
        glyph_parts.append(part)
    if len(glyph_parts) != 2:
        raise ValueError(f"expected two Codex glyphs, found {len(glyph_parts)}")
    glyph_parts.sort(key=lambda part: max(y for _, y in part) -
                     min(y for _, y in part), reverse=True)

    glyph_masks = []
    for part in glyph_parts:
        mask = Image.new("L", source.size, 0)
        mask_px = mask.load()
        for x, y in part:
            mask_px[x, y] = src[x, y][3]
        glyph_masks.append(fit_canvas(mask, crop, canvas_size))

    # Reconstruct the cloud underneath the terminal marks from each row's
    # real cloud color. Hiding a glyph later therefore reveals cloud, not a
    # white hole or an invented patch.
    cloud = Image.new("RGBA", source.size, (0, 0, 0, 0))
    cloud_px = cloud.load()
    row_color = {}
    for y in range(y0, y1):
        colors = [src[x, y][:3] for x in range(x0, x1) if colored_px[x, y]]
        if colors:
            colors.sort()
            row_color[y] = colors[len(colors) // 2]
    glyph_points = {point for part in glyph_parts for point in part}
    for y in range(y0, y1):
        for x in range(x0, x1):
            if colored_px[x, y]:
                cloud_px[x, y] = src[x, y]
            elif (x, y) in glyph_points:
                cloud_px[x, y] = (*row_color[y], 255)

    fitted = fit_canvas(cloud, crop, canvas_size)
    visible = [(r, g, b) for r, g, b, a in fitted.getdata() if a]
    strip = Image.new("RGB", (len(visible), 1))
    strip.putdata(visible)
    quantized = strip.quantize(colors=15, method=Image.Quantize.MEDIANCUT,
                               dither=Image.Dither.NONE)
    palette_raw = quantized.getpalette()
    used = sorted(set(quantized.getdata()))
    colors = [(palette_raw[i * 3], palette_raw[i * 3 + 1],
               palette_raw[i * 3 + 2]) for i in used]
    remap = {old: new + 1 for new, old in enumerate(used)}
    color_to_index = {}
    for color, old_index in zip(visible, quantized.getdata()):
        color_to_index[color] = remap[old_index]

    palette = bytearray(16 * 4)
    for index, (r, g, b) in enumerate(colors, start=1):
        palette[index * 4:index * 4 + 4] = bytes((b, g, r, 255))

    indices = []
    for r, g, b, a in fitted.getdata():
        indices.append(color_to_index[(r, g, b)] if a else 0)
    packed = bytearray()
    for offset in range(0, len(indices), 2):
        packed.append((indices[offset] << 4) | indices[offset + 1])

    return (bytes(palette + packed), bytes(glyph_masks[0].getdata()),
            bytes(glyph_masks[1].getdata()))


def c_array(name: str, data: bytes) -> str:
    rows = []
    for offset in range(0, len(data), 16):
        chunk = data[offset:offset + 16]
        rows.append("  " + ", ".join(f"0x{byte:02x}" for byte in chunk) + ",")
    return f"static const uint8_t {name}[] = {{\n" + "\n".join(rows) + "\n};\n"


def descriptor(name: str, data_name: str, color_format: str, stride: int,
               size: int, canvas: int = CANVAS) -> str:
    return f"""const lv_image_dsc_t {name} = {{
  .header = {{
    .magic = LV_IMAGE_HEADER_MAGIC,
    .cf = {color_format},
    .flags = 0,
    .w = {canvas},
    .h = {canvas},
    .stride = {stride},
  }},
  .data_size = {size},
  .data = {data_name},
}};
"""


def main() -> None:
    claude = build_claude()
    codex, chevron, underscore = build_codex()
    claude_32 = build_claude(32)
    codex_32, chevron_32, underscore_32 = build_codex(32)
    OUT_H.write_text("""#ifndef AGENT_ASSETS_H
#define AGENT_ASSETS_H

#include "lvgl.h"

extern const lv_image_dsc_t tk_img_claude;
extern const lv_image_dsc_t tk_img_codex_cloud;
extern const lv_image_dsc_t tk_img_codex_chevron;
extern const lv_image_dsc_t tk_img_codex_underscore;
extern const lv_image_dsc_t tk_img_claude_32;
extern const lv_image_dsc_t tk_img_codex_cloud_32;
extern const lv_image_dsc_t tk_img_codex_chevron_32;
extern const lv_image_dsc_t tk_img_codex_underscore_32;

#endif
""", encoding="utf-8")
    source = "#include \"agent_assets.h\"\n\n"
    source += c_array("tk_img_claude_data", claude)
    source += c_array("tk_img_codex_cloud_data", codex)
    source += c_array("tk_img_codex_chevron_data", chevron)
    source += c_array("tk_img_codex_underscore_data", underscore)
    source += c_array("tk_img_claude_32_data", claude_32)
    source += c_array("tk_img_codex_cloud_32_data", codex_32)
    source += c_array("tk_img_codex_chevron_32_data", chevron_32)
    source += c_array("tk_img_codex_underscore_32_data", underscore_32)
    source += "\n"
    source += descriptor("tk_img_claude", "tk_img_claude_data",
                         "LV_COLOR_FORMAT_A8", CANVAS, len(claude))
    source += descriptor("tk_img_codex_cloud", "tk_img_codex_cloud_data",
                         "LV_COLOR_FORMAT_I4", CANVAS // 2, len(codex))
    source += descriptor("tk_img_codex_chevron", "tk_img_codex_chevron_data",
                         "LV_COLOR_FORMAT_A8", CANVAS, len(chevron))
    source += descriptor("tk_img_codex_underscore", "tk_img_codex_underscore_data",
                         "LV_COLOR_FORMAT_A8", CANVAS, len(underscore))
    source += descriptor("tk_img_claude_32", "tk_img_claude_32_data",
                         "LV_COLOR_FORMAT_A8", 32, len(claude_32), 32)
    source += descriptor("tk_img_codex_cloud_32", "tk_img_codex_cloud_32_data",
                         "LV_COLOR_FORMAT_I4", 16, len(codex_32), 32)
    source += descriptor("tk_img_codex_chevron_32",
                         "tk_img_codex_chevron_32_data",
                         "LV_COLOR_FORMAT_A8", 32, len(chevron_32), 32)
    source += descriptor("tk_img_codex_underscore_32",
                         "tk_img_codex_underscore_32_data",
                         "LV_COLOR_FORMAT_A8", 32, len(underscore_32), 32)
    OUT_C.write_text(source, encoding="utf-8")


if __name__ == "__main__":
    main()
