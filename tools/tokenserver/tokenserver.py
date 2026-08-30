#!/usr/bin/env python3
"""Tokenmätarens tjänst: Claude Code-användningen som platt JSON över LAN.

Skannar sessionsloggarna i ~/.claude/projects/**/*.jsonl (samma källa som
usage-verktyg i ccusage-familjen läser), summerar tokens per dag och serverar
glance-mönstrets kontrakt på /api/tokens:

    {"v": 2, "dayTokens": ..., "dayTokensPerHour": ..., "daySessions": ...,
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
import base64
import hashlib
import ipaddress
import json
import logging
import math
import os
import queue
import re
import select
import socket
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import weakref
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Probelåset tas med olika systemanrop på olika plattformar; ingen av
# modulerna finns på båda. Importen får inte fälla hela tjänsten — utan lås
# är beteendet som före låset fanns, inte "startar inte alls".
try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
try:
    import msvcrt
except ImportError:  # macOS/Linux
    msvcrt = None

if __package__:
    from .discovery import DiscoveryAdvertiser
    from .agent_status import AgentStatusService
    from .codex_command import resolve_codex_executable
    from .codex_interactions import (
        codex_permission_response,
        codex_question_result,
        normalize_codex_permission,
        normalize_codex_question,
    )
    from .codex_rollout import codex_rollout_rate_limits, observation_timestamp
    from .github_monitor import GitHubMonitor, disabled_snapshot, normalize_repo
    from .interactions import InteractionStore
    from .max_tracker import MaxTrackerStore
    from .publisher import Publisher
    from .quota_cache import CachedQuota, QuotaCache
    from .usage_history import Forecast, UsageHistory
    from .vibepulse_config import (
        ConfigError,
        VibePulseConfig,
        config_lock,
        load_config,
        save_config,
    )
    from . import codex_usage, interactions, value_meter
else:  # direktkörning: python3 tools/tokenserver/tokenserver.py
    from discovery import DiscoveryAdvertiser
    from agent_status import AgentStatusService
    from codex_command import resolve_codex_executable
    from codex_interactions import (
        codex_permission_response,
        codex_question_result,
        normalize_codex_permission,
        normalize_codex_question,
    )
    from codex_rollout import codex_rollout_rate_limits, observation_timestamp
    from github_monitor import GitHubMonitor, disabled_snapshot, normalize_repo
    from interactions import InteractionStore
    from max_tracker import MaxTrackerStore
    from publisher import Publisher
    from quota_cache import CachedQuota, QuotaCache
    from usage_history import Forecast, UsageHistory
    from vibepulse_config import (
        ConfigError,
        VibePulseConfig,
        config_lock,
        load_config,
        save_config,
    )
    import codex_usage
    import interactions
    import value_meter

RECOMPUTE_EVERY_S = 30
LIMITS_EVERY_S = 240  # rate-limit-proben: 15 anrop/h — kontots bucket delas
AUTH_RECOVERY_EVERY_S = 15.0  # lokal tokenkontroll; ingen upstream vid väntan
                      # med Claude Code självt, och kvoten rör sig långsamt;
                      # panelens 30 s-pollar får ändå cachat svar direkt
CLAUDE_CREDENTIAL_WARNING_S = 30 * 60
CLAUDE_PLAN_USAGE_FRESH_S = 20 * 60
CLAUDE_PLAN_USAGE_MAX_BYTES = 2 * 1024 * 1024
HTTP_MAX_WORKERS = 32
JSON_BODY_TIMEOUT_S = 2.0

# Vilka token-källor och systemanrop som finns beror på plattformen, inte på
# konfiguration. Testerna patchar konstanten för att köra Windows-grenarna
# på en Mac.
_IS_WINDOWS = sys.platform == "win32"
_claude_plan_usage_status = "not_checked"


def _state_dir():
    """Tjänstens tillståndskatalog — låset, cachen, historiken, spåraren.

    ``~/Library/Application Support`` är macOS-konventionen; Windows
    motsvarighet är ``%LOCALAPPDATA%``. Sökvägarna FUNGERAR bokstavligt på
    Windows (``Path.home()`` löser ut), men skulle lägga ett ``Library``-träd
    i användarprofilen som ingenting annat på maskinen känner igen.
    """
    if _IS_WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (Path(local_app_data) if local_app_data
                else Path.home() / "AppData" / "Local")
        return base / "VibePulse"
    return Path.home() / "Library" / "Application Support" / "VibePulse"


def _claude_plan_usage_path():
    """Claude Desktops lokala, innehållsfria planhistorik.

    Filen ägs och uppdateras av den officiella klienten. Den innehåller bara
    tid, organisation och procentsatser för femtimmars-/veckofönstret — inga
    promptar, svar, kommandon eller OAuth-hemligheter.
    """
    if _IS_WINDOWS:
        app_data = os.environ.get("APPDATA")
        base = (Path(app_data) if app_data
                else Path.home() / "AppData" / "Roaming")
        return base / "Claude" / "plan-usage-history.json"
    return (Path.home() / "Library" / "Application Support" / "Claude" /
            "plan-usage-history.json")


def _read_claude_plan_usage(path=None, now_ts=None):
    """Returnera senaste strikta, färska lokala usageprovet eller ``None``.

    Det här är en passiv reserv när åtkomsttokenen som tokenservern kan läsa
    har gått ut men Claude Desktop fortfarande har en aktuell usagebild. Hela
    filen är storleksbegränsad och den senaste posten valideras fail-closed;
    rått organisations-id lämnar aldrig funktionen.
    """
    global _claude_plan_usage_status
    usage_path = (_claude_plan_usage_path() if path is None else Path(path))
    current_ts = time.time() if now_ts is None else now_ts
    try:
        size = usage_path.stat().st_size
        if size <= 0 or size > CLAUDE_PLAN_USAGE_MAX_BYTES:
            _claude_plan_usage_status = "invalid_size"
            return None
        payload = json.loads(usage_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        _claude_plan_usage_status = "missing"
        return None
    except (OSError, UnicodeError, json.JSONDecodeError):
        _claude_plan_usage_status = "invalid"
        return None

    if (not isinstance(payload, dict) or payload.get("version") != 2 or
            not isinstance(payload.get("samples"), list) or
            not payload["samples"]):
        _claude_plan_usage_status = "unsupported"
        return None

    samples = payload["samples"]
    latest = samples[-1]
    if not isinstance(latest, dict):
        _claude_plan_usage_status = "invalid"
        return None
    timestamp_ms = latest.get("t")
    org = latest.get("org")
    usage = latest.get("u")
    if (not isinstance(timestamp_ms, int) or isinstance(timestamp_ms, bool) or
            timestamp_ms <= 0 or not isinstance(org, str) or
            not 1 <= len(org.encode("utf-8")) <= 128 or
            not isinstance(usage, dict)):
        _claude_plan_usage_status = "invalid"
        return None

    values = []
    for key in ("fh", "sd"):
        value = usage.get(key)
        if (not isinstance(value, (int, float)) or isinstance(value, bool) or
                not math.isfinite(value) or not 0 <= value <= 100):
            _claude_plan_usage_status = "invalid"
            return None
        values.append(round(float(value), 1))

    observed_at = timestamp_ms / 1000
    age_s = current_ts - observed_at
    if age_s < -60 or age_s > CLAUDE_PLAN_USAGE_FRESH_S:
        _claude_plan_usage_status = "stale"
        return None

    _claude_plan_usage_status = "fresh"
    return {
        "session_pct": values[0],
        "week_pct": values[1],
        "observed_at": int(observed_at),
    }


def _log_dir():
    """Loggkatalogen. macOS har ~/Library/Logs; Windows har ingen egen
    logg-konvention för användartjänster, så loggen bor i tillståndsträdet."""
    return Path.home() / "Library" / "Logs" if not _IS_WINDOWS else (
        _state_dir() / "Logs")

# Diagnostiken går via logging till stderr med tidsstämplar (basicConfig i
# main; launchd samlar bägge strömmarna i loggfilen, se plisten). Regeln är
# ÖVERGÅNGAR, inte tillstånd: en statusändring loggas en gång och sedan är
# det tyst tills läget ändras igen — filen ska vara läsbar över veckor.
log = logging.getLogger("tokenserver")

# Loggfilen under launchd (plistens StandardOut/ErrorPath). ~/Library/Logs
# överlever omstart och syns i Konsol-appen — /tmp gjorde ingetdera. launchd
# har ingen egen rotation, så servern tar den vid start: se
# _maybe_rotate_own_log.
DEFAULT_LOG_PATH = _log_dir() / "torget-tokenserver.log"
_LOG_CAP_BYTES = 5 * 1024 * 1024
_LOG_TAIL_KEEP_BYTES = 256 * 1024


def _maybe_rotate_own_log(path=None, stderr_fd=2):
    """Trunkera loggfilen vid start när den vuxit förbi taket, med svansen
    bevarad i <namn>.old.

    Bara när stderr faktiskt ÄR filen (launchd-fallet): fstat/stat-jämförelsen
    skyddar terminalkörningar från att röra en fil de inte skriver till.
    Trunkering i stället för rename: launchd håller fd:n öppen med O_APPEND,
    så en rename hade bara fått processen att skriva vidare i den flyttade
    filen medan den nya förblev tom.

    Hela läs-kopiera-trunkera-sekvensen hålls under root-loggerns
    handlerlås (RLock — vår egen "roterad"-rad kan fortfarande skrivas), så
    en loggrad från en annan tråd inte kan landa mellan svansläsningen och
    trunkeringen och raderas ur bägge filerna. Råa stderr-skrivningar
    (agent_status-diagnostiken) går utanför låset; det kvarvarande fönstret
    är millisekunder mot en strypt rad per 30 s, en gång per 5 MB."""
    path = Path(path) if path else DEFAULT_LOG_PATH
    try:
        st = path.stat()
    except OSError:
        return False  # ingen fil (terminalkörning, färsk installation)
    handlers = list(logging.getLogger().handlers)
    for handler in handlers:
        handler.acquire()
    try:
        if st.st_size <= _LOG_CAP_BYTES:
            return False
        own = os.fstat(stderr_fd)
        if (own.st_dev, own.st_ino) != (st.st_dev, st.st_ino):
            return False
        with open(path, "rb+") as fh:
            fh.seek(max(0, st.st_size - _LOG_TAIL_KEEP_BYTES))
            tail = fh.read()  # läser till FAKTISKT EOF — även nyare rader
            path.with_name(path.name + ".old").write_bytes(tail)
            fh.truncate(0)
        log.info("loggfilen roterad (%d byte > taket %d; svansen ligger i "
                 "%s.old)", st.st_size, _LOG_CAP_BYTES, path.name)
        return True
    except Exception:
        # Rotering får aldrig fälla tjänsten; att den misslyckades ska synas.
        log.warning("logrotering av %s misslyckades", path, exc_info=True)
        return False
    finally:
        for handler in reversed(handlers):
            handler.release()


_LOG_ROTATE_CHECK_S = 3600.0


def _run_log_rotation_watch(stop_event, interval_s=None):
    """Timvis rotationsvakt: startrotationen räcker inte för en process som
    lever länge — en ihållande felande deltjänst kan annars skriva förbi
    taket tills en orelaterad omstart råkar städa. Samma fstat-vakt som vid
    start, så terminalkörningar förblir orörda; tråden sover resten av
    tiden."""
    interval = _LOG_ROTATE_CHECK_S if interval_s is None else interval_s
    while not stop_event.wait(interval):
        _maybe_rotate_own_log()


def _read_server_rev():
    """Git-revisionen som faktiskt serverar — gör 'fel kod kör' synligt i en
    curl i stället för en timmes processarkeologi."""
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        ).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def _normalized_source_bytes(path):
    """Return UTF-8 source with platform checkout newlines normalized."""
    return path.read_text(encoding="utf-8").encode("utf-8")


def _read_source_fingerprint():
    """Innehållshash av tjänstens källfiler, tagen vid start. Rev räcker
    inte för "kör servern det som ligger här?": en smutsig worktree, eller
    en redigering EFTER att processen startade, delar HEAD med checkouten
    och låter rev-jämförelsen ljuga "aktuell". Röktestet räknar om samma
    hash från disken och jämför. test_* och smoke.py ingår inte — tjänsten
    laddar dem aldrig, och en redigerad smoke ska inte se ut som en
    föråldrad server."""
    try:
        digest = hashlib.sha256()
        base = Path(os.path.dirname(os.path.abspath(__file__)))
        for f in sorted(base.glob("*.py")):
            if f.name.startswith("test_") or f.name == "smoke.py":
                continue
            digest.update(f.name.encode())
            # Text mode normalizes CRLF/LF so one source revision has the
            # same provenance fingerprint on Windows, macOS, and Linux.
            digest.update(_normalized_source_bytes(f))
        return digest.hexdigest()[:12]
    except Exception:
        return "unknown"


_SERVER_REV = _read_server_rev()
_SERVER_SRC = _read_source_fingerprint()
_SERVER_STARTED = datetime.now().astimezone().isoformat(timespec="seconds")
MAX_TRACKER_BACKFILL_TICK_S = 0.5  # samma kadens som agent_status.POLL_S
# Claude bär aldrig fönstrets minuttal i klartext (bara namnen "5h"/"7d") --
# till skillnad från Codex, vars rate-limits-snapshot har window_minutes
# rakt i JSON:en (se _codex_window nedan). Dessa två är plan-kontraktets
# fasta motsvarigheter, klassade enligt samma >600-minutersregel som
# MaxTrackerStore.observe_quota redan använder.
MAX_TRACKER_CLAUDE_SESSION_MINUTES = 300   # 5 timmar
MAX_TRACKER_CLAUDE_WEEK_MINUTES = 10080    # 7 dygn

# (day, ts, tokens, session, key, usd, unpriced) per usage-bearing log row.
# The first five are the minimum the day/month/rate/session aggregates need.
# The last two are the row's list-price value, carried PER ROW rather than as
# a per-file sum, so the (message id, requestId) dedup in _compute covers
# dollars exactly as it covers tokens -- Claude Code writes the same record
# into more than one transcript, and a duplicate must not be counted twice in
# either currency.
_cache_lock = threading.Lock()
_file_cache = {}   # path -> stat, parsed offset, month and compact records

# The value multiple's denominator and price table. Subscription prices are
# not in any API price list and no API reports which plan you are on, so the
# operator states what they pay, per provider: --plan claude=200 --plan
# codex=20. No allowlist of plan names -- Team, Enterprise, annual billing,
# EDU and VAT-inclusive pricing all exist and none of them fit a fixed table.
# --claude-plan/--codex-plan still select the Max Tracker badge, and their
# list prices remain the fallback when nothing is declared.
_claude_plan = None
_codex_plan = None
_plan_costs = {}
_price_table = None
_last_result = None
_last_computed = 0.0
_snapshot_refreshing = False
# Omräkningens hälsa: kraschar _compute serveras förra snapshotet vidare —
# rätt beteende, men det får inte ske TYST (då fryser siffrorna för alltid
# och ser färska ut). failing_since driver usageComputeOk på GET / och
# röktestets FAIL; loggen får övergången plus ett strypt fel.
# None = aldrig loggat. Inte 0.0: time.monotonic() räknar från boot, så på
# en nystartad maskin är "nu - 0.0" MINDRE än strypfönstret och första
# felet skulle sväljas — CI:ns färska VM fällde exakt det.
_compute_failing_since = None
_last_compute_error_logged = None
_history_lock = threading.Lock()
_default_usage_history = None
_quota_cache_lock = threading.Lock()
_default_quota_cache = None
_quota_writer_lock = threading.Lock()
_quota_writers = weakref.WeakKeyDictionary()


def _get_usage_history(path=None):
    global _default_usage_history
    if path is not None:
        return UsageHistory(Path(path))
    with _history_lock:
        if _default_usage_history is None:
            _default_usage_history = UsageHistory(
                _state_dir() / "usage-history.json")
        return _default_usage_history


def _get_quota_cache(path=None):
    global _default_quota_cache
    if path is not None:
        return QuotaCache(Path(path))
    with _quota_cache_lock:
        if _default_quota_cache is None:
            _default_quota_cache = QuotaCache(
                _state_dir() / "quota-cache.json")
        return _default_quota_cache


def _quota_identity(provider, scope, raw_identity=None):
    """Return a local opaque identity without retaining its raw input."""
    stable = "default-v1" if raw_identity is None else str(raw_identity)
    material = f"{provider}\0{scope}\0{stable}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _merge_claude_plan_usage(claude, quota_cache, now_ts, path=None):
    """Låt en färsk officiell lokal veckoprocent slå en äldre OAuthbild.

    Den lokala filen saknar reset-tid och modellpool. Därför får den endast
    komplettera den generella veckan när en fortfarande giltig, tidigare
    autentiserad cachepost bär samma pools reset. Modellkvoten förblir ärligt
    stale tills OAuth-proben återhämtar sig.
    """
    global _claude_plan_usage_status
    local = _read_claude_plan_usage(path=path, now_ts=now_ts)
    if local is None:
        return claude

    existing_at = claude.get("weekObservedAt")
    if (isinstance(existing_at, (int, float)) and
            not isinstance(existing_at, bool) and
            math.isfinite(existing_at) and
            existing_at >= local["observed_at"]):
        _claude_plan_usage_status = "oauth_newer"
        return claude

    cached = quota_cache.latest("claude", "general_weekly", now=now_ts)
    if cached is None or cached.reset_at <= now_ts:
        _claude_plan_usage_status = "fresh_without_reset"
        return claude

    merged = dict(claude)
    merged.update({
        "weekPct": local["week_pct"],
        "weekResetAt": cached.reset_at,
        "weekResetMin": max(0, int(round((cached.reset_at - now_ts) / 60))),
        "weekObservedAt": local["observed_at"],
        "weekIdentity": cached.identity,
    })
    _claude_plan_usage_status = "fresh_applied"
    return merged


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
                day = ts.strftime("%Y-%m-%d")
                usd, unpriced = value_meter.price_usage(
                    (entry.get("message") or {}).get("model"), usage,
                    table=_price_table)
                records.append((
                    day,
                    ts.timestamp(),
                    tokens,
                    entry.get("sessionId") or str(path),
                    key,
                    usd,
                    unpriced,
                ))
    except OSError:
        pass  # borttagen under läsning — nästa skanning ser det
    return records, parsed_until


def _observe_claude_volume(store, records):
    for day, _ts, tokens, _session, _key, _usd, _unpriced in records:
        store.observe_volume("claude", day, tokens)


def _compute(projects_dir: Path, max_tracker_store=None):
    now = datetime.now().astimezone()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    month_key = month_start.strftime("%Y-%m")
    today = now.strftime("%Y-%m-%d")
    hour_ago = now.timestamp() - 3600

    live_paths = set()
    volume_observed = False
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
            if max_tracker_store is not None and new_records:
                _observe_claude_volume(max_tracker_store, new_records)
                volume_observed = True
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
            if max_tracker_store is not None and records:
                _observe_claude_volume(max_tracker_store, records)
                volume_observed = True
    for stale in set(_file_cache) - live_paths:
        del _file_cache[stale]
    if volume_observed:
        _mark_max_tracker_dirty(max_tracker_store)

    day_tokens = 0
    month_tokens = 0
    hour_tokens = 0
    month_value_usd = 0.0
    month_priced_tokens = 0
    month_unpriced_tokens = 0
    day_sessions = set()
    seen = set()
    for entry in _file_cache.values():
        for day, ts, tokens, session, key, usd, unpriced in entry["records"]:
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            month_tokens += tokens
            month_value_usd += usd
            if unpriced:
                month_unpriced_tokens += unpriced
            else:
                month_priced_tokens += tokens
            if day == today:
                day_tokens += tokens
                day_sessions.add(session)
            if ts >= hour_ago:
                hour_tokens += tokens

    # Codex contributes to the same month total. Its rollout logs are a
    # separate tree with a separate scan; a machine without ~/.codex simply
    # returns zeros.
    codex_usd, codex_priced, codex_unpriced = codex_usage.month_value(
        now=now, table=_price_table)

    return {
        "v": 1,
        "dayTokens": day_tokens,
        "dayTokensPerHour": hour_tokens,  # senaste timmen = takt per timme
        "daySessions": len(day_sessions),
        "monthTokens": month_tokens,
        # Additive key: tokens_parse.c:219 skips unknown top-level keys, so
        # already-flashed screens ignore it instead of failing to parse.
        "value": value_meter.build_payload(
            month_value_usd + codex_usd,
            month_unpriced_tokens + codex_unpriced,
            month_priced_tokens + codex_priced,
            claude_plan=_claude_plan, codex_plan=_codex_plan,
            plan_costs=_plan_costs, table=_price_table,
            claude_usd=month_value_usd, codex_usd=codex_usd),
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
_probe_cooldown_until = 0.0
_probe_failure_streak = 0
_probe_status_logged = None  # senast loggade status — övergångar loggas, tillstånd inte
# Content-free credential readiness captured on the probe thread. GET / must
# never reread Keychain synchronously: startup health has a sub-second budget.
_claude_credential = {"status": "unknown"}
# Döda tokens (värde → orsak): en kandidat som fått 401/403 skickas ALDRIG
# igen. Det var mönstret bakom 429-straffrutan: nyckelringstokenen dog på
# natten och Desktops frusna processtoken hamrade API:t varje probecykel i
# timmar (loggen 2026-08-14 03:51–10:45 visar sex straffrundor i rad). En
# förnyad token har ett NYTT värde och provas därmed automatiskt igen;
# ordboken kapas vid 8 poster så den aldrig kan växa fritt.
_dead_tokens = {}


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


def _read_keychain_oauth():
    """Nyckelringsposten som ``(token, expires_at_ms)`` — det ``/login`` skrev."""
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


def _credentials_file_path():
    """Windows' credential store: ``%USERPROFILE%\\.claude\\.credentials.json``.

    Claude Code has no keychain integration on Windows, so ``claude login``
    writes the same ``{"claudeAiOauth": {...}}`` shape the macOS keychain
    holds to a plain file instead. ``Path.home()`` resolves to
    ``%USERPROFILE%`` there, so one expression covers both.
    """
    return Path.home() / ".claude" / ".credentials.json"


def _read_credentials_file_oauth():
    """The Windows credential file as ``(token, expires_at_ms)``.

    Same record ``/login`` writes, same field names as the keychain — only
    the store differs. A missing or malformed file is not an error worth
    logging: it just means this host has no credential file, which is the
    normal case everywhere except Windows.
    """
    try:
        raw = _credentials_file_path().read_text(encoding="utf-8")
        oauth = json.loads(raw).get("claudeAiOauth") or {}
        return oauth.get("accessToken"), oauth.get("expiresAt")
    except Exception:
        return None, None


def _read_oauth_candidates():
    """Distinct token candidates as ``[(token, expires_at_ms), ...]``.

    Which sources exist is a property of the platform. On Windows there is
    no keychain and no bundled Claude Desktop process to read an injected
    token from, so the credential file ``/login`` writes is the only source
    — asking ``pgrep``/``security`` there would only spawn doomed processes
    every probe cycle.

    On macOS both sources are live. Claude Desktop's injected process token
    is listed first (Desktop refreshes OAuth itself while the keychain
    record can lag), but ``ps eww`` shows the environment as of process
    launch: a Desktop child that outlives its token keeps serving the
    frozen, expired value even after a fresh ``/login`` has updated the
    keychain. Neither source is reliably the freshest, so the probe must try
    them in order rather than trust the first.
    """
    if _IS_WINDOWS:
        file_token, expires_at = _read_credentials_file_oauth()
        return [(file_token, expires_at)] if file_token else []

    candidates = []
    process_token = _read_process_oauth_token()
    if process_token:
        candidates.append((process_token, None))
    keychain_token, expires_at = _read_keychain_oauth()
    if keychain_token and keychain_token != process_token:
        candidates.append((keychain_token, expires_at))
    return candidates


def _oauth_credential_snapshot(candidates, now_s=None):
    """Return saved-credential expiry readiness without exposing tokens."""
    if not candidates:
        return {"status": "unavailable"}
    expiries = []
    for _token, raw_expiry in candidates:
        try:
            expiry_s = float(raw_expiry) / 1000
        except (TypeError, ValueError, OverflowError):
            continue
        if math.isfinite(expiry_s) and expiry_s > 0:
            expiries.append(expiry_s)
    if not expiries:
        return {"status": "unknown"}

    remaining_s = max(expiries) - (time.time() if now_s is None else now_s)
    if remaining_s <= 0:
        return {"status": "expired", "expiresInMin": 0}
    remaining_min = max(1, int(math.ceil(remaining_s / 60)))
    status = ("expiring" if remaining_s <= CLAUDE_CREDENTIAL_WARNING_S
              else "ready")
    return {"status": status, "expiresInMin": remaining_min}


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


def _parse_usage_limits(body, now_ts):
    """Map the read-only OAuth usage contract without inventing pool names."""
    if not isinstance(body, dict) or not isinstance(body.get("limits"), list):
        return {}
    found = {}
    model_labels = {
        "fable": "FABLE · WEEK",
        "opus": "OPUS · WEEK",
        "sonnet": "SONNET · WEEK",
    }
    for limit in body["limits"]:
        if not isinstance(limit, dict):
            continue
        kind = limit.get("kind")
        pct = limit.get("percent")
        reset_at = _parse_reset_at(limit.get("resets_at"), now_ts)
        if (not isinstance(pct, (int, float)) or isinstance(pct, bool) or
                not math.isfinite(pct) or not 0 <= pct <= 100 or
                reset_at is None or reset_at <= now_ts):
            continue
        if kind == "session":
            prefix = "session"
        elif kind == "weekly_all":
            prefix = "week"
        elif kind == "weekly_scoped" and (limit.get("is_active") is True or
                                          (isinstance(pct, (int, float)) and
                                           pct > 0)):
            # is_active betyder "just nu BINDANDE gräns" (5-timmarsfönstret
            # bär oftast den flaggan), INTE "poolen finns". Verifierat mot
            # live-svaret 2026-08-14: Fable veckan låg på 11 % med
            # is_active=false och försvann från glaset — verklig förbrukning
            # ska alltid visas. Bara en orörd pool (0 % och inaktiv) lämnas
            # onämnd, så en aldrig använd modell inte tar plats.
            scope = limit.get("scope")
            model = scope.get("model") if isinstance(scope, dict) else None
            display = (model.get("display_name")
                       if isinstance(model, dict) else None)
            normalized = display.strip().lower() if isinstance(display, str) else ""
            if normalized not in model_labels:
                continue
            prefix = "model"
            found["modelLabel"] = model_labels[normalized]
        else:
            continue
        found[f"{prefix}Pct"] = round(float(pct), 1)
        found[f"{prefix}ResetAt"] = reset_at
        found[f"{prefix}ResetMin"] = max(
            0, int(round((reset_at - now_ts) / 60)))
        if prefix in {"week", "model"}:
            scope_name = ("general_weekly" if prefix == "week"
                          else "model_weekly")
            found[f"{prefix}ObservedAt"] = int(now_ts)
            found[f"{prefix}Identity"] = _quota_identity(
                "claude", scope_name)
    return found


def _usage_request(token):
    return urllib.request.Request(
        "https://api.anthropic.com/api/oauth/usage",
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "User-Agent": "claude-cli/2.1.227 (external, cli)",
        },
    )


# Maskinvid probelås: OAVSETT hur många tokenservrar som råkar köra (launchd,
# en worktree, en manuell start på annan port) får högst EN prata med
# api.anthropic.com. Båda 429-incidenterna 2026-08-13/14 var i grunden
# överflödig upstream-trafik — den här grinden gör varianten "en instans
# till" strukturellt ofarlig i stället för att lita på att ingen startar en.
_PROBE_LOCK_PATH = _state_dir() / "claude-probe.lock"

# Straffrutan ÖVERLEVER omstarter: cooldownen var ren minnesstat, så varje
# serveromstart glömde pågående backoff och petade direkt på den heta
# bucketen igen (sett två gånger 2026-08-14, båda självförvållade). Filen
# bor bredvid probelåset och läses lat vid första probecykeln.
_PROBE_STATE_PATH = _state_dir() / "claude-probe-state.json"
_probe_state_loaded = False


def _load_probe_state():
    global _probe_state_loaded, _probe_cooldown_until, _probe_status
    if _probe_state_loaded:
        return
    _probe_state_loaded = True
    try:
        data = json.loads(_PROBE_STATE_PATH.read_text(encoding="utf-8"))
        until = float(data.get("cooldown_until", 0.0))
    except (OSError, ValueError, TypeError):
        return
    if math.isfinite(until) and until > time.time():
        _probe_cooldown_until = until
        _probe_status = (f"usage_http_429 + backoff_until_"
                         f"{datetime.fromtimestamp(until):%H:%M} (persisted)")


def _save_probe_state():
    try:
        _PROBE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PROBE_STATE_PATH.write_text(
            json.dumps({"cooldown_until": _probe_cooldown_until}),
            encoding="utf-8")
    except OSError:
        pass  # utan disk är beteendet som förr: bättre än att krascha


# ---------------------------------------------------------------------------
# OTA-annonsen: senaste bygget på Macen, läst ur torget.bin:s inbäddade
# appbeskrivning — samma sanning som enheten själv rapporterar om sin
# körande version. Enheten jämför och visar UPDATE READY-notisen vid
# skillnad (beslut 2026-08-14). Bara version och byggtid annonseras,
# aldrig sökvägar eller innehåll.

_OTA_BUILD_ROOT = Path(__file__).resolve().parents[2]
_ota_desc_cache = {}  # path -> (mtime, version|None)


def _read_app_desc_version(path):
    """esp_app_desc_t bor på offset 32 (imageheader 24 B + segmentheader
    8 B): magic_word, secure_version, reserv[2], version[32], project[32].
    Fel magi eller fel projekt ⇒ None — hellre tyst än fel avbild."""
    try:
        with open(path, "rb") as handle:
            handle.seek(32)
            desc = handle.read(80)
    except OSError:
        return None
    if len(desc) < 80:
        return None
    if desc[:4] != b"\x32\x54\xcd\xab":  # ESP_APP_DESC_MAGIC_WORD, LE
        return None
    project = desc[48:80].split(b"\x00")[0].decode("utf-8", "replace")
    if project != "torget":
        return None
    version = desc[16:48].split(b"\x00")[0].decode("utf-8", "replace")
    return version or None


def _ota_available_version():
    newest = None  # (mtime, version)
    for path in _OTA_BUILD_ROOT.glob("build*/torget.bin"):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        cached = _ota_desc_cache.get(str(path))
        if cached and cached[0] == mtime:
            version = cached[1]
        else:
            version = _read_app_desc_version(path)
            _ota_desc_cache[str(path)] = (mtime, version)
        if version and (newest is None or mtime > newest[0]):
            newest = (mtime, version)
    return newest[1] if newest else None


def _hold_probe_lock():
    """Icke-blockerande exklusivt lås; returnerar filobjektet eller None.

    ``flock`` på macOS/Linux, ``msvcrt.locking`` på Windows — samma grind,
    olika systemanrop. Båda släpps när filen stängs.
    """
    try:
        _PROBE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        handle = open(_PROBE_LOCK_PATH, "w")
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            # LK_NBLCK låser ett byte utan att blockera och höjer OSError om
            # en annan instans redan äger det. Filen måste ha en byte att
            # låsa, annars lyckas anropet utan att grinden betyder något.
            handle.write("1")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return handle
    except OSError:
        try:
            handle.close()
        except UnboundLocalError:
            pass
        return None


def _probe_limits():
    """Ett minimalt API-anrop; returnerar {sessionPct, sessionResetMin,
    weekPct, weekResetMin} eller None om något saknas på vägen."""
    global _probe_status, _probe_headers, _probe_unknown_buckets, \
        _probe_cooldown_until
    _load_probe_state()
    if time.time() < _probe_cooldown_until:
        # I nedkylning efter 429 — statusen står kvar på backoff-strängen.
        return None
    lock = _hold_probe_lock()
    if lock is None:
        # En annan instans äger upstream-trafiken just nu. Ingen nätaktivitet
        # härifrån — den andra instansens svar fyller ändå enhetens behov.
        _probe_status = "probe_held_by_other_instance"
        return None
    try:
        return _probe_limits_locked()
    finally:
        lock.close()  # stänger filen = släpper flocken


def _probe_limits_locked():
    global _probe_status, _probe_headers, _probe_unknown_buckets, \
        _probe_cooldown_until, _claude_credential
    candidates = _read_oauth_candidates()
    _claude_credential = _oauth_credential_snapshot(candidates)
    if not candidates:
        _probe_status = "no_claude_oauth_token"
        return None

    token = None
    for candidate, expires_at in candidates:
        if candidate in _dead_tokens:
            # Värdet är redan avvisat av API:t — vänta på ett nytt i stället
            # för att elda på 429-straffrutan med ett känt dött token.
            _probe_status = "token_dead_awaiting_refresh"
            continue
        if expires_at and expires_at / 1000 < time.time():
            # Tokenen har gått ut; Claude Code förnyar den i nyckelringen
            # nästa gång den pratar med API:t — vänta och läs om.
            _probe_status = (f"token_expired_"
                             f"{datetime.fromtimestamp(expires_at / 1000):%H:%M}")
            continue

        # The read-only usage contract is the only observed source that names
        # an active scoped weekly pool (for example Fable) and its reset
        # explicitly.
        try:
            with urllib.request.urlopen(
                    _usage_request(candidate), timeout=15) as resp:
                usage = json.load(resp)
        except urllib.error.HTTPError as error:
            _probe_status = f"usage_http_{error.code}"
            if error.code == 429:
                # Rate-limited: varje ytterligare anrop förlänger straffet.
                # Avbryt hela cykeln — ingen andra källa, ingen header-probe
                # — och vila minst tio minuter (mer om Retry-After kräver).
                retry_after = 0
                try:
                    retry_after = int((error.headers or {}).get(
                        "Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0
                _probe_cooldown_until = time.time() + max(retry_after, 600)
                _probe_status = (
                    f"usage_http_429 + backoff_until_"
                    f"{datetime.fromtimestamp(_probe_cooldown_until):%H:%M}")
                _save_probe_state()  # en omstart får inte glömma straffet
                return None
            if error.code in (401, 403):
                # Avvisad token säger inget om nästa källa — prova den innan
                # vi ger upp. Men skicka ALDRIG samma värde igen: det är dött
                # tills källan levererar ett nytt (ett felmarkerat värde
                # självläker på samma sätt, nästa förnyelse byter strängen).
                _dead_tokens[candidate] = f"http_{error.code}"
                while len(_dead_tokens) > 8:
                    _dead_tokens.pop(next(iter(_dead_tokens)))
                continue
            token = candidate
            break
        except Exception as error:
            _probe_status = f"usage_request_failed: {type(error).__name__}"
            token = candidate
            break
        else:
            found = _parse_usage_limits(usage, time.time())
            # Kräv inte sessionPct: utan aktivt 5-timmarsfönster (bara mobil-
            # eller molnarbete) rapporterar API:t sessionsraden med passerad
            # reset, och parsern hoppar korrekt över den. Veckosiffrorna är
            # fortfarande giltiga — kasta inte bort dem.
            if found:
                _probe_status = "usage_http_200 + ok"
                _probe_headers = []
                _probe_unknown_buckets = []
                return found
            _probe_status = "usage_http_200 + no_mapped_limits"
            token = candidate
            break

    if token is None:
        # Alla källor avvisade eller utgångna — header-proben med samma
        # tokens vore samma svar till högre kostnad.
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
    # Header-proben får aldrig skriva över usage-utfallet — det var så en
    # felmappning maskerades som "http_401" en hel kväll.
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        _probe_status += f"; fallback_http_{e.code}"
        headers = dict(e.headers) if e.headers else {}
    except Exception as e:
        _probe_status += f"; fallback_failed: {type(e).__name__}"
        return None
    else:
        _probe_status += "; fallback_http_200"

    now_ts = time.time()
    # Diagnostik vid första proben: headernamnen är hämtade ur Clawdmeters
    # beskrivning, inte ur egen observation — loggen är facit om de skiljer.
    global _headers_logged
    if not _headers_logged:
        _headers_logged = True
        for name in sorted(headers):
            if "ratelimit" in name.lower():
                log.info("ratelimit-header: %s", name)
    # Tre fönster, samma som Claudes egen usage-panel: 5-timmars, veckan
    # (alla modeller) och veckan för tyngsta modellen (Fable/Opus). Fönster-
    # namnet i headern varierar ("5h", "7d", "7d_opus", ...) — mappa på
    # innehåll, inte exakt namn.
    found = _parse_limit_headers(headers, now_ts)

    _probe_headers = sorted(
        n for n in headers if "ratelimit" in n.lower())
    _probe_unknown_buckets = found.pop("unknownBuckets", [])
    if not found:
        _probe_status += " + no_mapped_headers"
        return None
    _probe_status += " + ok"
    return found


def _probe_interval_s():
    """Backa av vid upprepade misslyckanden: 120 → 240 → 480 s (tak).

    En död token fick tidigare hamra API:t varannan minut i timmar — det
    mönstret utlöste en 429-straffruta. Lyckad probe återställer takten.
    """
    if _probe_status.startswith((
            "no_claude_oauth_token",
            "token_expired_",
            "token_dead_awaiting_refresh")):
        # De här lägena stannar i _probe_limits_locked innan urlopen. En kort
        # kontroll läser bara om den lokala nyckelringen/credentials-filen och
        # upptäcker snabbt när den officiella Claude-klienten bytt token.
        return AUTH_RECOVERY_EVERY_S
    return LIMITS_EVERY_S * (2 ** min(_probe_failure_streak, 2))


def _refresh_limits():
    global _last_limits, _last_probed, _limits_refreshing, \
        _probe_failure_streak, _probe_status_logged, _probe_status
    try:
        refreshed = _probe_limits()
    except Exception as e:
        refreshed = None
        # Kraschar proben INNAN den hunnit sätta status skulle den gamla
        # strängen stå kvar — i värsta fall "usage_http_200 + ok" medan
        # värdena försvinner. En krasch är en bugg (ingen vanlig felväg):
        # sätt en egen status och logga traceback en gång per episod.
        crashed = f"probe_crashed: {type(e).__name__}"
        if _probe_status != crashed:
            log.exception("claude-proben kraschade (status var %s)",
                          _probe_status)
        _probe_status = crashed
    # Övergångsloggen: 401 som dyker upp, 429-backoff, återhämtningen.
    # Läses här på probetråden (enda skrivaren), efter att statussträngen
    # är färdigbyggd — samma läge står stilla utan att skriva en rad till.
    if _probe_status != _probe_status_logged:
        log.info("claude-probe: %s -> %s",
                 _probe_status_logged or "start", _probe_status)
        _probe_status_logged = _probe_status
    with _limits_lock:
        _probe_failure_streak = 0 if refreshed else _probe_failure_streak + 1
        _last_limits = refreshed
        _last_probed = time.monotonic()
        _limits_refreshing = False


def get_limits():
    global _limits_refreshing
    with _limits_lock:
        if ((_last_probed == 0.0 or
             time.monotonic() - _last_probed > _probe_interval_s()) and
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
CODEX_APP_SERVER_TIMEOUT_S = 15
CODEX_LIMIT_SCAN_BYTES = 1024 * 1024
CODEX_WEEK_MINUTES = 10080
_codex_limits_lock = threading.Lock()
_last_codex_limits = None
_last_codex_read = 0.0
_codex_refreshing = False


# Moved to codex_rollout.py (a leaf module max_tracker.py's backfill can
# import too, without the two modules ever importing each other): the
# envelope-acceptance rule and timestamp parsing are re-exported here under
# their original private names so every existing call site is unchanged.
_codex_rollout_rate_limits = codex_rollout_rate_limits
_observation_timestamp = observation_timestamp


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
        if window_minutes != CODEX_WEEK_MINUTES:
            continue
        reset_at = int(rate_limits[key]["resets_at"])
        raw_identity = rate_limits.get("limit_id")
        return {
            "pct": pct,
            "reset_at": reset_at,
            "observed_at": int(observed_at),
            "identity": _quota_identity(
                "codex", "general_weekly", raw_identity),
            "window_minutes": window_minutes,
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
                    "observed_at": observed_at,
                    "window_minutes": window_minutes}
    return None


def _camel_codex_window(window):
    """Convert app-server camelCase windows to the rollout representation."""
    if not isinstance(window, dict):
        return None
    return {
        "used_percent": window.get("usedPercent"),
        "window_minutes": window.get("windowDurationMins"),
        "resets_at": window.get("resetsAt"),
    }


def _parse_codex_rate_limits_response(body, observed_at, now_ts):
    """Map account/rateLimits/read, the same snapshot Codex UI displays."""
    if not isinstance(body, dict):
        return {}
    buckets = body.get("rateLimitsByLimitId")
    rate_limits = (buckets.get("codex") if isinstance(buckets, dict)
                   else None)
    if not isinstance(rate_limits, dict):
        rate_limits = body.get("rateLimits")
    if not isinstance(rate_limits, dict):
        return {}
    normalized = {
        "limit_id": rate_limits.get("limitId"),
        "limit_name": rate_limits.get("limitName"),
        "primary": _camel_codex_window(rate_limits.get("primary")),
        "secondary": _camel_codex_window(rate_limits.get("secondary")),
    }
    weekly = _codex_general_observation(
        normalized, observed_at=observed_at, now_ts=now_ts)
    if weekly is None:
        return {}
    out = {
        "codexWeekPct": weekly["pct"],
        "codexWeekResetAt": weekly["reset_at"],
        "codexWeekObservedAt": weekly["observed_at"],
        "codexWeekIdentity": weekly["identity"],
        "codexWeekStale": False,
        "codexWeekWindowMinutes": weekly["window_minutes"],
    }
    session = _codex_session_observation(
        normalized, observed_at=observed_at, now_ts=now_ts)
    if session is not None:
        out.update({
            "codexSessionPct": session["pct"],
            "codexSessionResetMin": session["reset_min"],
            "codexSessionWindowMinutes": session["window_minutes"],
        })
    return out


def _codex_app_server_command():
    return resolve_codex_executable()


def _pump_lines(stream):
    """Rader från ``stream`` i en kö, lästa av en daemon-tråd.

    ``select`` dög inte: på Windows tar ``select()`` bara sockets, aldrig
    pipes, så app-server-läsningen kastade där i stället för att hämta
    Codex-kvoten. En tråd som blockerar i ``readline`` ger samma
    icke-blockerande läsning på alla plattformar.

    Tråden är daemon och äger inget: dör app-servern — eller dödar vi den i
    ``finally`` — returnerar ``readline`` tomt och tråden tar slut av sig
    själv. ``None`` i kön är EOF-vakten, så läsaren slipper vänta ut hela
    sin deadline när strömmen redan är stängd.
    """
    lines = queue.Queue()

    def pump():
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                lines.put(line)
        except (OSError, ValueError):
            pass  # stängd ström under nedstängning är väntat, inte ett fel
        finally:
            lines.put(None)

    threading.Thread(target=pump, daemon=True,
                     name="codex-app-server-reader").start()
    return lines


def _read_codex_app_server_limits(timeout_s=CODEX_APP_SERVER_TIMEOUT_S):
    """Read Codex's current quota snapshot through its local app protocol."""
    executable = _codex_app_server_command()
    if executable is None:
        return {}
    if isinstance(executable, (str, os.PathLike)):
        command = [executable]
    elif isinstance(executable, (list, tuple)):
        command = list(executable)
    else:
        return {}
    if not command or not all(isinstance(part, (str, os.PathLike))
                              for part in command):
        return {}
    process = None
    try:
        process = subprocess.Popen(
            command + ["app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, bufsize=1)

        def send(message):
            process.stdin.write(json.dumps(message) + "\n")
            process.stdin.flush()

        send({
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "vibepulse", "version": "1"},
                "capabilities": {},
            },
        })
        lines = _pump_lines(process.stdout)
        requested = False
        deadline = time.monotonic() + timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                line = lines.get(timeout=min(0.25, remaining))
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:  # EOF-vakten: strömmen är slut
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == 1 and not requested:
                send({"method": "initialized", "params": {}})
                send({"id": 2, "method": "account/rateLimits/read"})
                requested = True
            elif message.get("id") == 2:
                return _parse_codex_rate_limits_response(
                    message.get("result"), observed_at=int(time.time()),
                    now_ts=time.time())
    except (OSError, ValueError, BrokenPipeError):
        return {}
    finally:
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=1)
            # Rören stängs uttryckligen, i den här ordningen: processen är
            # redan död, så läsartråden har lämnat readline och kan inte
            # väckas mitt i en stängd ström. Utan det här hängde tre
            # deskriptorer per cykel på GC:n — en gång var 30:e sekund,
            # dygnet runt, i en tjänst som aldrig startar om.
            for pipe in (process.stdin, process.stdout, process.stderr):
                if pipe is not None:
                    try:
                        pipe.close()
                    except OSError:
                        pass
    return {}


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
            "codexWeekWindowMinutes": weekly["window_minutes"],
        })
    if session_candidates:
        session = max(session_candidates, key=lambda item: item["observed_at"])
        out.update({
            "codexSessionPct": session["pct"],
            "codexSessionResetMin": session["reset_min"],
            "codexSessionWindowMinutes": session["window_minutes"],
        })
    return out


