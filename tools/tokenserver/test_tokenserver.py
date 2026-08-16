import contextlib
import fcntl
import io
import json
import logging
import tempfile
import threading
import time
import unittest
import urllib.error
from datetime import datetime
from pathlib import Path
from unittest import mock
import os

from tools.tokenserver import codex_usage, tokenserver
from tools.tokenserver.max_tracker import MaxTrackerStore
from tools.tokenserver.quota_cache import CachedQuota, QuotaCache
from tools.tokenserver.usage_history import Forecast, UsageHistory


class StubHistory:
    def __init__(self, forecasts=None):
        self.forecasts = forecasts or {}
        self.record_calls = []

    def record(self, provider, window, pct, reset_at, at=None):
        self.record_calls.append(
            (provider, window, pct, reset_at, at))
        return True

    def record_many(self, samples, at=None):
        for provider, window, pct, reset_at in samples:
            self.record(provider, window, pct, reset_at, at=at)
        return len(self.record_calls)

    def delta_since(self, provider, window, since, reset_at, now=None):
        return None

    def forecast(self, provider, window, reset_at, now=None):
        return self.forecasts.get(provider, Forecast(state="unavailable"))


class _FakeUsageResponse:
    """Minsta möjliga urlopen-svar: kontexthanterare med en read()."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode()


class ClaudeLimitHeaderTests(unittest.TestCase):
    def setUp(self):
        # Probelåset och straffrutefilen pekas om till en tempkatalog så
        # testerna aldrig samsas om de riktiga filerna med en levande
        # tokenserver på samma maskin — och aldrig läser in en äkta backoff.
        self._lock_dir = tempfile.TemporaryDirectory(prefix="probe-lock-")
        for attr, value in (
                ("_PROBE_LOCK_PATH",
                 Path(self._lock_dir.name) / "claude-probe.lock"),
                ("_PROBE_STATE_PATH",
                 Path(self._lock_dir.name) / "claude-probe-state.json"),
                ("_probe_state_loaded", True),
        ):
            patcher = mock.patch.object(tokenserver, attr, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._lock_dir.cleanup)

    def test_usage_endpoint_maps_active_fable_scope_without_guessing(self):
        parsed = tokenserver._parse_usage_limits({
            "limits": [
                {"kind": "session", "percent": 2,
                 "resets_at": "2026-08-12T16:00:00+00:00"},
                {"kind": "weekly_all", "percent": 30,
                 "resets_at": "2026-08-14T06:00:00+00:00"},
                {"kind": "weekly_scoped", "percent": 41,
                 "resets_at": "2026-08-14T06:00:00+00:00",
                 "is_active": True,
                 "scope": {"model": {"display_name": "Fable"}}},
            ],
        }, now_ts=1_786_531_200)

        self.assertEqual(parsed["sessionPct"], 2.0)
        self.assertEqual(parsed["weekPct"], 30.0)
        self.assertEqual(parsed["modelPct"], 41.0)
        self.assertEqual(parsed["modelLabel"], "FABLE · WEEK")
        self.assertIn("modelIdentity", parsed)

    def test_usage_endpoint_maps_inactive_fable_pool_with_real_usage(self):
        """Live-svaret 2026-08-14: Fable veckan på 11 % bar is_active=false
        (flaggan pekar ut den BINDANDE gränsen, inte poolens existens) och
        procenten försvann från glaset. Verklig förbrukning ska visas."""
        parsed = tokenserver._parse_usage_limits({
            "limits": [{
                "kind": "weekly_scoped", "percent": 11,
                "resets_at": "2026-08-21T06:00:00+00:00",
                "is_active": False,
                "scope": {"model": {"id": None, "display_name": "Fable"},
                          "surface": None},
            }],
        }, now_ts=1_786_531_200)

        self.assertEqual(parsed["modelPct"], 11.0)
        self.assertEqual(parsed["modelLabel"], "FABLE · WEEK")

    def test_usage_endpoint_leaves_untouched_inactive_pool_unnamed(self):
        """0 % OCH inaktiv = en aldrig använd modellpool — den tar ingen
        plats på glaset. Gränsen sitter vid verklig förbrukning."""
        parsed = tokenserver._parse_usage_limits({
            "limits": [{
                "kind": "weekly_scoped", "percent": 0,
                "resets_at": "2026-08-21T06:00:00+00:00",
                "is_active": False,
                "scope": {"model": {"display_name": "Fable"}},
            }],
        }, now_ts=1_786_531_200)

        self.assertNotIn("modelPct", parsed)
        self.assertNotIn("modelLabel", parsed)

    def test_usage_endpoint_does_not_label_unknown_scoped_pool(self):
        parsed = tokenserver._parse_usage_limits({
            "limits": [{
                "kind": "weekly_scoped", "percent": 41,
                "resets_at": "2026-08-14T06:00:00+00:00",
                "is_active": True,
                "scope": {"model": {"display_name": "Future"}},
            }],
        }, now_ts=1_786_531_200)

        self.assertNotIn("modelPct", parsed)
        self.assertNotIn("modelLabel", parsed)

    def test_ota_available_reads_newest_valid_torget_binary(self):
        """Annonsen läser versionen ur torget.bin:s appbeskrivning: rätt
        magi, rätt projekt, nyaste mtime vinner; skräp och främmande
        projekt annonseras aldrig."""
        def fake_bin(version, project=b"torget"):
            desc = (b"\x32\x54\xcd\xab" + b"\x00" * 12 +
                    version.ljust(32, "\x00").encode() +
                    project.ljust(32, b"\x00"))
            return b"\x00" * 32 + desc

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old = root / "build" / "torget.bin"
            new = root / "build-diag" / "torget.bin"
            junk = root / "build-x" / "torget.bin"
            for path in (old, new, junk):
                path.parent.mkdir(parents=True)
            old.write_bytes(fake_bin("v0.1.0-old"))
            new.write_bytes(fake_bin("v0.2.0-new"))
            junk.write_bytes(fake_bin("v9.9.9", project=b"vibbe"))
            os.utime(old, (1_000_000, 1_000_000))
            os.utime(new, (2_000_000, 2_000_000))
            os.utime(junk, (3_000_000, 3_000_000))

            with mock.patch.object(tokenserver, "_OTA_BUILD_ROOT", root), \
                    mock.patch.object(tokenserver, "_ota_desc_cache", {}):
                self.assertEqual(
                    tokenserver._ota_available_version(), "v0.2.0-new")

    def test_desktop_process_token_wins_over_expired_keychain_token(self):
        process_command = (
            "/Users/test/Library/Application Support/Claude/claude-code/"
            "2.1.219/claude.app/Contents/MacOS/claude "
            "CLAUDE_CODE_OAUTH_TOKEN=fresh-process-token"
        )
        expired_keychain = json.dumps({
            "claudeAiOauth": {
                "accessToken": "expired-keychain-token",
                "expiresAt": 1,
            },
        })

        def run(command, **_kwargs):
            if command[0] == "pgrep":
                return mock.Mock(stdout="123\n")
            if command[0] == "ps":
                return mock.Mock(stdout=process_command)
            if command[0] == "security":
                return mock.Mock(stdout=expired_keychain)
            raise AssertionError(command)

        with mock.patch.object(tokenserver.subprocess, "run",
                               side_effect=run):
            candidates = tokenserver._read_oauth_candidates()

        # Processtokenen först, men den utgångna nyckelringsposten står kvar
        # som reserv — probens utgångsspärr sorterar bort den vid behov.
        self.assertEqual(candidates, [
            ("fresh-process-token", None),
            ("expired-keychain-token", 1),
        ])

    def test_unrelated_process_token_is_ignored(self):
        unrelated_command = (
            "/usr/bin/python3 worker.py "
            "CLAUDE_CODE_OAUTH_TOKEN=unrelated-secret"
        )
        keychain = json.dumps({
            "claudeAiOauth": {
                "accessToken": "keychain-token",
                "expiresAt": 1_900_000_000_000,
            },
        })

        def run(command, **_kwargs):
            if command[0] == "pgrep":
                return mock.Mock(stdout="456\n")
            if command[0] == "ps":
                return mock.Mock(stdout=unrelated_command)
            if command[0] == "security":
                return mock.Mock(stdout=keychain)
            raise AssertionError(command)

        with mock.patch.object(tokenserver.subprocess, "run",
                               side_effect=run):
            candidates = tokenserver._read_oauth_candidates()

        self.assertEqual(
            candidates, [("keychain-token", 1_900_000_000_000)])

    def test_keychain_remains_fallback_without_desktop_process(self):
        keychain = json.dumps({
            "claudeAiOauth": {
                "accessToken": "standalone-token",
                "expiresAt": 1_900_000_000_000,
            },
        })

        def run(command, **_kwargs):
            if command[0] == "pgrep":
                return mock.Mock(stdout="")
            if command[0] == "security":
                return mock.Mock(stdout=keychain)
            raise AssertionError(command)

        with mock.patch.object(tokenserver.subprocess, "run",
                               side_effect=run):
            candidates = tokenserver._read_oauth_candidates()

        self.assertEqual(
            candidates, [("standalone-token", 1_900_000_000_000)])

    def test_windows_reads_the_credentials_file_claude_login_writes(self):
        """Windows har ingen nyckelring — `claude login` skriver samma
        claudeAiOauth-post till %USERPROFILE%\\.claude\\.credentials.json."""
        credentials = json.dumps({
            "claudeAiOauth": {
                "accessToken": "windows-file-token",
                "expiresAt": 1_900_000_000_000,
                "refreshToken": "not-ours-to-touch",
            },
        })

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".claude" / ".credentials.json"
            path.parent.mkdir(parents=True)
            path.write_text(credentials, encoding="utf-8")

            with mock.patch.object(tokenserver, "_IS_WINDOWS", True), \
                    mock.patch.object(tokenserver.Path, "home",
                                      return_value=Path(temp_dir)), \
                    mock.patch.object(tokenserver.subprocess, "run",
                                      side_effect=AssertionError(
                                          "no subprocess on Windows")):
                candidates = tokenserver._read_oauth_candidates()

        # Filen är enda källan: ingen pgrep, inget `security`, ingen
        # utgången processtoken att sortera bort.
        self.assertEqual(
            candidates, [("windows-file-token", 1_900_000_000_000)])

    def test_windows_without_a_credentials_file_has_no_candidates(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.object(tokenserver, "_IS_WINDOWS", True), \
                    mock.patch.object(tokenserver.Path, "home",
                                      return_value=Path(temp_dir)):
                self.assertEqual(tokenserver._read_oauth_candidates(), [])

    def test_windows_credentials_file_that_is_garbage_is_not_a_candidate(self):
        """Halvskriven fil under `claude login` ska ge tystnad, inte krasch."""
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".claude" / ".credentials.json"
            path.parent.mkdir(parents=True)
            path.write_text('{"claudeAiOauth": {"accessTok',
                            encoding="utf-8")

            with mock.patch.object(tokenserver, "_IS_WINDOWS", True), \
                    mock.patch.object(tokenserver.Path, "home",
                                      return_value=Path(temp_dir)):
                self.assertEqual(tokenserver._read_oauth_candidates(), [])

    def test_macos_ignores_a_credentials_file(self):
        """Macen läser nyckelringen. En kvarglömd .credentials.json på samma
        maskin får inte smyga in en tredje kandidat."""
        keychain = json.dumps({
            "claudeAiOauth": {
                "accessToken": "keychain-token",
                "expiresAt": 1_900_000_000_000,
            },
        })

        def run(command, **_kwargs):
            if command[0] == "pgrep":
                return mock.Mock(stdout="")
            if command[0] == "security":
                return mock.Mock(stdout=keychain)
            raise AssertionError(command)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / ".claude" / ".credentials.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({
                "claudeAiOauth": {"accessToken": "stale-file-token"},
            }), encoding="utf-8")

            with mock.patch.object(tokenserver, "_IS_WINDOWS", False), \
                    mock.patch.object(tokenserver.Path, "home",
                                      return_value=Path(temp_dir)), \
                    mock.patch.object(tokenserver.subprocess, "run",
                                      side_effect=run):
                candidates = tokenserver._read_oauth_candidates()

        self.assertEqual(
            candidates, [("keychain-token", 1_900_000_000_000)])

    def test_windows_probe_reaches_the_api_with_the_file_token(self):
        """Hela Windows-kedjan i ett svep: fil → kandidat → lås → API-svar.
        Enhetstesten ovan kan alla passera medan kedjan ändå är bruten."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_header("Authorization"))
            return _FakeUsageResponse({"limits": [
                {"kind": "session", "percent": 12,
                 "resets_at": "2100-01-01T00:00:00+00:00"},
                {"kind": "weekly_all", "percent": 47,
                 "resets_at": "2100-01-02T00:00:00+00:00"},
            ]})

        class FakeMsvcrt:
            LK_NBLCK = 1

            @staticmethod
            def locking(fd, mode, nbytes):
                pass

        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir)
            credentials = home / ".claude" / ".credentials.json"
            credentials.parent.mkdir(parents=True)
            credentials.write_text(json.dumps({
                "claudeAiOauth": {
                    "accessToken": "windows-file-token",
                    "expiresAt": 1_900_000_000_000,
                },
            }), encoding="utf-8")

            with mock.patch.object(tokenserver, "_IS_WINDOWS", True), \
                    mock.patch.object(tokenserver, "fcntl", None), \
                    mock.patch.object(tokenserver, "msvcrt", FakeMsvcrt), \
                    mock.patch.object(tokenserver, "_PROBE_LOCK_PATH",
                                      home / "claude-probe.lock"), \
                    mock.patch.object(tokenserver, "_probe_cooldown_until",
                                      0.0), \
                    mock.patch.object(tokenserver, "_dead_tokens", {}), \
                    mock.patch.object(tokenserver.Path, "home",
                                      return_value=home), \
                    mock.patch.object(tokenserver.subprocess, "run",
                                      side_effect=AssertionError(
                                          "no subprocess on Windows")), \
                    mock.patch.object(tokenserver.urllib.request, "urlopen",
                                      side_effect=fake_urlopen):
                found = tokenserver._probe_limits()

        self.assertEqual(tokenserver._probe_status, "usage_http_200 + ok")
        self.assertEqual(calls, ["Bearer windows-file-token"])
        self.assertEqual(found["sessionPct"], 12.0)
        self.assertEqual(found["weekPct"], 47.0)

    def test_credentials_file_path_sits_under_home(self):
        with mock.patch.object(tokenserver.Path, "home",
                               return_value=Path("/home/tester")):
            self.assertEqual(
                tokenserver._credentials_file_path(),
                Path("/home/tester/.claude/.credentials.json"))

    def test_probe_falls_back_to_keychain_when_process_token_rejected(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append((req.get_full_url(),
                          req.get_header("Authorization")))
            if req.get_header("Authorization") == "Bearer stale-process-token":
                raise urllib.error.HTTPError(
                    req.get_full_url(), 401, "Unauthorized", None, None)
            return _FakeUsageResponse({"limits": [
                {"kind": "session", "percent": 12,
                 "resets_at": "2100-01-01T00:00:00+00:00"},
                {"kind": "weekly_all", "percent": 47,
                 "resets_at": "2100-01-02T00:00:00+00:00"},
            ]})

        with mock.patch.object(
                tokenserver, "_read_oauth_candidates",
                return_value=[("stale-process-token", None),
                              ("fresh-keychain-token", None)]), \
                mock.patch.object(tokenserver, "_dead_tokens", {}), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            found = tokenserver._probe_limits()

        self.assertEqual(tokenserver._probe_status, "usage_http_200 + ok")
        self.assertEqual(found["sessionPct"], 12.0)
        self.assertEqual([auth for _, auth in calls],
                         ["Bearer stale-process-token",
                          "Bearer fresh-keychain-token"])

    def test_probe_never_resends_a_dead_token(self):
        """429-straffrutan 2026-08-14: nyckelringstokenen dog på natten och
        Desktops frusna processtoken hamrades mot API:t varje cykel i timmar.
        Ett värde som fått 401 ska aldrig lämna Macen igen."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_header("Authorization"))
            raise urllib.error.HTTPError(
                req.get_full_url(), 401, "Unauthorized", None, None)

        with mock.patch.object(tokenserver, "_read_oauth_candidates",
                               return_value=[("dead-token", None)]), \
                mock.patch.object(tokenserver, "_dead_tokens", {}), \
                mock.patch.object(tokenserver, "_probe_cooldown_until", 0.0), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            first = tokenserver._probe_limits()
            second = tokenserver._probe_limits()
            status = tokenserver._probe_status

        self.assertIsNone(first)
        self.assertIsNone(second)
        # Exakt ETT nätanrop: avvisningen minns och andra cykeln rör inte
        # nätet alls med det döda värdet.
        self.assertEqual(calls, ["Bearer dead-token"])
        self.assertEqual(status, "token_dead_awaiting_refresh")

    def test_probe_yields_when_another_instance_holds_the_lock(self):
        """Maskinvida enprobe-garantin: håller någon annan process låset gör
        cykeln INGEN nätaktivitet alls — extra instanser (worktree, manuell
        start) blir strukturellt ofarliga för 429-straffrutan."""
        def explode(req, timeout=None):
            raise AssertionError("upstream-anrop trots att låset var upptaget")

        holder = open(tokenserver._PROBE_LOCK_PATH, "w")
        fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            with mock.patch.object(tokenserver, "_read_oauth_candidates",
                                   return_value=[("fresh-token", None)]), \
                    mock.patch.object(tokenserver, "_dead_tokens", {}), \
                    mock.patch.object(tokenserver, "_probe_cooldown_until",
                                      0.0), \
                    mock.patch.object(tokenserver.urllib.request, "urlopen",
                                      side_effect=explode):
                found = tokenserver._probe_limits()
        finally:
            holder.close()

        self.assertIsNone(found)
        self.assertEqual(tokenserver._probe_status,
                         "probe_held_by_other_instance")

    def test_probe_lock_uses_msvcrt_when_there_is_no_fcntl(self):
        """Windows saknar fcntl. Enprobe-grinden får inte tyst försvinna där
        — den tas med msvcrt.locking i stället, samma icke-blockerande
        semantik."""
        calls = []

        class FakeMsvcrt:
            LK_NBLCK = 1

            @staticmethod
            def locking(fd, mode, nbytes):
                calls.append((mode, nbytes))

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "claude-probe.lock"
            with mock.patch.object(tokenserver, "_PROBE_LOCK_PATH", path), \
                    mock.patch.object(tokenserver, "fcntl", None), \
                    mock.patch.object(tokenserver, "msvcrt", FakeMsvcrt):
                handle = tokenserver._hold_probe_lock()

            self.assertIsNotNone(handle)
            handle.close()
            self.assertEqual(calls, [(FakeMsvcrt.LK_NBLCK, 1)])
            # Låset måste ha en byte att sätta tänderna i.
            self.assertEqual(path.read_text(encoding="utf-8"), "1")

    def test_probe_lock_yields_when_msvcrt_reports_a_held_range(self):
        class FakeMsvcrt:
            LK_NBLCK = 1

            @staticmethod
            def locking(fd, mode, nbytes):
                raise OSError(13, "Permission denied")

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "claude-probe.lock"
            with mock.patch.object(tokenserver, "_PROBE_LOCK_PATH", path), \
                    mock.patch.object(tokenserver, "fcntl", None), \
                    mock.patch.object(tokenserver, "msvcrt", FakeMsvcrt):
                self.assertIsNone(tokenserver._hold_probe_lock())

    def test_probe_retries_a_refreshed_token_value(self):
        """Dödmarkeringen sitter på VÄRDET: när källan levererar en ny
        sträng (Claude Code förnyade i nyckelringen) provas den direkt."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_header("Authorization"))
            if req.get_header("Authorization") == "Bearer dead-token":
                raise urllib.error.HTTPError(
                    req.get_full_url(), 401, "Unauthorized", None, None)
            return _FakeUsageResponse({"limits": [
                {"kind": "weekly_all", "percent": 41,
                 "resets_at": "2100-01-02T00:00:00+00:00"},
            ]})

        candidates = [[("dead-token", None)], [("refreshed-token", None)]]
        with mock.patch.object(tokenserver, "_read_oauth_candidates",
                               side_effect=lambda: candidates.pop(0)), \
                mock.patch.object(tokenserver, "_dead_tokens", {}), \
                mock.patch.object(tokenserver, "_probe_cooldown_until", 0.0), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            first = tokenserver._probe_limits()
            second = tokenserver._probe_limits()

        self.assertIsNone(first)
        self.assertEqual(second["weekPct"], 41.0)
        self.assertEqual(calls, ["Bearer dead-token", "Bearer refreshed-token"])

    def test_probe_ok_when_session_window_lapsed(self):
        """Speglar ett live-svar 2026-08-13: sessionsfönstret hade löpt ut
        minuter tidigare, veckosiffrorna var giltiga. Proben ska behålla dem
        i stället för att falla vidare till header-proben."""
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_full_url())
            return _FakeUsageResponse({"limits": [
                {"kind": "session", "percent": 17,
                 "resets_at": "2020-01-01T00:00:00+00:00", "is_active": False},
                {"kind": "weekly_all", "percent": 60,
                 "resets_at": "2100-08-14T06:00:00+00:00", "is_active": False},
                {"kind": "weekly_scoped", "percent": 79,
                 "resets_at": "2100-08-14T06:00:00+00:00", "is_active": True,
                 "scope": {"model": {"display_name": "Fable"}}},
            ]})

        with mock.patch.object(tokenserver, "_read_oauth_candidates",
                               return_value=[("fresh-token", None)]), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            found = tokenserver._probe_limits()

        self.assertEqual(tokenserver._probe_status, "usage_http_200 + ok")
        self.assertNotIn("sessionPct", found)
        self.assertEqual(found["weekPct"], 60.0)
        self.assertEqual(found["modelPct"], 79.0)
        self.assertEqual(found["modelLabel"], "FABLE · WEEK")
        self.assertEqual(calls, ["https://api.anthropic.com/api/oauth/usage"])

    def test_probe_backs_off_on_rate_limit(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_full_url())
            raise urllib.error.HTTPError(
                req.get_full_url(), 429, "Too Many Requests", None, None)

        with mock.patch.object(tokenserver, "_probe_cooldown_until", 0.0), \
                mock.patch.object(tokenserver, "_read_oauth_candidates",
                                  return_value=[("a", None), ("b", None)]), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            first = tokenserver._probe_limits()
            second = tokenserver._probe_limits()
            status = tokenserver._probe_status

        self.assertIsNone(first)
        self.assertIsNone(second)
        self.assertTrue(status.startswith("usage_http_429 + backoff_until_"),
                        status)
        # 429 avbryter cykeln direkt: ingen andra källa, ingen header-probe,
        # och nästa cykel rör inte nätet alls under nedkylningen.
        self.assertEqual(len(calls), 1)

    def test_probe_backoff_interval_grows_with_failures(self):
        with mock.patch.object(tokenserver, "_probe_failure_streak", 0):
            self.assertEqual(tokenserver._probe_interval_s(), 240)
        with mock.patch.object(tokenserver, "_probe_failure_streak", 1):
            self.assertEqual(tokenserver._probe_interval_s(), 480)
        with mock.patch.object(tokenserver, "_probe_failure_streak", 5):
            self.assertEqual(tokenserver._probe_interval_s(), 960)

    def test_probe_respects_persisted_cooldown_across_restart(self):
        """Straffrutan får inte glömmas av en omstart: en framtida cooldown
        på disk ska stoppa cykeln före ALL nätaktivitet."""
        def explode(req, timeout=None):
            raise AssertionError("upstream-anrop trots persisterad backoff")

        tokenserver._PROBE_STATE_PATH.write_text(
            json.dumps({"cooldown_until": time.time() + 3600}),
            encoding="utf-8")
        with mock.patch.object(tokenserver, "_probe_state_loaded", False), \
                mock.patch.object(tokenserver, "_probe_cooldown_until", 0.0), \
                mock.patch.object(tokenserver, "_read_oauth_candidates",
                                  return_value=[("fresh-token", None)]), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=explode):
            found = tokenserver._probe_limits()
            status = tokenserver._probe_status

        self.assertIsNone(found)
        self.assertIn("persisted", status)

    def test_probe_ignores_expired_persisted_cooldown(self):
        tokenserver._PROBE_STATE_PATH.write_text(
            json.dumps({"cooldown_until": time.time() - 60}),
            encoding="utf-8")

        def fake_urlopen(req, timeout=None):
            return _FakeUsageResponse({"limits": [
                {"kind": "weekly_all", "percent": 12,
                 "resets_at": "2100-01-02T00:00:00+00:00"},
            ]})

        with mock.patch.object(tokenserver, "_probe_state_loaded", False), \
                mock.patch.object(tokenserver, "_probe_cooldown_until", 0.0), \
                mock.patch.object(tokenserver, "_dead_tokens", {}), \
                mock.patch.object(tokenserver, "_read_oauth_candidates",
                                  return_value=[("fresh-token", None)]), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            found = tokenserver._probe_limits()

        self.assertEqual(found["weekPct"], 12.0)

    def test_rate_limit_persists_cooldown_to_disk(self):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.get_full_url(), 429, "Too Many Requests", None, None)

        with mock.patch.object(tokenserver, "_probe_cooldown_until", 0.0), \
                mock.patch.object(tokenserver, "_dead_tokens", {}), \
                mock.patch.object(tokenserver, "_read_oauth_candidates",
                                  return_value=[("fresh-token", None)]), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            tokenserver._probe_limits()

        saved = json.loads(
            tokenserver._PROBE_STATE_PATH.read_text(encoding="utf-8"))
        self.assertGreater(saved["cooldown_until"], time.time() + 500)

    def test_refresh_updates_failure_streak(self):
        with mock.patch.object(tokenserver, "_probe_failure_streak", 0), \
                mock.patch.object(tokenserver, "_last_limits", None), \
                mock.patch.object(tokenserver, "_last_probed", 0.0), \
                mock.patch.object(tokenserver, "_limits_refreshing", False), \
                mock.patch.object(tokenserver, "_probe_limits",
                                  return_value=None):
            tokenserver._refresh_limits()
            self.assertEqual(tokenserver._probe_failure_streak, 1)
            tokenserver._refresh_limits()
            self.assertEqual(tokenserver._probe_failure_streak, 2)
        with mock.patch.object(tokenserver, "_probe_failure_streak", 4), \
                mock.patch.object(tokenserver, "_last_limits", None), \
                mock.patch.object(tokenserver, "_last_probed", 0.0), \
                mock.patch.object(tokenserver, "_limits_refreshing", False), \
                mock.patch.object(tokenserver, "_probe_limits",
                                  return_value={"weekPct": 60.0}):
            tokenserver._refresh_limits()
            self.assertEqual(tokenserver._probe_failure_streak, 0)

    def test_probe_gives_up_when_every_candidate_is_rejected(self):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.get_full_url())
            raise urllib.error.HTTPError(
                req.get_full_url(), 401, "Unauthorized", None, None)

        with mock.patch.object(
                tokenserver, "_read_oauth_candidates",
                return_value=[("stale-a", None), ("stale-b", None)]), \
                mock.patch.object(tokenserver.urllib.request, "urlopen",
                                  side_effect=fake_urlopen):
            found = tokenserver._probe_limits()

        self.assertIsNone(found)
        self.assertEqual(tokenserver._probe_status, "usage_http_401")
        # Båda källorna provades mot usage-kontraktet, och den döda tokenen
        # skickades aldrig vidare till header-proben.
        self.assertEqual(calls, [
            "https://api.anthropic.com/api/oauth/usage",
            "https://api.anthropic.com/api/oauth/usage",
        ])

    def _headers(self, model_bucket):
        return {
            "anthropic-ratelimit-unified-5h-utilization": "0.12",
            "anthropic-ratelimit-unified-5h-reset": "3600",
            "anthropic-ratelimit-unified-7d-utilization": "0.47",
            "anthropic-ratelimit-unified-7d-reset": "7200",
            f"anthropic-ratelimit-unified-{model_bucket}-utilization": "0.73",
            f"anthropic-ratelimit-unified-{model_bucket}-reset": "10800",
        }

    def test_named_model_week_buckets_keep_their_real_english_label(self):
        cases = {
            "7d_fable": "FABLE · WEEK",
            "7d_opus": "OPUS · WEEK",
            "7d_sonnet": "SONNET · WEEK",
        }

        for bucket, expected in cases.items():
            with self.subTest(bucket=bucket):
                parsed = tokenserver._parse_limit_headers(
                    self._headers(bucket), now_ts=1_800_000_000)
                self.assertEqual(parsed["modelLabel"], expected)
                self.assertEqual(parsed["modelPct"], 73.0)

    def test_generic_model_week_has_no_invented_model_label(self):
        parsed = tokenserver._parse_limit_headers(
            self._headers("7d_model"), now_ts=1_800_000_000)

        self.assertEqual(parsed["modelPct"], 73.0)
        self.assertNotIn("modelLabel", parsed)

    def test_generic_week_does_not_become_a_model_week(self):
        headers = {
            "anthropic-ratelimit-unified-5h-utilization": "12",
            "anthropic-ratelimit-unified-7d-utilization": "47",
        }

        parsed = tokenserver._parse_limit_headers(
            headers, now_ts=1_800_000_000)

        self.assertEqual(parsed["weekPct"], 47.0)
        self.assertNotIn("modelPct", parsed)
        self.assertNotIn("modelLabel", parsed)

    def test_unknown_named_week_cannot_overwrite_general_week(self):
        headers = self._headers("7d_haiku")

        parsed = tokenserver._parse_limit_headers(
            headers, now_ts=1_800_000_000)

        self.assertEqual(parsed["weekPct"], 47.0)
        self.assertNotIn("modelPct", parsed)
        self.assertEqual(parsed["unknownBuckets"], ["7d_haiku"])

    def test_snapshot_never_infers_label_from_active_agent_model(self):
        base = {
            "v": 1,
            "dayTokens": 0,
            "dayTokensPerHour": 0,
            "daySessions": 0,
            "monthTokens": 0,
            "at": "2026-08-07T10:00:00+02:00",
        }
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(tokenserver, "_compute", return_value=base), \
                mock.patch.object(tokenserver, "get_limits", return_value={
                    "sessionPct": 12.0,
                    "modelPct": 73.0,
                    "modelResetAt": 1_800_001_800,
                    "modelObservedAt": 1_800_000_000,
                    "modelIdentity": tokenserver._quota_identity(
                        "claude", "model_weekly"),
                }), \
                mock.patch.object(tokenserver, "_read_codex_limits",
                                  return_value={}), \
                mock.patch.object(
                    tokenserver, "_persist_quota_records_async"):
            tokenserver._last_result = None
            tokenserver._last_computed = 0.0
            snapshot = tokenserver.get_snapshot(
                Path("/unused"), history=StubHistory(),
                now_ts=1_800_000_000,
                quota_cache=QuotaCache(Path(temp_dir) / "quota.json"))

        self.assertEqual(snapshot["claudeModelWeekPct"], 73.0)
        self.assertIsNone(snapshot["claudeModelWeekLabel"])

    def test_stale_limits_refresh_in_background_without_blocking_response(self):
        started = threading.Event()
        release = threading.Event()

        def slow_probe():
            started.set()
            release.wait(timeout=1)
            return {"weekPct": 48.0}

        previous = (
            tokenserver._last_limits,
            tokenserver._last_probed,
            tokenserver._limits_refreshing,
        )
        try:
            tokenserver._last_limits = {"weekPct": 47.0}
            tokenserver._last_probed = 0.0
            tokenserver._limits_refreshing = False
            with mock.patch.object(
                    tokenserver, "_probe_limits", side_effect=slow_probe):
                before = time.perf_counter()
                limits = tokenserver.get_limits()
                elapsed = time.perf_counter() - before
                self.assertTrue(started.wait(timeout=0.2))
                self.assertLess(elapsed, 0.1)
                self.assertEqual(limits, {"weekPct": 47.0})
                release.set()
                for _ in range(20):
                    if not tokenserver._limits_refreshing:
                        break
                    time.sleep(0.01)
                self.assertEqual(tokenserver._last_limits,
                                 {"weekPct": 48.0})
        finally:
            release.set()
            (tokenserver._last_limits,
             tokenserver._last_probed,
             tokenserver._limits_refreshing) = previous

    def test_failed_background_refresh_marks_latest_attempt_failed(self):
        previous = (
            tokenserver._last_limits,
            tokenserver._last_probed,
            tokenserver._limits_refreshing,
        )
        try:
            tokenserver._last_limits = {"weekPct": 47.0}
            tokenserver._last_probed = 0.0
            tokenserver._limits_refreshing = False
            with mock.patch.object(
                    tokenserver, "_probe_limits", return_value=None):
                self.assertEqual(tokenserver.get_limits(),
                                 {"weekPct": 47.0})
                for _ in range(20):
                    if not tokenserver._limits_refreshing:
                        break
                    time.sleep(0.01)
            self.assertIsNone(tokenserver._last_limits)
        finally:
            (tokenserver._last_limits,
             tokenserver._last_probed,
             tokenserver._limits_refreshing) = previous


