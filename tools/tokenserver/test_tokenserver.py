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

    def test_snapshot_never_infers_label_from_active_agent_model(self):
        base = {
            "v": 1,
            "dayTokens": 0,
            "dayTokensPerHour": 0,
            "daySessions": 0,
            "monthTokens": 0,
            "at": "2026-08-07T10:00:00+02:00",
        }
        with mock.patch.object(tokenserver, "_compute", return_value=base), \
                mock.patch.object(tokenserver, "get_limits", return_value={
                    "sessionPct": 12.0,
                    "modelPct": 73.0,
                }), \
                mock.patch.object(tokenserver, "_read_codex_limits",
                                  return_value={}):
            tokenserver._last_result = None
            tokenserver._last_computed = 0.0
            snapshot = tokenserver.get_snapshot(
                Path("/unused"), history=StubHistory(),
                now_ts=1_800_000_000)

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

    def test_failed_background_refresh_keeps_last_good_limits(self):
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
            self.assertEqual(tokenserver._last_limits, {"weekPct": 47.0})
        finally:
            (tokenserver._last_limits,
             tokenserver._last_probed,
             tokenserver._limits_refreshing) = previous


class CodexLimitLogTests(unittest.TestCase):
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

    def test_latest_rate_limit_is_read_from_tail_without_read_text(self):
        rate_limits = {
            "primary": {
                "used_percent": 35.0,
                "window_minutes": 10080,
                "resets_at": 1_900_000_000,
            },
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-large.jsonl"
            path.write_bytes(
                b'{"type":"noise"}\n' * 100_000 +
                json.dumps({"rate_limits": rate_limits}).encode() + b"\n")

            with mock.patch.object(
                    Path, "read_text",
                    side_effect=AssertionError("full file read is forbidden")):
                found = tokenserver._read_latest_rate_limits(path)

        self.assertEqual(found, rate_limits)

    def test_reverse_scan_stops_at_configured_byte_limit(self):
        rate_limits = {"primary": {"used_percent": 35.0}}
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "rollout-no-recent-limit.jsonl"
            path.write_bytes(
                json.dumps({"rate_limits": rate_limits}).encode() + b"\n" +
                b'{"type":"noise"}\n' * 10_000)

            found = tokenserver._read_latest_rate_limits(
                path, block_size=4096, max_bytes=64 * 1024)

        self.assertIsNone(found)

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

    def _snapshot(self, history, now_ts, claude=None, codex=None):
        with mock.patch.object(tokenserver, "_compute",
                               return_value=self._base()), \
                mock.patch.object(tokenserver, "get_limits",
                                  return_value=claude or {}), \
                mock.patch.object(tokenserver, "_read_codex_limits",
                                  return_value=codex or {}):
            tokenserver._last_result = None
            tokenserver._last_computed = 0.0
            return tokenserver.get_snapshot(
                Path("/unused"), history=history, now_ts=now_ts)

    def test_stale_usage_totals_refresh_in_background(self):
        started = threading.Event()
        release = threading.Event()
        old_base = self._base()
        new_base = dict(old_base, dayTokens=999)

        def slow_compute(_projects_dir):
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
                                      return_value={}):
                before = time.perf_counter()
                snapshot = tokenserver.get_snapshot(
                    Path("/unused"), history=StubHistory(),
                    now_ts=1_800_000_000)
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
                    "weekPct": 47.0,
                    "weekResetMin": 300,
                    "modelPct": 73.0,
                    "modelResetMin": 300,
                    "modelLabel": "FABLE · WEEK",
                },
                codex={
                    "codexWeekPct": 35.0,
                    "codexWeekResetMin": 300,
                })

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
            claude={"weekPct": 47.0, "weekResetMin": 1080},
            codex={"codexWeekPct": 35.0, "codexWeekResetMin": 1080})

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
            with mock.patch(
                    "tools.tokenserver.usage_history.os.replace",
                    wraps=os.replace) as replace:
                self._snapshot(
                    history, now_ts,
                    claude={
                        "sessionPct": 21.0, "sessionResetMin": 180,
                        "weekPct": 47.0, "weekResetMin": 300,
                        "modelPct": 73.0, "modelResetMin": 300,
                    },
                    codex={
                        "codexWeekPct": 35.0,
                        "codexWeekResetMin": 300,
                    })

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


if __name__ == "__main__":
    unittest.main()