def _refresh_codex_limits():
    global _last_codex_limits, _last_codex_read, _codex_refreshing
    try:
        refreshed = _read_codex_app_server_limits(
            timeout_s=CODEX_APP_SERVER_TIMEOUT_S)
        if not refreshed:
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


def _quota_record_key(record):
    return record.provider, record.scope, record.identity


def _quota_cache_writer(cache):
    """Drain changed records through one background writer per cache."""
    while True:
        with _quota_writer_lock:
            state = _quota_writers.get(cache)
            if state is None or not state["queued"]:
                if state is not None:
                    state["running"] = False
                return
            key = next(iter(state["queued"]))
            record = state["queued"].pop(key)
            state["inflight"][key] = record
        try:
            persisted = cache.put(record)
        except Exception:
            persisted = False
        with _quota_writer_lock:
            state = _quota_writers.get(cache)
            if state is None:
                continue
            if state["inflight"].get(key) == record:
                del state["inflight"][key]
            if persisted:
                state["persisted"][key] = record


def _persist_quota_records_async(cache, records):
    """Queue changed live observations without blocking the serving thread."""
    records = tuple(record for record in records if record is not None)
    if not records:
        return
    start_writer = False
    with _quota_writer_lock:
        state = _quota_writers.get(cache)
        if state is None:
            state = {
                "queued": {},
                "inflight": {},
                "persisted": {},
                "running": False,
            }
            _quota_writers[cache] = state
        for record in records:
            key = _quota_record_key(record)
            if (state["persisted"].get(key) == record or
                    state["inflight"].get(key) == record or
                    state["queued"].get(key) == record):
                continue
            state["queued"][key] = record
        if state["queued"] and not state["running"]:
            state["running"] = True
            start_writer = True
    if start_writer:
        threading.Thread(
            target=_quota_cache_writer,
            args=(cache,),
            name="quota-cache-writer",
            daemon=True,
        ).start()


