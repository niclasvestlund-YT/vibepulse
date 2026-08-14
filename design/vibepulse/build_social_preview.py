#!/usr/bin/env python3
"""Render the GitHub social preview (1280x640) from real product frames.

"Ambient Signal" — see social-preview-philosophy.md. True black ground, two
meaning-bearing accents, depth by scale/blur/luminance rather than shadow.

The screens composited here are the SAME 480x480 rasters the panel renders,
so the banner can never drift from what the device actually shows. Run:

    . .venv/bin/activate
    python design/vibepulse/build_social_preview.py

Fonts: IBM Plex Sans TTFs. Point PLEX_DIR at a directory containing
IBMPlexSans-{Bold,SemiBold,Medium,Text}.ttf; the LVGL .c fonts in
platform/fonts/ are the same family but unusable here.
"""
import os
import pathlib
import sys

from PIL import Image, ImageDraw, ImageFilter, ImageFont

REPO = pathlib.Path(__file__).resolve().parents[2]
IMG = REPO / "docs" / "img"
OUT = IMG / "social-preview.png"

W, H = 1280, 640
MARGIN = 72

BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
CLAUDE = (217, 119, 87)      # #D97757
CODEX = (111, 120, 255)      # #6F78FF
GREY_TEXT = (154, 154, 164)
GREY_FAINT = (108, 108, 118)
BEZEL_EDGE = (46, 46, 52)
BEZEL_BODY = (10, 10, 11)

# Two candidates, kept side by side so the choice can be re-argued with
# renders rather than opinions. VARIANT=quota leads with the number instead
# of the alert; the headline has to follow the hero, never fight it.
VARIANTS = {
    "alert": {
        "hero": "vibepulse-needs-you.png",
        "head": [("When your agent", WHITE)],
        "accent_line": ("needs you", ","),
        "tail": [("the room knows.", WHITE)],
    },
    "quota": {
        "hero": "vibepulse-claude-week.png",
        "head": [("Know what's left", WHITE)],
        "accent_line": ("before", " you start."),
        "tail": [],
    },
}

PLEX_DIR = pathlib.Path(
    os.environ.get("PLEX_DIR", "/tmp/claude-0/-home-user-vibepulse/"
                   "8bda4e2e-7a10-574c-9de5-c5f07fabfcd0/scratchpad/plexttf"))


def font(weight, size):
    path = PLEX_DIR / f"IBMPlexSans-{weight}.ttf"
    if not path.exists():
        sys.exit(f"missing font: {path}\nSet PLEX_DIR to a directory with "
                 "IBM Plex Sans TTFs (OFL, github.com/IBM/plex).")
    return ImageFont.truetype(str(path), size)


def radial_glow(size, colour, peak_alpha, softness=1.0):
    """A wide, low-alpha halo. Felt, not seen — the philosophy's hard rule."""
    d = int(size)
    mask = Image.new("L", (d, d), 0)
    md = ImageDraw.Draw(mask)
    steps = 64
    for i in range(steps, 0, -1):
        t = i / steps
        r = (d / 2) * t
        # Smooth falloff; squared keeps the core tight and the edge long.
        a = int(peak_alpha * 255 * ((1 - t) ** 2))
        md.ellipse([d / 2 - r, d / 2 - r, d / 2 + r, d / 2 + r], fill=a)
    mask = mask.filter(ImageFilter.GaussianBlur(d * 0.06 * softness))
    layer = Image.new("RGBA", (d, d), colour + (0,))
    layer.putalpha(mask)
    return layer


def rounded_mask(size, radius, supersample=4):
    """Anti-aliased rounded-square mask, drawn large and downsampled."""
    s = size * supersample
    m = Image.new("L", (s, s), 0)
    ImageDraw.Draw(m).rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=radius * supersample, fill=255)
    return m.resize((size, size), Image.LANCZOS)


def device(screen_path, size, dim=1.0, blur=0.0):
    """One panel: rounded bezel, inset screen, hairline edge.

    Depth comes from size, luminance and focus only — never a drop shadow.
    """
    ss = 4
    s = size * ss
    radius = int(s * 0.155)
    card = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    cd = ImageDraw.Draw(card)
    cd.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius,
                         fill=BEZEL_BODY + (255,))
    # Hairline: the only thing separating object from ground on true black.
    cd.rounded_rectangle([0, 0, s - 1, s - 1], radius=radius,
                         outline=BEZEL_EDGE + (255,), width=max(2, int(ss * 1.5)))

    inset = int(s * 0.055)
    screen_px = s - inset * 2
    screen = Image.open(screen_path).convert("RGB").resize(
        (screen_px, screen_px), Image.LANCZOS)
    screen_r = int(radius * 0.62)
    card.paste(screen, (inset, inset), rounded_mask(screen_px, screen_r, 2))

    card = card.resize((size, size), Image.LANCZOS)
    if dim < 1.0:
        px = card.split()
        rgb = Image.merge("RGB", px[:3]).point(lambda v: int(v * dim))
        card = Image.merge("RGBA", (*rgb.split(), px[3]))
    if blur:
        card = card.filter(ImageFilter.GaussianBlur(blur))
    return card


