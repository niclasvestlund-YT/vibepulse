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
import hashlib
import json
import math
import os
import re
import subprocess
import threading
import time
import urllib.request
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__:
    from .agent_status import AgentStatusService
    from .quota_cache import CachedQuota, QuotaCache
    from .usage_history import Forecast, UsageHistory
else:  # direktkörning: python3 tools/tokenserver/tokenserver.py
    from agent_status import AgentStatusService
    from quota_cache import CachedQuota, QuotaCache
    from usage_history import Forecast, UsageHistory

RECOMPUTE_EVERY_S = 30
LIMITS_EVERY_S = 120  # rate-limit-proben: snäll mot API:t, färsk nog för hyllan

# (day, ts, tokens, session, key) per loggrad med usage — det minsta som
# behövs för dag-, månads-, takt- och sessionsaggregaten.
_cache_lock = threading.Lock()
_file_cache = {}   # path -> stat, parsed offset, month and compact records
_last_result = None
_last_computed = 0.0
_snapshot_refreshing = False
_history_lock = threading.Lock()
_default_usage_history = None
_quota_cache_lock = threading.Lock()
_default_quota_cache = None


def _get_usage_history(path=None):
    global _default_usage_history
    if path is not None:
        return UsageHistory(Path(path))
    with _history_lock:
        if _default_usage_history is None:
            _default_usage_history = UsageHistory(
                Path.home() / "Library" / "Application Support" /
                "VibePulse" / "usage-history.json")
        return _default_usage_history


def _get_quota_cache(path=None):
    global _default_quota_cache
    if path is not None:
        return QuotaCache(Path(path))
    with _quota_cache_lock:
        if _default_quota_cache is None:
            _default_quota_cache = QuotaCache(
                Path.home() / "Library" / "Application Support" /
                "VibePulse" / "quota-cache.json")
        return _default_quota_cache


def _quota_identity(provider, scope, raw_identity=None):
    """Return a local opaque identity without retaining its raw input."""
    stable = "default-v1" if raw_identity is None else str(raw_identity)
    material = f"{provider}\0{scope}\0{stable}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _parse_file(path: Path, month_start: datetime, start_offset=0):
    """Parse complete usage rows from ``start_offset`` and return new offset."""
    records = []
    parsed_until = start_offset
    try:
        with open(path, "rb") as f:
            f.seek(start_offset)
            while True:
                line_start = f.tell()
                raw_line = f.readline()
                if not raw_line:
                    break
                if not raw_line.endswith(b"\n"):
                    # A writer may still be appending this JSON row. Resume at
                    # its beginning next time instead of losing the fragment.
                    parsed_until = line_start
                    break
                parsed_until = f.tell()
                try:
                    entry = json.loads(raw_line)
                except (json.JSONDecodeError, UnicodeDecodeError):
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
    return records, parsed_until


def _compute(projects_dir: Path):
    now = datetime.now().astimezone()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_key = month_start.strftime("%Y-%m")
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
        identity = (st.st_dev, st.st_ino)
        if (cached and cached["stat"] == stat_key and
                cached.get("identity") == identity):
            continue
        can_append = (
            cached is not None and
            cached.get("month") == month_key and
            cached.get("identity") == identity and
            st.st_size > cached["stat"][1]
        )
        if can_append:
            new_records, parsed_until = _parse_file(
                path, month_start, start_offset=cached["offset"])
            cached["records"].extend(new_records)
            cached["stat"] = stat_key
            cached["offset"] = parsed_until
        else:
            records, parsed_until = _parse_file(
                path, month_start, start_offset=0)
            _file_cache[path] = {
                "stat": stat_key,
                "identity": identity,
                "offset": parsed_until,
                "month": month_key,
                "records": records,
            }
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
# från den aktiva Claude Desktop-processen eller nyckelringen och gör en
# minimal API-förfrågan — svaret är ointressant,
# HEADRARNA är datat (anthropic-ratelimit-unified-*: sessionens 5h-fönster
# och veckofönstret, i procent + återställningstid). max_tokens=0 betyder
# att inget genereras: proben är i praktiken gratis. Tokenen lämnar aldrig
# Macen; skärmen får bara procenttal.

