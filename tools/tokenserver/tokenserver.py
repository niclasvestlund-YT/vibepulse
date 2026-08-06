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
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

RECOMPUTE_EVERY_S = 30
LIMITS_EVERY_S = 120  # rate-limit-proben: snäll mot API:t, färsk nog för hyllan

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


# ---------------------------------------------------------------------------
# Rate-limit-proben (Clawdmeter-mönstret): läs Claude Codes egen OAuth-token
# ur nyckelringen och gör en minimal API-förfrågan — svaret är ointressant,
# HEADRARNA är datat (anthropic-ratelimit-unified-*: sessionens 5h-fönster
# och veckofönstret, i procent + återställningstid). max_tokens=0 betyder
# att inget genereras: proben är i praktiken gratis. Tokenen lämnar aldrig
# Macen; skärmen får bara procenttal.

_limits_lock = threading.Lock()
_last_limits = None
_last_probed = 0.0
_headers_logged = False
# Probe-diagnostik, exponerad på "/": var i kedjan Claude-proben fastnar
# (nyckelring → HTTP → headrar → mappning) plus de råa headernamnen.
_probe_status = "not_run"
_probe_headers = []


def _read_oauth_token():
    """Claude Codes accessToken ur macOS-nyckelringen: (token, expires_at_ms).
    (None, None) om otillgänglig."""
    try:
        raw = subprocess.run(
            ["security", "find-generic-password",
             "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        oauth = json.loads(raw).get("claudeAiOauth") or {}
        return oauth.get("accessToken"), oauth.get("expiresAt")
    except Exception:
        return None, None


def _parse_reset_minutes(value: str, now_ts: float):
    """Reset-headern kan vara epok-sekunder, sekunder-kvar eller ISO-tid."""
    try:
        n = float(value)
        # Stort tal = epoktid; litet = sekunder kvar.
        remaining = (n - now_ts) if n > 1e9 else n
        return max(0, int(round(remaining / 60)))
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return max(0, int(round((dt.timestamp() - now_ts) / 60)))
    except ValueError:
        return None


def _probe_limits():
    """Ett minimalt API-anrop; returnerar {sessionPct, sessionResetMin,
    weekPct, weekResetMin} eller None om något saknas på vägen."""
    global _probe_status, _probe_headers
    token, expires_at = _read_oauth_token()
    if not token:
        _probe_status = "no_keychain_token"
        return None
    if expires_at and expires_at / 1000 < time.time():
        # Tokenen har gått ut; Claude Code förnyar den i nyckelringen nästa
        # gång den pratar med API:t — vi behöver bara vänta och läsa om.
        _probe_status = (f"token_expired_"
                         f"{datetime.fromtimestamp(expires_at / 1000):%H:%M}")
        return None

    body = json.dumps({
        "model": "claude-haiku-4-5",  # billigaste proben; headrarna är desamma
        "max_tokens": 0,              # prefill utan output — i praktiken gratis
        "messages": [{"role": "user", "content": "ping"}],
    }).encode()
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}",
            "anthropic-version": "2023-06-01",
            "anthropic-beta": "oauth-2025-04-20",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        # Felsvar: ta med felkroppens början i diagnosen — 401-orsaken
        # (utgången token? fel scope?) står där. Headrarna följer ofta
        # med ändå.
        try:
            detail = e.read(200).decode(errors="replace")
        except Exception:
            detail = ""
        _probe_status = f"http_{e.code} {detail}".strip()
        headers = dict(e.headers) if e.headers else {}
    except Exception as e:
        _probe_status = f"request_failed: {type(e).__name__}"
        return None
    else:
        _probe_status = "http_200"

    now_ts = time.time()
    # Diagnostik vid första proben: headernamnen är hämtade ur Clawdmeters
    # beskrivning, inte ur egen observation — loggen är facit om de skiljer.
    global _headers_logged
    if not _headers_logged:
        _headers_logged = True
        for name, value in sorted(headers.items()):
            if "ratelimit" in name.lower():
                print(f"ratelimit-header: {name}: {value}")
    # Tre fönster, samma som Claudes egen usage-panel: 5-timmars, veckan
    # (alla modeller) och veckan för tyngsta modellen (Fable/Opus). Fönster-
    # namnet i headern varierar ("5h", "7d", "7d_opus", ...) — mappa på
    # innehåll, inte exakt namn.
    found = {}
    for name, value in headers.items():
        m = re.match(
            r"(?i)anthropic-ratelimit-unified-(.+?)[-_]"
            r"(utilization|reset|resets[-_]at)$", name)
        if not m:
            continue
        raw = m.group(1).lower()
        if "5h" in raw:
            window = "session"
        elif any(x in raw for x in ("opus", "fable", "sonnet", "model")):
            window = "model"
        elif "7d" in raw or "week" in raw:
            window = "week"
        else:
            continue
        kind = m.group(2).lower()
        if kind == "utilization":
            try:
                pct = float(value)
                found[f"{window}Pct"] = round(pct * 100 if pct <= 1.0 else pct, 1)
            except ValueError:
                pass
        else:
            mins = _parse_reset_minutes(value, now_ts)
            if mins is not None:
                found[f"{window}ResetMin"] = mins

    _probe_headers = sorted(
        f"{n}: {v}" for n, v in headers.items() if "ratelimit" in n.lower())
    if "sessionPct" not in found:
        _probe_status += " + no_mapped_headers"
        return None
    _probe_status += " + ok"
    return found


def get_limits():
    global _last_limits, _last_probed
    with _limits_lock:
        if time.monotonic() - _last_probed > LIMITS_EVERY_S:
            _last_limits = _probe_limits()
            _last_probed = time.monotonic()
        return _last_limits


# ---------------------------------------------------------------------------
# Codex-limits: PASSIV läsning — Codex CLI skriver sina rate-limits i
# rollout-filerna (~/.codex/sessions/**/rollout-*.jsonl) varje gång den kör:
# used_percent, window_minutes (10080 = veckofönstret) och resets_at (epok).
# Vi läser senaste snapshoten; har fönstret hunnit nollas sedan dess (resets_at
# passerat) är siffran meningslös och vi serverar null — aldrig gamla procent
# som låtsas vara färska.

CODEX_SESSIONS = Path(os.path.expanduser("~/.codex/sessions"))


def _find_rate_limits(obj):
    """Djupsök efter "rate_limits"-objektet i en rolloutrad (formatet har
    flyttat mellan Codex-versioner — nyckeln är stabil, vägen dit inte)."""
    if isinstance(obj, dict):
        if "rate_limits" in obj and isinstance(obj["rate_limits"], dict):
            return obj["rate_limits"]
        for v in obj.values():
            hit = _find_rate_limits(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_rate_limits(v)
            if hit:
                return hit
    return None


def _codex_window(win, now_ts):
    """{used_percent, window_minutes, resets_at} → (pct, reset_min) eller None."""
    if not isinstance(win, dict):
        return None
    pct = win.get("used_percent")
    resets_at = win.get("resets_at")
    if pct is None:
        return None
    reset_min = None
    if isinstance(resets_at, (int, float)):
        if resets_at < now_ts:
            return None  # fönstret har nollats sedan snapshoten — siffran ljuger
        reset_min = max(0, int(round((resets_at - now_ts) / 60)))
    return round(float(pct), 1), reset_min, win.get("window_minutes")


def _read_codex_limits():
    """Senaste rate_limits-snapshoten ur de nyaste rollout-filerna."""
    if not CODEX_SESSIONS.is_dir():
        return {}
    try:
        newest = sorted(CODEX_SESSIONS.glob("**/rollout-*.jsonl"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:5]
    except OSError:
        return {}
    now_ts = time.time()
    for path in newest:
        try:
            lines = path.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            if '"rate_limits"' not in line:
                continue
            try:
                rl = _find_rate_limits(json.loads(line))
            except json.JSONDecodeError:
                continue
            if not rl:
                continue
            out = {}
            for key in ("primary", "secondary"):
                win = _codex_window(rl.get(key), now_ts)
                if not win:
                    continue
                pct, reset_min, window_minutes = win
                # <= 10 h räknas som sessionsfönstret, längre som veckan.
                bucket = "codexSession" if (window_minutes or 0) <= 600 else "codexWeek"
                out.setdefault(f"{bucket}Pct", pct)
                out.setdefault(f"{bucket}ResetMin", reset_min)
            return out
    return {}


def get_snapshot(projects_dir: Path):
    global _last_result, _last_computed
    with _cache_lock:
        if _last_result is None or time.monotonic() - _last_computed > RECOMPUTE_EVERY_S:
            _last_result = _compute(projects_dir)
            _last_computed = time.monotonic()
        result = dict(_last_result)

    # null = ärlig frånvaro (nyckelring/probe/loggar otillgängliga) — skärmen
    # visar streck, aldrig hittade procent. Samma regel som sharePct.
    claude = get_limits() or {}
    codex = _read_codex_limits()
    result["claudeSessionPct"] = claude.get("sessionPct")
    result["claudeSessionResetMin"] = claude.get("sessionResetMin")
    result["claudeWeekPct"] = claude.get("weekPct")
    result["claudeWeekResetMin"] = claude.get("weekResetMin")
    result["claudeModelWeekPct"] = claude.get("modelPct")
    result["claudeModelWeekResetMin"] = claude.get("modelResetMin")
    result["codexSessionPct"] = codex.get("codexSessionPct")
    result["codexSessionResetMin"] = codex.get("codexSessionResetMin")
    result["codexWeekPct"] = codex.get("codexWeekPct")
    result["codexWeekResetMin"] = codex.get("codexWeekResetMin")
    result["v"] = 2
    return result


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
                             "endpoint": "/api/tokens",
                             "claudeProbe": _probe_status,
                             "ratelimitHeaders": _probe_headers})
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
