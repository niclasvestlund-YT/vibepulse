#!/usr/bin/env python3
"""Tokenmätarens tjänst: Claude Code-användningen som platt JSON över LAN.

Skannar sessionsloggarna i ~/.claude/projects/**/*.jsonl (samma källa som
usage-verktyg i ccusage-familjen läser), summerar tokens per dag och serverar
glance-mönstrets kontrakt på /api/tokens:

    {"v": 1, "dayTokens": ..., "dayTokensPerHour": ..., "daySessions": ...,
     "monthTokens": ...}

Designregler, ärvda från Solelkollens /api/glance:
  * Platt JSON, tal inte strängar, och en takt (dayTokensPerHour, senaste
    timmens brinntakt) så skärmen kan ticka lokalt mellan hämtningarna.
  * Inga hemligheter i svaret och ingen autentisering: siffrorna beskriver
    tokenvolym, inget innehåll. Tjänsten binder mot LAN:et — exponera den
    inte utanför hemmet.
  * Fel svarar {"error": "..."} — skärmens parser avvisar den formen per
    kontrakt och behåller sina senaste goda värden.

"Tokens" är alla som passerat modellen: in + ut + cacheskrivning +
cacheläsning — den enda siffran som ärligt beskriver hur mycket som malts.
Dubblettrader (samma message.id + requestId, som uppstår när sessioner
återupptas) räknas en gång, samma dedup som usage-verktygen gör.

Inkrementellt: filer med oförändrad (mtime, storlek) återanvänds ur cachen,
och filer äldre än månadsskiftet hoppas över helt. En full förstaskanning
tar några sekunder; därefter är varje svar i praktiken omedelbart.
Aggregatet räknas om högst var 30:e sekund oavsett hämttakt.

Körning:  python3 tokenserver.py [--port 8737] [--dir ~/.claude/projects]
Autostart: se README.md härintill (launchd-plist medföljer).
"""

import argparse
import json
import os
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RECOMPUTE_EVERY_S = 30

# (day, ts, tokens, session, key) per loggrad med usage — det minsta som
# behövs för dag-, månads-, takt- och sessionsaggregaten.
_cache_lock = threading.Lock()
_file_cache = {}   # path -> {"stat": (mtime, size), "records": [...]}
_last_result = None
_last_computed = 0.0


def _parse_file(path: Path, month_start: datetime):
    """Alla usage-rader i en sessionslogg, som kompakta poster."""
    records = []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue  # halvskriven sista rad — nästa skanning tar den
                usage = (entry.get("message") or {}).get("usage")
                ts_raw = entry.get("timestamp")
                if not usage or not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                ts = ts.astimezone()  # dygnsgränsen är Macens, inte UTC:s
                if ts < month_start:
                    continue
                tokens = (
                    (usage.get("input_tokens") or 0)
                    + (usage.get("output_tokens") or 0)
                    + (usage.get("cache_creation_input_tokens") or 0)
                    + (usage.get("cache_read_input_tokens") or 0)
                )
                if tokens <= 0:
                    continue
                msg_id = (entry.get("message") or {}).get("id")
                req_id = entry.get("requestId")
                key = f"{msg_id}:{req_id}" if msg_id and req_id else None
                records.append((
                    ts.strftime("%Y-%m-%d"),
                    ts.timestamp(),
                    tokens,
                    entry.get("sessionId") or str(path),
                    key,
                ))
    except OSError:
        pass  # borttagen under läsning — nästa skanning ser det
    return records


def _compute(projects_dir: Path):
    now = datetime.now().astimezone()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    today = now.strftime("%Y-%m-%d")
    hour_ago = now.timestamp() - 3600

    live_paths = set()
    for path in projects_dir.glob("**/*.jsonl"):
        try:
            st = path.stat()
        except OSError:
            continue
        # Äldre än månadsskiftet kan inte innehålla månadens rader (rader
        # skrivs framåt i tiden): hoppa över utan att öppna.
        if datetime.fromtimestamp(st.st_mtime).astimezone() < month_start:
            continue
        live_paths.add(path)
        cached = _file_cache.get(path)
        stat_key = (st.st_mtime, st.st_size)
        if cached and cached["stat"] == stat_key:
            continue
        _file_cache[path] = {"stat": stat_key,
                             "records": _parse_file(path, month_start)}
    for stale in set(_file_cache) - live_paths:
        del _file_cache[stale]

    day_tokens = 0
    month_tokens = 0
    hour_tokens = 0
    day_sessions = set()
    seen = set()
    for entry in _file_cache.values():
        for day, ts, tokens, session, key in entry["records"]:
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            month_tokens += tokens
            if day == today:
                day_tokens += tokens
                day_sessions.add(session)
            if ts >= hour_ago:
                hour_tokens += tokens

    return {
        "v": 1,
        "dayTokens": day_tokens,
        "dayTokensPerHour": hour_tokens,  # senaste timmen = takt per timme
        "daySessions": len(day_sessions),
        "monthTokens": month_tokens,
        "at": now.isoformat(timespec="seconds"),
    }


def get_snapshot(projects_dir: Path):
    global _last_result, _last_computed
    with _cache_lock:
        if _last_result is None or time.monotonic() - _last_computed > RECOMPUTE_EVERY_S:
            _last_result = _compute(projects_dir)
            _last_computed = time.monotonic()
        return _last_result


class Handler(BaseHTTPRequestHandler):
    projects_dir = None  # sätts i main

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tokens":
            try:
                self._send(200, get_snapshot(self.projects_dir))
            except Exception as e:  # skärmen avvisar error-formen per kontrakt
                self._send(500, {"error": str(e)})
        elif self.path == "/":
            self._send(200, {"service": "torget-tokenserver",
                             "endpoint": "/api/tokens"})
        else:
            self._send(404, {"error": "not found"})

    def log_message(self, fmt, *args):
        pass  # 30 s-pollning ska inte fylla loggen


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8737)
    ap.add_argument("--dir", default=os.path.expanduser("~/.claude/projects"))
    args = ap.parse_args()

    Handler.projects_dir = Path(args.dir)
    if not Handler.projects_dir.is_dir():
        raise SystemExit(f"hittar inte {Handler.projects_dir} — finns Claude Code på den här maskinen?")

    t0 = time.monotonic()
    snap = get_snapshot(Handler.projects_dir)
    print(f"förstaskanning {time.monotonic() - t0:.1f} s: "
          f"{snap['dayTokens']:,} tokens idag, {snap['daySessions']} sessioner, "
          f"{snap['monthTokens']:,} denna månad".replace(",", " "))

    srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"serverar http://0.0.0.0:{args.port}/api/tokens (LAN — exponera inte utåt)")
    srv.serve_forever()


if __name__ == "__main__":
    main()
