#!/usr/bin/env python3
"""Smoke test for the VibePulse chain on the computer: one run, one verdict.

Automates steps 1-4 of the comb routine (docs/observability.md): the
server's identity and rev, the Claude probe's status, all three API
responses, the log file and the state files. No part needs hardware -- the
screen's truth (comb steps 5-6) stays manual.

    python3 tools/tokenserver/smoke.py [--base-url http://localhost:8737]

Exit: 0 = all ok, 1 = warnings (works, but worth a look), 2 = at least one
FAIL. Pure stdlib, like the rest of the service.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

if __package__:
    from .tokenserver import (DEFAULT_LOG_PATH, LIMITS_EVERY_S,
                              _LOG_CAP_BYTES, _state_dir,
                              _read_source_fingerprint)
else:  # run directly: python3 tools/tokenserver/smoke.py
    from tokenserver import (DEFAULT_LOG_PATH, LIMITS_EVERY_S,
                             _LOG_CAP_BYTES, _state_dir,
                             _read_source_fingerprint)

DEFAULT_BASE_URL = "http://localhost:8737"
# The same directory the service actually writes to, not a second guess at
# it: the smoke test must not be able to look in the wrong tree on a
# Windows machine.
STATE_DIR = _state_dir()
STATE_FILES = ("usage-history.json", "quota-cache.json", "max-tracker.json")
# A version-skew tripwire, not a second parser: the screen's parsers are
# contract-strict and reject the WHOLE response on a wrong version, missing
# required fields or a wrong HTTP status -- HTTP can look healthy while the
# display freezes. Checked here: the version, the top-level fields and the
# coarsest types (tokens_parse.c:328-332, agent_status_parse.c:529-542,
# max_tracker_parse.c:303-317); the providers' INNER fields deliberately
# live only in the C tests and the fixtures. If a contract is bumped this
# table is part of the bump -- and the tests' healthy responses ARE the sim
# fixtures, so a drift fails in test before it lies in production.


def _tokens_shape(payload):
    bad = [k for k in ("dayTokens", "dayTokensPerHour", "daySessions",
                       "monthTokens")
           if isinstance(payload.get(k), bool)
           or not isinstance(payload.get(k), (int, float))]
    return f"non-numeric fields {bad}" if bad else None


def _agents_shape(payload):
    agents = payload.get("agents")
    if not isinstance(agents, dict) or not {"claude", "codex"} <= set(agents):
        return "agents must be an object with claude and codex"
    return None


def _tracker_shape(payload):
    if not isinstance(payload.get("stale"), bool):
        return "stale must be a bool"
    empty = [k for k in ("claude", "codex")
             if not isinstance(payload.get(k), dict) or not payload.get(k)]
    if empty:
        return f"{empty} must be populated provider objects"
    return None


ENDPOINT_SHAPE = {
    "/api/tokens": (2, ("dayTokens", "dayTokensPerHour", "daySessions",
                        "monthTokens"), _tokens_shape),
    "/api/agent-status": (2, ("seq", "agents"), _agents_shape),
    "/api/max-tracker": (1, ("weeks", "stale", "codingStreakDays",
                             "claude", "codex"), _tracker_shape),
}
PROBE_OK = "usage_http_200 + ok"
# More start lines than this in ONE log file suggest a respawn loop, not
# deliberate restarts (the rotation at start keeps the file to one era).
RESPAWN_SUSPICION_COUNT = 10
USAGE_HISTORY_FRESH_S = 24 * 3600

OK, WARN, FAIL = "ok", "warn", "fail"


# The loaders reset SILENTLY on a wrong shape despite valid JSON
# (usage_history requires {v:1, samples:[...]}, quota_cache exactly
# {v:1, records:[...]}, max_tracker provider sections) -- the same data
# loss as OBS-11, but through shape instead of corrupt bytes. The same
# tripwire level as ENDPOINT_SHAPE: the top level, not the record content.
def _history_state_shape(payload):
    if (not isinstance(payload, dict) or payload.get("v") != 1
            or not isinstance(payload.get("samples"), list)):
        return "requires v=1 and a samples list"
    return None


def _quota_state_shape(payload):
    if (not isinstance(payload, dict) or set(payload) != {"v", "records"}
            or payload.get("v") != 1
            or not isinstance(payload.get("records"), list)):
        return "requires exactly {v: 1, records: [...]}"
    return None


def _tracker_state_shape(payload):
    if not isinstance(payload, dict) or not all(
            isinstance(payload.get(p), dict) for p in ("claude", "codex")):
        return "requires the provider sections claude and codex"
    return None


STATE_SHAPE = {
    "usage-history.json": _history_state_shape,
    "quota-cache.json": _quota_state_shape,
    "max-tracker.json": _tracker_state_shape,
}


def _get_json(url, timeout=5):
    """(HTTP status, parsed JSON). The status comes along: the screen rejects
    everything but 200 (torget_http.c), so a proxy or cache answering 502
    with a healthy-looking body must not turn green here. The server's own
    500 carries the contract's {"error": ...} body and is parsed too."""
    # X-VibePulse-Accepts: the smoke test understands usageTotals and never
    # counts placeholders as measurements, so the service may answer 200
    # with them during the first scan (a client without the header gets a
    # 503 in the error form).
    request = urllib.request.Request(
        url, headers={"X-VibePulse-Accepts": "usage-totals"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, json.loads(
                resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, None


def repo_rev():
    """The checkout's rev, for the comparison with the server's -- None
    without git."""
    try:
        rev = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
            cwd=os.path.dirname(os.path.abspath(__file__)),
        ).stdout.strip()
        return rev or None
    except Exception:
        return None


def check_server(base_url, checkout_rev=None, checkout_src=None):
    """Comb steps 1-2: identity, rev, source fingerprint and probe status
    from GET /. Comparisons are made only when both sides exist (None
    skips)."""
    try:
        status, root = _get_json(f"{base_url}/")
    except Exception as e:
        service_hint = (
            "check Task Scheduler or start python tokenserver.py by hand"
            if os.name == "nt" else
            "is it running? launchctl list | grep torget, or start "
            "python3 tokenserver.py by hand"
        )
        return [(FAIL, f"the server does not answer on {base_url}/ "
                       f"({type(e).__name__}) — {service_hint}")]
    if status != 200:
        return [(FAIL, f"HTTP {status} on {base_url}/ — wrong service, proxy "
                       f"or broken server")]
    # Another service on the port can answer with valid JSON that is not an
    # object (list, number, null) -- that must become [FAIL], not a
    # traceback.
    if (not isinstance(root, dict)
            or root.get("service") != "torget-tokenserver"):
        got = (root.get("service") if isinstance(root, dict)
               else type(root).__name__)
        return [(FAIL, f"wrong service on {base_url} — 'service' is {got!r}")]
    results = []
    rev = root.get("rev", "unknown")
    started = root.get("startedAt", "unknown")
    if checkout_rev and rev != checkout_rev:
        results.append((WARN, f"the server runs rev {rev}, the checkout is "
                              f"{checkout_rev} — does the plist's "
                              f"WorkingDirectory point at another directory?"))
    else:
        results.append((OK, f"torget-tokenserver rev {rev}, up since "
                            f"{started}"))
    # The fingerprint catches what the rev cannot: a dirty worktree at
    # start, or files edited AFTER start -- both share HEAD with the
    # checkout.
    src = root.get("srcFingerprint")
    if src and checkout_src and src != checkout_src:
        results.append((WARN, f"the server runs different source than the "
                              f"checkout (fingerprint {src} ≠ {checkout_src}) "
                              f"— edited after start or a dirty worktree; "
                              f"restart the service"))
    probe = root.get("claudeProbe", "missing")
    if probe == PROBE_OK:
        results.append((OK, f"claude probe: {probe}"))
    elif probe == "not_run":
        results.append((WARN, f"the claude probe has not run yet "
                              f"({LIMITS_EVERY_S} s cycle) "
                              "— rerun the smoke test in a moment"))
    else:
        # OBS-18: say whether the probe is hammering or resting — dashes
        # look the same either way from the screen.
        detail = ""
        streak = root.get("claudeProbeStreak")
        interval = root.get("claudeProbeIntervalS")
        cooldown = root.get("claudeProbeCooldownLeftS")
        if isinstance(streak, int) and isinstance(interval, int):
            detail = f" ({streak} misses in a row, next try in ≤{interval} s"
            if isinstance(cooldown, int):
                detail += f", 429 rest {cooldown} s left"
            detail += ")"
        results.append((WARN, f"claude probe: {probe}{detail} — see the "
                              f"table in docs/agent-setup.md"))
    credential = root.get("claudeCredential")
    if isinstance(credential, dict):
        credential_status = credential.get("status")
        remaining = credential.get("expiresInMin")
        if credential_status == "ready" and isinstance(remaining, int):
            results.append((OK, f"claude credential: {remaining} min left"))
        elif credential_status == "expiring" and isinstance(remaining, int):
            current = ("; the current quota source is live"
                       if probe == PROBE_OK else "")
            results.append((WARN, f"the saved claude credential expires in "
                                  f"{remaining} min{current} — start a new "
                                  "Claude Code CLI turn before then"))
        elif credential_status == "expired":
            if probe == PROBE_OK:
                results.append((WARN, "the saved claude credential has "
                                      "expired; the current quota source is "
                                      "live, but the next client gap can make "
                                      "Fable stale — start a new Claude Code "
                                      "CLI turn; no server restart needed"))
            else:
                results.append((WARN, "the saved claude credential has "
                                      "expired — start a new Claude Code CLI "
                                      "turn; VibePulse rereads it "
                                      "automatically"))
        else:
            results.append((WARN, "the claude credential cannot be watched "
                                  "for expiry — run setup doctor"))
    else:
        results.append((WARN, "claude credential missing from the "
                              "diagnostics — restart the tokenserver with "
                              "current code"))
    unknown = root.get("unknownRateLimitBuckets") or []
    if unknown:
        results.append((WARN, f"unknown rate-limit buckets: {unknown} — "
                              f"upstream has gained a new window; worth an "
                              f"entry in docs/observability-backlog.md"))
    # Without the key we are talking to an older server -- nothing to judge.
    if root.get("usageComputeOk") is False:
        secs = root.get("usageComputeFailingForS") or 0
        results.append((FAIL, f"the usage recompute has crashed (for {secs} s) "
                              f"— /api/tokens serves frozen figures that "
                              f"look fresh; read the log file"))
    totals = root.get("usageTotals")
    if isinstance(totals, dict) and totals.get("state") == "refreshing":
        secs = totals.get("sinceS")
        secs = secs if isinstance(secs, int) else "?"
        results.append((WARN, f"first scan in progress ({secs} s) — "
                              f"/api/tokens serves placeholders "
                              f"(usageTotals=refreshing); the quota is live, "
                              f"the volume not measured yet"))
    if root.get("maxTrackerSaveOk") is False:
        secs = root.get("maxTrackerSaveFailingForS") or 0
        results.append((WARN, f"max-tracker cannot save (for {secs} s) — "
                              f"the observations stay in memory and the "
                              f"next attempt is coming; check disk space "
                              f"and permissions"))
    return results


def check_endpoints(base_url):
    """Comb step 2, continued: do all three APIs answer in the contract's
    shape?"""
    results = []
    for path, (expected_v, required, shape_check) in ENDPOINT_SHAPE.items():
        try:
            status, payload = _get_json(f"{base_url}{path}")
        except Exception as e:
            results.append((FAIL, f"{path}: no answer ({type(e).__name__})"))
            continue
        if isinstance(payload, dict) and "error" in payload:
            # The error form is the contract's "something is broken in the
            # server" -- the screen rejects it and freezes rather than
            # guesses. The cause is in the log file since the server
            # started logging its 500s.
            results.append((FAIL, f"{path}: error form in the answer "
                                  f"({payload.get('error')}) — read the "
                                  f"log file"))
            continue
        if status != 200:
            results.append((FAIL, f"{path}: HTTP {status} — the screen "
                                  f"requires 200 and keeps its old values"))
            continue
        if not isinstance(payload, dict):
            results.append((FAIL, f"{path}: the answer is "
                                  f"{type(payload).__name__}, not an "
                                  f"object — wrong service on the port?"))
            continue
        if payload.get("v") != expected_v:
            results.append((FAIL, f"{path}: contract version "
                                  f"{payload.get('v')!r}, the screen requires "
                                  f"{expected_v} — version skew server/"
                                  f"firmware? (compare the rev line above)"))
            continue
        missing = [k for k in required if k not in payload]
        if missing:
            results.append((FAIL, f"{path}: missing required fields {missing} "
                                  f"— the screen's parser rejects the whole "
                                  f"answer and the display freezes"))
            continue
        shape_error = shape_check(payload)
        if shape_error:
            results.append((FAIL, f"{path}: {shape_error} — the screen's "
                                  f"parser rejects the whole answer"))
            continue
        stale_keys = [k for k in ("claudeWeekStale", "claudeModelWeekStale",
                                  "codexWeekStale", "stale")
                      if payload.get(k) is True]
        if stale_keys:
            results.append((WARN, f"{path}: answers but "
                                  f"{', '.join(stale_keys)} — the source is "
                                  f"not delivering fresh data"))
        else:
            results.append((OK, f"{path}: answers"))
    return results


def check_log_file(path):
    """Comb step 3: does the log file exist, is it growing reasonably, and
    is there anything nasty in it?"""
    path = Path(path)
    if not path.exists():
        service = "Task Scheduler" if os.name == "nt" else "launchd"
        return [(WARN, f"no log file at {path} — normal for a terminal run; "
                       f"under {service} it means the service never "
                       f"started")]
    def tail_text(p, cap=2 * 1024 * 1024):
        # Read only the tail of a large file -- the smoke test must be fast.
        n = p.stat().st_size
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            if n > cap:
                fh.seek(n - cap)
            return fh.read()

    results = []
    size = path.stat().st_size
    # The rotation moves the latest tail to .old -- right after a rotation
    # the fresh evidence lives THERE, not in the newly truncated file.
    old = path.with_name(path.name + ".old")
    old_text = tail_text(old) if old.exists() else ""
    suffix = " (+ rotated tail in .old)" if old_text else ""
    if size > _LOG_CAP_BYTES:
        results.append((WARN, f"the log file is {size} bytes (> the cap "
                              f"{_LOG_CAP_BYTES}) — the rotation has not "
                              f"had a chance to run; is the service stuck?"))
    else:
        results.append((OK, f"log file {path.name}: {size} bytes{suffix}"))
    text = tail_text(path)
    tracebacks = (text.count("Traceback (most recent call last)")
                  + old_text.count("Traceback (most recent call last)"))
    if tracebacks:
        results.append((WARN, f"{tracebacks} traceback(s) in the log{suffix} "
                              f"— grep -n Traceback {path}*"))
    # Both signatures: the log survives an upgrade and is only rotated at
    # the size cap, so starts logged by the older Swedish build
    # ("serverar http://") sit above the current ones, and a respawn loop
    # spanning the upgrade must still add up (Codex review of #112).
    starts = sum(text.count(marker) + old_text.count(marker)
                 for marker in ("serving http://", "serverar http://"))
    if starts >= RESPAWN_SUSPICION_COUNT:
        service = "the autostart" if os.name == "nt" else "launchd"
        results.append((WARN, f"{starts} start lines in the log{suffix} — "
                              f"respawn loop? ({service} restarts the "
                              f"service on an early death)"))
    return results


def check_state_files(state_dir, now_ts=None):
    """Comb step 4: do the state files parse, and is the trend data moving?"""
    state_dir = Path(state_dir)
    now_ts = time.time() if now_ts is None else now_ts
    if not state_dir.is_dir():
        return [(WARN, f"no state directory {state_dir} — fresh install, or "
                       f"nothing has been observed yet")]
    results = []
    for name in STATE_FILES:
        f = state_dir / name
        if not f.exists():
            results.append((WARN, f"{name} missing — created at the first "
                                  f"observation"))
            continue
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            # The server silently restarts from zero on a corrupt file
            # (OBS-11) -- move it aside BEFORE the next save overwrites the
            # evidence.
            results.append((FAIL, f"{name} is CORRUPT ({type(e).__name__}) — "
                                  f"move the file aside for forensics before "
                                  f"the next write zeroes it"))
            continue
        shape_error = STATE_SHAPE[name](payload)
        if shape_error:
            results.append((FAIL, f"{name}: parses but has the WRONG SHAPE "
                                  f"({shape_error}) — the loader resets "
                                  f"silently at the next start; move the "
                                  f"file aside for forensics"))
            continue
        results.append((OK, f"{name}: parses ({f.stat().st_size} bytes)"))
        if name == "usage-history.json":
            age_s = now_ts - f.stat().st_mtime
            if age_s > USAGE_HISTORY_FRESH_S:
                results.append((WARN, f"usage-history.json last written "
                                      f"{age_s / 3600:.0f} h ago — trend "
                                      f"points are written every 15 min "
                                      f"while quota data flows"))
    return results


def run(base_url=DEFAULT_BASE_URL, log_path=None, state_dir=None,
        checkout_rev=None, out=None):
    out = out or sys.stdout
    results = []
    server_results = check_server(
        base_url,
        checkout_rev if checkout_rev else repo_rev(),
        checkout_src=_read_source_fingerprint())
    results += server_results
    # Skip the endpoint check ONLY when no real tokenserver answers
    # (unreachable, wrong service, wrong status -- the first line is then a
    # FAIL). A reached but sick server must get all three contracts
    # examined: it is in the middle of an outage that the independent
    # failures need to show.
    if server_results and server_results[0][0] != FAIL:
        results += check_endpoints(base_url)
    results += check_log_file(log_path or DEFAULT_LOG_PATH)
    results += check_state_files(state_dir or STATE_DIR)

    tag = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    for level, text in results:
        print(f"{tag[level]} {text}", file=out)
    counts = {lvl: sum(1 for level, _ in results if level == lvl)
              for lvl in (OK, WARN, FAIL)}
    print(f"smoke test: {counts[OK]} ok, {counts[WARN]} warnings, "
          f"{counts[FAIL]} failures", file=out)
    if counts[FAIL]:
        return 2
    if counts[WARN]:
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--log-file", default=None,
                    help=f"log file to examine (default {DEFAULT_LOG_PATH})")
    ap.add_argument("--state-dir", default=None,
                    help=f"state directory (default {STATE_DIR})")
    args = ap.parse_args()
    return run(args.base_url, args.log_file, args.state_dir)


if __name__ == "__main__":
    sys.exit(main())