_limits_lock = threading.Lock()
_last_limits = None
_last_probed = 0.0
_limits_refreshing = False
_headers_logged = False
# Probe-diagnostik, exponerad på "/": var i kedjan Claude-proben fastnar
# (nyckelring → HTTP → headrar → mappning) plus de råa headernamnen.
_probe_status = "not_run"
_probe_headers = []
_probe_unknown_buckets = []


_CLAUDE_DESKTOP_PROCESS = re.compile(
    r"^/Users/[^/\s]+/Library/Application Support/Claude/claude-code/"
    r"[^/\s]+/claude\.app/Contents/MacOS/claude(?:\s|$)"
)
_CLAUDE_PROCESS_TOKEN = re.compile(
    r"(?:^|\s)CLAUDE_CODE_OAUTH_TOKEN=([^\s]+)"
)


def _read_process_oauth_token():
    """Return Claude Desktop's injected child token without logging it.

    Claude Desktop refreshes OAuth itself and injects the current token into
    its bundled Claude Code process. The older keychain record can therefore
    be expired even while Claude Code is actively working. Only PIDs matching
    Claude Desktop's bundled binary are inspected; unrelated process
    environments are deliberately ignored.
    """
    try:
        pid_output = subprocess.run(
            [
                "pgrep", "-f",
                "/Library/Application Support/Claude/claude-code/.*"
                "/claude.app/Contents/MacOS/claude",
            ],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except Exception:
        return None

    for raw_pid in pid_output.splitlines():
        pid = raw_pid.strip()
        if not pid.isdigit():
            continue
        try:
            command = subprocess.run(
                ["ps", "eww", "-p", pid, "-o", "command="],
                capture_output=True, text=True, timeout=5,
            ).stdout.strip()
        except Exception:
            continue
        if not _CLAUDE_DESKTOP_PROCESS.match(command):
            continue
        match = _CLAUDE_PROCESS_TOKEN.search(command)
        if match:
            return match.group(1)
    return None


def _read_oauth_token():
    """Claude Codes active accessToken as ``(token, expires_at_ms)``.

    Prefer Claude Desktop's refreshed process token. Standalone Claude Code
    continues to use the macOS keychain fallback.
    """
    process_token = _read_process_oauth_token()
    if process_token:
        return process_token, None
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


def _parse_reset_at(value: str, now_ts: float):
    """Normalize an epoch, seconds-remaining, or ISO reset to epoch seconds."""
    try:
        number = float(value)
        if not math.isfinite(number) or number < 0:
            return None
        absolute = number if number > 1e9 else now_ts + number
        return int(absolute)
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_limit_headers(headers, now_ts):
    """Map Claude's named limit windows without guessing model identity."""
    found = {}
    unknown = set()
    model_labels = {
        "fable": "FABLE · WEEK",
        "opus": "OPUS · WEEK",
        "sonnet": "SONNET · WEEK",
    }
    for name, value in headers.items():
        match = re.match(
            r"(?i)anthropic-ratelimit-unified-(.+?)[-_]"
            r"(utilization|reset|resets[-_]at)$", name)
        if not match:
            continue
        raw = match.group(1).lower()
        named_model = next(
            (model for model in model_labels if model in raw), None)
        if raw == "5h":
            window = "session"
        elif named_model is not None or "model" in raw:
            window = "model"
            if named_model is not None:
                found["modelLabel"] = model_labels[named_model]
        elif raw in {"7d", "week"}:
            window = "week"
        else:
            sanitized = re.sub(r"[^a-z0-9_-]", "", raw)[:64]
            if sanitized:
                unknown.add(sanitized)
            continue
        kind = match.group(2).lower()
        if kind == "utilization":
            try:
                pct = float(value)
                found[f"{window}Pct"] = round(
                    pct * 100 if pct <= 1.0 else pct, 1)
            except (TypeError, ValueError):
                pass
        else:
            reset_at = _parse_reset_at(value, now_ts)
            if reset_at is not None:
                found[f"{window}ResetAt"] = reset_at
                found[f"{window}ResetMin"] = max(
                    0, int(round((reset_at - now_ts) / 60)))
    if unknown:
        found["unknownBuckets"] = sorted(unknown)
    observed_at = int(now_ts)
    for window, scope in (("week", "general_weekly"),
                          ("model", "model_weekly")):
        if (f"{window}Pct" in found and
                f"{window}ResetAt" in found):
            found[f"{window}ObservedAt"] = observed_at
            found[f"{window}Identity"] = _quota_identity(
                "claude", scope)
    return found


def _probe_limits():
    """Ett minimalt API-anrop; returnerar {sessionPct, sessionResetMin,
    weekPct, weekResetMin} eller None om något saknas på vägen."""
    global _probe_status, _probe_headers, _probe_unknown_buckets
    token, expires_at = _read_oauth_token()
    if not token:
        _probe_status = "no_claude_oauth_token"
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
        _probe_status = f"http_{e.code}"
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
        for name in sorted(headers):
            if "ratelimit" in name.lower():
                print(f"ratelimit-header: {name}")
    # Tre fönster, samma som Claudes egen usage-panel: 5-timmars, veckan
    # (alla modeller) och veckan för tyngsta modellen (Fable/Opus). Fönster-
    # namnet i headern varierar ("5h", "7d", "7d_opus", ...) — mappa på
    # innehåll, inte exakt namn.
    found = _parse_limit_headers(headers, now_ts)

    _probe_headers = sorted(
        n for n in headers if "ratelimit" in n.lower())
    _probe_unknown_buckets = found.pop("unknownBuckets", [])
    if "sessionPct" not in found:
        _probe_status += " + no_mapped_headers"
        return None
    _probe_status += " + ok"
    return found


def _refresh_limits():
    global _last_limits, _last_probed, _limits_refreshing
    try:
        refreshed = _probe_limits()
    except Exception:
        refreshed = None
    with _limits_lock:
        _last_limits = refreshed
        _last_probed = time.monotonic()
        _limits_refreshing = False


def get_limits():
    global _limits_refreshing
    with _limits_lock:
        if ((_last_probed == 0.0 or
             time.monotonic() - _last_probed > LIMITS_EVERY_S) and
                not _limits_refreshing):
            _limits_refreshing = True
            threading.Thread(
                target=_refresh_limits,
                name="claude-limit-probe",
                daemon=True,
            ).start()
        return _last_limits


# ---------------------------------------------------------------------------
# Codex-limits: PASSIV läsning — Codex CLI skriver sina rate-limits i
# rollout-filerna (~/.codex/sessions/**/rollout-*.jsonl) varje gång den kör:
# used_percent, window_minutes (10080 = veckofönstret) och resets_at (epok).
# Vi läser senaste snapshoten; har fönstret hunnit nollas sedan dess (resets_at
# passerat) är siffran meningslös och vi serverar null — aldrig gamla procent
# som låtsas vara färska.

CODEX_SESSIONS = Path(os.path.expanduser("~/.codex/sessions"))
CODEX_LIMITS_EVERY_S = 30
CODEX_LIMIT_SCAN_BYTES = 1024 * 1024
_codex_limits_lock = threading.Lock()
_last_codex_limits = None
_last_codex_read = 0.0
_codex_refreshing = False


def _codex_rollout_rate_limits(obj):
    """Accept only Codex's observed token-count rollout event envelope."""
    if not isinstance(obj, dict) or obj.get("type") != "event_msg":
        return None
    payload = obj.get("payload")
    if not isinstance(payload, dict) or payload.get("type") != "token_count":
        return None
    rate_limits = payload.get("rate_limits")
    return rate_limits if isinstance(rate_limits, dict) else None


def _observation_timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except (ValueError, OverflowError):
        return None


def _codex_window(win, now_ts):
    """{used_percent, window_minutes, resets_at} → (pct, reset_min) eller None."""
    if not isinstance(win, dict):
        return None
    pct = win.get("used_percent")
    resets_at = win.get("resets_at")
    window_minutes = win.get("window_minutes")
    if (not isinstance(pct, (int, float)) or isinstance(pct, bool) or
            not math.isfinite(pct) or not 0 <= pct <= 100 or
            not isinstance(window_minutes, (int, float)) or
            isinstance(window_minutes, bool) or
            not math.isfinite(window_minutes)):
        return None
    if (not isinstance(resets_at, (int, float)) or
            isinstance(resets_at, bool) or not math.isfinite(resets_at) or
            resets_at <= now_ts):
        return None
    reset_at = int(resets_at)
    reset_min = max(0, int(round((reset_at - now_ts) / 60)))
    return round(float(pct), 1), reset_min, window_minutes


def _codex_general_observation(rate_limits, observed_at, now_ts):
    """Classify an authoritative unnamed weekly Codex observation."""
    if not isinstance(rate_limits, dict):
        return None
    limit_name = rate_limits.get("limit_name")
    if limit_name is not None:
        if not isinstance(limit_name, str) or limit_name:
            return None
    if not isinstance(observed_at, int) or isinstance(observed_at, bool):
        return None
    for key in ("primary", "secondary"):
        parsed = _codex_window(rate_limits.get(key), now_ts)
        if parsed is None:
            continue
        pct, _reset_min, window_minutes = parsed
        if window_minutes <= 600:
            continue
        reset_at = int(rate_limits[key]["resets_at"])
        raw_identity = rate_limits.get("limit_id")
        return {
            "pct": pct,
            "reset_at": reset_at,
            "observed_at": int(observed_at),
            "identity": _quota_identity(
                "codex", "general_weekly", raw_identity),
        }
    return None


def _codex_session_observation(rate_limits, observed_at, now_ts):
    if not isinstance(rate_limits, dict) or not isinstance(observed_at, int):
        return None
    for key in ("primary", "secondary"):
        parsed = _codex_window(rate_limits.get(key), now_ts)
        if parsed is None:
            continue
        pct, reset_min, window_minutes = parsed
        if window_minutes <= 600:
            return {"pct": pct, "reset_min": reset_min,
                    "observed_at": observed_at}
    return None


def _read_latest_rate_limits(path: Path, block_size=64 * 1024,
                             max_bytes=CODEX_LIMIT_SCAN_BYTES):
    """Read a rollout backwards and stop at its newest rate-limit event.

    Active Codex rollouts can grow past 100 MB. Reading and splitting the
    complete file on every display poll is both slow and memory hungry, while
    the relevant event is normally within the final few kilobytes.
    """
    max_bytes = min(max_bytes, CODEX_LIMIT_SCAN_BYTES)
    try:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            position = source.tell()
            fragment = b""
            remaining = max_bytes
            while position > 0 and remaining > 0:
                read_size = min(block_size, position, remaining)
                position -= read_size
                remaining -= read_size
                source.seek(position)
                parts = (source.read(read_size) + fragment).split(b"\n")
                fragment = parts.pop(0)
                for raw_line in reversed(parts):
                    if b'"rate_limits"' not in raw_line:
                        continue
                    try:
                        found = _codex_rollout_rate_limits(
                            json.loads(raw_line))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    if found:
                        return found
            if position == 0 and b'"rate_limits"' in fragment:
                try:
                    return _codex_rollout_rate_limits(json.loads(fragment))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    pass
    except OSError:
        pass
    return None


def _read_codex_observations(path: Path, now_ts, block_size=64 * 1024,
                             max_bytes=CODEX_LIMIT_SCAN_BYTES):
    """Return newest general/session observations within a bounded tail."""
    max_bytes = min(max_bytes, CODEX_LIMIT_SCAN_BYTES)
    general = None
    session = None
    try:
        with path.open("rb") as source:
            source.seek(0, os.SEEK_END)
            position = source.tell()
            fragment = b""
            remaining = max_bytes
            while position > 0 and remaining > 0:
                read_size = min(block_size, position, remaining)
                position -= read_size
                remaining -= read_size
                source.seek(position)
                parts = (source.read(read_size) + fragment).split(b"\n")
                fragment = parts.pop(0)
                for raw_line in reversed(parts):
                    if b'"rate_limits"' not in raw_line:
                        continue
                    try:
                        event = json.loads(raw_line)
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                    limits = _codex_rollout_rate_limits(event)
                    observed_at = _observation_timestamp(event.get(
                        "timestamp")) if isinstance(event, dict) else None
                    if limits is None or observed_at is None:
                        continue
                    if general is None:
                        general = _codex_general_observation(
                            limits, observed_at, now_ts)
                    if session is None:
                        session = _codex_session_observation(
                            limits, observed_at, now_ts)
                    if general is not None and session is not None:
                        return general, session
            if position == 0 and b'"rate_limits"' in fragment:
                try:
                    event = json.loads(fragment)
                except (json.JSONDecodeError, UnicodeDecodeError):
                    event = None
                limits = _codex_rollout_rate_limits(event)
                observed_at = _observation_timestamp(event.get(
                    "timestamp")) if isinstance(event, dict) else None
                if limits is not None and observed_at is not None:
                    if general is None:
                        general = _codex_general_observation(
                            limits, observed_at, now_ts)
                    if session is None:
                        session = _codex_session_observation(
                            limits, observed_at, now_ts)
    except OSError:
        pass
    return general, session


def _scan_codex_limits():
    """Senaste rate_limits-snapshoten ur de nyaste rollout-filerna."""
    if not CODEX_SESSIONS.is_dir():
        return {}
    try:
        newest = sorted(CODEX_SESSIONS.glob("**/rollout-*.jsonl"),
                        key=lambda p: p.stat().st_mtime, reverse=True)[:20]
    except OSError:
        return {}
    now_ts = time.time()
    weekly_candidates = []
    session_candidates = []
    for path in newest:
        weekly, session = _read_codex_observations(path, now_ts)
        if weekly is not None:
            weekly_candidates.append(weekly)
        if session is not None:
            session_candidates.append(session)
    out = {}
    if weekly_candidates:
        weekly = max(weekly_candidates, key=lambda item: item["observed_at"])
        out.update({
            "codexWeekPct": weekly["pct"],
            "codexWeekResetAt": weekly["reset_at"],
            "codexWeekObservedAt": weekly["observed_at"],
            "codexWeekIdentity": weekly["identity"],
            "codexWeekStale": False,
        })
    if session_candidates:
        session = max(session_candidates, key=lambda item: item["observed_at"])
        out.update({
            "codexSessionPct": session["pct"],
            "codexSessionResetMin": session["reset_min"],
        })
    return out


def _refresh_codex_limits():
    global _last_codex_limits, _last_codex_read, _codex_refreshing
    try:
        refreshed = _scan_codex_limits()
    except Exception:
        refreshed = {}
    with _codex_limits_lock:
        _last_codex_limits = refreshed
        _last_codex_read = time.monotonic()
        _codex_refreshing = False


def _read_codex_limits():
    global _codex_refreshing
    with _codex_limits_lock:
        if ((_last_codex_read == 0.0 or
             time.monotonic() - _last_codex_read > CODEX_LIMITS_EVERY_S) and
                not _codex_refreshing):
            _codex_refreshing = True
            threading.Thread(
                target=_refresh_codex_limits,
                name="codex-limit-scan",
                daemon=True,
            ).start()
        return dict(_last_codex_limits or {})


def _reset_at(now_ts, reset_minutes):
    if (not isinstance(reset_minutes, (int, float)) or
            isinstance(reset_minutes, bool) or reset_minutes < 0):
        return None
    return now_ts + reset_minutes * 60


def _reset_minutes(reset_at, now_ts):
    if (not isinstance(reset_at, (int, float)) or
            isinstance(reset_at, bool) or reset_at <= now_ts):
        return None
    return max(0, int(round((reset_at - now_ts) / 60)))


def _resolve_weekly_quota(source, provider, scope, prefix, quota_cache,
                          now_ts, label_key=None):
    """Resolve authoritative live truth, otherwise an unexpired cache row."""
    pct = source.get(f"{prefix}Pct")
    reset_at = source.get(f"{prefix}ResetAt")
    observed_at = source.get(f"{prefix}ObservedAt")
    identity = source.get(f"{prefix}Identity")
    live = (
        isinstance(pct, (int, float)) and not isinstance(pct, bool) and
        math.isfinite(pct) and 0 <= pct <= 100 and
        isinstance(reset_at, (int, float)) and
        not isinstance(reset_at, bool) and math.isfinite(reset_at) and
        reset_at > now_ts and
        isinstance(observed_at, (int, float)) and
        not isinstance(observed_at, bool) and math.isfinite(observed_at) and
        isinstance(identity, str) and bool(identity)
    )
    if live:
        label = source.get(label_key) if label_key else None
        record = CachedQuota(
            provider=provider,
            scope=scope,
            identity=identity,
            pct=float(pct),
            reset_at=int(reset_at),
            observed_at=int(observed_at),
            label=label if isinstance(label, str) else None,
        )
        quota_cache.put(record)
        return {
            "pct": round(float(pct), 1),
            "reset_at": int(reset_at),
            "label": record.label,
            "stale": False,
            "live": True,
        }
    cached = quota_cache.latest(provider, scope, now=now_ts)
    if cached is not None:
        return {
            "pct": round(float(cached.pct), 1),
            "reset_at": cached.reset_at,
            "label": cached.label,
            "stale": True,
            "live": False,
        }
    return {"pct": None, "reset_at": None, "label": None,
            "stale": False, "live": False}


def _add_forecast(result, prefix, forecast):
    result[f"{prefix}ForecastState"] = forecast.state
    result[f"{prefix}ForecastPctAtReset"] = forecast.pct_at_reset
    result[f"{prefix}ForecastPaceFactor"] = forecast.pace_factor
    result[f"{prefix}ForecastAt"] = forecast.exhausts_at
    result[f"{prefix}ForecastOffsetMin"] = forecast.offset_minutes


def _refresh_usage_totals(projects_dir):
    global _last_result, _last_computed, _snapshot_refreshing
    try:
        refreshed = _compute(projects_dir)
    except Exception:
        refreshed = None
    with _cache_lock:
        if refreshed is not None:
            _last_result = refreshed
        _last_computed = time.monotonic()
        _snapshot_refreshing = False


def get_snapshot(projects_dir: Path, history=None, now_ts=None,
                 quota_cache=None):
    global _last_result, _last_computed, _snapshot_refreshing
    with _cache_lock:
        if _last_result is None:
            _last_result = _compute(projects_dir)
            _last_computed = time.monotonic()
        elif (time.monotonic() - _last_computed > RECOMPUTE_EVERY_S and
              not _snapshot_refreshing):
            _snapshot_refreshing = True
            threading.Thread(
                target=_refresh_usage_totals,
                args=(projects_dir,),
                name="usage-total-refresh",
                daemon=True,
            ).start()
        result = dict(_last_result)

    # null = ärlig frånvaro (nyckelring/probe/loggar otillgängliga) — skärmen
    # visar streck, aldrig hittade procent. Samma regel som sharePct.
    claude = get_limits() or {}
    codex = _read_codex_limits()
    current_ts = time.time() if now_ts is None else now_ts
    usage_history = _get_usage_history() if history is None else history
    cache = _get_quota_cache() if quota_cache is None else quota_cache

    session_pct = claude.get("sessionPct")
    session_reset_at = claude.get("sessionResetAt")
    session_reset_min = _reset_minutes(session_reset_at, current_ts)
    if session_pct is None or session_reset_min is None:
        session_pct = None
        session_reset_at = None
        session_reset_min = None
    result["claudeSessionPct"] = session_pct
    result["claudeSessionResetMin"] = session_reset_min

    claude_week = _resolve_weekly_quota(
        claude, "claude", "general_weekly", "week", cache, current_ts)
    claude_model = _resolve_weekly_quota(
        claude, "claude", "model_weekly", "model", cache, current_ts,
        label_key="modelLabel")
    codex_week = _resolve_weekly_quota(
        codex, "codex", "general_weekly", "codexWeek", cache, current_ts)

    result["claudeWeekPct"] = claude_week["pct"]
    result["claudeWeekResetMin"] = _reset_minutes(
        claude_week["reset_at"], current_ts)
    result["claudeWeekStale"] = bool(
        claude_week["pct"] is not None and claude_week["stale"])
    result["claudeModelWeekPct"] = claude_model["pct"]
    result["claudeModelWeekResetMin"] = _reset_minutes(
        claude_model["reset_at"], current_ts)
    result["claudeModelWeekLabel"] = claude_model["label"]
    result["claudeModelWeekStale"] = bool(
        claude_model["pct"] is not None and claude_model["stale"])
    result["codexSessionPct"] = codex.get("codexSessionPct")
    result["codexSessionResetMin"] = codex.get("codexSessionResetMin")
    result["codexWeekPct"] = codex_week["pct"]
    result["codexWeekResetMin"] = _reset_minutes(
        codex_week["reset_at"], current_ts)
    result["codexWeekStale"] = bool(
        codex_week["pct"] is not None and codex_week["stale"])

    claude_session_reset = session_reset_at
    claude_week_reset = (
        claude_week["reset_at"] if claude_week["live"] else None)
    claude_model_reset = (
        claude_model["reset_at"] if claude_model["live"] else None)
    codex_week_reset = (
        codex_week["reset_at"] if codex_week["live"] else None)
    quota_samples = [
        (provider, window, pct, reset_at)
        for provider, window, pct, reset_at in (
            ("claude", "session", result["claudeSessionPct"],
             claude_session_reset),
            ("claude", "week", result["claudeWeekPct"],
             claude_week_reset),
            ("claude", "model_week", result["claudeModelWeekPct"],
             claude_model_reset),
            ("codex", "week", result["codexWeekPct"],
             codex_week_reset),
        )
        if pct is not None and reset_at is not None
    ]
    usage_history.record_many(quota_samples, at=current_ts)

    local_now = datetime.fromtimestamp(current_ts).astimezone()
    day_start = local_now.replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    result["claudeWeekTodayDeltaPct"] = (
        None if claude_week_reset is None else usage_history.delta_since(
            "claude", "week", day_start, claude_week_reset,
            now=current_ts))
    result["claudeModelWeekTodayDeltaPct"] = (
        None if claude_model_reset is None else usage_history.delta_since(
            "claude", "model_week", day_start, claude_model_reset,
            now=current_ts))
    result["claudeSessionHourDeltaPct"] = (
        None if claude_session_reset is None else usage_history.delta_since(
            "claude", "session", current_ts - 60 * 60,
            claude_session_reset, now=current_ts))
    result["codexWeekTodayDeltaPct"] = (
        None if codex_week_reset is None else usage_history.delta_since(
            "codex", "week", day_start, codex_week_reset,
            now=current_ts))

    claude_forecast = (
        Forecast(state="unavailable") if claude_week_reset is None else
        usage_history.forecast("claude", "week", claude_week_reset,
                               now=current_ts))
    codex_forecast = (
        Forecast(state="unavailable") if codex_week_reset is None else
        usage_history.forecast("codex", "week", codex_week_reset,
                               now=current_ts))
    _add_forecast(result, "claude", claude_forecast)
    _add_forecast(result, "codex", codex_forecast)
    result["v"] = 2
    return result


class Handler(BaseHTTPRequestHandler):
    projects_dir = None  # sätts i main
    agent_status = None  # bakgrundstjänst, sätts i main

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
            except Exception:  # skärmen avvisar error-formen per kontrakt
                self._send(500, {"error": "internal server error"})
        elif self.path == "/api/agent-status":
            self._send(200, self.agent_status.snapshot())
        elif self.path == "/":
            self._send(200, {"service": "torget-tokenserver",
                             "endpoint": "/api/tokens",
                             "endpoints": ["/api/tokens", "/api/agent-status"],
                             "claudeProbe": _probe_status,
                             "ratelimitHeaders": _probe_headers,
                             "unknownRateLimitBuckets":
                                 _probe_unknown_buckets})
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

    status_service = AgentStatusService(
        projects_dir=Handler.projects_dir,
        codex_sessions=CODEX_SESSIONS,
    )
    status_service.poll_once()
    status_service.start()
    Handler.agent_status = status_service

    srv = None
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
        print(f"serverar http://0.0.0.0:{args.port}/api/tokens och "
              f"/api/agent-status (LAN — exponera inte utåt)")
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        status_service.stop()
        if srv is not None:
            srv.server_close()


if __name__ == "__main__":
    main()
