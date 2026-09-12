#!/usr/bin/env python3
"""The token meter's service: Claude Code usage as flat JSON over the LAN.

Scans the session logs in ~/.claude/projects/**/*.jsonl (the same source
the ccusage family of usage tools reads), sums tokens per day and serves
the glance pattern's contract on /api/tokens:

    {"v": 2, "dayTokens": ..., "dayTokensPerHour": ..., "daySessions": ...,
     "monthTokens": ...}

Design rules, inherited from Solelkollen's /api/glance:
  * Flat JSON, numbers not strings, and a rate (dayTokensPerHour, the last
    hour's burn rate) so the screen can tick locally between fetches.
  * No secrets in the response and no authentication: the figures describe
    token volume, never content. The service binds to the LAN -- do not
    expose it outside the home.
  * Errors answer {"error": "..."} -- the screen's parser rejects that form
    by contract and keeps its last good values.

"Tokens" are all that passed through the model: in + out + cache write +
cache read -- the only figure that honestly describes how much was ground.
Duplicate rows (same message.id + requestId, which appear when sessions
are resumed) count once, the same dedup the usage tools do.

Incremental: files with unchanged (mtime, size) are reused from the cache,
and files older than the turn of the month are skipped entirely. A full
first scan takes a few seconds; after that every answer is effectively
immediate. The aggregate is recomputed at most every 30 s regardless of
the fetch rate.

Run:       python3 tokenserver.py [--port 8737] [--dir ~/.claude/projects]
Autostart: see README.md next to this file (a launchd plist is included).
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
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# The probe lock is taken with different system calls on different
# platforms; neither module exists on both. The import must not take the
# whole service down -- without a lock the behaviour is what it was before
# the lock existed, not "does not start at all".
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
    from . import codex_usage, interactions, statusline_bridge, value_meter
else:  # run directly: python3 tools/tokenserver/tokenserver.py
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
    import statusline_bridge
    import value_meter

RECOMPUTE_EVERY_S = 30
# How long the warm-up thread waits for a scan that an early HTTP request
# managed to start before it, before it gives up on the log line.
FIRST_SCAN_WAIT_S = 600
LIMITS_EVERY_S = 240  # the rate-limit probe: 15 calls/h -- the account's
                      # bucket is shared with Claude Code itself, and the
                      # quota moves slowly; the panel's 30 s polls get the
                      # cached answer at once anyway
AUTH_RECOVERY_EVERY_S = 15.0  # local token check; no upstream while waiting
CLAUDE_CREDENTIAL_WARNING_S = 30 * 60
CLAUDE_PLAN_USAGE_FRESH_S = 20 * 60
# The Claude Code statusLine bridge (tools/tokenserver/statusline_bridge.py)
# leaves the account's session and week windows in the state directory on
# every assistant message. While both are fresh the OAuth probe is only a
# cross-check and runs at this slower cadence instead of LIMITS_EVERY_S --
# the account's rate-limit bucket is shared with Claude Code itself.
STATUSLINE_FRESH_S = statusline_bridge.FRESH_S
PROBE_WHEN_BRIDGED_S = 1800
CLAUDE_PLAN_USAGE_MAX_BYTES = 2 * 1024 * 1024
HTTP_MAX_WORKERS = 32
JSON_BODY_TIMEOUT_S = 2.0
# Draining an unread, announced body before an early rejection. Shares
# the deadline with _reject_busy (0.05 s) but not the byte cap: that one
# drains at most 8 KiB of *headers* and stops at \r\n\r\n, while this
# drain covers the announced body and has its own, larger cap.
REQUEST_DRAIN_LIMIT = 64 * 1024
REQUEST_DRAIN_TIMEOUT_S = 0.05

# Which token sources and system calls exist depends on the platform, not
# on configuration. The tests patch the constant to run the Windows
# branches on a Mac.
_IS_WINDOWS = sys.platform == "win32"
_claude_plan_usage_status = "not_checked"
# The statusLine bridge sample: content-free status word for GET / and
# the transition log (not_installed / missing / unreadable / invalid /
# empty / stale / fresh) and the last summary served. The file is under
# 64 KiB and read once per 30 s poll, so it is parsed every time: a
# parse cache keyed on (mtime, size) missed a rewrite on Windows CI, where
# two writes in one tick shared both.
_claude_statusline_status = "not_checked"
_claude_statusline_logged = None
_claude_statusline_view = {"status": "not_checked", "ageS": None,
                           "claudeCodeVersion": None}
_claude_statusline_bridged = False
_claude_statusline_lock = threading.Lock()
# Tests point this at a file that does not exist so a bridge installed on
# the developer's own machine never leaks into snapshot assertions.
_claude_statusline_path_override = None


def _state_dir():
    """The service's state directory -- the lock, cache, history, tracker.

    ``~/Library/Application Support`` is the macOS convention; the Windows
    counterpart is ``%LOCALAPPDATA%``. The paths DO work literally on
    Windows (``Path.home()`` resolves), but would put a ``Library`` tree in
    the user profile that nothing else on the machine recognizes.
    """
    if _IS_WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base = (Path(local_app_data) if local_app_data
                else Path.home() / "AppData" / "Local")
        return base / "VibePulse"
    return Path.home() / "Library" / "Application Support" / "VibePulse"


def _claude_plan_usage_path():
    """Claude Desktop's local, content-free plan history.

    The file is owned and updated by the official client. It contains only
    time, organization and percentages for the five-hour/week window -- no
    prompts, answers, commands or OAuth secrets.
    """
    if _IS_WINDOWS:
        app_data = os.environ.get("APPDATA")
        base = (Path(app_data) if app_data
                else Path.home() / "AppData" / "Roaming")
        return base / "Claude" / "plan-usage-history.json"
    return (Path.home() / "Library" / "Application Support" / "Claude" /
            "plan-usage-history.json")


def _read_claude_plan_usage(path=None, now_ts=None):
    """Return the latest strict, fresh local usage sample, or ``None``.

    This is a passive fallback for when the access token the tokenserver can
    read has expired while Claude Desktop still has a current usage picture.
    The whole file is size-capped and the latest entry is validated
    fail-closed; the raw organization id never leaves the function.
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


def _claude_statusline_path():
    if _claude_statusline_path_override is not None:
        return Path(_claude_statusline_path_override)
    return _state_dir() / statusline_bridge.SAMPLE_NAME


def _read_claude_statusline(path=None, now_ts=None):
    """The bridge's validated windows, or ``None`` when there is nothing
    usable.  Sets the status word and logs its transitions once."""
    global _claude_statusline_status, _claude_statusline_logged, \
        _claude_statusline_view
    sample_path = _claude_statusline_path() if path is None else Path(path)
    current_ts = time.time() if now_ts is None else now_ts
    with _claude_statusline_lock:
        status, document = statusline_bridge.peek_sample(sample_path)
        if status == "missing" and not statusline_bridge.config_path(
                sample_path.parent).exists():
            status = "not_installed"
        summary = None
        if status == "ok":
            summary = statusline_bridge.summarize_sample(document, current_ts)
            status = summary["status"]
        view = {"status": status,
                "ageS": summary["ageS"] if summary else None,
                "claudeCodeVersion":
                    summary["claudeCodeVersion"] if summary else None}
        _claude_statusline_status = status
        _claude_statusline_view = view
        if status != _claude_statusline_logged:
            log.info("claude-statusline: %s -> %s",
                     _claude_statusline_logged or "start", status)
            _claude_statusline_logged = status
    if summary is None or not summary["windows"]:
        return None
    return summary


def _valid_epoch_after(value, now_ts):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > now_ts)


def _valid_pct(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 100)


def _window_wins(candidate_reset, candidate_pct, incumbent_reset,
                 incumbent_pct):
    """The spec's arbitration between two readings of one rate-limit
    window: the later reset is the newer window; within one window usage
    only accumulates, so the higher figure is the later one.  Ties keep
    the incumbent."""
    if incumbent_reset is None:
        return True
    if candidate_reset != incumbent_reset:
        return candidate_reset > incumbent_reset
    return candidate_pct > incumbent_pct


