import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import tools.tokenserver.usage_history as usage_history_module
from tools.tokenserver.usage_history import (
    RESET_QUANTUM_S, Forecast, UsageHistory)


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

    def test_corrupt_file_is_quarantined_without_touching_sibling(self):
        # OBS-11: start empty, but keep the bytes beside the file and say so.
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "usage-history.json"
            sibling = root / "keep-me.txt"
            path.write_text("{broken", encoding="utf-8")
            sibling.write_text("unchanged", encoding="utf-8")

            with self.assertLogs("tokenserver.state", level="WARNING") as captured:
                history = UsageHistory(path)

            self.assertEqual(history.records, ())
            self.assertEqual(sibling.read_text(encoding="utf-8"),
                             "unchanged")
            self.assertFalse(path.exists())
            quarantined = list(root.glob("usage-history.json.corrupt-*"))
            self.assertEqual(len(quarantined), 1)
            self.assertEqual(quarantined[0].read_text(encoding="utf-8"),
                             "{broken")
            self.assertIn("quarantined", "\n".join(captured.output))

            self.assertTrue(history.record(
                "claude", "week", 10, reset_at=DAY, at=0))
            self.assertTrue(path.exists())

    def test_quarantine_rename_is_fsynced_like_a_save(self):
        # Codex review of #105: the rename that keeps the corrupt bytes is
        # only durable once its directory entry is, same as a save.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            path.write_text("{broken", encoding="utf-8")
            with mock.patch(
                    "tools.tokenserver.state_files.fsync_parent") as fsync, \
                    self.assertLogs("tokenserver.state", level="WARNING"):
                UsageHistory(path)
            quarantined = list(path.parent.glob("usage-history.json.corrupt-*"))
            self.assertEqual(len(quarantined), 1)
            fsync.assert_called_once_with(quarantined[0])

    def test_quarantine_survives_a_failed_directory_fsync(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            path.write_text("{broken", encoding="utf-8")
            with mock.patch("tools.tokenserver.state_files.fsync_parent",
                            side_effect=OSError("EIO")), \
                    self.assertLogs("tokenserver.state",
                                    level="WARNING") as captured:
                history = UsageHistory(path)
            self.assertEqual(history.records, ())
            self.assertEqual(
                len(list(path.parent.glob("usage-history.json.corrupt-*"))), 1)
            self.assertIn("not yet durable", "\n".join(captured.output))

    def test_post_replace_fsync_failure_keeps_memory_and_disk_together(self):
        # Codex review of #105: the replace has landed when the directory
        # fsync fails; rolling memory back made the next save drop the
        # sample that was already on disk.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            history = UsageHistory(path)
            with mock.patch("tools.tokenserver.usage_history.fsync_parent",
                            side_effect=OSError("EIO")), \
                    self.assertLogs("tokenserver.state",
                                    level="WARNING") as captured:
                self.assertTrue(history.record(
                    "claude", "week", 10, reset_at=DAY, at=0))
            self.assertEqual(len(history.records), 1)
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(len(on_disk["samples"]), 1)
            self.assertIn("directory fsync failed", "\n".join(captured.output))
            # The next save carries both samples: nothing was dropped.
            self.assertTrue(history.record(
                "claude", "week", 12, reset_at=DAY, at=usage_history_module.SAMPLE_INTERVAL_S))
            on_disk = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual([s["pct"] for s in on_disk["samples"]],
                             [10.0, 12.0])

    def test_an_unreadable_file_is_never_overwritten(self):
        # Codex review of #105: a permission or I/O error is not "empty".
        # The rename only needs the directory's permission, so a store
        # that started empty would replace the file on its first save.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            UsageHistory(path).record("claude", "week", 10, reset_at=DAY, at=0)
            original = path.read_bytes()
            with mock.patch.object(Path, "read_text",
                                   side_effect=PermissionError("denied")), \
                    self.assertLogs("tokenserver.state",
                                    level="WARNING") as captured:
                history = UsageHistory(path)
            self.assertEqual(history.records, ())
            self.assertIn("refusing to save", "\n".join(captured.output))
            self.assertFalse(history.record(
                "claude", "week", 50, reset_at=DAY, at=usage_history_module.SAMPLE_INTERVAL_S))
            self.assertEqual(path.read_bytes(), original)
            self.assertEqual(history.records, ())

    def test_wrong_shape_is_quarantined_too(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            path.write_text('{"v": 7, "samples": "no"}', encoding="utf-8")
            with self.assertLogs("tokenserver.state", level="WARNING"):
                history = UsageHistory(path)
            self.assertEqual(history.records, ())
            self.assertEqual(
                len(list(path.parent.glob("usage-history.json.corrupt-*"))), 1)

    def test_persist_fsyncs_the_parent_directory_after_the_rename(self):
        # OBS-21.
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "usage-history.json"
            history = UsageHistory(path)
            with mock.patch(
                    "tools.tokenserver.usage_history.fsync_parent") as fsync:
                self.assertTrue(history.record(
                    "claude", "week", 10, reset_at=DAY, at=0))
            fsync.assert_called_once_with(path)

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
    def test_delta_is_full_percent_when_cycle_started_inside_period(self):
        """Verkligheten 2026-08-14: veckopoolen nollställdes 08:00 men
        historiken började först 10:45 (429-mörkläggning) respektive vid
        parserfixen (Fable). Börjar cykeln EFTER "since" är baslinjen 0
        per definition — hela procenten föll inom perioden, och ett enda
        prov räcker för att säga det ärligt."""
        with tempfile.TemporaryDirectory() as temp_dir:
            history = UsageHistory(Path(temp_dir) / "history.json")
            since = 24 * HOUR                       # midnatt
            reset_at = (7 * 24 + 25) * HOUR         # cykelstart 25*HOUR
            history.record("claude", "model_week", 11,
                           reset_at=reset_at, at=26 * HOUR)

            delta = history.delta_since(
                "claude", "model_week", since=since,
                reset_at=reset_at, now=27 * HOUR)

            self.assertEqual(delta, 11.0)

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


class UsageHistoryConcurrencyTests(unittest.TestCase):
    """ThreadingHTTPServer serves requests on concurrent threads that share
    one UsageHistory; these tests pin the required thread-safety contract."""

    def _history(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "usage-history.json"
        return UsageHistory(path), path

    def test_interleaved_writers_keep_both_batches_in_memory_and_on_disk(self):
        history, path = self._history()
        first_dump_replaced = threading.Event()
        second_writer_done = threading.Event()
        real_replace = os.replace
        first_thread_name = "writer-claude"

        def ordered_replace(src, dst):
            if threading.current_thread().name == first_thread_name:
                first_dump_replaced.set()
                # Event-driven pre-fix: the second writer finishes within
                # microseconds and sets the event. The timeout exists only so
                # a serialized (locked) implementation, where the second
                # writer must wait for us, cannot deadlock the test.
                second_writer_done.wait(timeout=2.0)
            return real_replace(src, dst)

        # Recent wall-clock timestamps keep the reload below inside the
        # eight-day retention window.
        now = int(usage_history_module.time.time())

        def first_writer():
            history.record("claude", "week", 10.0,
                           reset_at=now + 7 * DAY, at=now)

        def second_writer():
            history.record("codex", "week", 20.0,
                           reset_at=now + 7 * DAY, at=now)
            second_writer_done.set()

        with mock.patch.object(usage_history_module.os, "replace",
                               ordered_replace):
            first = threading.Thread(target=first_writer,
                                     name=first_thread_name)
            first.start()
            self.assertTrue(first_dump_replaced.wait(timeout=5.0))
            second = threading.Thread(target=second_writer,
                                      name="writer-codex")
            second.start()
            first.join(timeout=10.0)
            second.join(timeout=10.0)
            self.assertFalse(first.is_alive())
            self.assertFalse(second.is_alive())

        surviving = {record["provider"] for record in history.records}
        self.assertEqual(surviving, {"claude", "codex"})
        reloaded = UsageHistory(path)
        self.assertEqual(
            {record["provider"] for record in reloaded.records},
            {"claude", "codex"})

    def test_barrier_stress_writers_never_drop_each_others_samples(self):
        history, path = self._history()
        thread_count = 4
        samples_per_thread = 120
        barrier = threading.Barrier(thread_count)
        added_counts = [0] * thread_count

        now = int(usage_history_module.time.time())

        def writer(index):
            provider = "claude" if index % 2 == 0 else "codex"
            window = ("session", "week", "model_week")[index % 3]
            barrier.wait()
            for i in range(samples_per_thread):
                # Unique reset cycle per sample defeats the 15-minute
                # rate limit, so every write must be accepted and kept.
                reset_at = now + DAY + (index * samples_per_thread + i) * \
                    2 * RESET_QUANTUM_S
                added_counts[index] += history.record_many(
                    ((provider, window, 50.0, reset_at),), at=now + i)

        threads = [threading.Thread(target=writer, args=(index,))
                   for index in range(thread_count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60.0)
            self.assertFalse(thread.is_alive())

        expected = thread_count * samples_per_thread
        self.assertEqual(sum(added_counts), expected)
        self.assertEqual(len(history.records), expected)
        self.assertEqual(len(UsageHistory(path).records), expected)

    def test_reader_only_sees_complete_sorted_snapshots(self):
        history, _ = self._history()
        barrier = threading.Barrier(2)
        writes = 200
        failures = []

        def writer():
            barrier.wait()
            # Decreasing timestamps force a real in-place re-sort per write,
            # which an unlocked reader could observe mid-permutation.
            for i in range(writes):
                history.record_many(
                    (("claude", "week", 50.0,
                      DAY + i * 2 * RESET_QUANTUM_S),),
                    at=10_000_000 - i)

        def reader():
            barrier.wait()
            for _ in range(writes):
                snapshot = history.records
                timestamps = [record["at"] for record in snapshot]
                if timestamps != sorted(timestamps):
                    failures.append("unsorted snapshot")
                    return
                for record in snapshot:
                    if set(record) != {
                            "at", "provider", "window", "pct", "reset"}:
                        failures.append("partial record")
                        return
                history.delta_since("claude", "week", since=0,
                                    reset_at=DAY, now=10_000_000)
                history.forecast("claude", "week", reset_at=DAY,
                                 now=10_000_000)

        threads = [threading.Thread(target=writer),
                   threading.Thread(target=reader)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60.0)
            self.assertFalse(thread.is_alive())
        self.assertEqual(failures, [])


if __name__ == "__main__":
    unittest.main()