# MaxTrackerStore.save() rewrites every provider-day it knows about (the
# quota cache above persists one small record at a time instead), so the
# analogous "atomic save" trigger here is a dirty flag drained by a single
# background writer -- mirroring _persist_quota_records_async's shape
# (queue off the calling thread, coalesce a burst into one trailing write)
# without a per-record dedup this store has no notion of.
_max_tracker_writer_lock = threading.Lock()
_max_tracker_dirty = False
_max_tracker_writer_running = False
_ERROR_LOG_THROTTLE_S = 300.0  # ihållande fel: en loggrad per 5 min räcker
_last_save_error_logged = None  # None = aldrig loggat (0.0 sväljer första
                                # felet på en nystartad maskin — monotonic
                                # räknar från boot)


def _max_tracker_writer(store):
    global _max_tracker_dirty, _max_tracker_writer_running, \
        _last_save_error_logged
    while True:
        with _max_tracker_writer_lock:
            if not _max_tracker_dirty:
                _max_tracker_writer_running = False
                return
            _max_tracker_dirty = False
        try:
            store.save()
            if _last_save_error_logged is not None:
                # Lyckad skrivning stänger felepisoden: logga slutet och
                # nollställ strypningen, så nästa fel (en NY episod) loggar
                # direkt i stället för att ärva gamla fönstret.
                log.info("max-tracker: save lyckades igen")
                _last_save_error_logged = None
        except Exception:
            # En misslyckad skrivning får inte tappa dirty-signalen: markera
            # om och avsluta, så NÄSTA observation (eller slutflushen)
            # försöker igen — ingen het loop, ingen tyst dataförlust.
            # Loggen är strypt: ett trasigt skrivmål ska inte fylla filen.
            now = time.monotonic()
            if (_last_save_error_logged is None or
                    now - _last_save_error_logged >= _ERROR_LOG_THROTTLE_S):
                _last_save_error_logged = now
                log.exception(
                    "max-tracker: save misslyckades — observationerna står "
                    "kvar i minnet och nästa försök kommer")
            with _max_tracker_writer_lock:
                _max_tracker_dirty = True
                _max_tracker_writer_running = False
            return


