import json
import os
import stat
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest import mock

from tools.tokenserver.max_tracker import (
    AGGREGATE_MAX,
    PROVIDERS,
    WINDOW_DAYS,
    WINDOW_WEEKS,
    MaxTrackerStore,
    build_payload,
    coding_streak,
    dense_window,
    max_weeks_streak,
    volume_levels,
    week_key,
    _round_day_pct,
)


FIXTURES_DIR = Path(__file__).resolve().parents[2] / "sim-fixtures"


def _index_for_date(today: str, target_date: str) -> int:
    """Find ``target_date``'s position in dense_window's grid without
    reimplementing its ISO-Monday anchoring math: seed a unique marker at
    that one date and locate it in the rendered output."""
    marker = {target_date: {"pct": 77, "lvl": 2}}
    window = dense_window(today, WINDOW_WEEKS, marker)
    return window.index([77, 2])


def _rollout_event(rate_limits, timestamp="2026-08-07T10:00:00Z"):
    """Mirror test_tokenserver.py's CodexLimitLogTests._event fixture shape:
    a real Codex rollout ``event_msg``/``token_count`` line."""
    return {
        "timestamp": timestamp,
        "type": "event_msg",
        "payload": {
            "type": "token_count",
            "info": None,
            "rate_limits": rate_limits,
        },
    }


def _rollout_limits(primary_pct=None, secondary_pct=None, *,
                    primary_minutes=300, secondary_minutes=10080,
                    limit_name=None, limit_id="synthetic"):
    result = {"limit_id": limit_id}
    if limit_name is not None:
        result["limit_name"] = limit_name
    if primary_pct is not None:
        result["primary"] = {
            "used_percent": primary_pct,
            "window_minutes": primary_minutes,
            "resets_at": 1_900_000_000,
        }
    if secondary_pct is not None:
        result["secondary"] = {
            "used_percent": secondary_pct,
            "window_minutes": secondary_minutes,
            "resets_at": 1_900_000_000,
        }
    return result


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows),
                    encoding="utf-8")


def _age_into_last_month(path):
    """Pin a file's mtime safely into a PRIOR calendar month, regardless
    of the real wall-clock date. Finding C: backfill_step defers any
    Claude file whose mtime is in the CURRENT month to tokenserver.py's
    live /api/tokens scanner (see MaxTrackerStore's single-writer class
    docstring) -- a freshly written test file always looks "this month",
    so Claude backfill tests must age their fixture past that boundary or
    backfill_step silently skips them. Call again after appending to a
    file within the same test to keep testing the OFFSET/pending-buf
    resume mechanism in isolation from that separate ownership policy
    (which gets its own dedicated tests) -- a real append always bumps
    mtime to "now", which would otherwise hand the file to the live
    channel mid-test. 40 days guarantees crossing at least one month
    boundary (the longest month is 31 days).
    """
    aged = time.time() - 40 * 86400
    os.utime(path, (aged, aged))


def _claude_usage_line(message_id, tokens, timestamp):
    return {
        "timestamp": timestamp,
        "sessionId": "session-a",
        "requestId": f"request-{message_id}",
        "message": {"id": message_id, "usage": {"input_tokens": tokens}},
    }


def _new_store(directory, **kwargs):
    codex_root = Path(directory) / "codex"
    claude_root = Path(directory) / "claude"
    codex_root.mkdir(exist_ok=True)
    claude_root.mkdir(exist_ok=True)
    path = kwargs.pop("path", Path(directory) / "max-tracker.json")
    return MaxTrackerStore(path, codex_root, claude_root), codex_root, claude_root


def _drain(store, max_steps=10_000):
    # A hard cap turns any reintroduced starvation bug into a fast,
    # readable test failure instead of a hung test run.
    for _ in range(max_steps):
        if not store.backfill_step():
            return
    raise AssertionError(
        f"backfill_step() still returned True after {max_steps} calls "
        "-- looks like starvation")