def _merge_claude_statusline(claude, quota_cache, now_ts, path=None):
    """Let a fresh statusLine sample stand in for -- or ahead of -- the
    OAuth probe's session and general week figures.

    Only a FRESH sample (Claude Code spoke within STATUSLINE_FRESH_S) is
    used: a stale one means no session is active and the probe is the
    better source again, so the merge is then a no-op and the cache keeps
    the last live figure.  Each window is arbitrated separately against
    the probe's (and, for the week, the cache's) reading by
    :func:`_window_wins`.  The model week has no statusLine counterpart
    and is never touched.
    """
    global _claude_statusline_bridged
    summary = _read_claude_statusline(path=path, now_ts=now_ts)
    if summary is None or summary["status"] != "fresh":
        _claude_statusline_bridged = False
        return claude
    windows = summary["windows"]
    merged = dict(claude)
    covered = 0

    five = windows.get("five_hour")
    probe_reset = claude.get("sessionResetAt")
    probe_pct = claude.get("sessionPct")
    probe_session = (_valid_epoch_after(probe_reset, now_ts)
                     and _valid_pct(probe_pct))
    if five is not None:
        if not probe_session or five["resets_at"] >= probe_reset:
            covered += 1
        if (not probe_session or _window_wins(
                five["resets_at"], five["pct"], probe_reset, probe_pct)):
            merged["sessionPct"] = five["pct"]
            merged["sessionResetAt"] = five["resets_at"]
            merged["sessionSource"] = "statusline"

    week = windows.get("seven_day")
    probe_reset = claude.get("weekResetAt")
    probe_pct = claude.get("weekPct")
    probe_week = (_valid_epoch_after(probe_reset, now_ts)
                  and _valid_pct(probe_pct)
                  and isinstance(claude.get("weekIdentity"), str))
    if week is not None:
        if not probe_week or week["resets_at"] >= probe_reset:
            covered += 1
        cached = quota_cache.latest("claude", "general_weekly", now=now_ts)
        wins = (not probe_week or _window_wins(
            week["resets_at"], week["pct"], probe_reset, probe_pct))
        if wins and cached is not None and _window_wins(
                cached.reset_at, cached.pct, week["resets_at"], week["pct"]):
            # The cache knows a strictly better reading (a later window,
            # or a higher figure in this one). An equal reading is the
            # bridge's own earlier sample coming back: still live.
            wins = False
        if wins:
            merged["weekPct"] = week["pct"]
            merged["weekResetAt"] = week["resets_at"]
            merged["weekResetMin"] = max(
                0, int(round((week["resets_at"] - now_ts) / 60)))
            merged["weekObservedAt"] = int(week["at"])
            merged["weekIdentity"] = _quota_identity("claude", "general_weekly")
            merged["weekSource"] = "statusline"
    # Both windows covered by a fresh sample no older than the probe's own
    # windows: the probe is a cross-check now and may slow down.
    _claude_statusline_bridged = covered == 2
    return merged


def _log_dir():
    """The log directory. macOS has ~/Library/Logs; Windows has no log
    convention of its own for user services, so the log lives in the state
    tree."""
    return Path.home() / "Library" / "Logs" if not _IS_WINDOWS else (
        _state_dir() / "Logs")

# Diagnostics go through logging to stderr with timestamps (basicConfig in
# main; launchd collects both streams in the log file, see the plist). The
# rule is TRANSITIONS, not states: a status change is logged once and then
# it is quiet until the state changes again -- the file must stay readable
# over weeks.
log = logging.getLogger("tokenserver")

# The log file under launchd (the plist's StandardOut/ErrorPath).
# ~/Library/Logs survives a reboot and shows in the Console app -- /tmp did
# neither. launchd has no rotation of its own, so the server does it at
# start: see _maybe_rotate_own_log.
DEFAULT_LOG_PATH = _log_dir() / "torget-tokenserver.log"
_LOG_CAP_BYTES = 5 * 1024 * 1024
_LOG_TAIL_KEEP_BYTES = 256 * 1024


def _maybe_rotate_own_log(path=None, stderr_fd=2):
    """Truncate the log file at start once it has grown past the cap, with
    the tail preserved in <name>.old.

    Only when stderr actually IS the file (the launchd case): the fstat/stat
    comparison protects terminal runs from touching a file they do not
    write to. Truncation rather than rename: launchd keeps the fd open with
    O_APPEND, so a rename would only have made the process keep writing
    into the moved file while the new one stayed empty.

    The whole read-copy-truncate sequence is held under the root logger's
    handler lock (an RLock -- our own "rotated" line can still be written),
    so a log line from another thread cannot land between the tail read
    and the truncation and be erased from both files. Raw stderr writes
    (the agent_status diagnostics) go outside the lock; the remaining
    window is milliseconds against a throttled line per 30 s, once per
    5 MB."""
    path = Path(path) if path else DEFAULT_LOG_PATH
    try:
        st = path.stat()
    except OSError:
        return False  # no file (terminal run, fresh install)
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
            tail = fh.read()  # reads to the ACTUAL EOF -- newer lines too
            path.with_name(path.name + ".old").write_bytes(tail)
            fh.truncate(0)
        log.info("log file rotated (%d bytes > the cap %d; the tail is in "
                 "%s.old)", st.st_size, _LOG_CAP_BYTES, path.name)
        return True
    except Exception:
        # Rotation must never take the service down; that it failed must show.
        log.warning("log rotation of %s failed", path, exc_info=True)
        return False
    finally:
        for handler in reversed(handlers):
            handler.release()


_LOG_ROTATE_CHECK_S = 3600.0


def _run_log_rotation_watch(stop_event, interval_s=None):
    """Hourly rotation watch: the start-up rotation is not enough for a
    long-lived process -- a persistently failing sub-service could otherwise
    write past the cap until an unrelated restart happens to clean up. The
    same fstat guard as at start, so terminal runs stay untouched; the
    thread sleeps the rest of the time."""
    interval = _LOG_ROTATE_CHECK_S if interval_s is None else interval_s
    while not stop_event.wait(interval):
        _maybe_rotate_own_log()