def _mark_max_tracker_dirty(store):
    """Queue an atomic MaxTrackerStore.save() off the calling thread."""
    global _max_tracker_dirty, _max_tracker_writer_running
    start_writer = False
    with _max_tracker_writer_lock:
        _max_tracker_dirty = True
        if not _max_tracker_writer_running:
            _max_tracker_writer_running = True
            start_writer = True
    if start_writer:
        threading.Thread(
            target=_max_tracker_writer,
            args=(store,),
            name="max-tracker-writer",
            daemon=True,
        ).start()


def _valid_window_minutes(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


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
        return {
            "pct": round(float(pct), 1),
            "reset_at": int(reset_at),
            "observed_at": int(observed_at),
            "label": record.label,
            "stale": False,
            "live": True,
            "cache_record": record,
        }
    cached = quota_cache.latest(provider, scope, now=now_ts)
    if cached is not None:
        return {
            "pct": round(float(cached.pct), 1),
            "reset_at": cached.reset_at,
            "observed_at": cached.observed_at,
            "label": cached.label,
            "stale": True,
            "live": False,
            "cache_record": None,
        }
    return {"pct": None, "reset_at": None, "label": None,
            "stale": False, "live": False, "cache_record": None}


def _add_forecast(result, prefix, forecast):
    result[f"{prefix}ForecastState"] = forecast.state
    result[f"{prefix}ForecastPctAtReset"] = forecast.pct_at_reset
    result[f"{prefix}ForecastPaceFactor"] = forecast.pace_factor
    result[f"{prefix}ForecastAt"] = forecast.exhausts_at
    result[f"{prefix}ForecastOffsetMin"] = forecast.offset_minutes


def _refresh_usage_totals(projects_dir, max_tracker_store=None):
    global _last_result, _last_computed, _snapshot_refreshing, \
        _compute_failing_since, _last_compute_error_logged
    try:
        refreshed = _compute(projects_dir, max_tracker_store)
    except Exception:
        refreshed = None
        now = time.monotonic()
        if _compute_failing_since is None:
            _compute_failing_since = now
        if (_last_compute_error_logged is None or
                now - _last_compute_error_logged >= _ERROR_LOG_THROTTLE_S):
            _last_compute_error_logged = now
            log.exception("usage-omräkningen kraschade — /api/tokens "
                          "serverar frysta siffror tills den lyckas igen")
    else:
        if _compute_failing_since is not None:
            log.info("usage-omräkningen frisk igen efter %.0f s",
                     time.monotonic() - _compute_failing_since)
            _compute_failing_since = None
            # Återhämtningen stänger episoden: nästa fel är en NY episod
            # och ska logga direkt, inte ärva gamla strypfönstret.
            _last_compute_error_logged = None
    with _cache_lock:
        if refreshed is not None:
            _last_result = refreshed
        _last_computed = time.monotonic()
        _snapshot_refreshing = False


def get_snapshot(projects_dir: Path, history=None, now_ts=None,
                 quota_cache=None, max_tracker_store=None):
    """Build the /api/tokens v2 payload.

    ``max_tracker_store`` is the Max Tracker live-rollup hook: omitted
    (``None``, the default), every Max Tracker call below is a no-op, which
    keeps every existing caller -- and every test that predates Max Tracker
    -- byte-identical. Only Handler.do_GET passes the server's real store,
    so live percentages/volume only ever reach the on-disk history for
    actual requests, never for a caller that didn't ask for it.
    """
    global _last_result, _last_computed, _snapshot_refreshing
    with _cache_lock:
        if _last_result is None:
            _last_result = _compute(projects_dir, max_tracker_store)
            _last_computed = time.monotonic()
        elif (time.monotonic() - _last_computed > RECOMPUTE_EVERY_S and
              not _snapshot_refreshing):
            _snapshot_refreshing = True
            threading.Thread(
                target=_refresh_usage_totals,
                args=(projects_dir, max_tracker_store),
                name="usage-total-refresh",
                daemon=True,
            ).start()
        result = dict(_last_result)

    # null = ärlig frånvaro (nyckelring/probe/loggar otillgängliga) — skärmen
    # visar streck, aldrig hittade procent. Samma regel som sharePct.
    current_ts = time.time() if now_ts is None else now_ts
    usage_history = _get_usage_history() if history is None else history
    cache = _get_quota_cache() if quota_cache is None else quota_cache
    claude = _merge_claude_plan_usage(
        get_limits() or {}, cache, current_ts)
    codex = _read_codex_limits()

    session_pct = claude.get("sessionPct")
    session_reset_at = claude.get("sessionResetAt")
    session_reset_min = _reset_minutes(session_reset_at, current_ts)
    if session_pct is None or session_reset_min is None:
        session_pct = None
        session_reset_at = None
        session_reset_min = None
    result["claudeSessionPct"] = session_pct
    result["claudeSessionResetMin"] = session_reset_min
    # Never disk-cached (a Claude session window resets every 5h, so a
    # fallback would be meaningless) -- a non-None reading here is always a
    # genuinely fresh probe result, the honest gate Task 6 requires before
    # anything reaches Max Tracker's day peaks.
    if max_tracker_store is not None and session_pct is not None:
        max_tracker_store.observe_quota(
            "claude", MAX_TRACKER_CLAUDE_SESSION_MINUTES, session_pct,
            current_ts)
        _mark_max_tracker_dirty(max_tracker_store)

    claude_week = _resolve_weekly_quota(
        claude, "claude", "general_weekly", "week", cache, current_ts)
    claude_model = _resolve_weekly_quota(
        claude, "claude", "model_weekly", "model", cache, current_ts,
        label_key="modelLabel")
    codex_week = _resolve_weekly_quota(
        codex, "codex", "general_weekly", "codexWeek", cache, current_ts)
    _persist_quota_records_async(cache, (
        claude_week["cache_record"],
        claude_model["cache_record"],
        codex_week["cache_record"],
    ))

    result["claudeWeekPct"] = claude_week["pct"]
    result["claudeWeekResetMin"] = _reset_minutes(
        claude_week["reset_at"], current_ts)
    result["claudeWeekObservedAt"] = claude_week.get("observed_at")
    result["claudeWeekStale"] = bool(
        claude_week["pct"] is not None and claude_week["stale"])
    # The exact "*Stale: false" gate: claude_week["live"] is precisely what
    # made claudeWeekStale false above -- never Max Tracker's own clock.
    if max_tracker_store is not None and claude_week["live"]:
        max_tracker_store.observe_quota(
            "claude", MAX_TRACKER_CLAUDE_WEEK_MINUTES, claude_week["pct"],
            claude_week["cache_record"].observed_at)
        _mark_max_tracker_dirty(max_tracker_store)
    result["claudeModelWeekPct"] = claude_model["pct"]
    result["claudeModelWeekResetMin"] = _reset_minutes(
        claude_model["reset_at"], current_ts)
    result["claudeModelWeekObservedAt"] = claude_model.get("observed_at")
    result["claudeModelWeekLabel"] = claude_model["label"]
    result["claudeModelWeekStale"] = bool(
        claude_model["pct"] is not None and claude_model["stale"])
    result["codexSessionPct"] = codex.get("codexSessionPct")
    result["codexSessionResetMin"] = codex.get("codexSessionResetMin")
    result["codexWeekPct"] = codex_week["pct"]
    result["codexWeekResetMin"] = _reset_minutes(
        codex_week["reset_at"], current_ts)
    result["codexWeekObservedAt"] = codex_week.get("observed_at")
    result["codexWeekStale"] = bool(
        codex_week["pct"] is not None and codex_week["stale"])
    if max_tracker_store is not None:
        # Codex's own rate-limit snapshot carries window_minutes natively
        # (threaded through above onto codex*WindowMinutes) -- unlike
        # Claude's headers, which only name a window ("5h"/"7d"), so this
        # is the real value rather than an assumed constant.
        codex_session_pct = result["codexSessionPct"]
        codex_session_window = codex.get("codexSessionWindowMinutes")
        if (codex_session_pct is not None and
                _valid_window_minutes(codex_session_window)):
            max_tracker_store.observe_quota(
                "codex", codex_session_window, codex_session_pct,
                current_ts)
            _mark_max_tracker_dirty(max_tracker_store)
        codex_week_window = codex.get("codexWeekWindowMinutes")
        if codex_week["live"] and _valid_window_minutes(codex_week_window):
            max_tracker_store.observe_quota(
                "codex", codex_week_window, codex_week["pct"],
                codex_week["cache_record"].observed_at)
            _mark_max_tracker_dirty(max_tracker_store)

    claude_session_reset = session_reset_at
    # Reset-tiderna tas från posten oavsett live/cache: en cachad post bär
    # sin cykels riktiga reset (quota_cache räknar ut poster vid passerad
    # reset), och deltan "idag" ska ÖVERLEVA en 429-mörkläggning — panelen
    # får bli ärligt äldre (stale-flaggan), aldrig tom (kravet 2026-08-14:
    # skottsäkert för alla som kör detta). I historiken SPELAS däremot bara
    # live-observationer in — en cachad procent är ingen ny mätning.
    claude_week_reset = claude_week["reset_at"]
    claude_model_reset = claude_model["reset_at"]
    codex_week_reset = codex_week["reset_at"]
    quota_samples = [
        (provider, window, pct, reset_at)
        for provider, window, pct, reset_at, is_live in (
            ("claude", "session", result["claudeSessionPct"],
             claude_session_reset, True),
            ("claude", "week", result["claudeWeekPct"],
             claude_week_reset, claude_week["live"]),
            ("claude", "model_week", result["claudeModelWeekPct"],
             claude_model_reset, claude_model["live"]),
            ("codex", "week", result["codexWeekPct"],
             codex_week_reset, codex_week["live"]),
        )
        if is_live and pct is not None and reset_at is not None
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
    # OTA-annonsen rider på kvotpollen: noll ny infrastruktur, och enheten
    # avgör själv (mot sin körande version) om notisen ska visas.
    result["otaAvailableVersion"] = _ota_available_version()
    result["v"] = 2
    return result


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """Thread-per-request HTTP with a fixed resource ceiling."""

    daemon_threads = True
    block_on_close = False

    def __init__(self, server_address, handler, *,
                 max_workers=HTTP_MAX_WORKERS):
        if type(max_workers) is not int or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self.max_workers = max_workers
        self._worker_slots = threading.BoundedSemaphore(max_workers)
        super().__init__(server_address, handler)

    def process_request(self, request, client_address):
        if not self._worker_slots.acquire(blocking=False):
            self._reject_busy(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._worker_slots.release()
            self.shutdown_request(request)
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._worker_slots.release()

    @staticmethod
    def _reject_busy(request):
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Length: 0\r\n"
            b"Connection: close\r\n\r\n"
        )
        try:
            request.settimeout(0.05)
        except OSError:
            pass
        # Closing a Windows socket with unread request bytes can turn the
        # intended 503 into WSAECONNABORTED at the client. Drain one bounded
        # header before replying; a slow/hostile peer still costs at most the
        # short timeout and 8 KiB.
        try:
            received = b""
            while len(received) < 8 * 1024 and b"\r\n\r\n" not in received:
                chunk = request.recv(min(2048, 8 * 1024 - len(received)))
                if not chunk:
                    break
                received += chunk
        except OSError:
            pass
        try:
            request.sendall(response)
        except OSError:
            pass
        try:
            request.shutdown(socket.SHUT_WR)
        except OSError:
            pass


class Handler(BaseHTTPRequestHandler):
    projects_dir = None  # sätts i main
    agent_status = None  # bakgrundstjänst, sätts i main
    max_tracker_store = None  # sätts i main
    github_monitor = None  # frivillig publik repo-monitor, sätts i main
    plans = {"claude": None, "codex": None}  # sätts i main från --*-plan
    interaction_store = None  # "Needs You", av som standard; sätts i main
    interaction_timeout_s = 120.0  # sätts i main från --interaction-timeout
    claude_interactions = False
    codex_interactions = False
    interaction_detail = False
    legacy_claude_panel_v1 = False
    interaction_relay_status = "off"
    interaction_relay_reason = None
    agent_status_relay_status = "off"
    agent_status_relay_reason = None
    discovery_status = "off"
    discovery_reason = None
    # Innehållsfritt närvarobevis för startup-hälsan. Den befintliga panelen
    # pollar /api/agent-status varje sekund. Två kända panel-GET från samma
    # icke-loopback-klient inom ett kort fönster är starkare evidens än en
    # ensam curl. Kandidatadressen hålls bara i processminnet och returneras
    # eller loggas aldrig.
    panel_poll_lock = threading.Lock()
    panel_poll_candidate_host = None
    panel_poll_candidate_at = None
    panel_poll_candidate_count = 0
    panel_last_seen_at = None
    panel_last_seen_route = None
    panel_last_http_stall_recovery_boot = False
    panel_confirm_window_s = 10.0
    panel_fresh_s = 15.0
    json_body_timeout_s = JSON_BODY_TIMEOUT_S

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_no_decision(self):
        """200 with an empty body: the documented "hook made no decision".

        This is the single most important response in the whole feature. It is
        what makes Claude Code render its own prompt, so every unknown — a
        payload we cannot display, a timeout, LEAVE IT, a bug in here — lands
        on it and the terminal simply behaves as it always did.
        """
        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _is_loopback(self):
        """Hooks may only come from this machine.

        Claude Code already refuses to POST a hook to a LAN address, so this
        is defence in depth rather than the only guard — but it is what keeps
        "the hook door" and "the device door" genuinely different doors.
        """
        host = self.client_address[0] if self.client_address else ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        mapped = getattr(address, "ipv4_mapped", None)
        return bool(mapped.is_loopback if mapped is not None
                    else address.is_loopback)

    def _record_panel_poll(self):
        """Confirm a live panel without trusting one anonymous LAN request."""
        if not getattr(self, "client_address", None):
            return
        if self._is_loopback():
            return
        host = self.client_address[0] if self.client_address else None
        if not isinstance(host, str) or not host:
            return
        now = time.monotonic()
        recovery_headers = self._header_values(
            "X-VibePulse-Recovery-Boot")
        recovery_boot = recovery_headers == ["http-stall-v1"]
        cls = type(self)
        became_ready = False
        with cls.panel_poll_lock:
            if (cls.panel_poll_candidate_host == host and
                    cls.panel_poll_candidate_at is not None and
                    now - cls.panel_poll_candidate_at <=
                    cls.panel_confirm_window_s):
                cls.panel_poll_candidate_count += 1
            else:
                cls.panel_poll_candidate_host = host
                cls.panel_poll_candidate_count = 1
            cls.panel_poll_candidate_at = now
            if cls.panel_poll_candidate_count >= 2:
                was_fresh = (cls.panel_last_seen_at is not None and
                             now - cls.panel_last_seen_at <= cls.panel_fresh_s)
                cls.panel_last_seen_at = now
                cls.panel_last_seen_route = self.path
                cls.panel_last_http_stall_recovery_boot = recovery_boot
                became_ready = not was_fresh
        if became_ready:
            log.info("startup-health: panelkontakt READY via %s", self.path)

    @classmethod
    def _panel_health_snapshot(cls):
        now = time.monotonic()
        with cls.panel_poll_lock:
            seen = cls.panel_last_seen_at
            route = cls.panel_last_seen_route
            recovery_boot = cls.panel_last_http_stall_recovery_boot
        if seen is None:
            return {"status": "waiting"}
        age_s = max(0, int(now - seen))
        return {
            "status": "ready" if age_s <= cls.panel_fresh_s else "stale",
            "ageS": age_s,
            "route": route,
            "httpStallRecoveryBoot": bool(recovery_boot),
        }

    def _header_values(self, name):
        headers = getattr(self, "headers", None)
        get_all = getattr(headers, "get_all", None)
        if callable(get_all):
            return get_all(name) or []
        if headers is None:
            return []
        value = headers.get(name)
        return [] if value is None else [value]

    def _has_valid_loopback_host(self):
        values = self._header_values("Host")
        if len(values) != 1 or not isinstance(values[0], str):
            return False
        authority = values[0].strip()
        port = None
        if authority.startswith("["):
            match = re.fullmatch(r"\[([^\]]+)\](?::([0-9]{1,5}))?",
                                 authority)
            if match is None:
                return False
            try:
                if ipaddress.ip_address(match.group(1)) != \
                        ipaddress.ip_address("::1"):
                    return False
            except ValueError:
                return False
            port = match.group(2)
        else:
            if authority.count(":") > 1:
                return False
            host = authority
            if ":" in authority:
                host, _, port = authority.rpartition(":")
                if not port:
                    return False
            lowered = host.lower()
            if lowered not in ("localhost", "localhost."):
                try:
                    address = ipaddress.ip_address(host)
                except ValueError:
                    return False
                if address.version != 4 or not address.is_loopback:
                    return False
        if port is None:
            return True
        try:
            expected_port = int(self.server.server_address[1])
            supplied_port = int(port)
        except (AttributeError, IndexError, TypeError, ValueError):
            return False
        return supplied_port == expected_port

    def _has_json_content_type(self):
        values = self._header_values("Content-Type")
        if len(values) != 1 or not isinstance(values[0], str):
            return False
        return re.fullmatch(
            r'application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?',
            values[0].strip(), flags=re.IGNORECASE) is not None

    def _read_json_body(self, limit=64 * 1024):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (TypeError, ValueError):
            return None
        if length <= 0 or length > limit:
            return None
        try:
            previous_timeout = self.connection.gettimeout()
            self.connection.settimeout(self.json_body_timeout_s)
        except (ConnectionError, TimeoutError, OSError):
            return None
        try:
            try:
                raw = self.rfile.read(length)
            except (ConnectionError, TimeoutError, OSError):
                return None
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except (ConnectionError, TimeoutError, OSError):
                pass
        if len(raw) != length:
            return None
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError,
                RecursionError):
            return None

    def _max_tracker_payload(self):
        # get_snapshot() is the single place fresh, non-stale/non-cached
        # Claude and Codex percentages get published (see the observe_quota
        # hooks inside it) -- calling it here both feeds today's peaks and
        # gives us the exact "*Stale: false" signal to mirror, so the top-
        # level stale flag below is never an invented second clock.
        quota_snapshot = get_snapshot(
            self.projects_dir, max_tracker_store=self.max_tracker_store)
        today = datetime.now().astimezone().date().isoformat()
        payload = self.max_tracker_store.snapshot(today, self.plans)
        payload["stale"] = bool(
            quota_snapshot.get("claudeWeekStale") or
            quota_snapshot.get("codexWeekStale"))
        return payload

    def _reply(self, produce):
        """Svara 200 med produce(), annars 500 {"error": ...} — skärmen
        avvisar error-formen per kontrakt och behåller senaste goda värden.
        Orsaken ska ändå alltid synas i loggen: ett tyst 500 var så
        max-tracker-buggarna förblev osynliga.

        Producenten och svarsskrivningen bedöms VAR FÖR SIG: ett
        ConnectionError/TimeoutError från producenten är ett serverfel som
        ska loggas och bli 500 — bara under själva skrivningen betyder det
        att klienten försvann."""
        try:
            payload = produce()
        except Exception:
            log.exception("500 på %s", self.path)
            try:
                self._send(500, {"error": "internal server error"})
            except OSError:
                pass
            return
        try:
            self._send(200, payload)
        except (ConnectionError, TimeoutError):
            pass  # klienten försvann mitt i svaret — inte ett serverfel
        except Exception:
            # T.ex. oserialiserbar payload — serverfel, inte klientens.
            log.exception("500 på %s (svarsskrivningen)", self.path)
            try:
                self._send(500, {"error": "internal server error"})
            except OSError:
                pass

    def _agent_status_payload(self):
        """Agent status, plus the pending interaction when there is one.

        `pending` is a NEW OPTIONAL ROOT KEY. The shipped firmware validates
        the root's *required* keys and pins `v` to 2, but does not reject
        unknown root keys — so adding it here does not break a panel running
        today's build, and `v` deliberately stays 2.

        The hard part is size, not schema: the device drops the WHOLE body
        past its 4096-byte buffer, which would take the agent list with it.
        So the pending item — the new, optional thing — is what gets dropped
        if the two together would not fit.
        """
        payload = self.agent_status.snapshot()
        if self.interaction_store is None:
            return payload
        pending = self.interaction_store.pending_public()
        if pending is None:
            return payload
        candidate = dict(payload)
        candidate["pending"] = pending
        if not interactions.response_fits(candidate):
            log.warning("pending-posten fick inte plats i /api/agent-status "
                        "(%d jobb) — agentlistan går före och posten "
                        "utelämnas", len(pending))
            return payload
        return candidate

    def _hook_client_gone(self):
        """Has the held hook's client hung up?

        The request body is fully read before parking, so the socket becoming
        readable can only mean EOF (the client closed) or a pipelined request
        (which Claude Code does not send on hook connections). A dead client
        must free its slot at once: the panel shows the oldest interaction
        first, so a ghost would shadow real prompts for the rest of its
        timeout and, with enough of them, fill the queue entirely.
        """
        try:
            readable, _, _ = select.select([self.connection], [], [], 0)
            if not readable:
                return False
            return self.connection.recv(1, socket.MSG_PEEK) == b""
        except (OSError, ValueError):
            return True

    def _handle_hook(self, kind):
        """Park a hook and hold its connection until a human decides."""
        event = self._read_json_body()
        if not isinstance(event, dict):
            self._send_no_decision()
            return
        park = (self.interaction_store.park_legacy
                if self.legacy_claude_panel_v1
                else self.interaction_store.park)
        entry = park(kind, event, self.interaction_timeout_s)
        if entry is None:
            # Not renderable, or too many already parked. The terminal is a
            # perfectly good place to answer this one.
            self._send_no_decision()
            return
        try:
            body = self.interaction_store.await_verdict(
                entry, is_alive=lambda: not self._hook_client_gone())
        except Exception:
            log.exception("interaktionen kraschade — lämnar beslutet till "
                          "terminalen")
            body = None
        try:
            if body is None:
                self._send_no_decision()
            else:
                self._send(200, body)
        except (ConnectionError, TimeoutError, OSError):
            pass  # Claude Code gav upp (timeout, avbruten session)

    def _send_codex_question_fallback(self, reason):
        self._send(200, {"status": "computer", "reason": reason})

    def _handle_codex_question(self):
        """Park a strict flat MCP question and return structured fallback."""
        event = self._read_json_body()
        identity_fields = {"cwd", "session_id", "turn_id"}
        question_fields = {"question", "header", "options"}
        if not isinstance(event, dict) or \
                not identity_fields.issubset(event) or \
                not {"question", "options"}.issubset(event) or \
                set(event) - identity_fields - question_fields:
            self._send_codex_question_fallback("invalid")
            return
        question = {key: event[key] for key in question_fields if key in event}
        normalized = normalize_codex_question(
            question, cwd=event["cwd"], session_id=event["session_id"],
            turn_id=event["turn_id"])
        if normalized is None:
            self._send_codex_question_fallback("invalid")
            return
        if not self.interaction_detail:
            # Labels remain private adapter state so the normalized schema is
            # intact, but every recommendation marker and every public text
            # field is removed before the store validates and freezes it.
            normalized = {
                **normalized,
                "options": [
                    {key: value for key, value in option.items()
                     if key != "recommended"}
                    for option in normalized["options"]
                ],
                "recommended_index": None,
                "view": {
                    "kind": "question",
                    "options_total": len(normalized["options"]),
                    "marked": False,
                    "can_approve": False,
                },
            }
        entry = self.interaction_store.park_normalized(
            normalized, self.interaction_timeout_s)
        if entry is None:
            self._send_codex_question_fallback("unavailable")
            return
        try:
            result = self.interaction_store.await_result(
                entry, is_alive=lambda: not self._hook_client_gone())
        except Exception:
            log.exception("Codex-frågan kraschade — lämnar beslutet till "
                          "datorn")
            result = None
        try:
            if result is None:
                reason = "disconnected" if self._hook_client_gone() \
                    else "timeout"
                self._send_codex_question_fallback(reason)
            else:
                self._send(200, codex_question_result(
                    result.verdict, normalized))
        except (ConnectionError, TimeoutError, OSError):
            pass

    def _handle_codex_permission(self):
        """Park a Codex hook and return its documented decision or silence."""
        event = self._read_json_body()
        normalized = normalize_codex_permission(
            event, reveal=self.interaction_detail)
        if normalized is None:
            self._send_no_decision()
            return
        entry = self.interaction_store.park_normalized(
            normalized, self.interaction_timeout_s)
        if entry is None:
            self._send_no_decision()
            return
        try:
            result = self.interaction_store.await_result(
                entry, is_alive=lambda: not self._hook_client_gone())
        except Exception:
            log.exception("Codex-behörigheten kraschade — lämnar beslutet "
                          "till datorn")
            result = None
        try:
            body = (codex_permission_response(result.verdict)
                    if result is not None else None)
            if body is None:
                self._send_no_decision()
            else:
                self._send(200, body)
        except (ConnectionError, TimeoutError, OSError):
            pass

    def _handle_answer(self, request_id):
        payload = self._read_json_body(limit=4096)
        if not isinstance(payload, dict):
            self._send(400, {"ok": False, "reason": "bad request"})
            return
        ok, reason = self.interaction_store.resolve(
            request_id, payload.get("verdict"), payload.get("ts"),
            payload.get("hmac"), provider=payload.get("provider"),
            view_sha256=payload.get("view_sha256"))
        self._send(200 if ok else 409, {"ok": ok, "reason": reason})

    def _handle_panic(self):
        """Panic stop: deny everything parked. Signed like any other answer.

        It can only ever deny, so the worst a stranger with the key can do is
        stop your agents — which is why this is the safest thing the device
        is allowed to do.
        """
        payload = self._read_json_body(limit=4096)
        if not isinstance(payload, dict):
            self._send(400, {"ok": False, "reason": "bad request"})
            return
        accepted, denied = self.interaction_store.panic(
            payload.get("ts"), payload.get("hmac"))
        if not accepted:
            self._send(409, {"ok": False, "reason": "signature rejected"})
            return
        log.warning("panikstopp från enheten: %d väntande beslut nekade",
                    denied)
        self._send(200, {"ok": True, "denied": denied})

    def do_POST(self):
        claude_route = self.path in (
            "/api/hook/question", "/api/hook/permission")
        codex_route = self.path in (
            "/api/codex/question", "/api/codex/permission")
        answer_route = self.path.startswith("/api/interaction/")
        panic_route = self.path == "/api/panic"
        if claude_route and (self.interaction_store is None or
                             not self.claude_interactions):
            self._send(404, {"error": "interactions are not enabled"})
            return
        if codex_route and (self.interaction_store is None or
                            not self.codex_interactions):
            self._send(404, {"error": "interactions are not enabled"})
            return
        if claude_route or codex_route:
            if not self._is_loopback():
                log.warning("hook-POST från %s avvisad — hookar får bara "
                            "komma från den här maskinen",
                            self.address_string())
                self._send(403, {"error": "hooks must be local"})
                return
            if not self._has_valid_loopback_host() or \
                    self._header_values("Origin"):
                self._send(403, {"error": "hook ingress rejected"})
                return
        if (answer_route or panic_route) and self.interaction_store is None:
            self._send(404, {"error": "interactions are not enabled"})
            return
        if (claude_route or codex_route or answer_route or panic_route) and \
                not self._has_json_content_type():
            self._send(415, {"error": "application/json required"})
            return
        if claude_route:
            self._handle_hook(
                "question" if self.path.endswith("question") else "approval")
        elif self.path == "/api/codex/question":
            self._handle_codex_question()
        elif self.path == "/api/codex/permission":
            self._handle_codex_permission()
        elif self.interaction_store is None:
            self._send(404, {"error": "interactions are not enabled"})
        elif answer_route:
            self._handle_answer(self.path[len("/api/interaction/"):])
        elif panic_route:
            self._handle_panic()
        else:
            self._send(404, {"error": "not found"})

    def do_GET(self):
        if self.path in ("/api/tokens", "/api/agent-status",
                         "/api/max-tracker", "/api/github"):
            self._record_panel_poll()
        if self.path == "/api/tokens":
            self._reply(lambda: get_snapshot(
                self.projects_dir,
                max_tracker_store=self.max_tracker_store))
        elif self.path == "/api/agent-status":
            self._reply(self._agent_status_payload)
        elif self.path == "/api/max-tracker":
            self._reply(self._max_tracker_payload)
        elif self.path == "/api/github":
            self._reply(lambda: (self.github_monitor.snapshot()
                                 if self.github_monitor is not None
                                 else disabled_snapshot()))
        elif self.path == "/":
            self._reply(self._root_payload)
        else:
            self._send(404, {"error": "not found"})

    def _root_payload(self):
        # EN läsning av failing_since — ok-flaggan och varaktigheten måste
        # komma ur samma ögonblick. Två läsningar lät en återhämtning mitt
        # emellan ge None i subtraktionen (500 på själva diagnostikrutten)
        # och en nystartad episod ge ok=true med varaktighet bredvid.
        failing_since = _compute_failing_since
        endpoints = ["/api/tokens", "/api/agent-status",
                     "/api/max-tracker", "/api/github"]
        return {"service": "torget-tokenserver",
                "rev": _SERVER_REV,
                "srcFingerprint": _SERVER_SRC,
                "startedAt": _SERVER_STARTED,
                "endpoint": "/api/tokens",
                "endpoints": endpoints,
                "github": (self.github_monitor.snapshot()
                           if self.github_monitor is not None
                           else disabled_snapshot()),
                "claudeProbe": _probe_status,
                "claudeCredential": dict(_claude_credential),
                "claudeLocalUsage": _claude_plan_usage_status,
                "ratelimitHeaders": _probe_headers,
                "unknownRateLimitBuckets": _probe_unknown_buckets,
                # GET / parsas aldrig av skärmen — fält kan läggas till
                # utan kontraktsrisk.
                "usageComputeOk": failing_since is None,
                "usageComputeFailingForS":
                    (int(time.monotonic() - failing_since)
                     if failing_since is not None else None),
                "discovery": {
                    "status": self.discovery_status,
                    **({"reason": self.discovery_reason}
                       if self.discovery_reason is not None else {}),
                },
                "interactions": {
                    "claude": bool(self.claude_interactions),
                    "codex": bool(self.codex_interactions),
                    "detail": bool(self.interaction_detail),
                    "legacyClaudePanelV1": bool(
                        self.legacy_claude_panel_v1),
                    "relay": ({
                        "status": self.interaction_relay_status,
                        **({"reason": self.interaction_relay_reason}
                           if self.interaction_relay_reason is not None
                           else {}),
                    }),
                    "agentStatusRelay": ({
                        "status": self.agent_status_relay_status,
                        **({"reason": self.agent_status_relay_reason}
                           if self.agent_status_relay_reason is not None
                           else {}),
                    }),
                    "panel": self._panel_health_snapshot(),
                    "transport": (
                        "lan+encrypted-relay"
                        if (self.interaction_relay_status == "ready" or
                            self.agent_status_relay_status == "ready")
                        else "lan"),
                }}

    def log_message(self, fmt, *args):
        pass  # 30 s-pollning ska inte fylla loggen

    def log_error(self, fmt, *args):
        # BaseHTTPRequestHandler ruttar log_error genom log_message, så den
        # tystade accessloggen tystade felen med sig. Accessloggen ska vara
        # tyst; felen ska inte.
        log.warning("http %s: %s", self.address_string(), fmt % args)


def _build_arg_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8737)
    ap.add_argument("--dir", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument(
        "--claude-plan", choices=["pro", "max5x", "max20x"], default=None,
        help="Claude-planen för Max Trackers badge (frivillig, allowlistad "
             "i max_tracker.PLAN_LABELS)")
    ap.add_argument(
        "--plan", action="append", metavar="PROVIDER=USD", default=[],
        help="what a subscription actually costs per month, in USD: "
             "--plan claude=200 --plan codex=20. Repeatable, one per "
             "provider, no allowlist of plan names. USD because the API list "
             "prices it is compared against are USD -- convert once if you "
             "pay in something else. Without it the provider's public list "
             "price is used and the payload marks the figure as a default")
    ap.add_argument(
        "--plan-cost-usd", type=float, default=None,
        help="deprecated alias for --plan claude=USD")
    ap.add_argument(
        "--prices", default=None,
        help="path to a JSON file merged over tools/tokenserver/prices.json "
             "(which is generated by update_prices.py). State only what "
             "differs: a corrected rate, or a model the catalogue does not "
             "carry yet. No code change needed")
    ap.add_argument(
        "--codex-plan", choices=["plus", "pro"], default=None,
        help="Codex-planen för Max Trackers badge (frivillig, allowlistad "
             "i max_tracker.PLAN_LABELS)")
    ap.add_argument(
        "--github-repo", type=normalize_repo,
        default=os.environ.get("VIBEPULSE_GITHUB_REPO") or None,
        help="Frivilligt publikt GitHub-repo som owner/repository. "
             "Kan också sättas med VIBEPULSE_GITHUB_REPO.")
    ap.add_argument(
        "--publish", metavar="RELAY_URL", default=None,
        help="also POST the numbers endpoints (/api/tokens, /api/max-tracker,"
             " /api/github) to a relay mailbox on the internet, so a panel"
             " that cannot reach this LAN still gets them (docs/relay.md)."
             " The URL is the mailbox address INCLUDING its secret path."
             " Agent status and Needs You are never published -- the relay"
             " carries numbers, not activity")
    ap.add_argument(
        "--publish-name", default=None,
        help="publisher name sent with every relay POST (default: this"
             " machine's hostname). Several machines may publish to the same"
             " mailbox; the mailbox merges freshest-per-source by name")
    claude_interactions = ap.add_mutually_exclusive_group()
    claude_interactions.add_argument(
        "--interactions", action="store_true",
        help="deprecated alias for --claude-interactions; accept Claude Code "
             "hooks on loopback and let a paired device "
             "answer them ('Needs You'). Enables Claude only and saves that "
             "choice. Needs a device key "
             "(VIBEPULSE_DEVICE_KEY, ~/.vibepulse-device-key, or "
             "TK_VIBEPULSE_DEVICE_KEY in secrets.h) before the device can "
             "answer anything")
    claude_interactions.add_argument(
        "--claude-interactions", action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable Claude Code interaction hooks on loopback; "
             "the explicit choice is saved (use --no-claude-interactions "
             "to disable a saved opt-in)")
    ap.add_argument(
        "--codex-interactions", action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable Codex question and permission interactions "
             "on loopback; the explicit choice is saved")
    ap.add_argument(
        "--interaction-detail", action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable sending question text and commands to the "
             "panel; the explicit choice is saved. Enabling is a "
             "DELIBERATE widening of the privacy contract — without it the "
             "screen learns only that something is waiting, and in which "
             "project, and can only deny or defer to the terminal")
    ap.add_argument(
        "--legacy-claude-panel-v1", action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable the saved INSECURE compatibility protocol "
             "for old Claude-only panel firmware. Default off. Current "
             "Claude and all Codex interactions remain provider/digest-bound")
    interaction_relay = ap.add_mutually_exclusive_group()
    interaction_relay.add_argument(
        "--interaction-relay", metavar="HTTPS_ORIGIN", default=None,
        help="enable the saved end-to-end encrypted Needs You relay at this "
             "HTTPS origin. Requires a provider, --interaction-detail, the "
             "paired device key, mailbox, private Mac token, and optional "
             "relay dependency; a missing requirement disables only this "
             "adapter")
    interaction_relay.add_argument(
        "--no-interaction-relay", action="store_const", const=False,
        dest="interaction_relay",
        help="disable the saved encrypted interaction relay without "
             "removing its URL, mailbox, providers, or other features")
    ap.add_argument(
        "--agent-status-relay", action=argparse.BooleanOptionalAction,
        default=None,
        help="enable or disable the independently saved end-to-end encrypted "
             "Claude/Codex live-status relay. Default off; Cloudflare sees "
             "only fixed-size ciphertext and timing")
    ap.add_argument(
        "--interaction-timeout", type=float, default=120.0,
        help="seconds to hold a hook open before handing the decision back "
             "to the terminal (default 120). Keep it below the timeout in "
             "the hook's own settings entry, so we answer before Claude "
             "Code stops listening")
    return ap


def _resolve_interaction_config(args, path=None):
    """Atomically merge explicit CLI choices into the saved switches.

    Explicit choices are persisted before later server startup (and therefore
    remain saved even if that process subsequently cannot bind its port).
    """
    config_path = (Path(path) if path is not None else
                   _state_dir() / "config.json")
    with config_lock(config_path):
        invalid_saved = False
        try:
            saved = load_config(config_path)
        except ConfigError:
            log.error("ogiltig VibePulse-konfiguration i %s — sparade "
                      "interaktioner stängs av", config_path, exc_info=True)
            saved = VibePulseConfig()
            invalid_saved = True
        claude_override = (True if args.interactions else
                           args.claude_interactions)

        def chosen(saved_value, override):
            return saved_value if override is None else override

        resolved = VibePulseConfig(
            claude_interactions=chosen(
                saved.claude_interactions, claude_override),
            codex_interactions=chosen(
                saved.codex_interactions, args.codex_interactions),
            interaction_detail=chosen(
                saved.interaction_detail, args.interaction_detail),
            legacy_claude_panel_v1=chosen(
                saved.legacy_claude_panel_v1,
                args.legacy_claude_panel_v1),
            interaction_relay=chosen(
                saved.interaction_relay,
                (True if isinstance(args.interaction_relay, str)
                 else args.interaction_relay)),
            agent_status_relay=chosen(
                saved.agent_status_relay, args.agent_status_relay),
            interaction_relay_url=(
                args.interaction_relay
                if isinstance(args.interaction_relay, str)
                else saved.interaction_relay_url),
            interaction_mailbox=saved.interaction_mailbox,
        )
        explicit = (claude_override is not None or
                    args.codex_interactions is not None or
                    args.interaction_detail is not None or
                    args.legacy_claude_panel_v1 is not None or
                    args.interaction_relay is not None or
                    args.agent_status_relay is not None)
        if explicit:
            # An explicit valid choice may repair a bad saved file. Keeping
            # this save under the same lock as the read prevents lost merges.
            save_config(config_path, resolved)
        elif invalid_saved:
            return VibePulseConfig()
        return resolved


def _configure_interactions(config, interaction_timeout, audit=None):
    """Install the resolved provider switches and their one shared store."""
    Handler.claude_interactions = config.claude_interactions
    Handler.codex_interactions = config.codex_interactions
    Handler.interaction_detail = config.interaction_detail
    Handler.legacy_claude_panel_v1 = config.legacy_claude_panel_v1
    Handler.interaction_store = None
    Handler.interaction_timeout_s = max(5.0, interaction_timeout)
    if not (config.claude_interactions or config.codex_interactions):
        return None
    secret = interactions.read_device_key()
    Handler.interaction_store = InteractionStore(
        secret=secret or "", reveal_detail=config.interaction_detail,
        audit=audit)
    return secret


def _read_interaction_mac_token(path=None, environ=None):
    """Read one private Mac-role bearer without ever logging its value."""
    def valid(value):
        if not isinstance(value, str) or \
                re.fullmatch(r"[A-Za-z0-9_-]{43}", value) is None:
            return False
        try:
            decoded = base64.b64decode(
                value + "=", altchars=b"-_", validate=True)
        except (TypeError, ValueError):
            return False
        return (len(decoded) == 32 and
                base64.urlsafe_b64encode(decoded).rstrip(b"=").decode(
                    "ascii") == value)

    environ = os.environ if environ is None else environ
    value = environ.get("VIBEPULSE_INTERACTION_MAC_TOKEN")
    if valid(value):
        return value
    if value is not None:
        return None

    token_path = (Path.home() / ".vibepulse-interaction-relay-token"
                  if path is None else Path(path))
    fd = None
    try:
        before = os.lstat(token_path)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            return None
        if os.name == "posix" and stat.S_IMODE(before.st_mode) != 0o600:
            return None
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | \
            getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(token_path, flags)
        descriptor = os.fstat(fd)
        after = os.lstat(token_path)
        if not stat.S_ISREG(descriptor.st_mode) or \
                stat.S_ISLNK(after.st_mode) or \
                not os.path.samestat(before, descriptor) or \
                not os.path.samestat(descriptor, after) or \
                (os.name == "posix" and
                 stat.S_IMODE(descriptor.st_mode) != 0o600):
            return None
        raw = os.read(fd, 257)
        if len(raw) > 256:
            return None
        try:
            text = raw.decode("ascii", "strict")
        except UnicodeError:
            return None
        if text.endswith("\n"):
            text = text[:-1]
        if not valid(text):
            return None
        return text
    except (FileNotFoundError, OSError):
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _configure_interaction_relay(config, secret, *, environ=None,
                                 relay_factory=None, audit=None):
    """Start either encrypted relay without coupling their opt-in rules."""
    Handler.interaction_relay_status = "off"
    Handler.interaction_relay_reason = None
    Handler.agent_status_relay_status = "off"
    Handler.agent_status_relay_reason = None
    want_interactions = config.interaction_relay
    want_status = config.agent_status_relay
    if not (want_interactions or want_status):
        return None

    def disable_interactions(reason):
        Handler.interaction_relay_status = "disabled"
        Handler.interaction_relay_reason = reason
    def disable_status(reason):
        Handler.agent_status_relay_status = "disabled"
        Handler.agent_status_relay_reason = reason

    publish_interactions = want_interactions
    publish_status = want_status
    if publish_interactions and (
            not (config.claude_interactions or config.codex_interactions) or
            Handler.interaction_store is None):
        disable_interactions("provider-required")
        publish_interactions = False
    if publish_interactions and not config.interaction_detail:
        disable_interactions("detail-required")
        publish_interactions = False
    status_source = getattr(Handler.agent_status, "snapshot", None)
    if publish_status and not callable(status_source):
        disable_status("status-source-missing")
        publish_status = False
    if not (publish_interactions or publish_status):
        return None

    def disable_shared(reason):
        if publish_interactions:
            disable_interactions(reason)
        if publish_status:
            disable_status(reason)
        return None

    if not isinstance(secret, str) or \
            re.fullmatch(r"[0-9A-Fa-f]{64}", secret) is None:
        return disable_shared("device-key-missing")
    if config.interaction_relay_url is None:
        return disable_shared("url-missing")
    if config.interaction_mailbox is None:
        return disable_shared("mailbox-missing")
    mac_token = _read_interaction_mac_token(environ=environ)
    if mac_token is None:
        return disable_shared("mac-token-missing")

    adapter = None
    try:
        if relay_factory is None:
            if __package__:
                from .interaction_relay import InteractionRelay
            else:  # direct execution
                from interaction_relay import InteractionRelay
            relay_factory = InteractionRelay
        adapter = relay_factory(
            store=(Handler.interaction_store
                   if publish_interactions else None),
            publish_interactions=publish_interactions,
            publish_agent_status=publish_status,
            status_source=(status_source if publish_status else None),
            base_url=config.interaction_relay_url,
            mailbox=config.interaction_mailbox,
            mac_token=mac_token,
            device_key_hex=secret,
            audit=audit,
        )
        adapter.start()
    except ImportError:
        return disable_shared("crypto-unavailable")
    except Exception:
        if adapter is not None:
            try:
                adapter.stop()
            except Exception:
                pass
        return disable_shared("configuration-invalid")
    if publish_interactions:
        Handler.interaction_relay_status = "ready"
        Handler.interaction_relay_reason = None
    if publish_status:
        Handler.agent_status_relay_status = "ready"
        Handler.agent_status_relay_reason = None
    return adapter


def _run_max_tracker_backfill(store, stop_event):
    """Drain MaxTrackerStore.backfill_step() on the same 0.5 s cadence as
    agent_status.POLL_S, forever -- never permanently stopping.

    Design choice, made explicit per Task 6: backfill_step()'s idle path
    (both roots fully drained) only globs each root and stats already-known
    files -- see MaxTrackerStore._advance_one_file, which returns without
    opening anything once every discovered inode is marked done. That is
    cheap enough to call every tick indefinitely, so this loop never
    switches itself off; it just keeps discovering newly-appeared rollout/
    session files on its own, without needing a restart.
    """
    while not stop_event.is_set():
        try:
            if store.backfill_step():
                _mark_max_tracker_dirty(store)
        except Exception:
            pass
        if stop_event.wait(MAX_TRACKER_BACKFILL_TICK_S):
            break


def _read_github_token():
    """A read-only GitHub token for the stargazer read, from env or a
    git-ignored file. Never committed; only ever sent to api.github.com.

    Order: ``GITHUB_TOKEN``/``TG_GITHUB_TOKEN`` env, then ``~/.torget-github-token``
    (stable across worktrees), then ``<repo>/.github-token``. A dedicated
    fine-grained token scoped to public repositories, read-only, is enough --
    GitHub only requires the request to be *authenticated*, not privileged.
    """
    for name in ("GITHUB_TOKEN", "TG_GITHUB_TOKEN"):
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    repo_root = Path(__file__).resolve().parents[2]
    for path in (Path.home() / ".torget-github-token",
                 repo_root / ".github-token"):
        try:
            token = path.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if token:
            return token
    # secrets.h -- the git-ignored C secret store, home of TG_OTA_TOKEN.
    # Parsed the same way tools/ota-flash.sh reads its token:
    #   #define TG_GITHUB_TOKEN "github_pat_..."
    try:
        header = (repo_root / "secrets.h").read_text(
            encoding="utf-8", errors="ignore")
        match = re.search(
            r'#\s*define\s+TG_GITHUB_TOKEN\s+"([^"]+)"', header)
        if match and match.group(1).strip():
            return match.group(1).strip()
    except OSError:
        pass
    return None


def main():
    ap = _build_arg_parser()
    args = ap.parse_args()

    logging.basicConfig(
        stream=sys.stderr,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    _maybe_rotate_own_log()
    threading.Thread(
        target=_run_log_rotation_watch,
        args=(threading.Event(),),
        name="log-rotation-watch",
        daemon=True,
    ).start()
    log.info("startar: rev %s", _SERVER_REV)
    global _claude_plan, _codex_plan, _plan_costs, _price_table
    _claude_plan = args.claude_plan
    _codex_plan = args.codex_plan
    _plan_costs = value_meter.parse_plan_costs(
        args.plan, legacy_claude=args.plan_cost_usd)
    # A bad --prices file is a hard startup failure, never a silent fallback:
    # pricing against rates the operator believes they replaced is exactly the
    # failure the value multiple exists to avoid.
    _price_table = value_meter.load_prices(args.prices)
    interaction_config = _resolve_interaction_config(args)

    github_monitor = None
    if args.github_repo:
        github_token = _read_github_token()
        github_monitor = GitHubMonitor(args.github_repo, token=github_token)
        github_monitor.start()
        log.info("GitHub-monitor startad för publika repot %s (stargazare: %s)",
                 args.github_repo,
                 "auth" if github_token else "anonym — namn blir 'någon'")
    Handler.github_monitor = github_monitor

    Handler.projects_dir = Path(args.dir)
    if not Handler.projects_dir.is_dir():
        # Var: SystemExit. Under launchd (KeepAlive utan ThrottleInterval)
        # blev det en tyst respawn var ~10:e sekund som fyllde loggen.
        # Vänta i stället — katalogen dyker upp när Claude Code körts en
        # första gång på maskinen.
        log.warning("hittar inte %s — finns Claude Code på den här "
                    "maskinen? Väntar på att katalogen dyker upp "
                    "(Ctrl-C avbryter).", Handler.projects_dir)
        try:
            while not Handler.projects_dir.is_dir():
                time.sleep(30)
        except KeyboardInterrupt:
            raise SystemExit(1)
        log.info("%s finns nu — fortsätter starten.", Handler.projects_dir)

    # Förstaskanningen är en uppvärmning plus en loggrad — /api/tokens gör om
    # get_snapshot per request ändå, och HTTP-trådarna kör den redan
    # parallellt, så samma anrop tål en bakgrundstråd. Den låg tidigare FÖRE
    # bind, vilket med stora loggkataloger höll porten stängd i minuter efter
    # varje `launchctl kickstart`. Det var alltid trist för skärmen; med
    # Needs You blev det på riktigt fel — en hook som får connection refused
    # faller (helt säkert, men helt i onödan) tillbaka till terminalen fast
    # tjänsten är sekunder från att kunna hålla den. Nu binder servern direkt
    # och värmer i bakgrunden: agent-status och hookarna är incrementella och
    # svarar meningsfullt på en gång, /api/tokens svarar när skanningen är
    # klar (skärmen visar streck/stale tills dess, precis som vid nätfel).
    def _first_scan_warmup():
        t0 = time.monotonic()
        try:
            snap = get_snapshot(Handler.projects_dir)
        except Exception:
            log.exception("förstaskanningen kraschade — /api/tokens värmer "
                          "vid första anropet i stället")
            return
        log.info("förstaskanning %.1f s: %s tokens idag, %d sessioner, "
                 "%s denna månad",
                 time.monotonic() - t0,
                 f"{snap['dayTokens']:,}".replace(",", " "),
                 snap["daySessions"],
                 f"{snap['monthTokens']:,}".replace(",", " "))

    threading.Thread(target=_first_scan_warmup, name="first-scan-warmup",
                     daemon=True).start()

    status_service = AgentStatusService(
        projects_dir=Handler.projects_dir,
        codex_sessions=CODEX_SESSIONS,
    )
    status_service.poll_once()
    status_service.start()
    Handler.agent_status = status_service

    max_tracker_store = MaxTrackerStore(
        _state_dir() / "max-tracker.json",
        CODEX_SESSIONS, Handler.projects_dir)
    Handler.max_tracker_store = max_tracker_store
    Handler.plans = {"claude": args.claude_plan, "codex": args.codex_plan}

    secret = _configure_interactions(
        interaction_config, args.interaction_timeout,
        audit=lambda action, row: log.info(
            "interaktion %s: %s", action,
            json.dumps(row, sort_keys=True)))
    if secret is None and interaction_config.agent_status_relay:
        secret = interactions.read_device_key()
    if interaction_config.legacy_claude_panel_v1:
        log.warning("OSÄKER KOMPATIBILITET PÅ: gamla Claude-panelens "
                    "v1-svar saknar provider/digest-bindning. Stäng av "
                    "med --no-legacy-claude-panel-v1 när gammal firmware "
                    "inte längre används. Codex förblir v2.")
    if Handler.interaction_store is not None:
        log.info("Needs You på: Claude=%s, Codex=%s på 127.0.0.1:%d, "
                 "håller %.0f s. "
                 "Enhetsnyckel: %s. Innehåll till skärmen: %s",
                 "ja" if interaction_config.claude_interactions else "nej",
                 "ja" if interaction_config.codex_interactions else "nej",
                 args.port, Handler.interaction_timeout_s,
                 "finns" if secret else "SAKNAS — enheten kan inte svara",
                 "ja (--interaction-detail)"
                 if interaction_config.interaction_detail
                 else "nej, bara att något väntar")
        if not secret:
            log.warning("ingen enhetsnyckel hittad — hookar parkeras och "
                        "faller tillbaka till terminalen. Sätt "
                        "TK_VIBEPULSE_DEVICE_KEY i secrets.h (samma värde "
                        "som skärmen bygger med) för att kunna svara.")

    interaction_relay_adapter = _configure_interaction_relay(
        interaction_config,
        secret,
        audit=lambda action, row: log.info(
            "krypterat interaktionsrelä %s: %s", action,
            json.dumps(row, sort_keys=True)),
    )
    if Handler.interaction_relay_status == "ready":
        log.info("krypterat Needs You-relä redo (E2E; inga "
                 "frågor eller kommandon loggas)")
    elif Handler.interaction_relay_status == "disabled":
        log.warning("krypterat Needs You-relä avstängt: %s; LAN och "
                    "terminalfallback fortsätter",
                    Handler.interaction_relay_reason)
    if Handler.agent_status_relay_status == "ready":
        log.info("krypterat agentstatus-relä redo (E2E; fast storlek, "
                 "kort livslängd, ingen klartext hos molnet)")
    elif Handler.agent_status_relay_status == "disabled":
        log.warning("krypterat agentstatus-relä avstängt: %s; direkt LAN "
                    "fortsätter", Handler.agent_status_relay_reason)

    relay_publisher = None
    if args.publish:
        # Producenterna ÄR handlarnas: reläet kan aldrig glida ifrån det
        # LAN-endpointsen serverar. Agentstatus och Needs You publiceras
        # medvetet inte — reläet bär siffror, aldrig aktivitet (samma gräns
        # som firmwarens test/test_relay_boundary.py håller).
        def _tokens_payload():
            return get_snapshot(Handler.projects_dir,
                                max_tracker_store=Handler.max_tracker_store)

        def _tracker_payload():
            quota_snapshot = get_snapshot(
                Handler.projects_dir,
                max_tracker_store=Handler.max_tracker_store)
            today = datetime.now().astimezone().date().isoformat()
            payload = Handler.max_tracker_store.snapshot(today, Handler.plans)
            payload["stale"] = bool(
                quota_snapshot.get("claudeWeekStale") or
                quota_snapshot.get("codexWeekStale"))
            return payload

        def _github_payload():
            return (github_monitor.snapshot() if github_monitor is not None
                    else disabled_snapshot())

        machine = args.publish_name or socket.gethostname().split(".")[0]
        relay_publisher = Publisher(args.publish, machine, {
            "/api/tokens": _tokens_payload,
            "/api/max-tracker": _tracker_payload,
            "/api/github": _github_payload,
        })
        relay_publisher.start()
        log.info("publicerar siffror till reläet som \"%s\" (högst var "
                 "5:e minut för kvoter och var 30:e minut för GitHub/Max "
                 "Tracker; agentstatus och Needs You publiceras ALDRIG)",
                 machine)

    backfill_stop = threading.Event()
    backfill_thread = threading.Thread(
        target=_run_max_tracker_backfill,
        args=(max_tracker_store, backfill_stop),
        name="max-tracker-backfill",
        daemon=True,
    )
    backfill_thread.start()

    srv = None
    discovery = DiscoveryAdvertiser(log)
    try:
        srv = BoundedThreadingHTTPServer(("0.0.0.0", args.port), Handler)
        discovery.start(args.port)
        Handler.discovery_status = discovery.status
        Handler.discovery_reason = discovery.reason
        log.info("serverar http://0.0.0.0:%d/api/tokens, "
                 "/api/agent-status, /api/max-tracker och /api/github "
                 "(LAN — exponera inte utåt)", args.port)
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        discovery.stop()
        if interaction_relay_adapter is not None:
            interaction_relay_adapter.stop()
        if relay_publisher is not None:
            relay_publisher.stop()
        if github_monitor is not None:
            github_monitor.stop()
        backfill_stop.set()
        backfill_thread.join(timeout=max(1.0, MAX_TRACKER_BACKFILL_TICK_S * 4))
        try:
            max_tracker_store.save()  # slutlig flush, samma som stop()-flödet
        except Exception:
            log.exception("max-tracker: slutlig flush misslyckades — "
                          "dagens toppar kan saknas efter omstart")
        status_service.stop()
        if srv is not None:
            srv.server_close()


if __name__ == "__main__":
    main()
