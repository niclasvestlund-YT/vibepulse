"""Native 800x480 one-bit design study. No device or provider API access.

Shared native renderer. Bundled IBM Plex fonts are licensed under OFL.
The PBM files are preview rasters, not a Waveshare wire format or firmware.
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent
FONT = ROOT / "fonts"
W, H = 800, 480


def font(size, serif=False, bold=False):
    name = (
        "IBMPlexSerif-Regular.ttf"
        if serif
        else ("IBMPlexSans-SemiBold.ttf" if bold else "IBMPlexSans-Regular.ttf")
    )
    return ImageFont.truetype(str(FONT / name), size)


def base(label):
    im = Image.new("1", (W, H), 1)
    return im, ImageDraw.Draw(im)


def text(d, x, y, value, size=24, bold=False, serif=False, fill=0):
    f = font(size, serif, bold)
    box = d.textbbox((x, y), value, font=f)
    assert box[0] >= 0 and box[1] >= 0 and box[2] <= W and box[3] <= H, (value, box)
    d.text((x, y), value, font=f, fill=fill)


def line(d, y):
    d.line((32, y, 768, y), fill=0, width=2)


def header(d, label):
    text(d, 32, 22, "VibePulse", 27, bold=True)
    text(d, 488, 28, label, 18)
    line(d, 66)


def footer(d, value="DEMO DATA  /  TURN TO BROWSE  /  PRESS TO OPEN"):
    line(d, 427)
    text(d, 32, 445, value, 17)
