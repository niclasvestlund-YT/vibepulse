#!/usr/bin/env python3
"""Röktest för VibePulse-kedjan på Macen: en körning, ett facit.

Automatiserar kamrutinens steg 1–4 (docs/observability.md): serverns
identitet och rev, Claude-probens status, alla tre API-svaren, loggfilen
och tillståndsfilerna. Ingen del kräver hårdvara — skärmens sanning
(kamsteg 5–6) förblir manuell.

    python3 tools/tokenserver/smoke.py [--base-url http://localhost:8737]

Utgång: 0 = allt ok, 1 = varningar (fungerar, men värt en titt),
2 = minst ett FAIL. Ren stdlib, som resten av tjänsten.
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
    from .tokenserver import (DEFAULT_LOG_PATH, _LOG_CAP_BYTES, _state_dir,
                              _read_source_fingerprint)
else:  # direktkörning: python3 tools/tokenserver/smoke.py
    from tokenserver import (DEFAULT_LOG_PATH, _LOG_CAP_BYTES, _state_dir,
                             _read_source_fingerprint)

DEFAULT_BASE_URL = "http://localhost:8737"
# Samma katalog tjänsten faktiskt skriver till, inte en andra gissning på
# den: röktestet ska inte kunna leta i fel träd på en Windows-maskin.
STATE_DIR = _state_dir()
STATE_FILES = ("usage-history.json", "quota-cache.json", "max-tracker.json")
# Versionsskev-snubbeltråd, inte en andra parser: skärmens parsrar är
# kontraktsstränga och avvisar HELA svaret vid fel version, saknade
# obligatoriska fält eller fel HTTP-status — HTTP kan se friskt ut medan
# displayen fryser. Här kontrolleras versionen, toppnivåfälten och de
# grövsta typerna (tokens_parse.c:328-332, agent_status_parse.c:529-542,
# max_tracker_parse.c:303-317); providrarnas INRE fält bor medvetet bara i
# C-testerna och fixturerna. Bumpas ett kontrakt är den här tabellen en
# del av bumpen — och testernas friska svar ÄR sim-fixturerna, så en drift
# faller i test innan den ljuger i drift.


def _tokens_shape(payload):
    bad = [k for k in ("dayTokens", "dayTokensPerHour", "daySessions",
                       "monthTokens")
           if isinstance(payload.get(k), bool)
           or not isinstance(payload.get(k), (int, float))]
    return f"icke-numeriska fält {bad}" if bad else None


def _agents_shape(payload):
    agents = payload.get("agents")
    if not isinstance(agents, dict) or not {"claude", "codex"} <= set(agents):
        return "agents ska vara ett objekt med claude och codex"
    return None


def _tracker_shape(payload):
    if not isinstance(payload.get("stale"), bool):
        return "stale ska vara bool"
    empty = [k for k in ("claude", "codex")
             if not isinstance(payload.get(k), dict) or not payload.get(k)]
    if empty:
        return f"{empty} ska vara ifyllda providerobjekt"
    return None


ENDPOINT_SHAPE = {
    "/api/tokens": (2, ("dayTokens", "dayTokensPerHour", "daySessions",
                        "monthTokens"), _tokens_shape),
    "/api/agent-status": (2, ("seq", "agents"), _agents_shape),
    "/api/max-tracker": (1, ("weeks", "stale", "codingStreakDays",
                             "claude", "codex"), _tracker_shape),
}
PROBE_OK = "usage_http_200 + ok"
# Fler startrader än så i EN loggfil tyder på en respawn-loop, inte på
# avsiktliga omstarter (rotationen vid start håller filen till en era).
RESPAWN_SUSPICION_COUNT = 10
USAGE_HISTORY_FRESH_S = 24 * 3600

OK, VARN, FAIL = "ok", "varn", "fail"


# Laddarna nollställer TYST på fel form trots giltig JSON (usage_history
# kräver {v:1, samples:[...]}, quota_cache exakt {v:1, records:[...]},
# max_tracker provider-sektioner) — samma dataförlust som OBS-11, fast
# via form i stället för korrupta bytes. Samma snubbeltrådsnivå som
# ENDPOINT_SHAPE: toppnivån, inte postinnehållet.
def _history_state_shape(payload):
    if (not isinstance(payload, dict) or payload.get("v") != 1
            or not isinstance(payload.get("samples"), list)):
        return "kräver v=1 och en samples-lista"
    return None


def _quota_state_shape(payload):
    if (not isinstance(payload, dict) or set(payload) != {"v", "records"}
            or payload.get("v") != 1
            or not isinstance(payload.get("records"), list)):
        return "kräver exakt {v: 1, records: [...]}"
    return None


def _tracker_state_shape(payload):
    if not isinstance(payload, dict) or not all(
            isinstance(payload.get(p), dict) for p in ("claude", "codex")):
        return "kräver provider-sektionerna claude och codex"
    return None


STATE_SHAPE = {
    "usage-history.json": _history_state_shape,
    "quota-cache.json": _quota_state_shape,
    "max-tracker.json": _tracker_state_shape,
}


def _get_json(url, timeout=5):
    """(HTTP-status, parsad JSON). Statusen följer med: skärmen avvisar
    allt utom 200 (torget_http.c), så en proxy eller cache som svarar 502
    med frisk-seende kropp får inte bli grönt här. Serverns egna 500 bär
    kontraktets {"error": ...}-kropp och parsas också."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, json.loads(
                resp.read().decode("utf-8", errors="replace"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        try:
            return e.code, json.loads(body)
        except json.JSONDecodeError:
            return e.code, None


def repo_rev():
    """Checkoutens rev, för jämförelsen mot serverns — None utan git."""
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
    """Kamsteg 1–2: identitet, rev, källfingerprint och probestatus från
    GET /. Jämförelser görs bara när båda sidor finns (None hoppar)."""
    try:
        status, root = _get_json(f"{base_url}/")
    except Exception as e:
        return [(FAIL, f"servern svarar inte på {base_url}/ ({type(e).__name__})"
                       " — kör den? launchctl list | grep torget, eller starta"
                       " python3 tokenserver.py för hand")]
    if status != 200:
        return [(FAIL, f"HTTP {status} på {base_url}/ — fel tjänst, proxy "
                       f"eller trasig server")]
    # En annan tjänst på porten kan svara med giltig JSON som inte är ett
    # objekt (lista, tal, null) — det ska bli [FAIL], inte en traceback.
    if (not isinstance(root, dict)
            or root.get("service") != "torget-tokenserver"):
        got = (root.get("service") if isinstance(root, dict)
               else type(root).__name__)
        return [(FAIL, f"fel tjänst på {base_url} — 'service' är {got!r}")]
    results = []
    rev = root.get("rev", "unknown")
    started = root.get("startedAt", "okänt")
    if checkout_rev and rev != checkout_rev:
        results.append((VARN, f"servern kör rev {rev}, checkouten är "
                              f"{checkout_rev} — pekar plistens "
                              f"WorkingDirectory på en annan katalog?"))
    else:
        results.append((OK, f"torget-tokenserver rev {rev}, igång sedan "
                            f"{started}"))
    # Fingerprint fångar det rev inte kan: smutsig worktree vid start,
    # eller filer redigerade EFTER start — bägge delar HEAD med checkouten.
    src = root.get("srcFingerprint")
    if src and checkout_src and src != checkout_src:
        results.append((VARN, f"servern kör annan källkod än checkouten "
                              f"(fingerprint {src} ≠ {checkout_src}) — "
                              f"redigerad efter start eller smutsig "
                              f"worktree; starta om tjänsten"))
    probe = root.get("claudeProbe", "saknas")
    if probe == PROBE_OK:
        results.append((OK, f"claude-proben: {probe}"))
    elif probe == "not_run":
        results.append((VARN, "claude-proben har inte kört än (120 s-cykel) "
                              "— kör om röktestet om en stund"))
    else:
        results.append((VARN, f"claude-proben: {probe} — se tabellen i "
                              f"docs/agent-setup.md"))
    unknown = root.get("unknownRateLimitBuckets") or []
    if unknown:
        results.append((VARN, f"okända rate-limit-buckets: {unknown} — "
                              f"uppströms har fått ett nytt fönster; värt en "
                              f"post i docs/observability-backlog.md"))
    # Saknas nyckeln pratar vi med en äldre server — inget att bedöma då.
    if root.get("usageComputeOk") is False:
        secs = root.get("usageComputeFailingForS") or 0
        results.append((FAIL, f"usage-omräkningen har kraschat (i {secs} s) "
                              f"— /api/tokens serverar frysta siffror som "
                              f"ser färska ut; läs loggfilen"))
    return results


def check_endpoints(base_url):
    """Kamsteg 2, forts: svarar alla tre API:erna med kontraktets form?"""
    results = []
    for path, (expected_v, required, shape_check) in ENDPOINT_SHAPE.items():
        try:
            status, payload = _get_json(f"{base_url}{path}")
        except Exception as e:
            results.append((FAIL, f"{path}: inget svar ({type(e).__name__})"))
            continue
        if isinstance(payload, dict) and "error" in payload:
            # Error-formen är kontraktets "något är trasigt i servern" —
            # skärmen avvisar den och fryser hellre än gissar. Orsaken står
            # i loggfilen sedan servern började logga sina 500.
            results.append((FAIL, f"{path}: error-form i svaret "
                                  f"({payload.get('error')}) — läs "
                                  f"loggfilen"))
            continue
        if status != 200:
            results.append((FAIL, f"{path}: HTTP {status} — skärmen kräver "
                                  f"200 och behåller sina gamla värden"))
            continue
        if not isinstance(payload, dict):
            results.append((FAIL, f"{path}: svaret är "
                                  f"{type(payload).__name__}, inte ett "
                                  f"objekt — fel tjänst på porten?"))
            continue
        if payload.get("v") != expected_v:
            results.append((FAIL, f"{path}: kontraktsversion "
                                  f"{payload.get('v')!r}, skärmen kräver "
                                  f"{expected_v} — versionsskev server/"
                                  f"firmware? (jämför rev-raden ovan)"))
            continue
        missing = [k for k in required if k not in payload]
        if missing:
            results.append((FAIL, f"{path}: saknar bärande fält {missing} — "
                                  f"skärmens parser avvisar hela svaret och "
                                  f"displayen fryser"))
            continue
        shape_error = shape_check(payload)
        if shape_error:
            results.append((FAIL, f"{path}: {shape_error} — skärmens parser "
                                  f"avvisar hela svaret"))
            continue
        stale_keys = [k for k in ("claudeWeekStale", "claudeModelWeekStale",
                                  "codexWeekStale", "stale")
                      if payload.get(k) is True]
        if stale_keys:
            results.append((VARN, f"{path}: svarar men {', '.join(stale_keys)}"
                                  f" — källan levererar inte färskt"))
        else:
            results.append((OK, f"{path}: svarar"))
    return results


def check_log_file(path):
    """Kamsteg 3: finns loggfilen, växer den lagom, står det otäckt i den?"""
    path = Path(path)
    if not path.exists():
        return [(VARN, f"ingen loggfil på {path} — normalt vid "
                       f"terminalkörning; under launchd betyder det att "
                       f"tjänsten aldrig startat")]
    def tail_text(p, cap=2 * 1024 * 1024):
        # Läs bara svansen på en stor fil — röktestet ska vara snabbt.
        n = p.stat().st_size
        with open(p, "r", encoding="utf-8", errors="replace") as fh:
            if n > cap:
                fh.seek(n - cap)
            return fh.read()

    results = []
    size = path.stat().st_size
    # Rotationen flyttar den senaste svansen till .old — direkt efter en
    # rotation bor de färska bevisen DÄR, inte i den nytrunkerade filen.
    old = path.with_name(path.name + ".old")
    old_text = tail_text(old) if old.exists() else ""
    suffix = " (+ roterad svans i .old)" if old_text else ""
    if size > _LOG_CAP_BYTES:
        results.append((VARN, f"loggfilen är {size} byte (> taket "
                              f"{_LOG_CAP_BYTES}) — rotationen har inte "
                              f"fått köra; står tjänsten still?"))
    else:
        results.append((OK, f"loggfil {path.name}: {size} byte{suffix}"))
    text = tail_text(path)
    tracebacks = (text.count("Traceback (most recent call last)")
                  + old_text.count("Traceback (most recent call last)"))
    if tracebacks:
        results.append((VARN, f"{tracebacks} traceback i loggen{suffix} — "
                              f"grep -n Traceback {path}*"))
    starts = (text.count("serverar http://")
              + old_text.count("serverar http://"))
    if starts >= RESPAWN_SUSPICION_COUNT:
        results.append((VARN, f"{starts} startrader i loggen{suffix} — "
                              f"respawn-loop? (launchd startar om var 30 s "
                              f"vid tidig död)"))
    return results


def check_state_files(state_dir, now_ts=None):
    """Kamsteg 4: parsar tillståndsfilerna, och rör sig trenddatat?"""
    state_dir = Path(state_dir)
    now_ts = time.time() if now_ts is None else now_ts
    if not state_dir.is_dir():
        return [(VARN, f"ingen tillståndskatalog {state_dir} — färsk "
                       f"installation, eller så har inget observerats än")]
    results = []
    for name in STATE_FILES:
        f = state_dir / name
        if not f.exists():
            results.append((VARN, f"{name} saknas — skapas vid första "
                                  f"observationen"))
            continue
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            # Servern startar tyst om från noll på en korrupt fil (OBS-11) —
            # flytta undan den INNAN nästa save skriver över bevisen.
            results.append((FAIL, f"{name} är KORRUPT ({type(e).__name__}) — "
                                  f"flytta undan filen för forensik innan "
                                  f"nästa skrivning nollar den"))
            continue
        shape_error = STATE_SHAPE[name](payload)
        if shape_error:
            results.append((FAIL, f"{name}: parsar men har FEL FORM "
                                  f"({shape_error}) — laddaren nollställer "
                                  f"tyst vid nästa start; flytta undan "
                                  f"filen för forensik"))
            continue
        results.append((OK, f"{name}: parsar ({f.stat().st_size} byte)"))
        if name == "usage-history.json":
            age_s = now_ts - f.stat().st_mtime
            if age_s > USAGE_HISTORY_FRESH_S:
                results.append((VARN, f"usage-history.json senast skriven "
                                      f"för {age_s / 3600:.0f} h sedan — "
                                      f"trendpunkter skrivs var 15:e minut "
                                      f"när kvotdata flödar"))
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
    # Hoppa över endpointkollen BARA när ingen riktig tokenserver svarar
    # (onåbar, fel tjänst, fel status — då är första raden ett FAIL). En
    # nådd men sjuk server ska få alla tre kontrakten granskade: det är
    # mitt i ett haveri de oberoende felen behöver synas.
    if server_results and server_results[0][0] != FAIL:
        results += check_endpoints(base_url)
    results += check_log_file(log_path or DEFAULT_LOG_PATH)
    results += check_state_files(state_dir or STATE_DIR)

    tag = {OK: "[ OK ]", VARN: "[VARN]", FAIL: "[FAIL]"}
    for level, text in results:
        print(f"{tag[level]} {text}", file=out)
    counts = {lvl: sum(1 for level, _ in results if level == lvl)
              for lvl in (OK, VARN, FAIL)}
    print(f"röktest: {counts[OK]} ok, {counts[VARN]} varningar, "
          f"{counts[FAIL]} fel", file=out)
    if counts[FAIL]:
        return 2
    if counts[VARN]:
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base-url", default=DEFAULT_BASE_URL)
    ap.add_argument("--log-file", default=None,
                    help=f"loggfil att granska (default {DEFAULT_LOG_PATH})")
    ap.add_argument("--state-dir", default=None,
                    help=f"tillståndskatalog (default {STATE_DIR})")
    args = ap.parse_args()
    return run(args.base_url, args.log_file, args.state_dir)


if __name__ == "__main__":
    sys.exit(main())
