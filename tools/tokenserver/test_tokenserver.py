import tempfile
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
    def _headers(self, model_bucket):
        return {
            "anthropic-ratelimit-unified-5h-utilization": "0.12",
            "anthropic-ratelimit-unified-5h-reset": "3600",
            "anthropic-ratelimit-unified-7d-utilization": "0.47",
            "anthropic-ratelimit-unified-7d-reset": "7200",
            f"anthropic-ratelimit-unified-{model_bucket}-utilization": "0.73",
            f"anthropic-ratelimit-unified-{model_bucket}-reset": "10800",
        }

    def test_named_model_week_buckets_keep_their_real_label(self):
        cases = {
            "7d_fable": "FABLE · VECKA",
            "7d_opus": "OPUS · VECKA",
            "7d_sonnet": "SONNET · VECKA",
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
                    "modelLabel": "FABLE · VECKA",
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
