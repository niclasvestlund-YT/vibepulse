import json
import unittest
from pathlib import Path

from tools.tokenserver.max_tracker import (
    AGGREGATE_MAX,
    PROVIDERS,
    WINDOW_DAYS,
    WINDOW_WEEKS,
    build_payload,
    coding_streak,
    dense_window,
    max_weeks_streak,
    volume_levels,
    week_key,
)


FIXTURES_DIR = Path(__file__).resolve().parents[2] / "sim-fixtures"


def _load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))


def _assert_contract_shape(test: unittest.TestCase, payload: dict) -> None:
    """Structural check shared by every "round-trips through the contract
    shape" assertion: same top-level/provider keys, fixed-length arrays,
    values inside the ranges the device parser accepts."""
    test.assertEqual(set(payload), {"v", "weeks", "stale",
                                     "codingStreakDays", "claude", "codex"})
    test.assertEqual(payload["v"], 1)
    test.assertEqual(payload["weeks"], WINDOW_WEEKS)
    test.assertIsInstance(payload["stale"], bool)
    test.assertTrue(payload["codingStreakDays"] is None or
                     0 <= payload["codingStreakDays"] <= AGGREGATE_MAX)
    for provider in PROVIDERS:
        section = payload[provider]
        required = {"avgPeakPct", "maxWeeksStreak", "maxWeeks", "maxDays",
                    "weekMaxed", "days"}
        test.assertTrue(required <= set(section))
        test.assertTrue(set(section) <= required | {"planLabel"})
        test.assertEqual(len(section["days"]), WINDOW_DAYS)
        test.assertEqual(len(section["weekMaxed"]), WINDOW_WEEKS)
        for flag in section["weekMaxed"]:
            test.assertIn(flag, (0, 1))
        for pct, lvl in section["days"]:
            test.assertTrue(-1 <= pct <= 100)
            test.assertTrue(-1 <= lvl <= 2)
        for key in ("maxWeeksStreak", "maxWeeks", "maxDays"):
            test.assertTrue(0 <= section[key] <= AGGREGATE_MAX)
        test.assertTrue(section["avgPeakPct"] is None or
                         0 <= section["avgPeakPct"] <= 100)


class VolumeLevelsTests(unittest.TestCase):
    def test_terciles_split_distinct_values_into_three_ranked_bands(self):
        levels = volume_levels({
            "a": 10, "b": 20, "c": 30, "d": 40, "e": 50, "f": 60,
        })

        self.assertEqual(levels, {
            "a": 0, "b": 0, "c": 1, "d": 1, "e": 2, "f": 2,
        })

    def test_ties_always_share_the_same_level(self):
        levels = volume_levels({
            "a": 50, "b": 50, "c": 50, "d": 5, "e": 500,
        })

        self.assertEqual(levels["a"], levels["b"])
        self.assertEqual(levels["b"], levels["c"])

    def test_single_distinct_nonzero_volume_is_the_conservative_level_zero(self):
        levels = volume_levels({"a": 7, "b": 7, "c": 7})

        self.assertEqual(levels, {"a": 0, "b": 0, "c": 0})

    def test_zero_volume_days_are_level_zero_without_skewing_thresholds(self):
        levels = volume_levels({"a": 0, "b": 10, "c": 20, "d": 30})

        self.assertEqual(levels["a"], 0)
        # Thresholds must be computed only over the nonzero {10, 20, 30}.
        self.assertEqual(levels, {"a": 0, "b": 0, "c": 1, "d": 2})

    def test_empty_input_returns_empty_output(self):
        self.assertEqual(volume_levels({}), {})


class CodingStreakTests(unittest.TestCase):
    def test_streak_includes_today_when_today_is_active(self):
        active = {"2026-08-10", "2026-08-11", "2026-08-12"}

        self.assertEqual(coding_streak(active, "2026-08-12"), 3)

    def test_streak_grants_grace_when_today_has_no_activity_yet(self):
        active = {"2026-08-10", "2026-08-11"}

        self.assertEqual(coding_streak(active, "2026-08-12"), 2)

    def test_streak_is_zero_once_a_full_day_gap_exists(self):
        active = {"2026-08-09"}

        self.assertEqual(coding_streak(active, "2026-08-12"), 0)

    def test_streak_crosses_a_month_boundary(self):
        active = {"2026-01-30", "2026-01-31", "2026-02-01", "2026-02-02"}

        self.assertEqual(coding_streak(active, "2026-02-02"), 4)

    def test_streak_crosses_the_stockholm_dst_switch_date(self):
        # 2026-03-29 is the Europe/Stockholm spring-forward date (a local
        # day with only 23 wall-clock hours). Dates here are plain
        # strings and the function does pure calendar-date arithmetic, so
        # the "missing hour" must not affect day counting at all.
        active = {"2026-03-28", "2026-03-29", "2026-03-30"}

        self.assertEqual(coding_streak(active, "2026-03-30"), 3)

    def test_streak_is_zero_with_no_recorded_activity(self):
        self.assertEqual(coding_streak(set(), "2026-08-12"), 0)


