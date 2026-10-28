"""Local USB raster bridge for the separate Waveshare e-paper bring-up firmware.

Reads already-running loopback services. No API keys, LAN server, Wi-Fi,
model calls, relays, or command approvals. Run using the workspace .venv.
"""

import argparse
import datetime as dt
import hashlib
import json
import math
from pathlib import Path
import struct
import sys
import time
import urllib.request
import zlib
import serial
from serial.tools import list_ports

try:
    from . import render as ui
except ImportError:
    import render as ui

ROOT = Path(__file__).resolve().parent
STATE = Path.home() / ".vibepulse-paper"
PAGES = ("codex", "claude", "projects", "device")


def get_json(url):
    try:
        with urllib.request.urlopen(url, timeout=4) as r:
            raw = r.read(262145)
            if len(raw) > 262144:
                return None
            payload = json.loads(raw)
            return (
                payload if isinstance(payload, dict) and payload.get("v") == 2 else None
            )
    except (OSError, ValueError):
        return None


def number(value):
    return (
        isinstance(value, (float, int))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def snapshot(base_url="http://127.0.0.1:8737"):
    # Read VibePulse itself; no dependency on the separate Codex Usage Pulse plugin.
    return dict(
        tokens=get_json(base_url + "/api/tokens"),
        agents=get_json(base_url + "/api/agent-status"),
    )


def quota(data, provider, now):
    tokens = data.get("tokens") or {}
    prefix = provider + "Week"
    value = tokens.get(prefix + "Pct")
    observed = tokens.get(prefix + "ObservedAt")
    minutes = tokens.get(prefix + "ResetMin")
    window = 10080  # These explicit tokenserver fields are weekly quotas.
    reset = (
        observed + minutes * 60
        if number(observed) and number(minutes) and minutes >= 0
        else None
    )
    stale = tokens.get(prefix + "Stale") is not False
    if not number(value) or not 0 <= value <= 100:
        value = None
    if not number(observed) or not 0 <= now - observed <= 180:
        stale = True
    return value, observed, reset, window, stale


def fit(d, x, y, value, max_width, size=24, bold=False):
    f = ui.font(size, bold=bold)
    value = str(value).replace("\n", " ").replace("\r", " ")
    while d.textlength(value, font=f) > max_width and value:
        value = value[:-2] + "…"
    ui.text(d, x, y, value, size, bold=bold)


def render_page(data, page="codex", now=None, comparison=False, timezone=None):
    now = time.time() if now is None else now
    im, d = ui.base(page)
    ui.header(d, "PAPER 0.2  /  USB")
    if page in ("codex", "claude"):
        value, observed, reset, window, stale = quota(data, page, now)
        ui.text(d, 32, 89, page.upper(), 28, bold=True)
        label = (
            "Weekly account quota used"
            if window == 10080
            else (
                f"{window:g}-minute account quota used"
                if number(window)
                else "Account quota used"
            )
        )
        fit(d, 33, 130, label, 345, size=21)
        ui.text(d, 25, 154, "—" if value is None else f"{value:.0f}", 155)
        if value is not None:
            ui.text(d, 290 if value >= 100 else 244, 233, "%", 48)
        if value is not None:
            d.rectangle((34, 320, 337, 331), fill=1)
            if value > 0:
                d.rectangle((34, 320, 34 + int(303 * value / 100), 331), fill=0)
        status = (
            "No verified reading"
            if value is None
            else ("STALE · last known" if stale else f"{100 - value:.0f}% remaining")
        )
        ui.text(d, 34, 354, status, 23)
        d.line((384, 94, 384, 395), fill=0, width=2)
        ui.text(d, 419, 94, "VIBEPULSE", 18, bold=True)
        ui.text(d, 419, 131, "Your desk.", 34, serif=True)
        agents = (data.get("agents") or {}).get("agents", {}).get(page, {})
        count = agents.get("active_count")
        ui.text(
            d,
            419,
            187,
            f"{count} active agents" if number(count) else "Agent status unknown",
            22,
        )
        d.line((420, 234, 766, 234), fill=0)
        ui.text(d, 419, 258, "QUOTA RESETS", 18, bold=True)
        if number(reset) and reset > now:
            ui.text(
                d,
                419,
                299,
                dt.datetime.fromtimestamp(reset, tz=timezone).strftime("%d %b  %H:%M"),
                28,
                serif=True,
            )
        else:
            ui.text(d, 419, 299, "—", 30)
        ui.text(
            d,
            419,
            358,
            "Check Claude on your Mac"
            if page == "claude" and value is None
            else "Turn for Claude / agents",
            20,
        )
        stamp = (
            dt.datetime.fromtimestamp(observed, tz=timezone).strftime("%d %b %H:%M")
            if number(observed)
            else "UNKNOWN"
        )
        ui.footer(
            d,
            "TURN: PAGE  /  PRESS: CODEX"
            if comparison
            else f"SAMPLE {stamp}  /  TURN: PAGE  /  PRESS: CODEX",
        )
    elif page == "projects":
        ui.text(d, 32, 94, "Your agents, together.", 43, serif=True)
        agents = (data.get("agents") or {}).get("agents", {})
        for i, provider in enumerate(("codex", "claude")):
            y = 177 + i * 84
            provider_data = agents.get(provider, {})
            count = provider_data.get("active_count")
            ui.text(d, 32, y, provider.capitalize(), 29, bold=True)
            ui.text(
                d, 340, y + 3, f"{count} active" if number(count) else "Unknown", 26
            )
            projects = list(
                dict.fromkeys(
                    j.get("project")
                    for j in provider_data.get("jobs", [])
                    if isinstance(j.get("project"), str) and j.get("project")
                )
            )
            if projects:
                fit(d, 34, y + 40, " / ".join(projects), 725, size=20)
        ui.text(d, 32, 357, "Grok Bot / OpenRouter: not connected", 24)
        ui.footer(
            d,
            "TURN: PAGE  /  PRESS: CODEX"
            if comparison
            else dt.datetime.fromtimestamp(now, tz=timezone).strftime("READ %d %b %H:%M")
            + "  /  TURN: PAGE  /  PRESS: CODEX",
        )
    else:
        ui.text(d, 32, 94, "Paper, connected.", 48, serif=True)
        for i, line in enumerate(
            (
                "Waveshare e-Paper 3.97 / 800 x 480",
                "USB data from this Mac",
                "Battery / audio / Wi-Fi: not qualified",
            )
        ):
            ui.text(d, 32, 200 + i * 56, line, 26)
        ui.footer(d, "PAPER 0.2 / USB EDITION / FEATURE LIMITS IN GUIDE")
    return im


def check_identity(port=None):
    candidates = [
        p for p in list_ports.comports() if p.vid == 0x303A and p.pid == 0x1001
    ]
    if port:
        candidates = [p for p in candidates if p.device == port]
    if len(candidates) != 1:
        raise RuntimeError(
            "Choose one Espressif USB port with --port; USB identity alone is not the panel model"
        )
    return candidates[0].device


def content_key(data, page, now):
    # The observation timestamp is intentionally excluded: a new source sample
    # with identical visible content must not trigger an e-paper refresh.
    return hashlib.sha256(
        render_page(data, page, now, comparison=True).tobytes()
    ).digest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port")
    ap.add_argument("--url", default="http://127.0.0.1:8737")
    ap.add_argument("--state-dir", type=Path, default=STATE)
    ap.add_argument("--once", action="store_true")
    args = ap.parse_args()
    args.port = check_identity(args.port)
    args.state_dir.mkdir(parents=True, exist_ok=True)
    usb = serial.Serial(port=None, baudrate=115200, timeout=0.2, write_timeout=20)
    usb.dtr = False
    usb.rts = False
    usb.port = args.port
    usb.open()
    try:
        usb.write(b"?")
        deadline = time.monotonic() + 40
        identified = False
        while time.monotonic() < deadline:
            line = usb.readline().decode("ascii", "replace").strip()
            if "HELLO VPP1 waveshare_epaper_397 paper-0.2.0 800 480" in line:
                identified = True
                break
            usb.write(b"?")
        if not identified:
            raise RuntimeError("Firmware identity handshake failed; no image sent")
        print("IDENTIFIED VPP1 waveshare_epaper_397 paper-0.2.0", flush=True)
        page = 0
        sequence = 0
        refresh = 0
        data = {}
        last_source = float("-inf")
        last_key = None
        while True:
            now = time.monotonic()
            if now >= refresh:
                if now - last_source > 10:
                    data = snapshot(args.url)
                    last_source = time.monotonic()
                key = content_key(data, PAGES[page], time.time())
                if key == last_key:
                    usb.write(
                        b"!"
                    )  # Host heartbeat; preserve pixels and waveform budget.
                    refresh = time.monotonic() + 30
                    continue
                im = render_page(data, PAGES[page])
                raw = im.tobytes()
                assert len(raw) == 48000
                im.save(args.state_dir / "device-current.png")
                sequence += 1
                crc = zlib.crc32(raw)
                usb.write(struct.pack("<4sIII", b"VPP1", sequence, len(raw), crc) + raw)
                usb.flush()
                deadline = time.monotonic() + 45
                acked = False
                queued = None
                while time.monotonic() < deadline:
                    line = usb.readline().decode("ascii", "replace").strip()
                    if line.startswith("BUTTON "):
                        queued = line.split()[1]
                    if line.startswith(f"ACK {sequence} {crc:08x} "):
                        acked = True
                        print(line, flush=True)
                        break
                    if line.startswith("ERROR "):
                        raise RuntimeError(line)
                if not acked:
                    raise RuntimeError("No matching device refresh acknowledgement")
                (args.state_dir / "usb-status.json").write_text(
                    json.dumps(
                        dict(
                            version="paper-0.2.0",
                            page=PAGES[page],
                            sequence=sequence,
                            crc32=f"{crc:08x}",
                            raster_sha256=hashlib.sha256(raw).hexdigest(),
                            ack=line,
                            acknowledged_at=dt.datetime.now().astimezone().isoformat(),
                            source_status={
                                p: dict(
                                    value=quota(data, p, time.time())[0],
                                    stale=quota(data, p, time.time())[4],
                                )
                                for p in ("codex", "claude")
                            },
                            physically_verified=False,
                        ),
                        indent=2,
                    )
                    + "\n"
                )
                if args.once:
                    return
                last_key = key
                refresh = time.monotonic() + 30
                if queued:
                    page = (
                        0
                        if queued == "home"
                        else (page + (1 if queued == "next" else -1)) % len(PAGES)
                    )
                    refresh = 0
            line = usb.readline().decode("ascii", "replace").strip()
            if line.startswith("OFFLINE ") or line == "PONG repaint-required":
                last_key = None
                refresh = 0
            if line.startswith("BUTTON "):
                action = line.split()[1]
                page = (
                    0
                    if action == "home"
                    else (page + (1 if action == "next" else -1)) % len(PAGES)
                )
                refresh = 0
    finally:
        usb.close()


if __name__ == "__main__":
    while True:
        try:
            main()
            break
        except (OSError, serial.SerialException, RuntimeError) as exc:
            print("USB HOST:", str(exc), flush=True)
            if "--once" in sys.argv:
                raise
            time.sleep(5)