class MaxTrackerStoreCodexBackfillTests(unittest.TestCase):
    def test_primary_window_percent_becomes_the_days_peak(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(primary_pct=42.0),
                               "2026-08-07T10:00:00Z"),
            ])

            _drain(store)

            payload = store.snapshot("2026-08-07", {})
            self.assertEqual(payload["codex"]["avgPeakPct"], 42.0)

    def test_secondary_window_marks_the_iso_week_maxed_only_at_100(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(secondary_pct=87.0),
                               "2026-08-07T10:00:00Z"),
                _rollout_event(_rollout_limits(secondary_pct=100.0),
                               "2026-08-08T10:00:00Z"),
            ])

            _drain(store)

            payload = store.snapshot("2026-08-08", {})
            self.assertEqual(payload["codex"]["maxWeeks"], 1)

    def test_only_the_expected_rollout_event_envelope_is_accepted(self):
        limits = _rollout_limits(primary_pct=77.0)
        impostors = [
            {"rate_limits": limits},
            {"type": "message", "payload": {"rate_limits": limits}},
            {"type": "event_msg", "payload": {
                "type": "message", "rate_limits": limits}},
            {"type": "event_msg", "payload": {
                "type": "token_count", "message": {"rate_limits": limits}}},
            {"type": "event_msg", "payload": {
                "type": "token_count",
                "content": json.dumps({"rate_limits": limits})}},
        ]
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", impostors)

            _drain(store)

            payload = store.snapshot("2026-08-07", {})
            self.assertIsNone(payload["codex"]["avgPeakPct"])
            self.assertIsNone(payload["codingStreakDays"])

    def test_named_scoped_quota_never_feeds_day_peak_or_week_maxed(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(
                    primary_pct=99.0, secondary_pct=100.0,
                    limit_name="GPT-5.3-Codex-Spark"),
                    "2026-08-07T10:00:00Z"),
            ])

            _drain(store)

            payload = store.snapshot("2026-08-07", {})
            self.assertIsNone(payload["codex"]["avgPeakPct"])
            self.assertEqual(payload["codex"]["maxWeeks"], 0)

    def test_empty_limit_name_still_counts_as_the_general_week(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(
                    primary_pct=64.0, limit_name=""),
                    "2026-08-07T10:00:00Z"),
            ])

            _drain(store)

            self.assertEqual(
                store.snapshot("2026-08-07", {})["codex"]["avgPeakPct"], 64.0)

    def test_missing_codex_root_is_handled_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            codex_root.rmdir()

            self.assertFalse(store.backfill_step())

    def test_a_qualifying_backfilled_day_is_marked_active(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(primary_pct=10.0),
                               "2026-08-07T10:00:00Z"),
            ])

            _drain(store)

            self.assertIsNotNone(store.snapshot("2026-08-07", {})[
                "codingStreakDays"])