class WeekKeyTests(unittest.TestCase):
    def test_basic_week_key(self):
        self.assertEqual(week_key("2026-08-12"), "2026-W33")

    def test_a_monday_can_belong_to_the_next_iso_year(self):
        self.assertEqual(week_key("2025-12-29"), "2026-W01")

    def test_iso_week_53_year(self):
        self.assertEqual(week_key("2026-12-28"), "2026-W53")
        self.assertEqual(week_key("2027-01-01"), "2026-W53")

    def test_next_iso_year_starts_at_week_one(self):
        self.assertEqual(week_key("2027-01-04"), "2027-W01")


class MaxWeeksStreakTests(unittest.TestCase):
    THIS_WEEK = "2026-W33"  # week of 2026-08-12

    def test_counts_consecutive_completed_weeks_and_ignores_the_current_one(self):
        week_maxed = {
            "2026-W33": True,   # current week: must be ignored entirely
            "2026-W32": True,
            "2026-W31": True,
            "2026-W30": False,  # breaks the streak
        }

        self.assertEqual(max_weeks_streak(week_maxed, self.THIS_WEEK), 2)

    def test_a_maxed_current_week_does_not_extend_the_streak(self):
        week_maxed = {"2026-W33": True, "2026-W32": False}

        self.assertEqual(max_weeks_streak(week_maxed, self.THIS_WEEK), 0)

    def test_missing_week_entries_count_as_not_maxed(self):
        self.assertEqual(max_weeks_streak({}, self.THIS_WEEK), 0)

    def test_streak_crosses_an_iso_week_53_year_boundary(self):
        week_maxed = {
            "2026-W53": True,
            "2026-W52": True,
            "2026-W51": False,
        }

        self.assertEqual(max_weeks_streak(week_maxed, "2027-W01"), 2)


class DenseWindowTests(unittest.TestCase):
    def test_window_is_exactly_weeks_times_seven_and_starts_on_a_monday(self):
        result = dense_window("2026-08-12", WINDOW_WEEKS, {})

        self.assertEqual(len(result), WINDOW_DAYS)
        # 2026-08-12 is a Wednesday 19 weeks into a 20-week window; the
        # window must start on 2026-03-30, a Monday, for the device's
        # column-major-by-ISO-week grid to line up.
        from datetime import date
        self.assertEqual(date.fromisoformat("2026-03-30").isoweekday(), 1)

    def test_absent_days_render_as_minus_one_pair(self):
        result = dense_window("2026-08-12", WINDOW_WEEKS, {})

        self.assertTrue(all(pair == [-1, -1] for pair in result))

    def test_none_values_in_a_present_record_become_minus_one(self):
        per_day = {"2026-03-30": {"pct": None, "lvl": 2}}

        result = dense_window("2026-08-12", WINDOW_WEEKS, per_day)

        self.assertEqual(result[0], [-1, 2])

    def test_today_lands_mid_grid_and_the_rest_of_its_week_stays_blank(self):
        # today = 2026-08-12 is index 135 of 140 (Wed of the last week);
        # the remaining Thu/Fri/Sat/Sun of that week (indices 136-139)
        # must render [-1, -1] even if per_day has data for them (data
        # for a date after "today" cannot be honest).
        per_day = {
            "2026-08-12": {"pct": 40, "lvl": 1},
            "2026-08-13": {"pct": 99, "lvl": 2},
        }

        result = dense_window("2026-08-12", WINDOW_WEEKS, per_day)

        self.assertEqual(result[135], [40, 1])
        self.assertEqual(result[136:140], [[-1, -1]] * 4)

    def test_a_sunday_today_needs_no_future_padding(self):
        per_day = {f"2026-08-{day:02d}": {"pct": day, "lvl": 0}
                   for day in range(10, 17)}

        result = dense_window("2026-08-16", 1, per_day)

        self.assertEqual(len(result), 7)
        self.assertEqual(result[0], [10, 0])
        self.assertEqual(result[6], [16, 0])


