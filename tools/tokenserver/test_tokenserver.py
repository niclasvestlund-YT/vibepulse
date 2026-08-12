import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock
import os

from tools.tokenserver import tokenserver
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


class ClaudeLimitHeaderTests(unittest.TestCase):
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
            token, expires_at = tokenserver._read_oauth_token()

        self.assertEqual(token, "fresh-process-token")
        self.assertIsNone(expires_at)

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
            token, expires_at = tokenserver._read_oauth_token()

        self.assertEqual(token, "keychain-token")
        self.assertEqual(expires_at, 1_900_000_000_000)

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
            token, expires_at = tokenserver._read_oauth_token()

        self.assertEqual(token, "standalone-token")
        self.assertEqual(expires_at, 1_900_000_000_000)

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

    def test_tokens_error_is_sanitized(self):
        handler = self._handler("/api/tokens")
        with mock.patch.object(
                tokenserver, "get_snapshot",
                side_effect=RuntimeError("/private/source/path secret")):
            handler.do_GET()

        handler._send.assert_called_once_with(
            500, {"error": "internal server error"})

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


class MaxTrackerDirtyWriterTests(unittest.TestCase):
    def setUp(self):
        self.previous = (
            tokenserver._max_tracker_dirty,
            tokenserver._max_tracker_writer_running,
        )
        tokenserver._max_tracker_dirty = False
        tokenserver._max_tracker_writer_running = False

    def tearDown(self):
        (tokenserver._max_tracker_dirty,
         tokenserver._max_tracker_writer_running) = self.previous

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
                side_effect=RuntimeError("/private/source/path secret")):
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


if __name__ == "__main__":
    unittest.main()