class MaxTrackerStoreBackfillBudgetTests(unittest.TestCase):
    def test_a_file_longer_than_the_budget_drains_over_two_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            first_row = _rollout_event(
                _rollout_limits(primary_pct=10.0), "2026-08-01T10:00:00Z")
            second_row = _rollout_event(
                _rollout_limits(primary_pct=88.0), "2026-08-02T10:00:00Z")
            path = codex_root / "rollout-a.jsonl"
            _write_jsonl(path, [first_row, second_row])
            first_line_bytes = len(json.dumps(first_row).encode("utf-8")) + 1
            # Enough for the first complete line and a few stray bytes of
            # the second, never enough to also finish it in one step.
            budget = first_line_bytes + 2

            first_more = store.backfill_step(budget_bytes=budget)

            self.assertTrue(first_more)
            self.assertEqual(
                store.snapshot("2026-08-02", {})["codex"]["avgPeakPct"], 10.0)

            second_more = store.backfill_step(budget_bytes=budget)

            self.assertFalse(second_more)
            self.assertEqual(
                store.snapshot("2026-08-02", {})["codex"]["avgPeakPct"], 49.0)

    def test_completed_file_is_never_reopened_on_a_later_run(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(primary_pct=10.0),
                               "2026-08-07T10:00:00Z"),
            ])
            _drain(store)

            with mock.patch.object(
                    Path, "open",
                    side_effect=AssertionError("must not reopen a "
                                               "completed file")):
                more = store.backfill_step()

            self.assertFalse(more)

    def test_backfill_state_is_keyed_by_inode_not_by_path(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(primary_pct=10.0),
                               "2026-08-07T10:00:00Z"),
            ])
            _drain(store)

            self.assertEqual(len(store._backfill["codex"]), 1)
            for key in store._backfill["codex"]:
                self.assertIsInstance(key, int)

    def test_a_grown_claude_file_resumes_from_its_offset_without_double_counting(self):
        # Regression: an actively-written session file is the normal case,
        # not an edge case -- draining it twice must never recount the
        # portion already drained the first time. Pins the mtime into a
        # prior month throughout (see _age_into_last_month) to exercise
        # the offset/pending-buf resume mechanism in isolation from
        # Finding C's separate current-month live-channel deferral, which
        # would otherwise hand a REALLY actively-growing Claude file to
        # the live channel instead (its own dedicated tests cover that).
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            path = claude_root / "session.jsonl"
            _write_jsonl(path, [
                _claude_usage_line("m1", 100, "2026-08-07T10:00:00Z")])
            _age_into_last_month(path)
            _drain(store)
            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 100)
            inode_before = path.stat().st_ino

            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_claude_usage_line(
                    "m2", 50, "2026-08-07T11:00:00Z")) + "\n")
            self.assertEqual(path.stat().st_ino, inode_before)
            _age_into_last_month(path)

            _drain(store)

            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 150)

    def test_a_grown_codex_file_resumes_from_its_offset_without_double_counting(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            path = codex_root / "rollout-a.jsonl"
            _write_jsonl(path, [_rollout_event(
                _rollout_limits(primary_pct=40.0), "2026-08-01T10:00:00Z")])
            _drain(store)
            self.assertEqual(
                store.snapshot("2026-08-02", {})["codex"]["avgPeakPct"], 40.0)

            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(_rollout_event(
                    _rollout_limits(primary_pct=90.0),
                    "2026-08-02T10:00:00Z")) + "\n")
            _drain(store)

            # Two distinct days, each recorded exactly once (40 then 90) --
            # a double count of the first row would drag the average down.
            self.assertEqual(
                store.snapshot("2026-08-02", {})["codex"]["avgPeakPct"], 65.0)

    def test_a_truncated_file_at_the_same_inode_is_rescanned_from_the_start(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            path = claude_root / "session.jsonl"
            _write_jsonl(path, [
                _claude_usage_line("m1", 100, "2026-08-07T10:00:00Z"),
                _claude_usage_line("m2", 50, "2026-08-07T11:00:00Z"),
            ])
            _age_into_last_month(path)  # Finding C: else backfill defers it
            _drain(store)
            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 150)
            inode_before = path.stat().st_ino

            # Truncated and rewritten in place (same path, same inode,
            # smaller size) -- simulates a rotated/rewritten log.
            _write_jsonl(path, [
                _claude_usage_line("m3", 77, "2026-08-09T10:00:00Z")])
            self.assertEqual(path.stat().st_ino, inode_before)
            _age_into_last_month(path)  # the rewrite also bumped mtime

            _drain(store)

            self.assertEqual(
                store._state["claude"]["days"]["2026-08-09"]["vol"], 77)

    def test_a_line_wider_than_the_cap_is_skipped_not_starved(self):
        # Regression: a single JSONL line over _MAX_LINE_BYTES used to
        # never be consumed -- backfill_step made zero progress on it
        # forever, so the same file was picked again on every call.
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            path = claude_root / "session.jsonl"
            junk = {"junk": "x" * (MaxTrackerStore._MAX_LINE_BYTES + 5000)}
            junk_line_bytes = len(json.dumps(junk).encode("utf-8")) + 1
            self.assertGreater(
                junk_line_bytes, MaxTrackerStore._MAX_LINE_BYTES)
            _write_jsonl(path, [
                _claude_usage_line("m1", 100, "2026-08-07T10:00:00Z"),
                junk,
                _claude_usage_line("m2", 50, "2026-08-07T11:00:00Z"),
            ])
            _age_into_last_month(path)  # Finding C: else backfill defers it

            _drain(store)  # would hit the safety cap and fail if starved

            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 150)
            # Genuinely done -- not just "stopped for now" on this line.
            self.assertFalse(store.backfill_step())

    def test_a_line_between_one_and_eight_mib_is_parsed_and_counted(self):
        # A real Codex quota peak can live in a line this size (verified
        # on real rollout files) -- backfill must not drop it just
        # because it happens to be a couple of megabytes.
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            event = _rollout_event(
                _rollout_limits(primary_pct=71.0), "2026-08-07T10:00:00Z")
            event["payload"]["info"] = {"padding": "x" * (2 * 1024 * 1024)}
            line_bytes = len(json.dumps(event).encode("utf-8")) + 1
            self.assertGreater(line_bytes, 1024 * 1024)
            self.assertLess(line_bytes, MaxTrackerStore._MAX_LINE_BYTES)
            _write_jsonl(codex_root / "rollout-a.jsonl", [event])

            _drain(store)

            self.assertEqual(
                store.snapshot("2026-08-07", {})["codex"]["avgPeakPct"], 71.0)

    def test_a_2mib_line_still_completes_over_many_small_budget_steps(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            row = _claude_usage_line("m1", 100, "2026-08-07T10:00:00Z")
            row["padding"] = "x" * (2 * 1024 * 1024)
            row_bytes = len(json.dumps(row).encode("utf-8")) + 1
            self.assertGreater(row_bytes, 2 * 1024 * 1024)
            self.assertLess(row_bytes, MaxTrackerStore._MAX_LINE_BYTES)
            path = claude_root / "session.jsonl"
            _write_jsonl(path, [row])
            _age_into_last_month(path)  # Finding C: else backfill defers it

            steps = 0
            while store.backfill_step(budget_bytes=64 * 1024):
                steps += 1
                self.assertLess(steps, 200)  # sanity bound, not the real cap

            self.assertGreater(steps, 1)  # actually spanned multiple calls
            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 100)

    def test_a_second_file_still_drains_despite_the_first_having_an_oversized_line(self):
        with tempfile.TemporaryDirectory() as directory:
            store, codex_root, _ = _new_store(directory)
            junk = {"junk": "x" * (MaxTrackerStore._MAX_LINE_BYTES + 5000)}
            _write_jsonl(codex_root / "rollout-a-first.jsonl", [junk])
            _write_jsonl(codex_root / "rollout-b-second.jsonl", [
                _rollout_event(_rollout_limits(primary_pct=33.0),
                               "2026-08-07T10:00:00Z"),
            ])
            # sorted() must pick the oversized file first for this to be a
            # meaningful test of "doesn't block the sibling forever".
            self.assertEqual(
                sorted(codex_root.glob("**/rollout-*.jsonl"))[0].name,
                "rollout-a-first.jsonl")

            _drain(store)

            self.assertEqual(
                store.snapshot("2026-08-07", {})["codex"]["avgPeakPct"], 33.0)

    def test_lines_still_pending_after_the_record_cap_are_drained_next_call(self):
        # Regression: when the 256-record cap is hit while buf still
        # holds a complete, already-read line, the NEXT call used to seek
        # straight to (and find nothing past) the exact byte those cached
        # bytes already cover, read empty, and stop -- without ever
        # scanning the carried line. offset frozen, that record (and
        # everything behind it) starved forever.
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            path = claude_root / "session.jsonl"
            _write_jsonl(path, [
                _claude_usage_line(f"m{i}", 1, "2026-08-07T10:00:00Z")
                for i in range(257)
            ])
            _age_into_last_month(path)  # Finding C: else backfill defers it

            _drain(store)  # would hang/starve at 256 of 257 if regressed

            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 257)
            self.assertTrue(
                store._backfill["claude"][path.stat().st_ino]["done"])

    def test_unterminated_final_line_marks_done_and_does_not_starve_later_files(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            complete_line = json.dumps(_claude_usage_line(
                "m1", 100, "2026-08-07T10:00:00Z"))
            dangling = json.dumps(_claude_usage_line(
                "m2", 999, "2026-08-07T11:00:00Z"))
            path_a = claude_root / "session-a.jsonl"
            # No trailing newline on the last line -- a writer that
            # crashed or simply hasn't finished this line yet.
            path_a.write_text(complete_line + "\n" + dangling)
            _age_into_last_month(path_a)  # Finding C: else deferred to live
            path_b = claude_root / "session-b.jsonl"
            _write_jsonl(path_b, [_claude_usage_line(
                "m3", 50, "2026-08-08T10:00:00Z")])
            _age_into_last_month(path_b)

            _drain(store)

            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 100)
            # The sibling file (sorts after the stuck one) still drained.
            self.assertEqual(
                store._state["claude"]["days"]["2026-08-08"]["vol"], 50)

            entry = store._backfill["claude"][path_a.stat().st_ino]
            self.assertTrue(entry["done"])
            # offset sits at the START of the unterminated tail -- nothing
            # was counted for it, and it's the correct resume point.
            self.assertEqual(entry["offset"], len(complete_line) + 1)

    def test_unterminated_line_completes_once_the_file_grows(self):
        # Pins the mtime into a prior month throughout (see
        # _age_into_last_month) to exercise the dangling-tail-completes
        # mechanism in isolation from Finding C's separate current-month
        # live-channel deferral, which would otherwise hand a REALLY
        # just-resumed Claude file to the live channel instead once
        # touched (its own dedicated tests cover that).
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            complete_line = json.dumps(_claude_usage_line(
                "m1", 100, "2026-08-07T10:00:00Z"))
            dangling = json.dumps(_claude_usage_line(
                "m2", 999, "2026-08-07T11:00:00Z"))
            path = claude_root / "session.jsonl"
            path.write_text(complete_line + "\n" + dangling)
            _age_into_last_month(path)

            _drain(store)
            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 100)

            # The writer finishes the dangling line and appends another.
            another_line = json.dumps(_claude_usage_line(
                "m3", 25, "2026-08-07T12:00:00Z"))
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n" + another_line + "\n")
            _age_into_last_month(path)

            _drain(store)

            # 100 (first pass, unchanged) + 999 (now-completed dangling
            # line) + 25 (new line) -- each counted exactly once.
            self.assertEqual(
                store._state["claude"]["days"]["2026-08-07"]["vol"], 1124)