def track(draw, xy, text, fnt, fill, spacing):
    """Letter-spaced text. Small type is tracked wide; large type is not."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + spacing
    return x


def track_width(draw, text, fnt, spacing):
    if not text:
        return 0
    return (sum(draw.textlength(c, font=fnt) for c in text)
            + spacing * (len(text) - 1))


def main():
    name = os.environ.get("VARIANT", "alert")
    if name not in VARIANTS:
        sys.exit(f"unknown VARIANT={name}; choose {sorted(VARIANTS)}")
    spec = VARIANTS[name]
    canvas = Image.new("RGB", (W, H), BLACK)

    # --- the object: two panels, one dominant ------------------------------
    # Right edge lands exactly on the margin; nothing bleeds off canvas.
    hero_size, rear_size = 356, 232
    hero_x, hero_y = 700, 142
    rear_x, rear_y = W - MARGIN - rear_size, 104

    def glow(centre, size, colour, alpha, soft=1.0):
        layer = radial_glow(size, colour, alpha, soft)
        canvas.paste(layer, (int(centre[0] - size / 2),
                             int(centre[1] - size / 2)), layer)

    # Each halo belongs to the panel it lights, and to that panel's provider:
    # warm under the Claude alert, cool under the Codex quota. Kept low enough
    # that the ground still reads as true black, never as a dark grey wash.
    glow((rear_x + rear_size / 2, rear_y + rear_size / 2), 560, CODEX, 0.055)
    glow((hero_x + hero_size / 2, hero_y + hero_size / 2), 660, CLAUDE, 0.075)
    # A whisper of indigo keeps the left column from dying into flat black.
    glow((110, 150), 560, CODEX, 0.032)

    d = ImageDraw.Draw(canvas)

    # The rear panel is two-thirds occluded, so it must read as texture
    # rather than text: the heatmap survives blur where a cropped numeral
    # would just look like damage.
    rear = device(IMG / "vibepulse-max-tracker.png", rear_size,
                  dim=0.55, blur=1.3)
    canvas.paste(rear, (rear_x, rear_y), rear)

    hero = device(IMG / spec["hero"], hero_size)
    canvas.paste(hero, (hero_x, hero_y), hero)

    # --- wordmark ----------------------------------------------------------
    x = MARGIN + 12
    y = 92
    mark_f = font("Bold", 33)
    # Pulse mark: a lit dot with its own halo — the product in one glyph.
    dot_r, cx = 7, x + 7
    cy = y + 22
    halo = radial_glow(72, CLAUDE, 0.40)
    canvas.paste(halo, (int(cx - 36), int(cy - 36)), halo)
    d.ellipse([cx - dot_r, cy - dot_r, cx + dot_r, cy + dot_r], fill=CLAUDE)
    track(d, (x + 30, y), "VIBEPULSE", mark_f, WHITE, 3.4)

    # --- headline: the shout ----------------------------------------------
    head_f = font("Bold", 61)
    lead = 70
    hy = 178
    row = 0
    for text, colour in spec["head"]:
        d.text((MARGIN, hy + lead * row), text, font=head_f, fill=colour)
        row += 1
    # The accent carries meaning, not decoration: it is the hero panel's own
    # colour, so the eye ties the phrase to the screen without being told.
    seg, tail = spec["accent_line"]
    d.text((MARGIN, hy + lead * row), seg, font=head_f, fill=CLAUDE)
    d.text((MARGIN + d.textlength(seg, font=head_f), hy + lead * row),
           tail, font=head_f, fill=WHITE)
    row += 1
    for text, colour in spec["tail"]:
        d.text((MARGIN, hy + lead * row), text, font=head_f, fill=colour)
        row += 1

    # --- subhead: the whisper ---------------------------------------------
    sub_f = font("Text", 22)
    sy = hy + lead * row + 26
    for i, line in enumerate((
            "Claude Code & Codex quota, live agent activity, and a",
            "full-screen alert — on a $30 panel on your shelf.")):
        d.text((MARGIN, sy + i * 33), line, font=sub_f, fill=GREY_TEXT)

    # --- badges ------------------------------------------------------------
    badge_f = font("SemiBold", 15)
    by = sy + 33 * 2 + 34
    bx = MARGIN
    for i, label in enumerate(
            ("OPEN SOURCE", "ESP32-S3", "NOTHING LEAVES YOUR LAN")):
        if i:
            bx += 13
            d.text((bx, by), "·", font=badge_f, fill=(74, 74, 82))
            bx += d.textlength("·", font=badge_f) + 13
        bx = track(d, (bx, by), label, badge_f, GREY_FAINT, 1.5)

    assert bx < W - MARGIN, f"badge row overflows: {bx}"
    assert hero_x + hero_size < W - 40, "hero panel too close to the edge"
    assert by + 20 < H - MARGIN, "badge row breaks the bottom margin"
    out = pathlib.Path(os.environ.get("OUT", OUT))
    canvas.save(out, "PNG", optimize=True)
    print(f"wrote {out} ({out.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
