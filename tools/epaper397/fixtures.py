"""Deterministic native publication fixtures; never a live provider reading."""

import datetime as dt
from pathlib import Path

from . import render as ui
from .usb_host import PAGES, render_page

NOW = 1793196000


def frames():
    data = {
        "tokens": {
            "v": 2,
            "codexWeekPct": 38,
            "codexWeekObservedAt": NOW,
            "codexWeekResetMin": 9400,
            "codexWeekStale": False,
        },
        "agents": {"v": 2, "agents": {
            "codex": {"active_count": 2, "jobs": []},
        }},
    }
    result = {
        page: render_page(data, page, NOW + 1,
                          timezone=dt.timezone(dt.timedelta(hours=1)))
        for page in PAGES
    }
    for name in ("boot", "offline"):
        im, d = ui.base(name)
        ui.header(d, "PAPER 0.2 / USB EDITION")
        ui.text(d, 32, 97, "VibePulse", 65, serif=True)
        ui.text(d, 32, 195, "Connect to your Mac." if name == "boot"
                else "USB source offline.", 32, bold=True)
        ui.text(d, 32, 266, "800 x 480 / Waveshare e-Paper 3.97", 24)
        ui.text(d, 32, 320, "Waiting for verified data." if name == "boot"
                else "No recent host heartbeat.", 24)
        ui.footer(d, "USB EDITION / BATTERY AND AUDIO NOT QUALIFIED")
        result[name] = im
    return result


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[2]
    for name, im in frames().items():
        im.save(root / "docs/img/epaper397" / f"{name}.png")
        if name in ("boot", "offline"):
            (root / "firmware/epaper_397/main" / f"{name}.raw").write_bytes(im.tobytes())