def _read_server_rev():
    """The git revision actually serving -- makes 'the wrong code is running'
    visible in one curl instead of an hour of process archaeology."""
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
    """Content hash of the service's source files, taken at start. The rev
    is not enough for "is the server running what lies here?": a dirty
    worktree, or an edit AFTER the process started, shares HEAD with the
    checkout and lets the rev comparison lie "current". The smoke test
    recomputes the same hash from disk and compares. test_* and smoke.py
    are not included -- the service never loads them, and an edited smoke
    must not look like an outdated server."""
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
MAX_TRACKER_BACKFILL_TICK_S = 0.5  # same cadence as agent_status.POLL_S
# Claude never carries the window's minute count in the clear (only the
# names "5h"/"7d") -- unlike Codex, whose rate-limits snapshot has
# window_minutes right in the JSON (see _codex_window below). These two
# are the plan contract's fixed counterparts, classified by the same
# >600-minute rule MaxTrackerStore.observe_quota already uses.
MAX_TRACKER_CLAUDE_SESSION_MINUTES = 300   # 5 hours
MAX_TRACKER_CLAUDE_WEEK_MINUTES = 10080    # 7 days

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
# When the latest SUCCESSFUL recompute finished (monotonic). None until the
# first scan has completed: that is what separates "placeholder" from
# "frozen figures" in the usageTotals block below.
_last_result_at = None
_SERVER_STARTED_MONO = time.monotonic()
# The recompute's health: if _compute crashes the previous snapshot keeps
# being served -- correct, but it must not happen SILENTLY (the figures
# would freeze forever and look fresh). failing_since drives
# usageComputeOk on GET / and the smoke test's FAIL; the log gets the
# transition plus a throttled error.
# None = never logged. Not 0.0: time.monotonic() counts from boot, so on a
# freshly started machine "now - 0.0" is LESS than the throttle window and
# the first error would be swallowed -- CI's fresh VM tripped exactly that.
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
    """Let a fresh official local week percentage beat an older OAuth picture.

    The local file lacks the reset time and the model pool. It may therefore
    only complement the general week when a still-valid, earlier
    authenticated cache entry carries the same pool's reset. The model quota
    stays honestly stale until the OAuth probe recovers.
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
    existing_pct = claude.get("weekPct")
    if (claude.get("weekResetAt") == cached.reset_at and
            _valid_pct(existing_pct) and local["week_pct"] <= existing_pct):
        # Same window, no higher figure: usage only accumulates within a
        # window, so a later-but-lower local sample is a replay of an
        # earlier reading (the statusLine bridge made this case real).
        _claude_plan_usage_status = "not_higher"
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
                    continue  # half-written last row -- the next scan takes it
                usage = (entry.get("message") or {}).get("usage")
                ts_raw = entry.get("timestamp")
                if not usage or not ts_raw:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                except ValueError:
                    continue
                ts = ts.astimezone()  # the day boundary is the Mac's, not UTC's
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
        pass  # removed while being read -- the next scan will see it
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
        # Older than the turn of the month cannot hold this month's rows
        # (rows are written forward in time): skip without opening.
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
        "dayTokensPerHour": hour_tokens,  # the last hour = rate per hour
        "daySessions": len(day_sessions),
        "monthTokens": month_tokens,
        # The honesty invariant, API side: on a Codex-only machine there is
        # no Claude directory, and the four counters above become zeros
        # that are not measurements. The percentages already tell the
        # truth (they become null and the screen shows dashes), but the
        # counters cannot become null without older panels failing to
        # parse the payload at all
        # (tokens_parse.c: `if (!num(root, "dayTokens", &day)) goto done;`).
        # This flag says instead what the zeros mean, so no reader -- log,
        # panel or future consumer -- has to guess. Additive key, the same
        # pattern as "value" below.
        "claudeSourcePresent": projects_dir.is_dir(),
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
# The rate-limit probe (the Clawdmeter pattern): read Claude Code's own
# OAuth token from the active Claude Desktop process or the keychain and
# make a minimal API request -- the answer is uninteresting, the HEADERS
# are the data (anthropic-ratelimit-unified-*: the session's 5 h window
# and the week window, as percent + reset time). max_tokens=0 means
# nothing is generated: the probe is effectively free. The token never
# leaves the Mac; the screen only gets percentages.

_limits_lock = threading.Lock()
_last_limits = None
_last_probed = 0.0
_limits_refreshing = False
_headers_logged = False
# Probe diagnostics, exposed on "/": where in the chain the Claude probe
# gets stuck (keychain -> HTTP -> headers -> mapping) plus the raw header
# names.
_probe_status = "not_run"
_probe_headers = []
_probe_unknown_buckets = []
_probe_cooldown_until = 0.0
_probe_failure_streak = 0
_probe_status_logged = None  # last logged status: transitions are logged, states are not
# OBS-20: why the keychain gave no token, as a content-free word ("None" =
# it gave one, or was never asked). Set on the probe thread, logged on
# change only, and carried into claudeProbe/claudeCredential on GET /.
_keychain_reason = None
# The transition log's "previous" word. A successful read is None there
# too, so a separate sentinel marks "nothing logged yet": otherwise a
# failure after a recovery would read ``start -> …`` as if the service had
# just booted, instead of ``ok -> …`` (Codex review of #111).
_KEYCHAIN_UNLOGGED = object()
_keychain_reason_logged = _KEYCHAIN_UNLOGGED
# Content-free credential readiness captured on the probe thread. GET / must
# never reread Keychain synchronously: startup health has a sub-second budget.
_claude_credential = {"status": "unknown"}
# Dead tokens (value -> reason): a candidate that got a 401/403 is NEVER
# sent again. That was the pattern behind the 429 penalty box: the
# keychain token died overnight and Desktop's frozen process token hammered
# the API every probe cycle for hours (the log for 2026-08-14 03:51-10:45
# shows six penalty rounds in a row). A refreshed token has a NEW value and
# is thereby tried again automatically; the dict is capped at 8 entries so
# it can never grow unbounded.
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


def _note_keychain_reason(reason):
    """Publish the keychain outcome; one log line per change (OBS-04)."""
    global _keychain_reason, _keychain_reason_logged
    _keychain_reason = reason
    unlogged = _keychain_reason_logged is _KEYCHAIN_UNLOGGED
    if unlogged or reason != _keychain_reason_logged:
        log.info("claude-keychain: %s -> %s",
                 "start" if unlogged else (_keychain_reason_logged or "ok"),
                 reason or "ok")
        _keychain_reason_logged = reason


def _read_keychain_oauth():
    """The keychain entry as ``(token, expires_at_ms)`` -- what ``/login`` wrote.

    OBS-20: a blanket ``except Exception`` used to fold "no ``security``
    binary", "the user clicked Deny on the keychain prompt", "the prompt sat
    unanswered until the timeout" and "the entry is not JSON" into one
    ``(None, None)``. Each cause is its own word now, published through
    ``_note_keychain_reason`` so the probe status and ``GET /`` can say
    which one, and the log says when it changed. Exit 44 is
    ``errSecItemNotFound`` (no entry: never logged in on this account);
    any other non-zero exit is the keychain refusing us — Deny on the
    prompt, or a locked keychain — which is the case the README coaches
    people through.
    """
    token = expires_at = None
    reason = None
    try:
        completed = subprocess.run(
            ["security", "find-generic-password",
             "-s", "Claude Code-credentials", "-w"],
            capture_output=True, text=True, timeout=10,
        )
    except FileNotFoundError:
        reason = "keychain_security_missing"
    except subprocess.TimeoutExpired:
        reason = "keychain_timeout"  # the prompt sat unanswered
    except OSError as error:
        reason = f"keychain_spawn_failed: {type(error).__name__}"
    else:
        if completed.returncode == 44:
            reason = "keychain_no_entry"
        elif completed.returncode != 0:
            reason = (f"keychain_denied_or_locked "
                      f"(exit {completed.returncode})")
        else:
            try:
                record = json.loads((completed.stdout or "").strip())
                oauth = record.get("claudeAiOauth") or {}
                token = oauth.get("accessToken")
                expires_at = oauth.get("expiresAt")
            except (ValueError, AttributeError):
                reason = "keychain_malformed"
            else:
                if not token:
                    reason = "keychain_entry_without_token"
    _note_keychain_reason(reason)
    return token, expires_at


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
    """The reset header can be epoch seconds, seconds left or an ISO time."""
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
            # is_active means "the BINDING limit right now" (the 5-hour
            # window usually carries that flag), NOT "the pool exists".
            # Verified against the live answer 2026-08-14: the Fable week
            # sat at 11 % with is_active=false and vanished from the glass
            # -- real consumption must always be shown. Only an untouched
            # pool (0 % and inactive) is left unmentioned, so a never-used
            # model takes no space.
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


# Machine-wide probe lock: NO MATTER how many tokenservers happen to run
# (launchd, a worktree, a manual start on another port), at most ONE may
# talk to api.anthropic.com. Both 429 incidents of 2026-08-13/14 were at
# bottom redundant upstream traffic -- this gate makes the "one more
# instance" variant structurally harmless instead of trusting that nobody
# starts one.
_PROBE_LOCK_PATH = _state_dir() / "claude-probe.lock"

# The penalty box SURVIVES restarts: the cooldown was pure memory state, so
# every server restart forgot the backoff in progress and poked the hot
# bucket again at once (seen twice on 2026-08-14, both self-inflicted). The
# file lives next to the probe lock and is read lazily on the first probe
# cycle.
_PROBE_STATE_PATH = _state_dir() / "claude-probe-state.json"
_probe_state_loaded = False


def _load_probe_state_locked():
    """Load a persisted 429 rest once. The caller holds ``_limits_lock``:
    the status and cooldown it publishes must land in the same critical
    section as the streak and timestamp the resting cycle records right
    after (Codex review of #111), so ``GET /`` never pairs the persisted
    status with the initial streak and a ``null`` age."""
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
                         f"{datetime.fromtimestamp(until):%H:%M}"
                         " (persisted)")


def _save_probe_state(cooldown_until):
    try:
        _PROBE_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _PROBE_STATE_PATH.write_text(
            json.dumps({"cooldown_until": cooldown_until}),
            encoding="utf-8")
    except OSError:
        pass  # without disk the behaviour is as before: better than crashing


# ---------------------------------------------------------------------------
# The OTA announcement: the latest build on the Mac, read from torget.bin's
# embedded app descriptor -- the same truth the device itself reports about
# its running version. The device compares and shows the UPDATE READY
# notice on a difference (decision 2026-08-14). Only version and build time
# are announced, never paths or content.

_OTA_BUILD_ROOT = Path(__file__).resolve().parents[2]
_ota_desc_cache = {}  # path -> (mtime, version|None)


def _read_app_desc_version(path):
    """esp_app_desc_t lives at offset 32 (image header 24 B + segment
    header 8 B): magic_word, secure_version, reserv[2], version[32],
    project[32]. Wrong magic or wrong project => None -- better silent than
    the wrong image."""
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
    """Non-blocking exclusive lock; returns the file object or None.

    ``flock`` on macOS/Linux, ``msvcrt.locking`` on Windows -- the same
    gate, different system calls. Both are released when the file closes.
    """
    try:
        _PROBE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
        handle = open(_PROBE_LOCK_PATH, "w")
        if fcntl is not None:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            # LK_NBLCK locks one byte without blocking and raises OSError
            # if another instance already owns it. The file must have a
            # byte to lock, otherwise the call succeeds without the gate
            # meaning anything.
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


class _ProbeOutcome:
    """One probe cycle's evidence, assembled off the shared globals.

    OBS-18 (c): the status used to be built with ``+=`` on the probe
    thread while HTTP threads read it unlocked, so ``GET /`` could serve
    half a status ("usage_http_401" with the "; fallback_…" suffix still
    to come). The cycle now writes here and ``_publish_probe_outcome``
    swaps everything in at once, under ``_limits_lock``. Headers start
    empty every cycle (OBS-18 b): a cycle that never reaches the fallback
    probe leaves no hours-old header names beside a current failure. A
    429 sets ``cooldown_until`` here too, so the rest is published in the
    same section as the status that explains it (Codex review of #111).
    """

    def __init__(self, status):
        self.status = status
        self.headers = []
        self.unknown_buckets = []
        self.credential = None
        self.cooldown_until = None


# Set by the publish helpers when a cycle has already recorded its streak
# and timestamp together with its outcome; _refresh_limits then leaves the
# scheduling state alone instead of writing it a second time, later, where
# GET / could see a new status beside the old streak (Codex review of #111).
_probe_cycle_published = False


def _note_probe_schedule_locked(refreshed):
    """Streak and timestamp for one finished cycle. Caller holds the lock."""
    global _probe_failure_streak, _last_probed, _probe_cycle_published
    _probe_failure_streak = 0 if refreshed else _probe_failure_streak + 1
    _last_probed = time.monotonic()
    _probe_cycle_published = True


def _publish_probe_outcome(outcome, refreshed):
    """Status, evidence, credential AND the backoff for the next cycle, in
    one critical section, so a reader never pairs them across cycles."""
    global _probe_status, _probe_headers, _probe_unknown_buckets, \
        _claude_credential, _probe_cooldown_until
    with _limits_lock:
        _probe_status = outcome.status
        _probe_headers = list(outcome.headers)
        _probe_unknown_buckets = list(outcome.unknown_buckets)
        if outcome.credential is not None:
            _claude_credential = outcome.credential
        if outcome.cooldown_until is not None:
            _probe_cooldown_until = outcome.cooldown_until
        _note_probe_schedule_locked(refreshed)


def _publish_probe_status(status):
    """A status-only outcome (nothing was probed, or the probe crashed).

    The header evidence is the most recent cycle's, and this cycle saw
    none: publish it empty rather than leave an earlier fallback's names
    and unknown buckets beside the new status (Codex review of #111).
    The credential block stays: it describes the saved credential, not
    the cycle. The miss still counts for the backoff.
    """
    global _probe_status, _probe_headers, _probe_unknown_buckets
    with _limits_lock:
        _probe_status = status
        _probe_headers = []
        _probe_unknown_buckets = []
        _note_probe_schedule_locked(False)


def _probe_view():
    """Every correlated probe field for ``GET /``, copied under ONE lock.

    OBS-18 (a): the backoff behind ``claudeProbe``, content-free. Dashes
    on the screen used to look the same whether the probe was failing
    every four minutes or resting at 960 s after a streak; ``GET /`` now
    says which. Status, evidence, credential and backoff are read in the
    same critical section the probe thread publishes them in, so the
    payload never pairs an old status with a new cycle's numbers.
    """
    with _limits_lock:
        status = _probe_status
        credential = dict(_claude_credential)
        headers = list(_probe_headers)
        unknown = list(_probe_unknown_buckets)
        streak = _probe_failure_streak
        interval_s = _probe_interval_s()
        probed_at = _last_probed
        cooldown_left = _probe_cooldown_until - time.time()
    return {
        "claudeProbe": status,
        "claudeProbeStreak": streak,
        "claudeProbeIntervalS": int(interval_s),
        "claudeProbeCooldownLeftS": (int(math.ceil(cooldown_left))
                                     if cooldown_left > 0 else None),
        "claudeProbeAgeS": (int(time.monotonic() - probed_at)
                            if probed_at else None),
        "claudeCredential": credential,
        "ratelimitHeaders": headers,
        "unknownRateLimitBuckets": unknown,
    }


def _probe_diagnostics():
    """The backoff fields of :func:`_probe_view` alone (tests, smoke)."""
    view = _probe_view()
    return {key: view[key] for key in (
        "claudeProbeStreak", "claudeProbeIntervalS",
        "claudeProbeCooldownLeftS", "claudeProbeAgeS")}


def _probe_limits():
    """One minimal API call; returns {sessionPct, sessionResetMin,
    weekPct, weekResetMin} or None if anything is missing along the way."""
    with _limits_lock:
        _load_probe_state_locked()
        resting = time.time() < _probe_cooldown_until
        if resting:
            # Cooling down after a 429 -- the status stays on the backoff
            # string, and the skipped cycle counts as a miss HERE, in the
            # same section, not later in _refresh_limits.
            _note_probe_schedule_locked(False)
    if resting:
        return None
    lock = _hold_probe_lock()
    if lock is None:
        # Another instance owns the upstream traffic right now. No network
        # activity from here -- the other instance's answer serves the
        # device's needs anyway.
        _publish_probe_status("probe_held_by_other_instance")
        return None
    try:
        return _probe_limits_locked()
    finally:
        lock.close()  # closing the file = releasing the flock


def _probe_limits_locked():
    """Run one cycle and publish its evidence once (see _ProbeOutcome).

    A cycle that raises publishes nothing itself: ``_refresh_limits``
    records the crash as its own status, with empty header evidence.
    """
    outcome = _ProbeOutcome(_probe_status)
    found = _probe_cycle(outcome)
    _publish_probe_outcome(outcome, bool(found))
    return found


def _probe_cycle(outcome):
    candidates = _read_oauth_candidates()
    outcome.credential = _oauth_credential_snapshot(candidates)
    if not candidates:
        outcome.status = "no_claude_oauth_token"
        # OBS-20: on macOS the keychain says why, so the status and the
        # credential block carry it — "the user clicked Deny" is a
        # different fix from "never logged in".
        if _keychain_reason and not _IS_WINDOWS:
            outcome.status += f": {_keychain_reason}"
            outcome.credential = dict(outcome.credential,
                                      reason=_keychain_reason)
        return None

    token = None
    for candidate, expires_at in candidates:
        if candidate in _dead_tokens:
            # The value is already rejected by the API -- wait for a new one
            # instead of feeding the 429 penalty box with a known dead token.
            outcome.status = "token_dead_awaiting_refresh"
            continue
        if expires_at and expires_at / 1000 < time.time():
            # The token has expired; Claude Code renews it in the keychain
            # the next time it talks to the API -- wait and reread.
            outcome.status = (f"token_expired_"
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
            outcome.status = f"usage_http_{error.code}"
            if error.code == 429:
                # Rate-limited: every further call extends the penalty.
                # Abort the whole cycle -- no second source, no header
                # probe -- and rest at least ten minutes (more if
                # Retry-After demands it).
                retry_after = 0
                try:
                    retry_after = int((error.headers or {}).get(
                        "Retry-After", 0))
                except (TypeError, ValueError):
                    retry_after = 0
                # Published together with the status in
                # _publish_probe_outcome, so GET / never sees a new
                # cooldown beside an old status.
                outcome.cooldown_until = time.time() + max(retry_after, 600)
                outcome.status = (
                    f"usage_http_429 + backoff_until_"
                    f"{datetime.fromtimestamp(outcome.cooldown_until):%H:%M}")
                # A restart must not forget the penalty.
                _save_probe_state(outcome.cooldown_until)
                return None
            if error.code in (401, 403):
                # A rejected token says nothing about the next source -- try
                # it before giving up. But NEVER send the same value again:
                # it is dead until the source delivers a new one (a
                # wrongly marked value self-heals the same way, the next
                # renewal changes the string).
                _dead_tokens[candidate] = f"http_{error.code}"
                while len(_dead_tokens) > 8:
                    _dead_tokens.pop(next(iter(_dead_tokens)))
                continue
            token = candidate
            break
        except Exception as error:
            outcome.status = f"usage_request_failed: {type(error).__name__}"
            token = candidate
            break
        else:
            found = _parse_usage_limits(usage, time.time())
            # Do not require sessionPct: without an active 5-hour window
            # (only mobile or cloud work) the API reports the session row
            # with a passed reset, and the parser correctly skips it. The
            # week figures are still valid -- do not throw them away.
            if found:
                outcome.status = "usage_http_200 + ok"
                return found
            outcome.status = "usage_http_200 + no_mapped_limits"
            token = candidate
            break

    if token is None:
        # Every source rejected or expired -- the header probe with the
        # same tokens would be the same answer at a higher cost.
        return None

    body = json.dumps({
        "model": "claude-haiku-4-5",  # the cheapest probe; the headers are the same
        "max_tokens": 0,              # prefill without output -- effectively free
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
    # The header probe must never overwrite the usage outcome -- that is how
    # a mapping bug was masked as "http_401" for a whole evening.
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            headers = dict(resp.headers)
    except urllib.error.HTTPError as e:
        outcome.status += f"; fallback_http_{e.code}"
        headers = dict(e.headers) if e.headers else {}
    except Exception as e:
        outcome.status += f"; fallback_failed: {type(e).__name__}"
        return None
    else:
        outcome.status += "; fallback_http_200"

    now_ts = time.time()
    # Diagnostics at the first probe: the header names come from
    # Clawdmeter's description, not from our own observation -- the log is
    # the answer key if they differ.
    global _headers_logged
    if not _headers_logged:
        _headers_logged = True
        for name in sorted(headers):
            if "ratelimit" in name.lower():
                log.info("ratelimit-header: %s", name)
    # Three windows, the same as Claude's own usage panel: 5-hour, the week
    # (all models) and the week for the heaviest model (Fable/Opus). The
    # window name in the header varies ("5h", "7d", "7d_opus", ...) -- map
    # on content, not on the exact name.
    found = _parse_limit_headers(headers, now_ts)

    outcome.headers = sorted(
        n for n in headers if "ratelimit" in n.lower())
    outcome.unknown_buckets = found.pop("unknownBuckets", [])
    if not found:
        outcome.status += " + no_mapped_headers"
        return None
    outcome.status += " + ok"
    return found


def _probe_interval_s():
    """Back off on repeated failures: 240 -> 480 -> 960 s (cap).

    A dead token used to hammer the API every other minute for hours --
    that pattern triggered a 429 penalty box. A successful probe restores
    the cadence.
    """
    if _probe_status.startswith((
            "no_claude_oauth_token",
            "token_expired_",
            "token_dead_awaiting_refresh")):
        # These states stop in _probe_limits_locked before urlopen. A short
        # check only rereads the local keychain/credentials file and quickly
        # notices when the official Claude client has changed the token.
        return AUTH_RECOVERY_EVERY_S
    if _claude_statusline_bridged and _probe_status == "usage_http_200 + ok":
        # The statusLine bridge covers both windows with a fresh sample:
        # the probe is a cross-check and spends fewer of the shared
        # bucket's calls. Any failure state keeps its own ladder.
        return PROBE_WHEN_BRIDGED_S
    return LIMITS_EVERY_S * (2 ** min(_probe_failure_streak, 2))


def _refresh_limits():
    global _last_limits, _limits_refreshing, _probe_status_logged, \
        _probe_cycle_published
    with _limits_lock:
        _probe_cycle_published = False  # this cycle's flag, nobody else's
    try:
        refreshed = _probe_limits()
    except Exception as e:
        refreshed = None
        # If the probe crashes BEFORE it has set a status the old string
        # would stand -- at worst "usage_http_200 + ok" while the values
        # vanish. A crash is a bug (not an ordinary error path): set a
        # status of its own and log the traceback once per episode.
        crashed = f"probe_crashed: {type(e).__name__}"
        if _probe_status != crashed:
            log.exception("the claude probe crashed (status was %s)",
                          _probe_status)
        _publish_probe_status(crashed)
    # The transition log: a 401 appearing, the 429 backoff, the recovery.
    # Read here on the probe thread (the only writer), after the status
    # string is fully built -- the same state stays put without writing
    # another line.
    if _probe_status != _probe_status_logged:
        log.info("claude-probe: %s -> %s",
                 _probe_status_logged or "start", _probe_status)
        _probe_status_logged = _probe_status
    with _limits_lock:
        # Every path through _probe_limits records its streak and
        # timestamp in the critical section it publishes in (outcome,
        # status-only, or the resting 429 cycle); this fallback covers a
        # replaced _probe_limits, such as the tests', and nothing else.
        if not _probe_cycle_published:
            _note_probe_schedule_locked(refreshed)
        _probe_cycle_published = False
        _last_limits = refreshed
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
# Codex limits: PASSIVE reading -- the Codex CLI writes its rate limits
# into the rollout files (~/.codex/sessions/**/rollout-*.jsonl) every time
# it runs: used_percent, window_minutes (10080 = the week window) and
# resets_at (epoch). We read the latest snapshot; if the window has reset
# since (resets_at passed) the figure is meaningless and we serve null --
# never old percentages pretending to be fresh.

# Follows CODEX_HOME, just like the Codex CLI and the month scan: the
# desktop app and managed Windows installs set it, and run-windows-task.ps1
# exports it before the service starts. A hard-coded ~/.codex here made the
# readiness gate, the rate-limit scan, agent status and Max Tracker read a
# different profile than the month value -- on a Codex-only machine with
# its own CODEX_HOME the port never opened.
CODEX_SESSIONS = codex_usage.default_sessions_dir()
CODEX_LIMITS_EVERY_S = 30
CODEX_APP_SERVER_TIMEOUT_S = 15
CODEX_LIMIT_SCAN_BYTES = 1024 * 1024
CODEX_WEEK_MINUTES = 10080
_codex_limits_lock = threading.Lock()
_last_codex_limits = None


def _any_provider_dir(projects_dir):
    """True when at least one of the providers' directories exists.

    Startup waits on this instead of on Claude alone: either provider is
    enough for the service to have something to serve.
    """
    return projects_dir.is_dir() or CODEX_SESSIONS.is_dir()


_last_codex_read = 0.0
_codex_refreshing = False


# Moved to codex_rollout.py (a leaf module max_tracker.py's backfill can
# import too, without the two modules ever importing each other): the
# envelope-acceptance rule and timestamp parsing are re-exported here under
# their original private names so every existing call site is unchanged.
_codex_rollout_rate_limits = codex_rollout_rate_limits
_observation_timestamp = observation_timestamp


def _codex_window(win, now_ts):
    """{used_percent, window_minutes, resets_at} -> (pct, reset_min) or None."""
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
    """Lines from ``stream`` in a queue, read by a daemon thread.

    ``select`` would not do: on Windows ``select()`` takes only sockets,
    never pipes, so the app-server read raised there instead of fetching
    the Codex quota. A thread blocking in ``readline`` gives the same
    non-blocking read on every platform.

    The thread is a daemon and owns nothing: if the app server dies -- or
    we kill it in ``finally`` -- ``readline`` returns empty and the thread
    ends by itself. ``None`` in the queue is the EOF sentinel, so the
    reader need not wait out its whole deadline when the stream is already
    closed.
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
            pass  # a closed stream during shutdown is expected, not an error
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
            if line is None:  # the EOF sentinel: the stream is done
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
            # The pipes are closed explicitly, in this order: the process
            # is already dead, so the reader thread has left readline and
            # cannot be woken in the middle of a closed stream. Without
            # this, three descriptors per cycle hung on the GC -- once
            # every 30 s, around the clock, in a service that never
            # restarts.
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
    """The latest rate_limits snapshot from the newest rollout files."""
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
_ERROR_LOG_THROTTLE_S = 300.0  # persistent error: one log line per 5 min is enough
_max_tracker_save_failing_since = None  # monotonic; None = saving fine
_last_save_error_logged = None  # None = never logged (0.0 swallows the first
                                # error on a freshly started machine --
                                # monotonic counts from boot)


def _max_tracker_writer(store):
    global _max_tracker_dirty, _max_tracker_writer_running, \
        _last_save_error_logged, _max_tracker_save_failing_since
    while True:
        with _max_tracker_writer_lock:
            if not _max_tracker_dirty:
                _max_tracker_writer_running = False
                return
            _max_tracker_dirty = False
        try:
            store.save()
            if _last_save_error_logged is not None:
                # A successful write closes the error episode: log the end
                # and reset the throttle, so the next error (a NEW episode)
                # logs at once instead of inheriting the old window.
                log.info("max-tracker: save succeeded again after %.0f s",
                         time.monotonic()
                         - (_max_tracker_save_failing_since
                            or time.monotonic()))
                _last_save_error_logged = None
            _max_tracker_save_failing_since = None
        except Exception:
            # A failed write must not drop the dirty signal: mark again
            # and exit, so the NEXT observation (or the final flush) tries
            # again -- no hot loop, no silent data loss. The log is
            # throttled: a broken write target must not fill the file.
            # The episode shows on GET / (maxTrackerSaveOk) so a full disk
            # (ENOSPC, issue #62) is degraded health, not a log line nobody
            # reads.
            now = time.monotonic()
            if _max_tracker_save_failing_since is None:
                _max_tracker_save_failing_since = now
            if (_last_save_error_logged is None or
                    now - _last_save_error_logged >= _ERROR_LOG_THROTTLE_S):
                _last_save_error_logged = now
                log.exception(
                    "max-tracker: save failed — the observations stay in "
                    "memory and the next attempt is coming")
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
        _last_result_at, _compute_failing_since, _last_compute_error_logged
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
            log.exception("usage recompute crashed — /api/tokens serves "
                          "frozen figures until it succeeds again")
    else:
        if _compute_failing_since is not None:
            log.info("usage recompute healthy again after %.0f s",
                     time.monotonic() - _compute_failing_since)
            _compute_failing_since = None
            # The recovery closes the episode: the next error is a NEW
            # episode and must log at once, not inherit the old throttle
            # window.
            _last_compute_error_logged = None
    with _cache_lock:
        if refreshed is not None:
            _last_result = refreshed
            _last_result_at = time.monotonic()
        _last_computed = time.monotonic()
        _snapshot_refreshing = False


def _usage_totals_state_locked(have_result):
    """The additive ``usageTotals`` block, on ``/api/tokens`` and ``GET /``.

    ``have_result`` is whether the counters in the payload being built
    are a completed scan (True) or placeholder zeros (False). The caller
    decides that UNDER ``_cache_lock`` from the same read that picked the
    counters, so the block can never describe a different snapshot than
    the one it rides on (a first scan that landed between the two reads
    used to label zeros ``ready``, and the publisher would have sent
    them). Three states plus one honest flag (issue #62):

    ``refreshing``
        The first history scan has not finished. ``placeholder`` is true:
        the counters are zeros, not measurements. ``sinceS`` is how long
        the service has been up. Quota percentages in the same payload are
        live: they come from the probe and the quota cache, not the scan.
    ``ready``
        The counters are the last completed scan, ``ageS`` seconds old.
    ``failing``
        The recompute is crashing (OBS-08). With a completed scan behind
        it the counters are frozen at ``ageS`` old; without one they are
        still placeholders (``placeholder`` true, ``sinceS``), and the
        state says *failing*, not refreshing, so a hook or doctor does not
        call a broken scan a warm-up. ``usageComputeOk`` /
        ``usageComputeFailingForS`` on ``GET /`` carry the detail.
    """
    now = time.monotonic()
    failing = _compute_failing_since is not None
    if not have_result:
        return {"state": "failing" if failing else "refreshing",
                "sinceS": int(now - _SERVER_STARTED_MONO),
                "placeholder": True}
    age = (int(now - _last_result_at)
           if _last_result_at is not None else None)
    return {"state": "failing" if failing else "ready",
            "ageS": age,
            "placeholder": False}


def _usage_totals_state():
    """``GET /``'s view of the block: one consistent read under the lock."""
    with _cache_lock:
        return _usage_totals_state_locked(_last_result is not None)


def usage_totals_are_placeholders(payload):
    """True when a ``/api/tokens`` payload's counters are not measurements.

    The one question every consumer has to ask before treating the volume
    counters as numbers: the publisher (never relay a placeholder), the
    handler (never hand one to a client that would apply it), and any
    tool reading the endpoint.
    """
    if not isinstance(payload, dict):
        return False
    totals = payload.get("usageTotals")
    return isinstance(totals, dict) and totals.get("placeholder") is True


def _startup_totals_placeholder(projects_dir):
    """What ``/api/tokens`` serves for the volume counters until the first
    scan completes.

    The firmware contract requires the four counters to be numbers
    (tokens_parse.c: ``if (!num(root, "dayTokens", &day)) goto done;``),
    so an honest ``null`` is not an option there. Zeros it is, with
    ``usageTotals.state == "refreshing"`` saying so in the same payload;
    ``claudeSourcePresent`` keeps its own meaning (is there a Claude
    directory at all). The value block is the real builder fed zero
    volume, so its ``state``/``cost_source`` are exactly what a genuinely
    empty month would produce.
    """
    return {
        "v": 1,
        "dayTokens": 0,
        "dayTokensPerHour": 0,
        "daySessions": 0,
        "monthTokens": 0,
        "claudeSourcePresent": projects_dir.is_dir(),
        "value": value_meter.build_payload(
            0.0, 0, 0,
            claude_plan=_claude_plan, codex_plan=_codex_plan,
            plan_costs=_plan_costs, table=_price_table,
            claude_usd=0.0, codex_usd=0.0),
        "at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _start_usage_refresh(projects_dir, max_tracker_store, name):
    """Start one background recompute. Caller holds ``_cache_lock`` and has
    already set ``_snapshot_refreshing``; this only spawns the thread."""
    threading.Thread(
        target=_refresh_usage_totals,
        args=(projects_dir, max_tracker_store),
        name=name,
        daemon=True,
    ).start()


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
    global _snapshot_refreshing
    with _cache_lock:
        if _last_result is None:
            # The first scan used to sit here, UNDER the lock, and took
            # 211 s on a Mac with a large history: every /api/tokens call
            # queued behind it and timed out, so the panel went STALE
            # after every restart of a healthy service (issue #62). Now it
            # is started in the background and the answer is a placeholder
            # that says what it is (usageTotals below). If it crashes it
            # is restarted only after RECOMPUTE_EVERY_S -- not per call.
            now = time.monotonic()
            if (not _snapshot_refreshing and
                    (_last_computed == 0.0 or
                     now - _last_computed > RECOMPUTE_EVERY_S)):
                _snapshot_refreshing = True
                _start_usage_refresh(projects_dir, max_tracker_store,
                                     "usage-total-first-scan")
            result = _startup_totals_placeholder(projects_dir)
            totals = _usage_totals_state_locked(have_result=False)
        else:
            if (time.monotonic() - _last_computed > RECOMPUTE_EVERY_S and
                    not _snapshot_refreshing):
                _snapshot_refreshing = True
                _start_usage_refresh(projects_dir, max_tracker_store,
                                     "usage-total-refresh")
            result = dict(_last_result)
            totals = _usage_totals_state_locked(have_result=True)

    # null = honest absence (keychain/probe/logs unavailable) -- the screen
    # shows dashes, never invented percentages. The same rule as sharePct.
    current_ts = time.time() if now_ts is None else now_ts
    usage_history = _get_usage_history() if history is None else history
    cache = _get_quota_cache() if quota_cache is None else quota_cache
    claude = _merge_claude_plan_usage(
        _merge_claude_statusline(get_limits() or {}, cache, current_ts),
        cache, current_ts)
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
    # The reset times come from the entry whether live or cached: a cached
    # entry carries its cycle's real reset (quota_cache expires entries at
    # a passed reset), and the "today" delta must SURVIVE a 429 blackout
    # -- the panel may become honestly older (the stale flag), never empty
    # (the requirement of 2026-08-14: bulletproof for everyone running
    # this). Only live observations are RECORDED into the history, though
    # -- a cached percentage is not a new measurement.
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
    # The OTA announcement rides on the quota poll: zero new infrastructure,
    # and the device decides itself (against its running version) whether
    # to show the notice.
    result["otaAvailableVersion"] = _ota_available_version()
    # Additive key (tokens_parse.c skips unknown top-level keys): says
    # whether the volume counters above are measurements, placeholders or
    # frozen. Captured under the lock above, from the SAME read that chose
    # the counters.
    result["usageTotals"] = totals
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
    projects_dir = None  # set in main
    agent_status = None  # background service, set in main
    max_tracker_store = None  # set in main
    github_monitor = None  # optional public repo monitor, set in main
    plans = {"claude": None, "codex": None}  # set in main from --*-plan
    interaction_store = None  # "Needs You", off by default; set in main
    interaction_timeout_s = 120.0  # set in main from --interaction-timeout
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
    # Content-free presence evidence for the startup health. The existing
    # panel polls /api/agent-status every second. Two known panel GETs from
    # the same non-loopback client within a short window are stronger
    # evidence than a lone curl. The candidate address is held only in
    # process memory and is never returned or logged.
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
    request_drain_limit = REQUEST_DRAIN_LIMIT
    request_drain_timeout_s = REQUEST_DRAIN_TIMEOUT_S

    def handle_one_request(self):
        # New request, new bookkeeping: the body is unread until
        # _read_json_body says otherwise.
        self._request_body_consumed = False
        super().handle_one_request()

    def _advertised_body_length(self):
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except (AttributeError, TypeError, ValueError):
            return 0
        return length if length > 0 else 0

    def _drain_request_body(self):
        """Drain an unread, announced body before the response is sent.

        An early rejection (403 wrong Host/Origin, 415 wrong Content-Type,
        404 disabled route) answers on the headers alone and then closes --
        with the body still unread in the socket. Windows treats a close
        with unread bytes as an abort (WSAECONNABORTED, WinError 10053) and
        discards the response already sent, so a real hook client sees an
        abort where a 403/415/404 was meant.

        The same idea BoundedThreadingHTTPServer._reject_busy uses before
        its 503, but not the same cap: that one drains at most 8 KiB of
        *headers* and stops at the end of the headers, while this drain
        covers the announced body. The deadline (0.05 s) is shared. Two
        caps apply here: at most request_drain_limit bytes AND at most
        request_drain_timeout_s in total (a deadline of its own, not just
        a socket timeout per read -- otherwise a peer dripping one byte at
        a time could hold us forever). A slow or hostile peer therefore
        never costs more than that. The bytes are discarded unopened --
        never parsed, never logged.

        Returns the number of drained bytes (0 when there was nothing to
        do), which only the tests care about.
        """
        if getattr(self, "_request_body_consumed", False):
            return 0
        # Only one attempt per request, however it went.
        self._request_body_consumed = True
        remaining = min(self._advertised_body_length(),
                        self.request_drain_limit)
        if remaining <= 0:
            return 0
        try:
            previous_timeout = self.connection.gettimeout()
            self.connection.settimeout(self.request_drain_timeout_s)
        except (AttributeError, OSError):
            return 0
        drained = 0
        deadline = time.monotonic() + self.request_drain_timeout_s
        try:
            while remaining > 0 and time.monotonic() < deadline:
                chunk = self.rfile.read1(min(4096, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                drained += len(chunk)
        except (AttributeError, OSError, ValueError):
            pass
        finally:
            try:
                self.connection.settimeout(previous_timeout)
            except OSError:
                pass
        return drained

    def _send(self, code, payload):
        self._drain_request_body()
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
        self._drain_request_body()
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
            log.info("startup-health: panel contact READY via %s", self.path)

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
        # The whole announced body is now in memory, nothing left in the
        # socket -- _drain_request_body has nothing to do.
        self._request_body_consumed = True
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

    # The header a client sends to say it understands ``usageTotals`` and
    # will not apply placeholder counters as measurements. Firmware from
    # 2026-09-10 on sends it on every fetch; the hook and the smoke test
    # send it too. A client without it gets the contract's error form
    # instead of a placeholder, so an already-flashed panel keeps its last
    # good values (and goes honestly STALE) rather than learning zeros.
    ACCEPTS_HEADER = "X-VibePulse-Accepts"
    ACCEPTS_USAGE_TOTALS = "usage-totals"

    def _accepts_usage_totals(self):
        headers = getattr(self, "headers", None)
        accepts = (headers.get(self.ACCEPTS_HEADER) if headers else None) or ""
        return self.ACCEPTS_USAGE_TOTALS in accepts.lower()

    class _NotMeasuredYet(Exception):
        """Raised by _tokens_payload for a client that must not see zeros."""

        def __init__(self, totals):
            super().__init__("usage totals not measured yet")
            self.totals = totals

    def _tokens_payload(self):
        payload = get_snapshot(self.projects_dir,
                               max_tracker_store=self.max_tracker_store)
        if (usage_totals_are_placeholders(payload) and
                not self._accepts_usage_totals()):
            raise self._NotMeasuredYet(payload["usageTotals"])
        return payload

    def _reply(self, produce):
        """Answer 200 with produce(), otherwise 500 {"error": ...} -- the
        screen rejects the error form by contract and keeps its last good
        values. The cause must still always show in the log: a silent 500
        is how the max-tracker bugs stayed invisible.

        The producer and the response write are judged SEPARATELY: a
        ConnectionError/TimeoutError from the producer is a server error
        that must be logged and become a 500 -- only during the write
        itself does it mean the client went away.

        One exception: a placeholder (issue #62) to a client that has not
        said it understands them is not a server error but a 503 in the
        error form, with the usageTotals block beside it so the reader
        knows why."""
        try:
            payload = produce()
        except self._NotMeasuredYet as pending:
            try:
                self._send(503, {"error": "usage totals not measured yet",
                                 "usageTotals": pending.totals})
            except OSError:
                pass
            return
        except Exception:
            log.exception("500 on %s", self.path)
            try:
                self._send(500, {"error": "internal server error"})
            except OSError:
                pass
            return
        try:
            self._send(200, payload)
        except (ConnectionError, TimeoutError):
            pass  # the client went away mid-response -- not a server error
        except Exception:
            # E.g. an unserializable payload -- a server error, not the client's.
            log.exception("500 on %s (response write)", self.path)
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
            log.warning("the pending entry did not fit in /api/agent-status "
                        "(%d jobs) — the agent list takes precedence and "
                        "the entry is left out", len(pending))
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
            log.exception("the interaction crashed — leaving the decision "
                          "to the terminal")
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
            log.exception("the Codex question crashed — leaving the "
                          "decision to the computer")
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
            log.exception("the Codex permission crashed — leaving the "
                          "decision to the computer")
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
        log.warning("panic stop from the device: %d pending decisions denied",
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
                log.warning("hook POST from %s rejected — hooks may only "
                            "come from this machine",
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
            self._reply(self._tokens_payload)
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
        # ONE read of failing_since -- the ok flag and the duration must
        # come from the same instant. Two reads let a recovery in between
        # put None into the subtraction (a 500 on the diagnostics route
        # itself) and a freshly started episode give ok=true with a
        # duration beside it.
        failing_since = _compute_failing_since
        save_failing_since = _max_tracker_save_failing_since
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
                # Status, backoff, credential and header evidence come from
                # one locked read so they always describe the same cycle.
                **_probe_view(),
                "claudeLocalUsage": _claude_plan_usage_status,
                "claudeStatusline": {**_claude_statusline_view,
                                     "bridged": _claude_statusline_bridged,
                                     "account": "assumed-single"},
                # GET / is never parsed by the screen -- fields can be
                # added without contract risk.
                "usageComputeOk": failing_since is None,
                "usageComputeFailingForS":
                    (int(time.monotonic() - failing_since)
                     if failing_since is not None else None),
                "usageTotals": _usage_totals_state(),
                "maxTrackerSaveOk": save_failing_since is None,
                "maxTrackerSaveFailingForS":
                    (int(time.monotonic() - save_failing_since)
                     if save_failing_since is not None else None),
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
        pass  # 30 s polling must not fill the log

    def log_error(self, fmt, *args):
        # BaseHTTPRequestHandler routes log_error through log_message, so
        # the silenced access log silenced the errors with it. The access
        # log must be quiet; the errors must not.
        log.warning("http %s: %s", self.address_string(), fmt % args)


def _build_arg_parser():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8737)
    ap.add_argument("--dir", default=os.path.expanduser("~/.claude/projects"))
    ap.add_argument(
        "--claude-plan", choices=["pro", "max5x", "max20x"], default=None,
        help="the Claude plan for Max Tracker's badge (optional, "
             "allowlisted in max_tracker.PLAN_LABELS)")
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
        help="the Codex plan for Max Tracker's badge (optional, "
             "allowlisted in max_tracker.PLAN_LABELS)")
    ap.add_argument(
        "--github-repo", type=normalize_repo,
        default=os.environ.get("VIBEPULSE_GITHUB_REPO") or None,
        help="optional public GitHub repo as owner/repository. "
             "Can also be set with VIBEPULSE_GITHUB_REPO.")
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
            log.error("invalid VibePulse configuration in %s — saved "
                      "interactions are turned off", config_path,
                      exc_info=True)
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
            except Exception:  # noqa: S110 - best-effort teardown on the failure path
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
    # None = never logged. Not 0.0: time.monotonic() counts from boot, so
    # on a machine up for less than the throttle window "now - 0.0" is
    # below it and the FIRST failure would be swallowed -- the same trap
    # _last_compute_error_logged already documents (CI's fresh VM).
    last_error_logged = None
    while not stop_event.is_set():
        try:
            if store.backfill_step():
                _mark_max_tracker_dirty(store)
        except Exception as exc:
            # The loop must survive a bad file (OBS-08's lesson: a crashed
            # recompute must not freeze the numbers), but not silently:
            # the first failure logs at once, then one line per ten
            # minutes names the failure class.
            now = time.monotonic()
            if last_error_logged is None or now - last_error_logged >= 600:
                last_error_logged = now
                log.warning("max-tracker backfill step failed: %s: %s",
                            type(exc).__name__, exc)
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
    log.info("starting: rev %s", _SERVER_REV)
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
        log.info("GitHub monitor started for the public repo %s "
                 "(stargazers: %s)", args.github_repo,
                 "auth" if github_token else "anonymous — the name becomes "
                 "'someone'")
    Handler.github_monitor = github_monitor

    Handler.projects_dir = Path(args.dir)
    if not _any_provider_dir(Handler.projects_dir):
        # Was: SystemExit. Under launchd (KeepAlive without
        # ThrottleInterval) that became a silent respawn every ~10 s that
        # filled the log. Wait instead -- the directory appears once the
        # agent has run a first time on the machine.
        #
        # Wait for EITHER provider, not just Claude. The Codex figures are
        # read from CODEX_SESSIONS and do not need projects_dir at all, so
        # a Codex-only machine has everything it needs -- had we waited for
        # Claude there, the port would never open, nothing would be
        # advertised over DNS-SD, and the panel would find a computer it
        # cannot ask. The README's prerequisite is "Claude Code and/or
        # Codex"; either is enough.
        log.warning("found neither %s nor %s — is Claude Code or Codex on "
                    "this machine? Waiting for one of them to appear "
                    "(Ctrl-C aborts).",
                    Handler.projects_dir, CODEX_SESSIONS)
        try:
            while not _any_provider_dir(Handler.projects_dir):
                time.sleep(30)
        except KeyboardInterrupt:
            raise SystemExit(1) from None
        log.info("found a provider directory — continuing startup.")
    if not Handler.projects_dir.is_dir():
        # Starts anyway: Path.glob on a directory that does not exist gives
        # an empty result without raising, so the Claude figures become
        # zero instead of an error. Codex-only is a fully valid state, not
        # a half-broken one.
        log.info("%s missing — continuing without Claude figures (Codex "
                 "found).",
                 Handler.projects_dir)

    # The first scan is a warm-up plus a log line -- /api/tokens redoes
    # get_snapshot per request anyway, and the HTTP threads already run it
    # in parallel, so the same call tolerates a background thread. It used
    # to sit BEFORE bind, which with large log directories kept the port
    # closed for minutes after every `launchctl kickstart`. That was always
    # sad for the screen; with Needs You it became actually wrong -- a hook
    # that gets connection refused falls back (entirely safely, but
    # entirely needlessly) to the terminal although the service is seconds
    # from being able to hold it. Now the server binds at once and warms
    # up in the background: agent status and the hooks are incremental and
    # answer meaningfully right away, /api/tokens answers once the scan is
    # done (the screen shows dashes/stale until then, just as on a network
    # error).
    def _first_scan_warmup():
        global _snapshot_refreshing
        t0 = time.monotonic()
        # Do the scan itself here, on this thread, instead of going via
        # get_snapshot: that now answers at once with a placeholder (issue
        # #62) and starts the scan in the background, and a log line built
        # on the placeholder would have said "0 tokens today". The claim is
        # made under the lock so an early /api/tokens and the warm-up never
        # run _compute at the same time; if we lose the race we wait for
        # that thread's result instead.
        with _cache_lock:
            claimed = _last_result is None and not _snapshot_refreshing
            if claimed:
                _snapshot_refreshing = True
        if claimed:
            _refresh_usage_totals(Handler.projects_dir)
        else:
            while (_last_result is None and
                   time.monotonic() - t0 < FIRST_SCAN_WAIT_S):
                time.sleep(0.2)
        snap = _last_result
        if snap is None:
            # _refresh_usage_totals has already logged the crash (throttled)
            # and usageTotals on GET / stays on refreshing until the next
            # attempt.
            log.warning("the first scan produced no result in %.0f s — "
                        "/api/tokens serves placeholders until a recompute "
                        "succeeds", time.monotonic() - t0)
            return
        # The same honesty as net.c and the simulator: without a Claude
        # source the zeros are not measurements, and "0 tokens today" in
        # the startup event reads as a day without work to whoever combs
        # the logs.
        if not snap.get("claudeSourcePresent", True):
            log.info("first scan %.1f s: no Claude source on this machine "
                     "— volume unknown, not zero",
                     time.monotonic() - t0)
            return
        log.info("first scan %.1f s: %s tokens today, %d sessions, "
                 "%s this month",
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
            "interaction %s: %s", action,
            json.dumps(row, sort_keys=True)))
    if secret is None and interaction_config.agent_status_relay:
        secret = interactions.read_device_key()
    if interaction_config.legacy_claude_panel_v1:
        log.warning("UNSAFE COMPATIBILITY ON: the old Claude panel's v1 "
                    "answers lack the provider/digest binding. Turn it off "
                    "with --no-legacy-claude-panel-v1 once old firmware is "
                    "no longer in use. Codex stays v2.")
    if Handler.interaction_store is not None:
        log.info("Needs You on: Claude=%s, Codex=%s on 127.0.0.1:%d, "
                 "holding %.0f s. "
                 "Device key: %s. Content to the screen: %s",
                 "yes" if interaction_config.claude_interactions else "no",
                 "yes" if interaction_config.codex_interactions else "no",
                 args.port, Handler.interaction_timeout_s,
                 "present" if secret else "MISSING — the device cannot answer",
                 "yes (--interaction-detail)"
                 if interaction_config.interaction_detail
                 else "no, only that something is waiting")
        if not secret:
            log.warning("no device key found — hooks are parked and fall "
                        "back to the terminal. Set TK_VIBEPULSE_DEVICE_KEY "
                        "in secrets.h (the same value the screen is built "
                        "with) to be able to answer.")

    interaction_relay_adapter = _configure_interaction_relay(
        interaction_config,
        secret,
        audit=lambda action, row: log.info(
            "encrypted interaction relay %s: %s", action,
            json.dumps(row, sort_keys=True)),
    )
    if Handler.interaction_relay_status == "ready":
        log.info("encrypted Needs You relay ready (E2E; no questions or "
                 "commands are logged)")
    elif Handler.interaction_relay_status == "disabled":
        log.warning("encrypted Needs You relay off: %s; LAN and the "
                    "terminal fallback continue",
                    Handler.interaction_relay_reason)
    if Handler.agent_status_relay_status == "ready":
        log.info("encrypted agent-status relay ready (E2E; fixed size, "
                 "short lifetime, no plaintext at the cloud)")
    elif Handler.agent_status_relay_status == "disabled":
        log.warning("encrypted agent-status relay off: %s; direct LAN "
                    "continues", Handler.agent_status_relay_reason)

    relay_publisher = None
    if args.publish:
        # The producers ARE the handler's: the relay can never drift from
        # what the LAN endpoints serve. Agent status and Needs You are
        # deliberately not published -- the relay carries figures, never
        # activity (the same boundary the firmware's
        # test/test_relay_boundary.py holds).
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
        log.info("publishing figures to the relay as \"%s\" (at most every "
                 "5 min for quotas and every 30 min for GitHub/Max Tracker; "
                 "agent status and Needs You are NEVER published)",
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
        log.info("serving http://0.0.0.0:%d/api/tokens, "
                 "/api/agent-status, /api/max-tracker and /api/github "
                 "(LAN — do not expose it outward)", args.port)
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
            max_tracker_store.save()  # final flush, the same as the stop() flow
        except Exception:
            log.exception("max-tracker: final flush failed — today's peaks "
                          "may be missing after a restart")
        status_service.stop()
        if srv is not None:
            srv.server_close()


if __name__ == "__main__":
    main()