class CodexLimitLogTests(unittest.TestCase):
    @staticmethod
    def _event(rate_limits, timestamp="2026-08-07T10:00:00Z"):
        return {
            "timestamp": timestamp,
            "type": "event_msg",
            "payload": {
                "type": "token_count",
                "info": None,
                "rate_limits": rate_limits,
            },
        }

    @staticmethod
    def _limits(pct, reset_at=1_900_000_000, *, name=None,
                window_minutes=10080, limit_id="synthetic-general"):
        result = {
            "limit_id": limit_id,
            "primary": {
                "used_percent": pct,
                "window_minutes": window_minutes,
                "resets_at": reset_at,
            },
        }
        if name is not None:
            result["limit_name"] = name
        return result

    def test_codex_window_value_preserves_used_percent(self):
        window = {
            "used_percent": 57.0,
            "window_minutes": 10080,
            "resets_at": 1_900_000_000,
        }
        pct, reset_min, window_min = tokenserver._codex_window(
            window, now_ts=1_899_996_400)
        self.assertEqual(pct, 57.0)
        self.assertEqual(reset_min, 60)
        self.assertEqual(window_min, 10080)

    def test_app_server_rate_limit_maps_remaining_38_to_used_62(self):
        response = {
            "rateLimits": {
                "limitId": "codex",
                "limitName": None,
                "primary": {
                    "usedPercent": 62,
                    "windowDurationMins": 10080,
                    "resetsAt": 1_900_000_000,
                },
            },
            "rateLimitsByLimitId": {
                "codex": {
                    "limitId": "codex",
                    "limitName": None,
                    "primary": {
                        "usedPercent": 62,
                        "windowDurationMins": 10080,
                        "resetsAt": 1_900_000_000,
                    },
                },
                "codex_bengalfox": {
                    "limitId": "codex_bengalfox",
                    "limitName": "GPT-5.3-Codex-Spark",
                    "primary": {
                        "usedPercent": 0,
                        "windowDurationMins": 10080,
                        "resetsAt": 1_900_100_000,
                    },
                },
            },
        }

        found = tokenserver._parse_codex_rate_limits_response(
            response, observed_at=1_800_000_000,
            now_ts=1_800_000_000)

        self.assertEqual(found["codexWeekPct"], 62.0)
        self.assertEqual(found["codexWeekResetAt"], 1_900_000_000)
        self.assertFalse(found["codexWeekStale"])

    def test_refresh_prefers_live_app_server_over_stale_rollout(self):
        previous = (
            tokenserver._last_codex_limits,
            tokenserver._last_codex_read,
            tokenserver._codex_refreshing,
        )
        try:
            tokenserver._codex_refreshing = True
            with mock.patch.object(
                    tokenserver, "_read_codex_app_server_limits",
                    return_value={"codexWeekPct": 62.0}), \
                    mock.patch.object(
                        tokenserver, "_scan_codex_limits",
                        return_value={"codexWeekPct": 58.0}):
                tokenserver._refresh_codex_limits()

            self.assertEqual(
                tokenserver._last_codex_limits["codexWeekPct"], 62.0)
        finally:
            (tokenserver._last_codex_limits,
             tokenserver._last_codex_read,
             tokenserver._codex_refreshing) = previous

    def test_latest_rate_limit_is_read_from_tail_without_read_text(self):
        rate_limits = self._limits(35.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-large.jsonl"
            path.write_bytes(
                b'{"type":"noise"}\n' * 100_000 +
                json.dumps(self._event(rate_limits)).encode() + b"\n")

            with mock.patch.object(
                    Path, "read_text",
                    side_effect=AssertionError("full file read is forbidden")):
                found = tokenserver._read_latest_rate_limits(path)

        self.assertEqual(found, rate_limits)

    def test_reverse_scan_stops_at_configured_byte_limit(self):
        rate_limits = self._limits(35.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-no-recent-limit.jsonl"
            path.write_bytes(
                json.dumps(self._event(rate_limits)).encode() + b"\n" +
                b'{"type":"noise"}\n' * 10_000)

            found = tokenserver._read_latest_rate_limits(
                path, block_size=4096, max_bytes=64 * 1024)

        self.assertIsNone(found)

    def test_reverse_scan_never_reads_more_than_one_mebibyte(self):
        rate_limits = self._limits(35.0)
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-bounded.jsonl"
            path.write_bytes(
                json.dumps(self._event(rate_limits)).encode() + b"\n" +
                b'{"type":"noise"}\n' * 80_000)

            found = tokenserver._read_latest_rate_limits(
                path, max_bytes=2 * tokenserver.CODEX_LIMIT_SCAN_BYTES)

        self.assertIsNone(found)

    def test_only_expected_rollout_event_envelope_is_accepted(self):
        limits = self._limits(46.0)
        impostors = [
            {"rate_limits": limits},
            {"type": "message", "payload": {"rate_limits": limits}},
            {"type": "event_msg", "payload": {
                "type": "message", "rate_limits": limits}},
            {"type": "event_msg", "payload": {
                "type": "token_count", "message": {
                    "rate_limits": limits}}},
            {"type": "event_msg", "payload": {
                "type": "token_count", "content": json.dumps({
                    "rate_limits": limits})}},
        ]
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout.jsonl"
            path.write_text("".join(json.dumps(row) + "\n"
                                    for row in impostors))

            found = tokenserver._read_latest_rate_limits(path)

        self.assertIsNone(found)

    def test_missing_or_non_numeric_window_is_never_classified(self):
        for value in (None, "10080", True):
            limits = self._limits(46.0, window_minutes=value)
            if value is None:
                del limits["primary"]["window_minutes"]
            with self.subTest(value=value):
                self.assertIsNone(tokenserver._codex_general_observation(
                    limits, observed_at=1_800_000_000,
                    now_ts=1_800_000_000))

    def test_empty_limit_name_remains_general_weekly(self):
        observation = tokenserver._codex_general_observation(
            self._limits(46.0, name=""), observed_at=1_800_000_000,
            now_ts=1_800_000_000)

        self.assertEqual(observation["pct"], 46.0)

    def test_non_string_limit_name_is_unclassifiable(self):
        for value in (False, 0, [], {}):
            limits = self._limits(46.0)
            limits["limit_name"] = value
            with self.subTest(value=value):
                self.assertIsNone(tokenserver._codex_general_observation(
                    limits, observed_at=1_800_000_000,
                    now_ts=1_800_000_000))

    def test_newer_named_quota_does_not_hide_older_general_quota(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            older = root / "rollout-older.jsonl"
            newer = root / "rollout-newer.jsonl"
            older.write_text(json.dumps(self._event(
                self._limits(46.0), "2026-08-07T10:00:00Z")) + "\n")
            newer.write_text(json.dumps(self._event(self._limits(
                0.0, name="GPT-5.3-Codex-Spark", limit_id="spark"),
                "2026-08-07T11:00:00Z")) + "\n")
            os.utime(older, (100, 100))
            os.utime(newer, (200, 200))
            with mock.patch.object(tokenserver, "CODEX_SESSIONS", root), \
                    mock.patch.object(tokenserver.time, "time",
                                      return_value=1_800_000_000):
                found = tokenserver._scan_codex_limits()

        self.assertEqual(found["codexWeekPct"], 46.0)
        self.assertFalse(found["codexWeekStale"])

    def test_scan_searches_twenty_files_and_selects_newest_general(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            for index in range(21):
                path = root / f"rollout-{index:02d}.jsonl"
                limits = self._limits(
                    61.0 if index == 18 else 0.0,
                    name=None if index == 18 else "named",
                    limit_id=f"id-{index}")
                path.write_text(json.dumps(self._event(
                    limits, f"2026-08-07T10:{index:02d}:00Z")) + "\n")
                os.utime(path, (index, index))
            with mock.patch.object(tokenserver, "CODEX_SESSIONS", root), \
                    mock.patch.object(tokenserver.time, "time",
                                      return_value=1_800_000_000), \
                    mock.patch.object(
                        tokenserver, "_read_codex_observations",
                        wraps=tokenserver._read_codex_observations) as read:
                found = tokenserver._scan_codex_limits()

        self.assertEqual(read.call_count, 20)
        self.assertEqual(found["codexWeekPct"], 61.0)

    def test_identity_is_hashed_and_raw_limit_id_is_not_returned(self):
        raw_identity = "synthetic-raw-limit-id"
        observation = tokenserver._codex_general_observation(
            self._limits(46.0, limit_id=raw_identity),
            observed_at=1_800_000_000, now_ts=1_800_000_000)

        serialized = json.dumps(observation)
        self.assertNotIn(raw_identity, serialized)
        self.assertRegex(observation["identity"], r"^[0-9a-f]{64}$")

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_path = Path(temp_dir) / "quota.json"
            cache = QuotaCache(cache_path, now=lambda: 1_800_000_000)
            cache.put(CachedQuota(
                provider="codex", scope="general_weekly",
                identity=observation["identity"], pct=observation["pct"],
                reset_at=observation["reset_at"],
                observed_at=observation["observed_at"]))
            persisted = cache_path.read_text()

        self.assertNotIn(raw_identity, persisted)
        self.assertIn(observation["identity"], persisted)

    def test_codex_scan_refreshes_in_background_without_blocking(self):
        started = threading.Event()
        release = threading.Event()

        def slow_scan():
            started.set()
            release.wait(timeout=1)
            return {"codexWeekPct": 49.0}

        previous = (
            tokenserver._last_codex_limits,
            tokenserver._last_codex_read,
            tokenserver._codex_refreshing,
        )
        try:
            tokenserver._last_codex_limits = {"codexWeekPct": 48.0}
            tokenserver._last_codex_read = 0.0
            tokenserver._codex_refreshing = False
            with mock.patch.object(
                    tokenserver, "_read_codex_app_server_limits",
                    return_value={}), mock.patch.object(
                    tokenserver, "_scan_codex_limits",
                    side_effect=slow_scan):
                before = time.perf_counter()
                limits = tokenserver._read_codex_limits()
                elapsed = time.perf_counter() - before
                self.assertTrue(started.wait(timeout=0.2))
                self.assertLess(elapsed, 0.1)
                self.assertEqual(limits, {"codexWeekPct": 48.0})
                release.set()
                for _ in range(20):
                    if not tokenserver._codex_refreshing:
                        break
                    time.sleep(0.01)
                self.assertEqual(tokenserver._last_codex_limits,
                                 {"codexWeekPct": 49.0})
        finally:
            release.set()
            (tokenserver._last_codex_limits,
             tokenserver._last_codex_read,
             tokenserver._codex_refreshing) = previous


class IncrementalUsageLogTests(unittest.TestCase):
    def setUp(self):
        self.previous_cache = tokenserver._file_cache
        tokenserver._file_cache = {}

    def tearDown(self):
        tokenserver._file_cache = self.previous_cache

    @staticmethod
    def _line(message_id, tokens):
        return json.dumps({
            "timestamp": datetime.now().astimezone().isoformat(),
            "sessionId": "session-a",
            "requestId": f"request-{message_id}",
            "message": {
                "id": message_id,
                "usage": {"input_tokens": tokens},
            },
        }) + "\n"

    def test_growing_log_is_parsed_only_from_previous_end(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            path = projects / "session.jsonl"
            path.write_text(self._line("first", 5))
            first_size = path.stat().st_size
            first = tokenserver._compute(projects)

            with path.open("a") as output:
                output.write(self._line("second", 7))
            with mock.patch.object(
                    tokenserver, "_parse_file",
                    wraps=tokenserver._parse_file) as parse_file:
                second = tokenserver._compute(projects)

        self.assertEqual(first["dayTokens"], 5)
        self.assertEqual(second["dayTokens"], 12)
        self.assertEqual(parse_file.call_args.kwargs["start_offset"], first_size)

    def test_atomically_replaced_larger_log_is_reparsed_from_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            path = projects / "session.jsonl"
            path.write_text(self._line("old", 5))
            first = tokenserver._compute(projects)

            replacement = projects / "replacement.jsonl"
            replacement.write_text(
                self._line("new", 7) + '{"type":"noise"}\n' * 20)
            os.replace(replacement, path)
            with mock.patch.object(
                    tokenserver, "_parse_file",
                    wraps=tokenserver._parse_file) as parse_file:
                second = tokenserver._compute(projects)

        self.assertEqual(first["dayTokens"], 5)
        self.assertEqual(second["dayTokens"], 7)
        self.assertEqual(parse_file.call_args.kwargs["start_offset"], 0)

    def test_partial_last_line_is_completed_on_next_increment(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            path = projects / "session.jsonl"
            pending = self._line("second", 7)
            split_at = len(pending) // 2
            path.write_text(self._line("first", 5) + pending[:split_at])

            first = tokenserver._compute(projects)
            with path.open("a") as output:
                output.write(pending[split_at:])
            second = tokenserver._compute(projects)

        self.assertEqual(first["dayTokens"], 5)
        self.assertEqual(second["dayTokens"], 12)


class UsageSnapshotTests(unittest.TestCase):
    @staticmethod
    def _base():
        return {
            "v": 1,
            "dayTokens": 123,
            "dayTokensPerHour": 45,
            "daySessions": 2,
            "monthTokens": 678,
            "at": "2026-08-07T12:00:00+02:00",
        }

    def _snapshot(self, history, now_ts, claude=None, codex=None,
                  quota_cache=None):
        if quota_cache is None:
            with tempfile.TemporaryDirectory() as temp_dir, \
                    mock.patch.object(
                        tokenserver, "_persist_quota_records_async"):
                return self._snapshot(
                    history, now_ts, claude=claude, codex=codex,
                    quota_cache=QuotaCache(Path(temp_dir) / "quota.json",
                                           now=lambda: now_ts))
        with mock.patch.object(tokenserver, "_compute",
                               return_value=self._base()), \
                mock.patch.object(tokenserver, "get_limits",
                                  return_value=claude or {}), \
                mock.patch.object(tokenserver, "_read_codex_limits",
                                  return_value=codex or {}):
            tokenserver._last_result = None
            tokenserver._last_computed = 0.0
            return tokenserver.get_snapshot(
                Path("/unused"), history=history, now_ts=now_ts,
                quota_cache=quota_cache)

    @staticmethod
    def _live_claude(now_ts, pct=47.0):
        return {
            "weekPct": pct,
            "weekResetAt": int(now_ts + 300 * 60),
            "weekObservedAt": int(now_ts),
            "weekIdentity": tokenserver._quota_identity(
                "claude", "general_weekly"),
        }

    @staticmethod
    def _live_codex(now_ts, pct=35.0):
        return {
            "codexWeekPct": pct,
            "codexWeekResetAt": int(now_ts + 300 * 60),
            "codexWeekObservedAt": int(now_ts),
            "codexWeekIdentity": tokenserver._quota_identity(
                "codex", "general_weekly", "synthetic"),
        }

    def test_stale_usage_totals_refresh_in_background(self):
        started = threading.Event()
        release = threading.Event()
        old_base = self._base()
        new_base = dict(old_base, dayTokens=999)

        def slow_compute(_projects_dir, _max_tracker_store=None):
            started.set()
            release.wait(timeout=1)
            return new_base

        previous = (
            tokenserver._last_result,
            tokenserver._last_computed,
            tokenserver._snapshot_refreshing,
        )
        try:
            tokenserver._last_result = old_base
            tokenserver._last_computed = (
                time.monotonic() - tokenserver.RECOMPUTE_EVERY_S - 1)
            tokenserver._snapshot_refreshing = False
            with mock.patch.object(
                    tokenserver, "_compute", side_effect=slow_compute), \
                    mock.patch.object(tokenserver, "get_limits",
                                      return_value={}), \
                    mock.patch.object(tokenserver, "_read_codex_limits",
                                      return_value={}), \
                    tempfile.TemporaryDirectory() as temp_dir:
                before = time.perf_counter()
                snapshot = tokenserver.get_snapshot(
                    Path("/unused"), history=StubHistory(),
                    now_ts=1_800_000_000,
                    quota_cache=QuotaCache(
                        Path(temp_dir) / "quota.json"))
                elapsed = time.perf_counter() - before
                self.assertTrue(started.wait(timeout=0.2))
                self.assertLess(elapsed, 0.1)
                self.assertEqual(snapshot["dayTokens"], 123)
                release.set()
                for _ in range(20):
                    if not tokenserver._snapshot_refreshing:
                        break
                    time.sleep(0.01)
                self.assertEqual(tokenserver._last_result["dayTokens"], 999)
        finally:
            release.set()
            (tokenserver._last_result,
             tokenserver._last_computed,
             tokenserver._snapshot_refreshing) = previous

    def test_snapshot_emits_today_hour_deltas_and_real_forecasts(self):
        local_tz = datetime.now().astimezone().tzinfo
        now_ts = datetime(2026, 8, 7, 12, 0, tzinfo=local_tz).timestamp()
        week_reset = now_ts + 5 * 60 * 60
        session_reset = now_ts + 3 * 60 * 60
        with tempfile.TemporaryDirectory() as temp_dir:
            history = UsageHistory(Path(temp_dir) / "history.json")
            for provider, window, points, reset_at in (
                    ("claude", "week", ((40, -2), (43, -1)), week_reset),
                    ("claude", "model_week", ((70, -2), (71, -1)),
                     week_reset),
                    ("claude", "session", ((10, -1),), session_reset),
                    ("codex", "week", ((30, -2), (32, -1)), week_reset)):
                for pct, hours_ago in points:
                    history.record(provider, window, pct,
                                   reset_at=reset_at,
                                   at=now_ts + hours_ago * 60 * 60)

            snapshot = self._snapshot(
                history, now_ts,
                claude={
                    "sessionPct": 21.0,
                    "sessionResetMin": 180,
                    "sessionResetAt": int(session_reset),
                    "weekPct": 47.0,
                    "weekResetAt": int(week_reset),
                    "weekObservedAt": int(now_ts),
                    "weekIdentity": tokenserver._quota_identity(
                        "claude", "general_weekly"),
                    "modelPct": 73.0,
                    "modelResetAt": int(week_reset),
                    "modelObservedAt": int(now_ts),
                    "modelIdentity": tokenserver._quota_identity(
                        "claude", "model_weekly"),
                    "modelLabel": "FABLE · WEEK",
                },
                codex=self._live_codex(now_ts))

        self.assertEqual(snapshot["v"], 2)
        self.assertEqual(snapshot["dayTokens"], 123)
        self.assertEqual(snapshot["claudeWeekTodayDeltaPct"], 7.0)
        self.assertEqual(snapshot["claudeModelWeekTodayDeltaPct"], 3.0)
        self.assertEqual(snapshot["claudeSessionHourDeltaPct"], 11.0)
        self.assertEqual(snapshot["codexWeekTodayDeltaPct"], 5.0)
        self.assertEqual(snapshot["claudeForecastState"], "at_reset")
        self.assertIsInstance(snapshot["claudeForecastPctAtReset"], int)
        self.assertEqual(snapshot["codexForecastState"], "at_reset")

    def test_snapshot_flattens_collecting_and_exhaustion_states(self):
        now_ts = 1_800_000_000
        history = StubHistory({
            "claude": Forecast(state="collecting"),
            "codex": Forecast(
                state="exhausts", exhausts_at=now_ts + 2 * 60 * 60,
                offset_minutes=-540),
        })

        snapshot = self._snapshot(
            history, now_ts,
            claude=self._live_claude(now_ts),
            codex=self._live_codex(now_ts))

        self.assertEqual(snapshot["claudeForecastState"], "collecting")
        self.assertIsNone(snapshot["claudeForecastPctAtReset"])
        self.assertEqual(snapshot["codexForecastState"], "exhausts")
        self.assertEqual(snapshot["codexForecastAt"], now_ts + 7200)
        self.assertEqual(snapshot["codexForecastOffsetMin"], -540)

    def test_snapshot_marks_forecast_unavailable_without_weekly_reset(self):
        snapshot = self._snapshot(
            StubHistory(), 1_800_000_000,
            claude={"weekPct": 47.0}, codex={})

        self.assertEqual(snapshot["claudeForecastState"], "unavailable")
        self.assertEqual(snapshot["codexForecastState"], "unavailable")
        self.assertIsNone(snapshot["claudeWeekTodayDeltaPct"])
        self.assertIsNone(snapshot["codexWeekTodayDeltaPct"])

    def test_snapshot_batches_all_quota_samples_into_one_atomic_write(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            history = UsageHistory(Path(temp_dir) / "history.json")
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            cache_write = threading.Event()
            cache_write_count = 0

            def record_cache_write(_record):
                nonlocal cache_write_count
                cache_write_count += 1
                if cache_write_count == 3:
                    cache_write.set()
                return True

            with mock.patch(
                    "tools.tokenserver.usage_history.os.replace",
                    wraps=os.replace) as replace, \
                    mock.patch.object(
                        cache, "put", side_effect=record_cache_write):
                self._snapshot(
                    history, now_ts,
                    claude={
                        "sessionPct": 21.0,
                        "sessionResetAt": int(now_ts + 180 * 60),
                        "weekPct": 47.0,
                        "weekResetAt": int(now_ts + 300 * 60),
                        "weekObservedAt": int(now_ts),
                        "weekIdentity": tokenserver._quota_identity(
                            "claude", "general_weekly"),
                        "modelPct": 73.0,
                        "modelResetAt": int(now_ts + 300 * 60),
                        "modelObservedAt": int(now_ts),
                        "modelIdentity": tokenserver._quota_identity(
                            "claude", "model_weekly"),
                    },
                    codex=self._live_codex(now_ts), quota_cache=cache)
                self.assertTrue(cache_write.wait(timeout=1))

            replace.assert_called_once()
            self.assertEqual(len(history.records), 4)

    def test_default_history_path_is_under_vibepulse_application_support(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(Path, "home", return_value=Path(temp_dir)):
            tokenserver._default_usage_history = None

            history = tokenserver._get_usage_history()

        self.assertEqual(
            history.path,
            Path(temp_dir) / "Library" / "Application Support" /
            "VibePulse" / "usage-history.json")
        tokenserver._default_usage_history = None

    def test_default_quota_cache_path_is_under_vibepulse_support(self):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(Path, "home", return_value=Path(temp_dir)):
            tokenserver._default_quota_cache = None
            cache = tokenserver._get_quota_cache()

        self.assertEqual(
            cache.path,
            Path(temp_dir) / "Library" / "Application Support" /
            "VibePulse" / "quota-cache.json")
        tokenserver._default_quota_cache = None

    def test_named_only_codex_uses_cache_stale_and_without_cache_is_null(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            cache.put(CachedQuota(
                provider="codex", scope="general_weekly",
                identity=tokenserver._quota_identity(
                    "codex", "general_weekly", "general"),
                pct=46.0, reset_at=now_ts + 3600,
                observed_at=now_ts - 60))
            stale = self._snapshot(
                StubHistory(), now_ts,
                codex={"codexNamedOnly": True}, quota_cache=cache)
            empty = self._snapshot(
                StubHistory(), now_ts,
                codex={"codexNamedOnly": True},
                quota_cache=QuotaCache(Path(temp_dir) / "empty.json",
                                       now=lambda: now_ts))

        self.assertEqual(stale["codexWeekPct"], 46.0)
        self.assertTrue(stale["codexWeekStale"])
        self.assertIsNone(empty["codexWeekPct"])
        self.assertFalse(empty["codexWeekStale"])

    def test_success_then_failed_attempt_resolves_only_through_stale_cache(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            original_put = cache.put
            persisted = threading.Event()

            def persist(record):
                result = original_put(record)
                if (record.provider == "codex" and
                        record.scope == "general_weekly"):
                    persisted.set()
                return result

            with mock.patch.object(cache, "put", side_effect=persist):
                fresh = self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts),
                    codex=self._live_codex(now_ts), quota_cache=cache)
                self.assertTrue(persisted.wait(timeout=1))
            stale = self._snapshot(
                StubHistory(), now_ts + 60,
                claude={}, codex={}, quota_cache=cache)

        self.assertFalse(fresh["claudeWeekStale"])
        self.assertFalse(fresh["codexWeekStale"])
        self.assertTrue(stale["claudeWeekStale"])
        self.assertTrue(stale["codexWeekStale"])

    def test_slow_cache_put_does_not_block_snapshot_and_is_single_flight(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            write_started = threading.Event()
            release_write = threading.Event()
            snapshot_returned = threading.Event()
            created_threads = []
            real_thread = threading.Thread

            def slow_put(_record):
                write_started.set()
                release_write.wait(timeout=2)
                return True

            def make_thread(*args, **kwargs):
                thread = real_thread(*args, **kwargs)
                created_threads.append(thread)
                return thread

            def serve():
                self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts), quota_cache=cache)
                snapshot_returned.set()

            with mock.patch.object(cache, "put", side_effect=slow_put), \
                    mock.patch.object(tokenserver.threading, "Thread",
                                      side_effect=make_thread):
                caller = real_thread(target=serve)
                caller.start()
                self.assertTrue(write_started.wait(timeout=1))
                self.assertTrue(snapshot_returned.wait(timeout=0.2))
                for offset in range(1, 6):
                    self._snapshot(
                        StubHistory(), now_ts + offset,
                        claude=self._live_claude(now_ts + offset,
                                                 pct=47.0 + offset),
                        quota_cache=cache)
                self.assertEqual(len(created_threads), 1)
                release_write.set()
                caller.join(timeout=1)
                created_threads[0].join(timeout=1)

    def test_unchanged_live_snapshot_does_not_replace_cache_again(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            first_put = threading.Event()
            original_put = cache.put

            def persist(record):
                result = original_put(record)
                first_put.set()
                return result

            with mock.patch.object(cache, "put", side_effect=persist):
                self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts), quota_cache=cache)
                self.assertTrue(first_put.wait(timeout=1))
            with mock.patch(
                    "tools.tokenserver.quota_cache.os.replace",
                    wraps=os.replace) as replace:
                self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts), quota_cache=cache)

            self.assertEqual(replace.call_count, 0)

    def test_changed_live_observation_is_eventually_cached_for_failure(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quota.json"
            cache = QuotaCache(path, now=lambda: now_ts)
            original_put = cache.put
            changed_persisted = threading.Event()

            def persist(record):
                result = original_put(record)
                if record.pct == 48.0:
                    changed_persisted.set()
                return result

            with mock.patch.object(cache, "put", side_effect=persist):
                self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts), quota_cache=cache)
                self._snapshot(
                    StubHistory(), now_ts + 60,
                    claude=self._live_claude(now_ts + 60, pct=48.0),
                    quota_cache=cache)
                self.assertTrue(changed_persisted.wait(timeout=1))

            stale = self._snapshot(
                StubHistory(), now_ts + 120, claude={}, quota_cache=cache)

        self.assertEqual(stale["claudeWeekPct"], 48.0)
        self.assertTrue(stale["claudeWeekStale"])

    def test_failed_async_cache_write_retries_unchanged_observation(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            original_put = cache.put
            first_failed = threading.Event()
            retry_persisted = threading.Event()
            attempts = 0
            workers = []
            real_thread = threading.Thread

            def flaky_put(record):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    first_failed.set()
                    return False
                result = original_put(record)
                retry_persisted.set()
                return result

            def make_thread(*args, **kwargs):
                thread = real_thread(*args, **kwargs)
                workers.append(thread)
                return thread

            with mock.patch.object(cache, "put", side_effect=flaky_put), \
                    mock.patch.object(tokenserver.threading, "Thread",
                                      side_effect=make_thread):
                self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts), quota_cache=cache)
                self.assertTrue(first_failed.wait(timeout=1))
                workers[0].join(timeout=1)
                self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts), quota_cache=cache)
                self.assertTrue(retry_persisted.wait(timeout=1))
                workers[1].join(timeout=1)

            stale = self._snapshot(
                StubHistory(), now_ts + 60, claude={}, quota_cache=cache)

        self.assertEqual(attempts, 2)
        self.assertEqual(stale["claudeWeekPct"], 47.0)
        self.assertTrue(stale["claudeWeekStale"])

    def test_cache_lock_held_by_writer_does_not_block_changed_snapshot(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            lock_held = threading.Event()
            release_lock = threading.Event()
            snapshot_returned = threading.Event()
            workers = []
            real_thread = threading.Thread

            def hold_cache_lock():
                with cache._lock:
                    lock_held.set()
                    release_lock.wait(timeout=2)

            def make_thread(*args, **kwargs):
                thread = real_thread(*args, **kwargs)
                workers.append(thread)
                return thread

            def serve_changed():
                claude = self._live_claude(now_ts + 60, pct=48.0)
                claude.update({
                    "modelPct": 73.0,
                    "modelResetAt": int(now_ts + 360 * 60),
                    "modelObservedAt": int(now_ts + 60),
                    "modelIdentity": tokenserver._quota_identity(
                        "claude", "model_weekly"),
                })
                self._snapshot(
                    StubHistory(), now_ts + 60,
                    claude=claude,
                    codex=self._live_codex(now_ts + 60, pct=36.0),
                    quota_cache=cache)
                snapshot_returned.set()

            holder = real_thread(target=hold_cache_lock)
            holder.start()
            self.assertTrue(lock_held.wait(timeout=1))
            try:
                with mock.patch.object(tokenserver.threading, "Thread",
                                       side_effect=make_thread):
                    caller = real_thread(target=serve_changed)
                    caller.start()
                    returned_without_cache_lock = snapshot_returned.wait(
                        timeout=0.2)
            finally:
                release_lock.set()
            holder.join(timeout=1)
            caller.join(timeout=1)
            for worker in workers:
                worker.join(timeout=1)
            self.assertTrue(returned_without_cache_lock)

    def test_missing_snapshot_reads_stale_while_cache_lock_is_held(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            cache.put(CachedQuota(
                provider="claude", scope="general_weekly",
                identity=tokenserver._quota_identity(
                    "claude", "general_weekly"), pct=47.0,
                reset_at=now_ts + 3600, observed_at=now_ts - 60))
            returned = threading.Event()
            result = []

            def serve_missing():
                result.append(self._snapshot(
                    StubHistory(), now_ts, claude={}, codex={},
                    quota_cache=cache))
                returned.set()

            with cache._lock:
                caller = threading.Thread(target=serve_missing)
                caller.start()
                returned_without_writer_lock = returned.wait(timeout=0.2)
            caller.join(timeout=1)

        self.assertTrue(returned_without_writer_lock)
        self.assertEqual(result[0]["claudeWeekPct"], 47.0)
        self.assertTrue(result[0]["claudeWeekStale"])

    def test_mixed_snapshot_reads_stale_while_cache_lock_is_held(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            cache.put(CachedQuota(
                provider="codex", scope="general_weekly",
                identity=tokenserver._quota_identity(
                    "codex", "general_weekly", "synthetic"), pct=35.0,
                reset_at=now_ts + 3600, observed_at=now_ts - 60))
            returned = threading.Event()
            result = []

            def serve_mixed():
                result.append(self._snapshot(
                    StubHistory(), now_ts,
                    claude=self._live_claude(now_ts), codex={},
                    quota_cache=cache))
                returned.set()

            with mock.patch.object(
                    tokenserver, "_persist_quota_records_async"):
                with cache._lock:
                    caller = threading.Thread(target=serve_mixed)
                    caller.start()
                    returned_without_writer_lock = returned.wait(timeout=0.2)
                caller.join(timeout=1)

        self.assertTrue(returned_without_writer_lock)
        self.assertEqual(result[0]["claudeWeekPct"], 47.0)
        self.assertFalse(result[0]["claudeWeekStale"])
        self.assertEqual(result[0]["codexWeekPct"], 35.0)
        self.assertTrue(result[0]["codexWeekStale"])

    def test_restart_cache_expires_exactly_and_reset_minutes_decrease(self):
        now_ts = 1_800_000_000
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "quota.json"
            first_cache = QuotaCache(path, now=lambda: now_ts)
            first_cache.put(CachedQuota(
                provider="claude", scope="general_weekly",
                identity=tokenserver._quota_identity(
                    "claude", "general_weekly"), pct=47.0,
                reset_at=now_ts + 120, observed_at=now_ts))
            restarted = QuotaCache(path, now=lambda: now_ts)
            first = self._snapshot(
                StubHistory(), now_ts, quota_cache=restarted)
            later = self._snapshot(
                StubHistory(), now_ts + 60, quota_cache=restarted)
            expired = self._snapshot(
                StubHistory(), now_ts + 120, quota_cache=restarted)

        self.assertEqual(first["claudeWeekResetMin"], 2)
        self.assertEqual(later["claudeWeekResetMin"], 1)
        self.assertIsNone(expired["claudeWeekPct"])
        self.assertIsNone(expired["claudeWeekResetMin"])
        self.assertFalse(expired["claudeWeekStale"])

    def test_stale_cache_is_not_recorded_or_used_for_forecast(self):
        now_ts = 1_800_000_000
        history = StubHistory()
        with tempfile.TemporaryDirectory() as temp_dir:
            cache = QuotaCache(Path(temp_dir) / "quota.json",
                               now=lambda: now_ts)
            cache.put(CachedQuota(
                provider="claude", scope="general_weekly",
                identity=tokenserver._quota_identity(
                    "claude", "general_weekly"), pct=47.0,
                reset_at=now_ts + 3600, observed_at=now_ts - 60))
            snapshot = self._snapshot(
                history, now_ts, claude={}, codex={}, quota_cache=cache)

        self.assertEqual(history.record_calls, [])
        self.assertIsNone(snapshot["claudeWeekTodayDeltaPct"])
        self.assertEqual(snapshot["claudeForecastState"], "unavailable")

    def test_optional_stale_flags_are_false_when_percent_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            snapshot = self._snapshot(
                StubHistory(), 1_800_000_000,
                quota_cache=QuotaCache(Path(temp_dir) / "quota.json"))

        self.assertFalse(snapshot["claudeWeekStale"])
        self.assertFalse(snapshot["claudeModelWeekStale"])
        self.assertFalse(snapshot["codexWeekStale"])


class HandlerPrivacyTests(unittest.TestCase):
    def _handler(self, path):
        handler = tokenserver.Handler.__new__(tokenserver.Handler)
        handler.path = path
        handler.projects_dir = Path("/private/source/path")
        handler.agent_status = mock.Mock()
        handler._send = mock.Mock()
        return handler

    def test_tokens_error_is_sanitized_but_cause_reaches_the_local_log(self):
        handler = self._handler("/api/tokens")
        with mock.patch.object(
                tokenserver, "get_snapshot",
                side_effect=RuntimeError("/private/source/path secret")), \
                self.assertLogs("tokenserver", level="ERROR") as captured:
            handler.do_GET()

        # Över LAN:et: bara kontraktets sanerade form. I den lokala loggen:
        # hela orsaken — ett tyst 500 var så serverfel förblev osynliga.
        handler._send.assert_called_once_with(
            500, {"error": "internal server error"})
        self.assertIn("/private/source/path secret",
                      "\n".join(captured.output))

    def test_root_diagnostics_contain_names_but_no_header_values_or_body(self):
        handler = self._handler("/")
        with mock.patch.object(tokenserver, "_probe_status", "http_401"), \
                mock.patch.object(tokenserver, "_probe_headers", [
                    "anthropic-ratelimit-unified-7d-utilization"]), \
                mock.patch.object(tokenserver, "_probe_unknown_buckets", [
                    "7d_haiku"]):
            handler.do_GET()

        payload = handler._send.call_args.args[1]
        serialized = json.dumps(payload)
        self.assertNotIn("response body", serialized)
        self.assertEqual(payload["ratelimitHeaders"], [
            "anthropic-ratelimit-unified-7d-utilization"])
        self.assertEqual(payload["unknownRateLimitBuckets"], ["7d_haiku"])


class HandlerErrorLoggingTests(unittest.TestCase):
    """500-kontraktet plus loggning: felen ska synas lokalt, aldrig på LAN:et,
    och en försvunnen klient är inte ett serverfel."""

    def _handler(self, path):
        handler = tokenserver.Handler.__new__(tokenserver.Handler)
        handler.path = path
        handler.projects_dir = Path("/private/source/path")
        handler.agent_status = mock.Mock()
        handler.client_address = ("192.0.2.10", 4711)
        handler._send = mock.Mock()
        return handler

    def test_agent_status_route_keeps_the_error_contract(self):
        # Rutten saknade try/except: ett fel läckte som rå traceback över
        # HTTP i stället för kontraktets {"error": ...}.
        handler = self._handler("/api/agent-status")
        handler.agent_status.snapshot.side_effect = RuntimeError("trasig")
        with self.assertLogs("tokenserver", level="ERROR") as captured:
            handler.do_GET()

        handler._send.assert_called_once_with(
            500, {"error": "internal server error"})
        self.assertIn("trasig", "\n".join(captured.output))

    def test_client_disconnect_is_quiet_and_not_a_500(self):
        handler = self._handler("/api/agent-status")
        handler.agent_status.snapshot.return_value = {"v": 1}
        handler._send.side_effect = BrokenPipeError()
        with self.assertNoLogs("tokenserver"):
            handler.do_GET()

        handler._send.assert_called_once()  # inget 500-försök till ett lik

    def test_producer_connection_error_is_a_server_error_not_a_disconnect(self):
        # ConnectionError FRÅN producenten är ett serverfel och ska logga +
        # 500 — bara under svarsskrivningen betyder det att klienten dog.
        handler = self._handler("/api/agent-status")
        handler.agent_status.snapshot.side_effect = ConnectionError("inuti")
        with self.assertLogs("tokenserver", level="ERROR") as captured:
            handler.do_GET()

        handler._send.assert_called_once_with(
            500, {"error": "internal server error"})
        self.assertIn("inuti", "\n".join(captured.output))

    def test_unwritable_payload_logs_and_falls_back_to_500(self):
        handler = self._handler("/api/agent-status")
        handler.agent_status.snapshot.return_value = {"v": 1}
        handler._send.side_effect = [TypeError("oserialiserbar"), None]
        with self.assertLogs("tokenserver", level="ERROR") as captured:
            handler.do_GET()

        self.assertEqual(handler._send.call_count, 2)
        self.assertEqual(handler._send.call_args.args[0], 500)
        self.assertIn("svarsskrivningen", "\n".join(captured.output))

    def test_log_error_reaches_the_log_while_access_log_stays_muted(self):
        handler = self._handler("/api/tokens")
        with self.assertLogs("tokenserver", level="WARNING") as captured:
            handler.log_error("code %d, message %s", 400, "Bad request")
        self.assertIn("Bad request", "\n".join(captured.output))

        with self.assertNoLogs("tokenserver"):
            handler.log_message("%s", "GET /api/tokens 200")


class UsageComputeHealthTests(unittest.TestCase):
    """OBS-08: en kraschad omräkning får frysa siffrorna (senaste goda
    serveras vidare) men aldrig tyst — logg vid övergången, strypt
    upprepning, usageComputeOk på GET /, och återhämtningen loggad."""

    def test_compute_crash_logs_throttled_and_flags_the_root_payload(self):
        with mock.patch.object(tokenserver, "_compute_failing_since", None), \
                mock.patch.object(tokenserver, "_last_compute_error_logged",
                                  None), \
                mock.patch.object(tokenserver, "_last_result", {"v": 1}), \
                mock.patch.object(tokenserver, "_last_computed", 0.0), \
                mock.patch.object(tokenserver, "_snapshot_refreshing", True), \
                mock.patch.object(tokenserver, "_compute",
                                  side_effect=RuntimeError("boom")):
            with self.assertLogs("tokenserver", level="ERROR") as captured:
                tokenserver._refresh_usage_totals(Path("/x"))
            self.assertIn("frysta siffror", "\n".join(captured.output))
            # Ihållande fel stryps — ingen ny rad inom fönstret.
            with self.assertNoLogs("tokenserver"):
                tokenserver._refresh_usage_totals(Path("/x"))
            self.assertIsNotNone(tokenserver._compute_failing_since)
            # Senaste goda snapshotet serveras vidare — det är avsikten.
            self.assertEqual(tokenserver._last_result, {"v": 1})

            handler = tokenserver.Handler.__new__(tokenserver.Handler)
            handler.path = "/"
            handler._send = mock.Mock()
            handler.do_GET()
            payload = handler._send.call_args.args[1]
            self.assertFalse(payload["usageComputeOk"])
            self.assertIsInstance(payload["usageComputeFailingForS"], int)

    def test_recovery_logs_the_transition_and_clears_the_flag(self):
        with mock.patch.object(tokenserver, "_compute_failing_since",
                               time.monotonic() - 42.0), \
                mock.patch.object(tokenserver, "_last_compute_error_logged",
                                  time.monotonic()), \
                mock.patch.object(tokenserver, "_last_result", None), \
                mock.patch.object(tokenserver, "_last_computed", 0.0), \
                mock.patch.object(tokenserver, "_snapshot_refreshing", True), \
                mock.patch.object(tokenserver, "_compute",
                                  return_value={"v": 2}):
            with self.assertLogs("tokenserver", level="INFO") as captured:
                tokenserver._refresh_usage_totals(Path("/x"))
            self.assertIn("frisk igen", "\n".join(captured.output))
            self.assertIsNone(tokenserver._compute_failing_since)
            self.assertEqual(tokenserver._last_result, {"v": 2})

            handler = tokenserver.Handler.__new__(tokenserver.Handler)
            handler.path = "/"
            handler._send = mock.Mock()
            handler.do_GET()
            payload = handler._send.call_args.args[1]
            self.assertTrue(payload["usageComputeOk"])
            self.assertIsNone(payload["usageComputeFailingForS"])

            # Återhämtningen stänger episoden: ett NYTT fel inom gamla
            # strypfönstret ska logga direkt, inte tigas ihjäl.
            self.assertIsNone(tokenserver._last_compute_error_logged)
            with mock.patch.object(tokenserver, "_compute",
                                   side_effect=RuntimeError("ny episod")), \
                    self.assertLogs("tokenserver", level="ERROR"):
                tokenserver._refresh_usage_totals(Path("/x"))


class MaxTrackerLiveHookTests(unittest.TestCase):
    """get_snapshot()'s observe_quota wiring: session/week windows, the
    *Stale: false gate, and Codex's natively-carried window_minutes."""

    @staticmethod
    def _base():
        return {
            "v": 1, "dayTokens": 0, "dayTokensPerHour": 0,
            "daySessions": 0, "monthTokens": 0,
            "at": "2026-08-07T12:00:00+02:00",
        }

    def _snapshot(self, now_ts, claude=None, codex=None, store=None):
        with tempfile.TemporaryDirectory() as temp_dir, \
                mock.patch.object(
                    tokenserver, "_persist_quota_records_async"), \
                mock.patch.object(tokenserver, "_compute",
                                  return_value=self._base()), \
                mock.patch.object(tokenserver, "get_limits",
                                  return_value=claude or {}), \
                mock.patch.object(tokenserver, "_read_codex_limits",
                                  return_value=codex or {}):
            tokenserver._last_result = None
            tokenserver._last_computed = 0.0
            return tokenserver.get_snapshot(
                Path("/unused"), history=StubHistory(), now_ts=now_ts,
                quota_cache=QuotaCache(Path(temp_dir) / "quota.json",
                                       now=lambda: now_ts),
                max_tracker_store=store)

    def test_live_claude_session_observation_uses_the_300_minute_window(self):
        store = mock.Mock()
        now_ts = 1_800_000_000
        self._snapshot(now_ts, claude={
            "sessionPct": 21.0,
            "sessionResetAt": now_ts + 3600,
        }, store=store)

        store.observe_quota.assert_any_call("claude", 300, 21.0, now_ts)

    def test_live_claude_week_observation_uses_the_10080_minute_window(self):
        store = mock.Mock()
        now_ts = 1_800_000_000
        self._snapshot(now_ts, claude={
            "weekPct": 47.0,
            "weekResetAt": now_ts + 300 * 60,
            "weekObservedAt": now_ts,
            "weekIdentity": tokenserver._quota_identity(
                "claude", "general_weekly"),
        }, store=store)

        store.observe_quota.assert_any_call("claude", 10080, 47.0, now_ts)

    def test_stale_or_absent_claude_never_reaches_observe_quota(self):
        store = mock.Mock()
        # No live claude payload at all: _resolve_weekly_quota's "live"
        # stays False (no cache hit either), session_pct stays None.
        self._snapshot(1_800_000_000, claude={}, store=store)

        for call in store.observe_quota.call_args_list:
            self.assertNotEqual(call.args[0], "claude")

    def test_codex_observations_carry_their_real_window_minutes(self):
        store = mock.Mock()
        now_ts = 1_800_000_000
        self._snapshot(now_ts, codex={
            "codexSessionPct": 12.0,
            "codexSessionResetMin": 90,
            "codexSessionWindowMinutes": 300,
            "codexWeekPct": 35.0,
            "codexWeekResetAt": now_ts + 300 * 60,
            "codexWeekObservedAt": now_ts,
            "codexWeekIdentity": tokenserver._quota_identity(
                "codex", "general_weekly", "synthetic"),
            "codexWeekWindowMinutes": 10080,
        }, store=store)

        store.observe_quota.assert_any_call("codex", 300, 12.0, now_ts)
        store.observe_quota.assert_any_call("codex", 10080, 35.0, now_ts)

    def test_codex_observation_without_window_minutes_is_never_guessed(self):
        store = mock.Mock()
        self._snapshot(1_800_000_000, codex={
            "codexSessionPct": 12.0,
            "codexSessionResetMin": 90,
            # deliberately no codexSessionWindowMinutes: never fabricate one
        }, store=store)

        for call in store.observe_quota.call_args_list:
            self.assertNotEqual(call.args[0], "codex")

    def test_no_store_means_the_hook_is_a_total_no_op(self):
        result = self._snapshot(1_800_000_000, claude={
            "sessionPct": 21.0, "sessionResetAt": 1_800_003_600})
        self.assertEqual(result["claudeSessionPct"], 21.0)  # ran normally


class MaxTrackerVolumeHookTests(unittest.TestCase):
    """_compute()'s observe_volume wiring: only newly-parsed records."""

    def setUp(self):
        self.previous_cache = tokenserver._file_cache
        tokenserver._file_cache = {}

    def tearDown(self):
        tokenserver._file_cache = self.previous_cache

    @staticmethod
    def _line(message_id, tokens):
        return json.dumps({
            "timestamp": datetime.now().astimezone().isoformat(),
            "sessionId": "session-a",
            "requestId": f"request-{message_id}",
            "message": {"id": message_id, "usage": {"input_tokens": tokens}},
        }) + "\n"

    def test_newly_parsed_records_observe_volume_exactly_once(self):
        store = mock.Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            (projects / "session.jsonl").write_text(self._line("first", 5))

            tokenserver._compute(projects, store)
            tokenserver._compute(projects, store)  # unchanged: no new work

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        store.observe_volume.assert_called_once_with("claude", today, 5)

    def test_growth_only_observes_the_new_tail(self):
        store = mock.Mock()
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            path = projects / "session.jsonl"
            path.write_text(self._line("first", 5))
            tokenserver._compute(projects, store)
            with path.open("a") as output:
                output.write(self._line("second", 7))
            tokenserver._compute(projects, store)

        today = datetime.now().astimezone().strftime("%Y-%m-%d")
        self.assertEqual(store.observe_volume.call_args_list, [
            mock.call("claude", today, 5),
            mock.call("claude", today, 7),
        ])

    def test_no_store_skips_the_volume_hook_entirely(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            (projects / "session.jsonl").write_text(self._line("first", 5))
            result = tokenserver._compute(projects)  # max_tracker_store=None

        self.assertEqual(result["dayTokens"], 5)


class MaxTrackerSingleWriterTests(unittest.TestCase):
    """Finding C: _compute()'s live volume scan and MaxTrackerStore's own
    backfill_step() both incrementally read ~/.claude/projects/**/*.jsonl.
    Without a single-writer boundary between them, a current-month Claude
    file gets its tokens counted twice -- once via each channel's own,
    independently-tracked byte offset into the exact same file. Drives a
    REAL store through BOTH channels (unlike MaxTrackerVolumeHookTests
    above, which only checks _compute's own call arguments in isolation
    via a mock) to prove the actual end-to-end total is never doubled."""

    def setUp(self):
        self.previous_cache = tokenserver._file_cache
        tokenserver._file_cache = {}

    def tearDown(self):
        tokenserver._file_cache = self.previous_cache

    @staticmethod
    def _line(message_id, tokens):
        return json.dumps({
            "timestamp": datetime.now().astimezone().isoformat(),
            "sessionId": "session-a",
            "requestId": f"request-{message_id}",
            "message": {"id": message_id, "usage": {"input_tokens": tokens}},
        }) + "\n"

    def test_live_scan_and_backfill_together_count_a_growing_file_once(self):
        # _compute() marks the store dirty and a REAL background thread
        # saves it (see _mark_max_tracker_dirty) whenever it observes
        # volume -- irrelevant to what this test checks (pure in-memory
        # counting), and left unpatched it can still be mid-write when
        # the tempdir below gets torn down. Patched out exactly like
        # MaxTrackerDirtyWriterTests isolates that separate concern.
        with mock.patch.object(tokenserver, "_mark_max_tracker_dirty"), \
                tempfile.TemporaryDirectory() as temp_dir:
            claude_root = Path(temp_dir) / "claude"
            claude_root.mkdir()
            codex_root = Path(temp_dir) / "codex"
            codex_root.mkdir()
            store = MaxTrackerStore(
                Path(temp_dir) / "max-tracker.json", codex_root, claude_root)

            path = claude_root / "session.jsonl"
            # A freshly written file's mtime is "now" -- current month --
            # the live scanner's exclusive territory per the single-writer
            # rule; backfill_step must defer to it entirely.
            path.write_text(self._line("first", 100))

            tokenserver._compute(claude_root, max_tracker_store=store)
            while store.backfill_step():
                pass

            today = datetime.now().astimezone().strftime("%Y-%m-%d")
            self.assertEqual(
                store._state["claude"]["days"][today]["vol"], 100)
            # Confirmed via the mechanism, not just the total: backfill
            # never READ this inode's content, but it DID record a
            # stat-only watermark at the file's current size (the other
            # half of the single-writer rule -- see the class docstring's
            # "live coverage advances the backfill watermark" note; a
            # deferred file with no bookkeeping at all would re-read and
            # double-count everything live already covered the moment it
            # ages into a new month).
            ino = path.stat().st_ino
            size_after_first = path.stat().st_size
            self.assertEqual(store._backfill["claude"], {
                ino: {"offset": size_after_first, "size": size_after_first,
                     "done": True, "discarding": False}})

            # The writer appends more -- both channels get another tick.
            with path.open("a") as output:
                output.write(self._line("second", 50))

            tokenserver._compute(claude_root, max_tracker_store=store)
            while store.backfill_step():
                pass

            self.assertEqual(
                store._state["claude"]["days"][today]["vol"], 150)
            # The watermark advanced to match -- still deferred (mtime is
            # "now" either way), still never read.
            size_after_second = path.stat().st_size
            self.assertGreater(size_after_second, size_after_first)
            self.assertEqual(store._backfill["claude"], {
                ino: {"offset": size_after_second, "size": size_after_second,
                     "done": True, "discarding": False}})

    def test_a_dormant_file_from_last_month_is_backfills_exclusive_territory(self):
        # See the patch note in the previous test -- same isolation from
        # the real background save thread _mark_max_tracker_dirty starts.
        with mock.patch.object(tokenserver, "_mark_max_tracker_dirty"), \
                tempfile.TemporaryDirectory() as temp_dir:
            claude_root = Path(temp_dir) / "claude"
            claude_root.mkdir()
            codex_root = Path(temp_dir) / "codex"
            codex_root.mkdir()
            store = MaxTrackerStore(
                Path(temp_dir) / "max-tracker.json", codex_root, claude_root)

            path = claude_root / "session.jsonl"
            old_timestamp = "2020-01-15T10:00:00Z"
            path.write_text(json.dumps({
                "timestamp": old_timestamp, "sessionId": "session-a",
                "requestId": "request-old",
                "message": {"id": "old", "usage": {"input_tokens": 300}},
            }) + "\n")
            aged = time.time() - 40 * 86400
            os.utime(path, (aged, aged))

            # The live scanner's own month filter excludes it outright
            # (mtime predates the current month) -- _compute contributes
            # nothing for this file, by its own pre-existing rule.
            tokenserver._compute(claude_root, max_tracker_store=store)
            while store.backfill_step():
                pass

            self.assertEqual(
                store._state["claude"]["days"]["2020-01-15"]["vol"], 300)
            # Backfill is the one that actually touched it.
            self.assertEqual(len(store._backfill["claude"]), 1)


class MaxTrackerDirtyWriterTests(unittest.TestCase):
    def setUp(self):
        self.previous = (
            tokenserver._max_tracker_dirty,
            tokenserver._max_tracker_writer_running,
            tokenserver._last_save_error_logged,
        )
        tokenserver._max_tracker_dirty = False
        tokenserver._max_tracker_writer_running = False
        # None = "aldrig loggat" — 0.0 hade svalt första felet på en maskin
        # med kort uptime, eftersom monotonic räknar från boot (CI fällde
        # exakt det).
        tokenserver._last_save_error_logged = None

    def tearDown(self):
        (tokenserver._max_tracker_dirty,
         tokenserver._max_tracker_writer_running,
         tokenserver._last_save_error_logged) = self.previous

    def _wait_for_writer_stop(self):
        for _ in range(50):
            if not tokenserver._max_tracker_writer_running:
                return
            time.sleep(0.01)
        self.fail("writer-tråden stannade aldrig")

    def test_marking_dirty_eventually_saves_off_the_calling_thread(self):
        store = mock.Mock()
        calling_thread = threading.current_thread()
        saved_from = []
        store.save.side_effect = (
            lambda: saved_from.append(threading.current_thread()))

        tokenserver._mark_max_tracker_dirty(store)

        for _ in range(50):
            if store.save.called:
                break
            time.sleep(0.01)

        store.save.assert_called_once()
        self.assertNotEqual(saved_from[0], calling_thread)

    def test_bursts_of_dirty_marks_coalesce_without_a_second_writer(self):
        store = mock.Mock()
        entered = threading.Event()
        release = threading.Event()

        def slow_save():
            entered.set()
            release.wait(timeout=1)

        store.save.side_effect = slow_save

        tokenserver._mark_max_tracker_dirty(store)
        self.assertTrue(entered.wait(timeout=1))
        for _ in range(5):  # burst while the writer is mid-save
            tokenserver._mark_max_tracker_dirty(store)
        release.set()

        for _ in range(50):
            if not tokenserver._max_tracker_writer_running:
                break
            time.sleep(0.01)

        # Coalesced into (at most) one trailing save after the in-flight
        # one, never a save per dirty mark.
        self.assertLessEqual(store.save.call_count, 2)

    def test_failed_save_keeps_dirty_so_the_next_mark_retries(self):
        # Var: dirty nollades FÖRE save() och felet svaldes — en disk- eller
        # rättighetsmiss slängde observationerna tyst tills någon orelaterad
        # händelse råkade markera om (OBS-10 i observability-backloggen).
        store = mock.Mock()
        store.save.side_effect = OSError("disken full")

        with self.assertLogs("tokenserver", level="ERROR") as captured:
            tokenserver._mark_max_tracker_dirty(store)
            for _ in range(50):
                if store.save.called:
                    break
                time.sleep(0.01)
            self._wait_for_writer_stop()

        store.save.assert_called_once()
        self.assertTrue(tokenserver._max_tracker_dirty)
        self.assertIn("save misslyckades", "\n".join(captured.output))

        # Nästa markering försöker igen — signalen överlevde felet, och
        # den lyckade skrivningen stänger episoden i loggen.
        store.save.side_effect = None
        with self.assertLogs("tokenserver", level="INFO") as recovered:
            tokenserver._mark_max_tracker_dirty(store)
            for _ in range(50):
                if store.save.call_count == 2:
                    break
                time.sleep(0.01)
            self._wait_for_writer_stop()
        self.assertEqual(store.save.call_count, 2)
        self.assertFalse(tokenserver._max_tracker_dirty)
        self.assertIn("lyckades igen", "\n".join(recovered.output))
        # Episoden är stängd: ett nytt fel loggar direkt trots att gamla
        # strypfönstret inte hunnit löpa ut.
        store.save.side_effect = OSError("disken full igen")
        with self.assertLogs("tokenserver", level="ERROR"):
            tokenserver._mark_max_tracker_dirty(store)
            for _ in range(50):
                if store.save.call_count == 3:
                    break
                time.sleep(0.01)
            self._wait_for_writer_stop()
        self.assertTrue(tokenserver._max_tracker_dirty)


class MaxTrackerBackfillLoopTests(unittest.TestCase):
    def test_loop_ticks_forever_and_marks_dirty_only_on_progress(self):
        store = mock.Mock()
        store.backfill_step.return_value = True
        stop_event = threading.Event()

        with mock.patch.object(
                tokenserver, "MAX_TRACKER_BACKFILL_TICK_S", 0.01), \
                mock.patch.object(
                    tokenserver, "_mark_max_tracker_dirty") as dirty:
            thread = threading.Thread(
                target=tokenserver._run_max_tracker_backfill,
                args=(store, stop_event))
            thread.start()
            for _ in range(200):
                if store.backfill_step.call_count >= 3:
                    break
                time.sleep(0.005)
            stop_event.set()
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(store.backfill_step.call_count, 3)
        self.assertGreaterEqual(dirty.call_count, 3)

    def test_loop_keeps_polling_after_backfill_step_goes_idle(self):
        store = mock.Mock()
        store.backfill_step.return_value = False
        stop_event = threading.Event()

        with mock.patch.object(
                tokenserver, "MAX_TRACKER_BACKFILL_TICK_S", 0.01), \
                mock.patch.object(
                    tokenserver, "_mark_max_tracker_dirty") as dirty:
            thread = threading.Thread(
                target=tokenserver._run_max_tracker_backfill,
                args=(store, stop_event))
            thread.start()
            for _ in range(200):
                if store.backfill_step.call_count >= 3:
                    break
                time.sleep(0.005)
            stop_event.set()
            thread.join(timeout=1)

        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(store.backfill_step.call_count, 3)
        dirty.assert_not_called()  # idle: cheap glob+stat only, never saved


class MaxTrackerEndpointTests(unittest.TestCase):
    @staticmethod
    def _stub_payload():
        return {
            "v": 1, "weeks": 20, "stale": False, "codingStreakDays": None,
            "claude": {"avgPeakPct": None, "maxWeeksStreak": 0,
                       "maxWeeks": 0, "maxDays": 0, "weekMaxed": [0] * 20,
                       "days": [[-1, -1]] * 140},
            "codex": {"avgPeakPct": None, "maxWeeksStreak": 0,
                      "maxWeeks": 0, "maxDays": 0, "weekMaxed": [0] * 20,
                      "days": [[-1, -1]] * 140},
        }

    def _handler(self, max_tracker_store, plans=None):
        handler = tokenserver.Handler.__new__(tokenserver.Handler)
        handler.path = "/api/max-tracker"
        handler.projects_dir = Path("/private/source/path")
        handler.agent_status = mock.Mock()
        handler.max_tracker_store = max_tracker_store
        handler.plans = plans or {"claude": "max20x", "codex": "plus"}
        handler._send = mock.Mock()
        return handler

    def test_route_serves_v1_payload_with_stale_mirroring_quota_cache(self):
        store = mock.Mock()
        store.snapshot.return_value = self._stub_payload()
        handler = self._handler(store)
        with mock.patch.object(
                tokenserver, "get_snapshot",
                return_value={"claudeWeekStale": True,
                             "codexWeekStale": False}):
            handler.do_GET()

        handler._send.assert_called_once()
        code, payload = handler._send.call_args.args
        self.assertEqual(code, 200)
        self.assertEqual(payload["v"], 1)
        self.assertEqual(payload["weeks"], 20)
        self.assertIn("claude", payload)
        self.assertIn("codex", payload)
        self.assertTrue(payload["stale"])

        store.snapshot.assert_called_once()
        call_args = store.snapshot.call_args.args
        self.assertEqual(call_args[1], {"claude": "max20x", "codex": "plus"})

    def test_route_is_not_stale_when_both_quota_windows_are_live(self):
        store = mock.Mock()
        store.snapshot.return_value = self._stub_payload()
        handler = self._handler(store)
        with mock.patch.object(
                tokenserver, "get_snapshot",
                return_value={"claudeWeekStale": False,
                             "codexWeekStale": False}):
            handler.do_GET()

        payload = handler._send.call_args.args[1]
        self.assertFalse(payload["stale"])

    def test_route_is_stale_when_only_codex_week_is_stale(self):
        store = mock.Mock()
        store.snapshot.return_value = self._stub_payload()
        handler = self._handler(store)
        with mock.patch.object(
                tokenserver, "get_snapshot",
                return_value={"claudeWeekStale": False,
                             "codexWeekStale": True}):
            handler.do_GET()

        payload = handler._send.call_args.args[1]
        self.assertTrue(payload["stale"])

    def test_route_error_is_sanitized(self):
        handler = self._handler(mock.Mock())
        with mock.patch.object(
                tokenserver, "get_snapshot",
                side_effect=RuntimeError("/private/source/path secret")), \
                self.assertLogs("tokenserver", level="ERROR"):
            handler.do_GET()

        handler._send.assert_called_once_with(
            500, {"error": "internal server error"})

    def test_root_listing_includes_max_tracker(self):
        handler = tokenserver.Handler.__new__(tokenserver.Handler)
        handler.path = "/"
        handler.agent_status = mock.Mock()
        handler._send = mock.Mock()

        handler.do_GET()

        payload = handler._send.call_args.args[1]
        self.assertIn("/api/max-tracker", payload["endpoints"])


class ArgumentParsingTests(unittest.TestCase):
    def test_claude_plan_choices_reject_unknown_value(self):
        parser = tokenserver._build_arg_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--claude-plan", "not-a-real-plan"])

    def test_codex_plan_choices_reject_unknown_value(self):
        parser = tokenserver._build_arg_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args(["--codex-plan", "not-a-real-plan"])

    def test_valid_plan_flags_are_accepted(self):
        parser = tokenserver._build_arg_parser()
        args = parser.parse_args(
            ["--claude-plan", "max5x", "--codex-plan", "pro"])
        self.assertEqual(args.claude_plan, "max5x")
        self.assertEqual(args.codex_plan, "pro")

    def test_plan_flags_default_to_none(self):
        parser = tokenserver._build_arg_parser()
        args = parser.parse_args([])
        self.assertIsNone(args.claude_plan)
        self.assertIsNone(args.codex_plan)


class ValueMultipleIntegrationTests(unittest.TestCase):
    """The value block, end to end through the transcript scanner."""

    def setUp(self):
        self.previous_cache = tokenserver._file_cache
        tokenserver._file_cache = {}
        self.previous_plan = tokenserver._claude_plan
        self.previous_codex_plan = tokenserver._codex_plan
        self.previous_override = tokenserver._plan_costs
        tokenserver._claude_plan = "max5x"
        tokenserver._codex_plan = None
        tokenserver._plan_costs = {"claude": 100.0}
        # Point the Codex scan at an empty tree so a developer machine that
        # happens to have ~/.codex cannot leak real usage into these figures.
        self.codex_dir = tempfile.TemporaryDirectory()
        self.previous_codex_root = codex_usage.DEFAULT_SESSIONS_DIR
        codex_usage.DEFAULT_SESSIONS_DIR = Path(self.codex_dir.name)
        codex_usage.reset_cache()

    def tearDown(self):
        tokenserver._file_cache = self.previous_cache
        tokenserver._claude_plan = self.previous_plan
        tokenserver._codex_plan = self.previous_codex_plan
        tokenserver._plan_costs = self.previous_override
        codex_usage.DEFAULT_SESSIONS_DIR = self.previous_codex_root
        codex_usage.reset_cache()
        self.codex_dir.cleanup()

    @staticmethod
    def _line(message_id, model="claude-opus-5", cache_read=1_000_000):
        return json.dumps({
            "timestamp": datetime.now().astimezone().isoformat(),
            "sessionId": "session-a",
            "requestId": f"request-{message_id}",
            "message": {
                "id": message_id,
                "model": model,
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "cache_read_input_tokens": cache_read,
                },
            },
        }) + "\n"

    def test_payload_carries_a_priced_value_block(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            (projects / "a.jsonl").write_text(self._line("one"))
            value = tokenserver._compute(projects)["value"]
        # 1M Opus 5 cache-read tokens: 5.00 x 0.10 = $0.50
        self.assertEqual(value["value_usd"], 0.5)
        self.assertEqual(value["state"], "ok")
        self.assertEqual(value["plan_usd"], 100.0)
        self.assertEqual(value["cost_source"], "configured")

    def test_duplicate_records_are_deduplicated_in_dollars_too(self):
        """The reason price rides on the record, not on a per-file sum.

        Claude Code writes the same assistant record into more than one
        transcript (a session and its subagent log). _compute already
        deduplicates tokens by (message id, requestId); value has to ride the
        very same dedup or the multiple silently inflates with every
        duplicated record.
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            line = self._line("shared")
            (projects / "a.jsonl").write_text(line)
            (projects / "b.jsonl").write_text(line)
            snapshot = tokenserver._compute(projects)
        self.assertEqual(snapshot["monthTokens"], 1_000_000)
        self.assertEqual(snapshot["value"]["value_usd"], 0.5)

    def test_distinct_records_accumulate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            (projects / "a.jsonl").write_text(
                self._line("one") + self._line("two"))
            value = tokenserver._compute(projects)["value"]
        self.assertEqual(value["value_usd"], 1.0)

    def test_unpriceable_model_suppresses_the_multiple(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            (projects / "a.jsonl").write_text(
                self._line("future", model="claude-something-6"))
            value = tokenserver._compute(projects)["value"]
        self.assertEqual(value["state"], "partial")
        self.assertIsNone(value["multiple"])
        self.assertEqual(value["unpriced_token_share"], 1.0)

    def test_value_is_absent_from_the_firmware_contract_when_unknown(self):
        """An unset plan cost must dash the multiple, not invent one."""
        tokenserver._claude_plan = None
        tokenserver._plan_costs = {}
        with tempfile.TemporaryDirectory() as temp_dir:
            projects = Path(temp_dir)
            (projects / "a.jsonl").write_text(self._line("one"))
            value = tokenserver._compute(projects)["value"]
        self.assertEqual(value["state"], "no_plan_cost")
        self.assertIsNone(value["multiple"])
        self.assertEqual(value["value_usd"], 0.5)

class LogRotationTests(unittest.TestCase):
    """_maybe_rotate_own_log: trunkera bara launchd-loggen, bara över taket,
    och bevara svansen — utan att någonsin röra en fil stderr inte äger."""

    def _big_file(self, tmp):
        path = Path(tmp) / "torget-tokenserver.log"
        filler = b"x" * (tokenserver._LOG_CAP_BYTES + 4096)
        path.write_bytes(filler[:-8] + b"SVANSEN\n")
        return path

    def test_rotates_when_stderr_is_the_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._big_file(tmp)
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            try:
                rotated = tokenserver._maybe_rotate_own_log(
                    path, stderr_fd=fd)
            finally:
                os.close(fd)

            self.assertTrue(rotated)
            self.assertEqual(path.stat().st_size, 0)
            old = path.with_name(path.name + ".old")
            tail = old.read_bytes()
            self.assertEqual(len(tail), tokenserver._LOG_TAIL_KEEP_BYTES)
            self.assertTrue(tail.endswith(b"SVANSEN\n"))

    def test_rotation_holds_the_logging_handler_locks(self):
        # En loggrad mellan svansläsningen och trunkeringen skulle raderas
        # ur BÅDA filerna — rotationen ska hålla handlerlåsen så att
        # logging-skrivningar inte kan interfoliera.
        class CountingHandler(logging.Handler):
            def __init__(self):
                super().__init__()
                self.acquisitions = 0

            def acquire(self):
                self.acquisitions += 1
                super().acquire()

            def emit(self, record):
                pass

        counting = CountingHandler()
        root = logging.getLogger()
        root.addHandler(counting)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = self._big_file(tmp)
                fd = os.open(path, os.O_WRONLY | os.O_APPEND)
                try:
                    rotated = tokenserver._maybe_rotate_own_log(
                        path, stderr_fd=fd)
                finally:
                    os.close(fd)
            self.assertTrue(rotated)
            self.assertGreaterEqual(counting.acquisitions, 1)
        finally:
            root.removeHandler(counting)

    def test_terminal_run_never_touches_the_file(self):
        # stderr_fd=2 är testkörarens stderr, inte filen — fstat/stat-vakten
        # ska vägra, oavsett storlek.
        with tempfile.TemporaryDirectory() as tmp:
            path = self._big_file(tmp)
            size_before = path.stat().st_size
            self.assertFalse(
                tokenserver._maybe_rotate_own_log(path, stderr_fd=2))
            self.assertEqual(path.stat().st_size, size_before)

    def test_under_cap_and_missing_file_are_noops(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "liten.log"
            path.write_bytes(b"kort\n")
            fd = os.open(path, os.O_WRONLY | os.O_APPEND)
            try:
                self.assertFalse(
                    tokenserver._maybe_rotate_own_log(path, stderr_fd=fd))
            finally:
                os.close(fd)
            self.assertEqual(path.read_bytes(), b"kort\n")

            self.assertFalse(tokenserver._maybe_rotate_own_log(
                Path(tmp) / "finns-inte.log", stderr_fd=2))


class SourceFingerprintTests(unittest.TestCase):
    def test_fingerprint_is_stable_and_served_on_root(self):
        first = tokenserver._read_source_fingerprint()
        self.assertRegex(first, r"^[0-9a-f]{12}$")
        self.assertEqual(first, tokenserver._read_source_fingerprint())

        handler = tokenserver.Handler.__new__(tokenserver.Handler)
        handler.path = "/"
        handler._send = mock.Mock()
        handler.do_GET()
        payload = handler._send.call_args.args[1]
        self.assertEqual(payload["srcFingerprint"], tokenserver._SERVER_SRC)


class LogRotationWatchTests(unittest.TestCase):
    def test_watch_keeps_checking_until_stopped(self):
        # Startrotationen räcker inte för en långlivad process — vakten ska
        # titta om och om igen och dö snyggt på stoppsignalen.
        stop = threading.Event()
        calls = []
        with mock.patch.object(tokenserver, "_maybe_rotate_own_log",
                               side_effect=lambda: calls.append(1)):
            watcher = threading.Thread(
                target=tokenserver._run_log_rotation_watch,
                args=(stop,), kwargs={"interval_s": 0.01}, daemon=True)
            watcher.start()
            for _ in range(200):
                if len(calls) >= 2:
                    break
                time.sleep(0.01)
            stop.set()
            watcher.join(timeout=2)
        self.assertGreaterEqual(len(calls), 2)
        self.assertFalse(watcher.is_alive())


class ProbeTransitionLogTests(unittest.TestCase):
    """Övergångsloggen: en rad när probestatusen ändras, tystnad medan
    samma läge står — loggfilen ska vara läsbar över veckor."""

    def test_status_change_logs_once_then_stays_quiet(self):
        with mock.patch.object(tokenserver, "_probe_status",
                               "usage_http_401"), \
                mock.patch.object(tokenserver, "_probe_status_logged", None), \
                mock.patch.object(tokenserver, "_last_limits", None), \
                mock.patch.object(tokenserver, "_last_probed", 0.0), \
                mock.patch.object(tokenserver, "_limits_refreshing", False), \
                mock.patch.object(tokenserver, "_probe_failure_streak", 0), \
                mock.patch.object(tokenserver, "_probe_limits",
                                  return_value=None):
            with self.assertLogs("tokenserver", level="INFO") as captured:
                tokenserver._refresh_limits()
            self.assertIn("start -> usage_http_401",
                          "\n".join(captured.output))

            with self.assertNoLogs("tokenserver"):
                tokenserver._refresh_limits()

    def test_probe_crash_replaces_stale_ok_status_and_logs_once(self):
        # Kraschar proben innan den satt status fick "usage_http_200 + ok"
        # stå kvar och ljuga medan värdena försvann — kraschen ska bli en
        # egen status (syns på GET / och i röktestet) med traceback en
        # gång per episod.
        with mock.patch.object(tokenserver, "_probe_status",
                               "usage_http_200 + ok"), \
                mock.patch.object(tokenserver, "_probe_status_logged",
                                  "usage_http_200 + ok"), \
                mock.patch.object(tokenserver, "_last_limits", None), \
                mock.patch.object(tokenserver, "_last_probed", 0.0), \
                mock.patch.object(tokenserver, "_limits_refreshing", False), \
                mock.patch.object(tokenserver, "_probe_failure_streak", 0), \
                mock.patch.object(tokenserver, "_probe_limits",
                                  side_effect=RuntimeError("boom")):
            with self.assertLogs("tokenserver", level="INFO") as captured:
                tokenserver._refresh_limits()
            out = "\n".join(captured.output)
            self.assertEqual(tokenserver._probe_status,
                             "probe_crashed: RuntimeError")
            self.assertIn("kraschade", out)
            self.assertIn("-> probe_crashed: RuntimeError", out)

            # Samma krasch igen: samma episod, ingen ny rad.
            with self.assertNoLogs("tokenserver"):
                tokenserver._refresh_limits()

    def test_recovery_is_a_transition_too(self):
        with mock.patch.object(tokenserver, "_probe_status",
                               "usage_http_200 + ok"), \
                mock.patch.object(tokenserver, "_probe_status_logged",
                                  "usage_http_401"), \
                mock.patch.object(tokenserver, "_last_limits", None), \
                mock.patch.object(tokenserver, "_last_probed", 0.0), \
                mock.patch.object(tokenserver, "_limits_refreshing", False), \
                mock.patch.object(tokenserver, "_probe_failure_streak", 3), \
                mock.patch.object(tokenserver, "_probe_limits",
                                  return_value={"weekPct": 60.0}):
            with self.assertLogs("tokenserver", level="INFO") as captured:
                tokenserver._refresh_limits()
            self.assertIn("usage_http_401 -> usage_http_200 + ok",
                          "\n".join(captured.output))


if __name__ == "__main__":
    unittest.main()