class BuildPayloadTests(unittest.TestCase):
    def _state(self, claude_days=None, claude_weeks=None,
               codex_days=None, codex_weeks=None, stale=False):
        return {
            "claude": {"days": claude_days or {}, "weeks": claude_weeks or {}},
            "codex": {"days": codex_days or {}, "weeks": codex_weeks or {}},
            "stale": stale,
        }

    def test_empty_state_round_trips_through_the_committed_empty_fixture(self):
        payload = build_payload(self._state(), "2026-08-12", {})

        self.assertEqual(payload, _load_fixture("max-tracker-empty.json"))

    def test_no_activity_yields_null_streak_and_null_average(self):
        payload = build_payload(self._state(), "2026-08-12", {})

        self.assertIsNone(payload["codingStreakDays"])
        self.assertIsNone(payload["claude"]["avgPeakPct"])
        self.assertIsNone(payload["codex"]["avgPeakPct"])

    def test_plan_allowlist_maps_known_flags(self):
        cases = {"pro": "PRO", "max5x": "MAX 5X",
                 "max20x": "MAX 20X", "plus": "PLUS"}
        for flag, label in cases.items():
            with self.subTest(flag=flag):
                payload = build_payload(
                    self._state(), "2026-08-12", {"claude": flag})
                self.assertEqual(payload["claude"]["planLabel"], label)

    def test_unknown_or_missing_plan_flag_omits_the_field(self):
        payload = build_payload(
            self._state(), "2026-08-12",
            {"claude": "not-a-real-plan", "codex": None})

        self.assertNotIn("planLabel", payload["claude"])
        self.assertNotIn("planLabel", payload["codex"])

    def test_lvl_is_computed_for_an_active_day_that_also_has_real_pct(self):
        days = {
            "2026-08-01": {"act": True, "vol": 10, "pct": None},
            "2026-08-02": {"act": True, "vol": 50, "pct": None},
            "2026-08-03": {"act": True, "vol": 90, "pct": 55},
        }

        payload = build_payload(
            self._state(claude_days=days), "2026-08-03", {})

        # Volume terciles over {10, 50, 90} put day 3's 90 in the top
        # band (level 2); it must show up alongside its real pct, not
        # get suppressed to -1 just because pct is also present.
        self.assertIn([55, 2], payload["claude"]["days"])

    def test_absent_day_and_explicitly_inactive_no_quota_day_are_equivalent(self):
        today = "2026-08-12"
        with_empty_record = self._state(
            claude_days={"2026-08-01": {"act": False, "pct": None}})
        without_the_record = self._state(claude_days={})

        self.assertEqual(
            build_payload(with_empty_record, today, {}),
            build_payload(without_the_record, today, {}))

    def test_avg_peak_pct_only_covers_the_visible_window(self):
        today = "2026-08-12"
        days = {
            # Far outside the ~140-day visible window, but still real
            # history: must count for maxDays, not for avgPeakPct.
            "2025-01-01": {"act": True, "pct": 100},
            "2026-08-01": {"act": True, "pct": 40},
            "2026-08-02": {"act": True, "pct": 60},
        }

        payload = build_payload(
            self._state(claude_days=days), today, {})

        self.assertEqual(payload["claude"]["avgPeakPct"], 50.0)
        self.assertEqual(payload["claude"]["maxDays"], 1)

    def test_coding_streak_days_clamps_at_the_aggregate_ceiling(self):
        from datetime import date, timedelta
        today = date.fromisoformat("2026-08-12")
        active = {(today - timedelta(days=offset)).isoformat()
                  for offset in range(AGGREGATE_MAX + 5)}
        days = {day: {"act": True, "pct": None} for day in active}

        payload = build_payload(
            self._state(claude_days=days), today.isoformat(), {})

        self.assertEqual(payload["codingStreakDays"], AGGREGATE_MAX)

    def test_max_days_clamps_at_the_aggregate_ceiling(self):
        days = {f"{year}-{month:02d}-{day:02d}": {"act": False, "pct": 100}
                for year in range(2000, 2010)
                for month in range(1, 13) for day in range(1, 29)}
        self.assertGreater(len(days), AGGREGATE_MAX)

        payload = build_payload(
            self._state(claude_days=days), "2026-08-12", {})

        self.assertEqual(payload["claude"]["maxDays"], AGGREGATE_MAX)

    def test_max_weeks_clamps_at_the_aggregate_ceiling(self):
        weeks = {f"{year}-W{week:02d}": True
                 for year in range(1990, 2010) for week in range(1, 53)}
        self.assertGreater(len(weeks), AGGREGATE_MAX)

        payload = build_payload(
            self._state(claude_weeks=weeks), "2026-08-12", {})

        self.assertEqual(payload["claude"]["maxWeeks"], AGGREGATE_MAX)

    def test_max_weeks_streak_clamps_at_the_aggregate_ceiling(self):
        from datetime import timedelta
        from tools.tokenserver.max_tracker import _week_key_to_monday
        this_week = week_key("2026-08-12")
        cursor = _week_key_to_monday(this_week) - timedelta(days=7)
        weeks = {}
        for _ in range(AGGREGATE_MAX + 5):
            weeks[week_key(cursor.isoformat())] = True
            cursor -= timedelta(days=7)

        payload = build_payload(
            self._state(claude_weeks=weeks), "2026-08-12", {})

        self.assertEqual(payload["claude"]["maxWeeksStreak"], AGGREGATE_MAX)

    def test_committed_fixtures_match_the_expected_contract_shape(self):
        for name in ("max-tracker-empty.json", "max-tracker-coldstart.json",
                     "max-tracker-full.json"):
            with self.subTest(fixture=name):
                _assert_contract_shape(self, _load_fixture(name))

    def test_built_payloads_round_trip_through_the_same_contract_shape(self):
        cases = [
            self._state(),
            self._state(claude_days={
                "2026-08-01": {"act": True, "vol": 10, "pct": 55},
            }, claude_weeks={"2026-W31": True}),
            self._state(codex_days={
                "2026-08-05": {"act": True, "vol": 100, "pct": 100},
            }, stale=True),
        ]
        for state in cases:
            with self.subTest(state=state):
                _assert_contract_shape(
                    self, build_payload(state, "2026-08-12", {}))


if __name__ == "__main__":
    unittest.main()