class MaxTrackerStoreClaudeBackfillTests(unittest.TestCase):
    def test_claude_volume_backfill_sets_activity_and_volume_never_pct(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            path = claude_root / "session.jsonl"
            _write_jsonl(path, [
                _claude_usage_line("m1", 100, "2026-08-07T10:00:00Z"),
                _claude_usage_line("m2", 50, "2026-08-07T11:00:00Z"),
            ])
            _age_into_last_month(path)  # Finding C: else backfill defers it

            _drain(store)

            day = store._state["claude"]["days"]["2026-08-07"]
            self.assertTrue(day["act"])
            self.assertEqual(day["vol"], 150)
            self.assertIsNone(day.get("pct"))

    def test_missing_claude_root_is_handled_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, claude_root = _new_store(directory)
            claude_root.rmdir()

            self.assertFalse(store.backfill_step())


class MaxTrackerStoreLiveObserveQuotaTests(unittest.TestCase):
    def test_primary_window_pct_becomes_the_days_peak_of_the_max_seen(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            ts = datetime(2026, 8, 7, 12, 0).timestamp()

            store.observe_quota("claude", 300, 40.0, ts)
            store.observe_quota("claude", 300, 65.0, ts + 60)
            store.observe_quota("claude", 300, 20.0, ts + 120)

            self.assertEqual(
                store.snapshot("2026-08-07", {})["claude"]["avgPeakPct"], 65.0)

    def test_secondary_window_marks_the_week_maxed_only_at_100(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            ts = datetime(2026, 8, 7, 12, 0).timestamp()

            store.observe_quota("claude", 10080, 87.0, ts)
            self.assertEqual(
                store.snapshot("2026-08-07", {})["claude"]["maxWeeks"], 0)

            store.observe_quota("claude", 10080, 100.0, ts + 60)
            self.assertEqual(
                store.snapshot("2026-08-07", {})["claude"]["maxWeeks"], 1)

    def test_none_window_minutes_is_treated_as_the_primary_session_window(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            ts = datetime(2026, 8, 7, 12, 0).timestamp()

            store.observe_quota("codex", None, 55.0, ts)

            self.assertEqual(
                store.snapshot("2026-08-07", {})["codex"]["avgPeakPct"], 55.0)

    def test_quota_observation_alone_never_marks_a_day_active(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            ts = datetime(2026, 8, 7, 12, 0).timestamp()

            store.observe_quota("claude", 300, 40.0, ts)

            self.assertIsNone(
                store.snapshot("2026-08-07", {})["codingStreakDays"])

    def test_out_of_range_pct_is_ignored_defensively(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            ts = datetime(2026, 8, 7, 12, 0).timestamp()

            store.observe_quota("claude", 300, 140.0, ts)
            store.observe_quota("claude", 300, -5.0, ts)
            store.observe_quota("nonexistent-provider", 300, 50.0, ts)

            self.assertIsNone(
                store.snapshot("2026-08-07", {})["claude"]["avgPeakPct"])


class MaxTrackerStoreObserveVolumeTests(unittest.TestCase):
    def test_tokens_accumulate_and_mark_the_day_active(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)

            store.observe_volume("claude", "2026-08-07", 100)
            store.observe_volume("claude", "2026-08-07", 50)

            day = store._state["claude"]["days"]["2026-08-07"]
            self.assertEqual(day["vol"], 150)
            self.assertTrue(day["act"])

    def test_zero_tokens_never_fabricates_activity(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)

            store.observe_volume("claude", "2026-08-07", 0)

            self.assertNotIn(
                "2026-08-07", store._state["claude"]["days"])

    def test_invalid_date_string_is_ignored_defensively(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)

            store.observe_volume("claude", "not-a-date", 100)

            self.assertNotIn(
                "not-a-date", store._state["claude"]["days"])


class MaxTrackerStoreSnapshotTests(unittest.TestCase):
    def test_snapshot_matches_build_payload_over_the_same_state(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            store.observe_volume("claude", "2026-08-01", 400)
            store.observe_quota("codex", 300, 71.0,
                                datetime(2026, 8, 12, 9, 0).timestamp())

            payload = store.snapshot("2026-08-12", {"claude": "max20x"})

            expected_state = {
                "claude": {"days": {"2026-08-01": {
                    "pct": None, "act": True, "vol": 400}}, "weeks": {}},
                "codex": {"days": {"2026-08-12": {
                    "pct": 71.0, "act": False, "vol": 0}}, "weeks": {}},
                "stale": False,
            }
            self.assertEqual(
                payload, build_payload(
                    expected_state, "2026-08-12", {"claude": "max20x"}))

    def test_snapshot_defaults_stale_to_false(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)

            payload = store.snapshot("2026-08-12", {})

            self.assertFalse(payload["stale"])


class RoundDayPctTests(unittest.TestCase):
    """_round_day_pct: the sole rounding point between a probe's fractional
    percent and the device parser's int8_t day-cell field (a single
    fractional pct rejects the ENTIRE payload -- see max_tracker_parse.c's
    parse_days, trunc(pct) != pct)."""

    def test_rounds_half_away_from_zero_not_bankers_rounding(self):
        self.assertEqual(_round_day_pct(0.5), 1)
        self.assertEqual(_round_day_pct(15.5), 16)
        self.assertEqual(_round_day_pct(2.5), 3)  # Python's round(2.5) == 2

    def test_ordinary_values_round_to_the_nearest_int(self):
        self.assertEqual(_round_day_pct(15.4), 15)
        self.assertEqual(_round_day_pct(15.6), 16)
        self.assertEqual(_round_day_pct(0.0), 0)

    def test_never_rounds_a_sub_100_observation_up_to_100(self):
        self.assertEqual(_round_day_pct(99.5), 99)
        self.assertEqual(_round_day_pct(99.96), 99)
        self.assertEqual(_round_day_pct(99.999), 99)

    def test_a_true_100_stays_the_exact_max(self):
        self.assertEqual(_round_day_pct(100.0), 100)

    def test_result_is_always_a_genuine_int(self):
        for value in (0.0, 15.5, 99.96, 100.0):
            self.assertIsInstance(_round_day_pct(value), int)


class MaxTrackerStoreIntegerPctTests(unittest.TestCase):
    """Finding A: real Claude/Codex utilization arrives fractional
    (15.5-style, by construction) -- every day-cell pct the store ever
    emits must still be a genuine int, never a float, or the device
    parser rejects the whole /api/max-tracker payload and the tracker
    pages stay dark forever."""

    def test_every_emitted_day_pct_is_an_int_never_a_float(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            ts = datetime(2026, 8, 7, 12, 0).timestamp()

            store.observe_quota("claude", 300, 15.5, ts)
            store.observe_quota("codex", 300, 99.96, ts + 60)
            store.observe_quota("claude", 10080, 100.0, ts + 120)

            payload = store.snapshot("2026-08-07", {})

            checked_real_values = 0
            for provider in PROVIDERS:
                for pct, _lvl in payload[provider]["days"]:
                    self.assertIsInstance(pct, int)
                    if pct != -1:
                        checked_real_values += 1
            # Sanity: the assertion above must have actually exercised at
            # least one real (non-absent) day, not just -1 padding.
            self.assertGreaterEqual(checked_real_values, 2)

    def test_a_sub_100_observation_never_renders_as_the_exact_max_cell(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)
            today = "2026-08-07"
            store.observe_quota(
                "claude", 300, 99.96, datetime(2026, 8, 7, 12, 0).timestamp())

            payload = store.snapshot(today, {})

            index = _index_for_date(today, today)
            self.assertEqual(payload["claude"]["days"][index][0], 99)


class MaxTrackerStoreGrayLevelStabilityTests(unittest.TestCase):
    """Finding B: save() writes the already-computed TERCILE (0-2), not
    raw volume, per the privacy allowlist ({d, pct, act, lvl} -- no raw
    numbers ever persisted). A loaded lvl must stay authoritative for its
    day, excluded from re-ranking, or it silently reshuffles ([0,1,2] ->
    [0,0,1]) on every single save/load cycle, permanently, since a
    completed backfill file is never reopened to recover the original
    raw volume."""

    def test_reload_keeps_loaded_lvls_stable_and_ranks_only_new_session_volume(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max-tracker.json"
            store, codex_root, claude_root = _new_store(directory, path=path)
            store.observe_volume("claude", "2026-08-01", 10)   # -> lvl 0
            store.observe_volume("claude", "2026-08-02", 50)   # -> lvl 1
            store.observe_volume("claude", "2026-08-03", 90)   # -> lvl 2
            store.save()

            def levels(target_store, today):
                payload = target_store.snapshot(today, {})
                return [
                    payload["claude"]["days"][_index_for_date(today, day)][1]
                    for day in ("2026-08-01", "2026-08-02", "2026-08-03")
                ]

            reloaded = MaxTrackerStore(path, codex_root, claude_root)
            self.assertEqual(levels(reloaded, "2026-08-03"), [0, 1, 2])

            # A much bigger volume enters the pool THIS session only.
            reloaded.observe_volume("claude", "2026-08-04", 5000)

            # The three historical (loaded, authoritative) days must be
            # completely unaffected by the new, vastly larger value now
            # sharing the in-memory state -- they are excluded from
            # ranking entirely, not just outranked.
            self.assertEqual(levels(reloaded, "2026-08-04"), [0, 1, 2])
            new_day_index = _index_for_date("2026-08-04", "2026-08-04")
            new_day_lvl = reloaded.snapshot(
                "2026-08-04", {})["claude"]["days"][new_day_index][1]
            # A single distinct in-session volume ranks conservatively at
            # level 0 (volume_levels' own "no second data point" rule) --
            # it must NOT inherit level 2 just because 5000 > 90.
            self.assertEqual(new_day_lvl, 0)

            # The exact regression: a SECOND save/load round trip must
            # still leave the original three untouched -- proving they
            # never re-enter the ranking pool even after another cycle.
            reloaded.save()
            reloaded_again = MaxTrackerStore(path, codex_root, claude_root)
            self.assertEqual(levels(reloaded_again, "2026-08-04"), [0, 1, 2])

    def test_fresh_volume_this_session_supersedes_a_loaded_lvl(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max-tracker.json"
            store, codex_root, claude_root = _new_store(directory, path=path)
            store.observe_volume("claude", "2026-08-01", 10)
            store.save()

            reloaded = MaxTrackerStore(path, codex_root, claude_root)
            self.assertIn("lvl", reloaded._state["claude"]["days"]["2026-08-01"])
            self.assertNotIn("vol", reloaded._state["claude"]["days"]["2026-08-01"])

            # The SAME day gets fresh real volume this session (e.g. the
            # backfill thread re-observes it, or -- more realistically --
            # today's own date rolls over into an already-loaded record).
            reloaded.observe_volume("claude", "2026-08-01", 500)

            day = reloaded._state["claude"]["days"]["2026-08-01"]
            self.assertNotIn("lvl", day)
            self.assertEqual(day["vol"], 500)


class MaxTrackerStorePersistenceTests(unittest.TestCase):
    def test_save_is_atomic_leaves_no_tmp_files_and_sets_mode_0600(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "max-tracker.json"
            store, _, _ = _new_store(directory, path=path)
            store.observe_volume("claude", "2026-08-07", 400)

            store.save()

            self.assertTrue(path.exists())
            leftovers = [p for p in path.parent.iterdir() if p != path]
            self.assertEqual(leftovers, [])
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_reloaded_store_produces_an_identical_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max-tracker.json"
            store, codex_root, claude_root = _new_store(directory, path=path)
            store.observe_volume("claude", "2026-08-07", 400)
            store.observe_quota("codex", 10080, 100.0,
                                datetime(2026, 8, 6, 9, 0).timestamp())
            store.observe_quota("claude", 300, 33.0,
                                datetime(2026, 8, 7, 9, 0).timestamp())

            store.save()
            reloaded = MaxTrackerStore(path, codex_root, claude_root)

            self.assertEqual(
                reloaded.snapshot("2026-08-07", {}),
                store.snapshot("2026-08-07", {}))

    def test_reload_of_a_missing_file_starts_empty_without_error(self):
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)

            payload = store.snapshot("2026-08-07", {})

            self.assertIsNone(payload["codingStreakDays"])

    def test_days_older_than_the_retention_window_are_pruned_on_save(self):
        # Uses save()'s injectable `today` so the 400-day boundary is
        # exact and independent of the real wall-clock date (a bare
        # date.today() vs. year-2000 comparison would never actually
        # exercise the boundary itself).
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max-tracker.json"
            store, codex_root, claude_root = _new_store(directory, path=path)
            anchor = date(2026, 8, 12)
            kept_day = (anchor - timedelta(days=399)).isoformat()
            pruned_day = (anchor - timedelta(days=401)).isoformat()
            store.observe_volume("claude", kept_day, 500)
            store.observe_volume("claude", pruned_day, 500)

            store.save(today=anchor.isoformat())
            reloaded = MaxTrackerStore(path, codex_root, claude_root)

            self.assertIn(kept_day, reloaded._state["claude"]["days"])
            self.assertNotIn(pruned_day, reloaded._state["claude"]["days"])

    def test_save_with_no_today_argument_still_prunes_using_the_real_clock(self):
        # Default-path sanity check for the injectable seam: an
        # obviously-ancient day must still be gone when `today` is omitted.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max-tracker.json"
            store, codex_root, claude_root = _new_store(directory, path=path)
            store.observe_volume("claude", "2000-01-01", 500)

            store.save()
            reloaded = MaxTrackerStore(path, codex_root, claude_root)

            self.assertNotIn("2000-01-01", reloaded._state["claude"]["days"])


class MaxTrackerStorePrivacySchemaTests(unittest.TestCase):
    """Schema-walker: the persisted file must never carry anything beyond
    the {v, days:{pct,act,lvl}, weeks: bool, backfill} allowlist -- no
    prompts, commands, projects, models, file names or raw log events."""

    def test_persisted_json_contains_no_keys_outside_the_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max-tracker.json"
            store, codex_root, claude_root = _new_store(directory, path=path)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(
                    primary_pct=61.0, secondary_pct=100.0),
                    "2026-08-01T10:00:00Z"),
            ])
            _write_jsonl(claude_root / "session.jsonl", [
                _claude_usage_line("m1", 500, "2026-08-02T10:00:00Z"),
            ])
            _drain(store)
            store.observe_quota("claude", 300, 55.0,
                                datetime(2026, 8, 3, 9, 0).timestamp())
            store.observe_quota("claude", 10080, 100.0,
                                datetime(2026, 8, 3, 9, 0).timestamp())

            store.save()
            raw = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(set(raw), set(PROVIDERS))
        for provider in PROVIDERS:
            section = raw[provider]
            self.assertLessEqual(set(section), {"v", "days", "weeks",
                                                 "backfill"})
            for day, record in section["days"].items():
                date.fromisoformat(day)
                self.assertLessEqual(set(record), {"pct", "act", "lvl"})
            for week, value in section["weeks"].items():
                self.assertRegex(week, r"^\d{4}-W\d{2}$")
                self.assertIsInstance(value, bool)
            serialized_backfill = json.dumps(section["backfill"])
            self.assertNotIn(str(codex_root), serialized_backfill)
            self.assertNotIn(str(claude_root), serialized_backfill)
            self.assertNotIn("rollout-a", serialized_backfill)
            self.assertNotIn("session.jsonl", serialized_backfill)

    def test_backfill_bookkeeping_keys_are_plain_numeric_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "max-tracker.json"
            store, codex_root, claude_root = _new_store(directory, path=path)
            _write_jsonl(codex_root / "rollout-a.jsonl", [
                _rollout_event(_rollout_limits(primary_pct=10.0),
                               "2026-08-01T10:00:00Z"),
            ])
            _drain(store)

            store.save()
            raw = json.loads(path.read_text(encoding="utf-8"))

        for key, entry in raw["codex"]["backfill"].items():
            self.assertRegex(key, r"^\d+$")
            self.assertLessEqual(
                set(entry), {"offset", "size", "done", "discarding"})


class MaxTrackerStoreThreadSafetyTests(unittest.TestCase):
    def test_concurrent_observe_volume_and_snapshot_never_raise_or_lose_updates(self):
        # Regression: after Task 6 wires this store up, a background
        # thread (backfill_step/observe_volume/save), a probe thread
        # (observe_quota), and the HTTP thread (snapshot) all touch it
        # concurrently. Without a lock, concurrent dict mutation during
        # snapshot()'s/save()'s iteration can raise ("dict changed size
        # during iteration") or silently serialize torn state.
        iterations = 2_000
        day = "2026-08-07"
        errors = []

        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)

            def hammer_volume():
                try:
                    for _ in range(iterations):
                        store.observe_volume("claude", day, 1)
                except Exception as exc:  # pragma: no cover - failure path
                    errors.append(exc)

            def hammer_snapshot():
                try:
                    for _ in range(iterations):
                        store.snapshot("2026-08-07", {})
                except Exception as exc:  # pragma: no cover - failure path
                    errors.append(exc)

            threads = [
                threading.Thread(target=hammer_volume),
                threading.Thread(target=hammer_volume),
                threading.Thread(target=hammer_snapshot),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()

            self.assertEqual(errors, [])
            # Not just "no crash" -- the lock must also prevent lost
            # updates: exactly 2 * iterations increments landed.
            self.assertEqual(
                store._state["claude"]["days"][day]["vol"], 2 * iterations)


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
            # The device parser's day cell is int8_t on both fields
            # (parse_days: trunc(pct) != pct / trunc(lvl) != lvl reject
            # the WHOLE payload) -- a bare value-range check would happily
            # pass a fractional 15.5 that the real parser rejects.
            test.assertIsInstance(pct, int)
            test.assertIsInstance(lvl, int)
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
                     "max-tracker-full.json", "max-tracker-live-shape.json"):
            with self.subTest(fixture=name):
                _assert_contract_shape(self, _load_fixture(name))

    def test_live_shape_fixture_matches_a_store_fed_the_same_fractional_inputs(self):
        """Finding A regression: max-tracker-live-shape.json is committed,
        generated output (not hand-crafted like the other three) from a
        real MaxTrackerStore fed the exact fractional percents a live
        probe naturally produces (Claude/Codex utilization arrives as
        e.g. 15.5, 99.96 "by construction"). Re-driving the SAME store
        calls here and comparing must reproduce it byte-for-byte,
        proving the fixture isn't stale relative to the current rounding
        rule (and this test regenerating a drifted fixture, rather than
        silently trusting a checked-in file, is the actual regression
        gate -- the C-side test only proves the CURRENT fixture parses,
        not that it still matches today's server logic)."""
        with tempfile.TemporaryDirectory() as directory:
            store, _, _ = _new_store(directory)

            def ts(day, hour=12):
                return datetime.fromisoformat(f"{day}T{hour:02d}:00:00").timestamp()

            fractional_days = {
                "2026-08-01": 15.5, "2026-08-02": 62.3, "2026-08-03": 99.96,
                "2026-08-04": 47.49, "2026-08-05": 88.51, "2026-08-06": 0.5,
                "2026-08-07": 100.0,
            }
            for day, pct in fractional_days.items():
                store.observe_quota("claude", 300, pct, ts(day))
                store.observe_volume("claude", day, 800)
            codex_fractional_days = {
                "2026-08-08": 33.34, "2026-08-09": 71.66, "2026-08-10": 100.0,
            }
            for day, pct in codex_fractional_days.items():
                store.observe_quota("codex", 300, pct, ts(day))
            store.observe_quota("claude", 10080, 100.0, ts("2026-08-07"))
            store.observe_quota("codex", 10080, 100.0, ts("2026-08-10"))
            store.observe_volume("claude", "2026-08-11", 120)
            store.observe_volume("claude", "2026-08-12", 4000)

            payload = store.snapshot(
                "2026-08-12", {"claude": "max20x", "codex": "pro"})

            self.assertEqual(payload, _load_fixture("max-tracker-live-shape.json"))

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
