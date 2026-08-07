import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver.usage_history import Forecast, UsageHistory


HOUR = 60 * 60
DAY = 24 * HOUR


class UsageHistoryPersistenceTests(unittest.TestCase):
    def test_records_at_most_one_sample_per_fifteen_minutes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            history = UsageHistory(path)

            self.assertTrue(history.record(
                "claude", "week", 10.0, reset_at=7 * DAY, at=0))
            self.assertFalse(history.record(
                "claude", "week", 11.0, reset_at=7 * DAY, at=899))
            self.assertTrue(history.record(
                "claude", "week", 12.0, reset_at=7 * DAY, at=900))

            self.assertEqual([sample["pct"] for sample in history.records],
                             [10.0, 12.0])

    def test_prunes_samples_older_than_eight_days(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            history = UsageHistory(path)
            history.record("claude", "week", 5.0,
                           reset_at=7 * DAY, at=0)

            history.record("codex", "week", 20.0,
                           reset_at=10 * DAY, at=8 * DAY + 1)

            self.assertEqual(len(history.records), 1)
            self.assertEqual(history.records[0]["provider"], "codex")

    def test_persists_with_atomic_replace_and_fixed_privacy_schema(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "nested" / "usage-history.json"
            history = UsageHistory(path)

            with mock.patch(
                    "tools.tokenserver.usage_history.os.replace",
                    wraps=os.replace) as replace:
                history.record("claude", "model_week", 73.0,
                               reset_at=7 * DAY, at=HOUR)

            replace.assert_called_once()
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"v", "samples"})
            self.assertEqual(payload["v"], 1)
            self.assertEqual(set(payload["samples"][0]), {
                "at", "provider", "window", "pct", "reset",
            })
            self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_corrupt_file_starts_empty_without_touching_sibling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "usage-history.json"
            sibling = root / "keep-me.txt"
            path.write_text("{broken", encoding="utf-8")
            sibling.write_text("unchanged", encoding="utf-8")

            history = UsageHistory(path)

            self.assertEqual(history.records, ())
            self.assertEqual(sibling.read_text(encoding="utf-8"),
                             "unchanged")

    def test_rejects_unbounded_provider_or_window_names(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = UsageHistory(Path(temp_dir) / "history.json")

            self.assertFalse(history.record(
                "private prompt", "week", 10, reset_at=DAY, at=0))
            self.assertFalse(history.record(
                "claude", "private filename", 10, reset_at=DAY, at=0))
            self.assertEqual(history.records, ())


class UsageHistoryForecastTests(unittest.TestCase):
    def _history(self, samples, reset_at, now=None):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        history = UsageHistory(Path(temp_dir.name) / "history.json")
        for at, pct in samples:
            history.record("claude", "week", pct,
                           reset_at=reset_at, at=at)
        return history, samples[-1][0] if now is None else now

    def test_forecast_is_unavailable_without_samples(self):
        history, _ = self._history([], reset_at=4 * HOUR, now=0)

        self.assertEqual(
            history.forecast("claude", "week", reset_at=4 * HOUR, now=0),
            Forecast(state="unavailable"))

    def test_forecast_collects_until_three_points_span_ninety_minutes(self):
        history, now = self._history(
            [(0, 20), (HOUR, 25), (HOUR + 29 * 60, 28)],
            reset_at=4 * HOUR)

        self.assertEqual(
            history.forecast("claude", "week",
                             reset_at=4 * HOUR, now=now).state,
            "collecting")

    def test_forecast_collects_until_usage_moves_one_percentage_point(self):
        history, now = self._history(
            [(0, 20.0), (HOUR, 20.3), (2 * HOUR, 20.8)],
            reset_at=4 * HOUR)

        self.assertEqual(
            history.forecast("claude", "week",
                             reset_at=4 * HOUR, now=now).state,
            "collecting")

    def test_low_pace_projects_reset_percentage_and_required_multiplier(self):
        history, now = self._history(
            [(0, 20), (HOUR, 25), (2 * HOUR, 30)],
            reset_at=4 * HOUR)

        forecast = history.forecast(
            "claude", "week", reset_at=4 * HOUR, now=now)

        self.assertEqual(forecast.state, "at_reset")
        self.assertEqual(forecast.pct_at_reset, 40)
        self.assertAlmostEqual(forecast.pace_factor, 7.0)
        self.assertIsNone(forecast.exhausts_at)

    def test_fast_pace_projects_exhaustion_before_reset(self):
        history, now = self._history(
            [(0, 70), (HOUR, 80), (2 * HOUR, 90)],
            reset_at=4 * HOUR)

        forecast = history.forecast(
            "claude", "week", reset_at=4 * HOUR, now=now)

        self.assertEqual(forecast.state, "exhausts")
        self.assertEqual(forecast.exhausts_at, 3 * HOUR)
        self.assertEqual(forecast.offset_minutes, -60)
        self.assertIsNone(forecast.pct_at_reset)

    def test_falling_usage_has_no_misleading_forecast(self):
        history, now = self._history(
            [(0, 50), (HOUR, 48), (2 * HOUR, 46)],
            reset_at=4 * HOUR)

        self.assertEqual(
            history.forecast("claude", "week",
                             reset_at=4 * HOUR, now=now).state,
            "unavailable")

    def test_forecast_uses_only_current_reset_cycle(self):
        history, now = self._history(
            [(0, 80), (HOUR, 90)], reset_at=4 * HOUR)
        history.record("claude", "week", 10,
                       reset_at=11 * HOUR, at=2 * HOUR)
        history.record("claude", "week", 15,
                       reset_at=11 * HOUR, at=3 * HOUR)
        history.record("claude", "week", 20,
                       reset_at=11 * HOUR, at=4 * HOUR)

        forecast = history.forecast(
            "claude", "week", reset_at=11 * HOUR, now=4 * HOUR)

        self.assertEqual(forecast.state, "at_reset")
        self.assertEqual(forecast.pct_at_reset, 55)

    def test_forecast_ignores_points_older_than_twenty_four_hours(self):
        reset_at = 32 * HOUR
        history, _ = self._history(
            [(0, 70), (25 * HOUR, 10), (26 * HOUR, 15), (27 * HOUR, 20)],
            reset_at=reset_at, now=27 * HOUR)

        forecast = history.forecast(
            "claude", "week", reset_at=reset_at, now=27 * HOUR)

        self.assertEqual(forecast.state, "at_reset")
        self.assertEqual(forecast.pct_at_reset, 45)


class UsageHistoryDeltaTests(unittest.TestCase):
    def test_delta_uses_last_sample_before_period_start(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = UsageHistory(Path(temp_dir) / "history.json")
            reset_at = 10 * HOUR
            history.record("claude", "week", 40,
                           reset_at=reset_at, at=HOUR)
            history.record("claude", "week", 43,
                           reset_at=reset_at, at=2 * HOUR)
            history.record("claude", "week", 47,
                           reset_at=reset_at, at=3 * HOUR)

            delta = history.delta_since(
                "claude", "week", since=HOUR + 30 * 60,
                reset_at=reset_at, now=3 * HOUR)

            self.assertEqual(delta, 7.0)

    def test_delta_needs_two_samples_in_the_current_reset_cycle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = UsageHistory(Path(temp_dir) / "history.json")
            history.record("claude", "week", 80,
                           reset_at=4 * HOUR, at=0)
            history.record("claude", "week", 10,
                           reset_at=11 * HOUR, at=2 * HOUR)

            delta = history.delta_since(
                "claude", "week", since=HOUR,
                reset_at=11 * HOUR, now=2 * HOUR)

            self.assertIsNone(delta)

    def test_negative_correction_is_not_reported_as_usage_burn(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            history = UsageHistory(Path(temp_dir) / "history.json")
            reset_at = 10 * HOUR
            history.record("codex", "week", 30,
                           reset_at=reset_at, at=HOUR)
            history.record("codex", "week", 29,
                           reset_at=reset_at, at=2 * HOUR)

            self.assertIsNone(history.delta_since(
                "codex", "week", since=HOUR,
                reset_at=reset_at, now=2 * HOUR))


if __name__ == "__main__":
    unittest.main()
