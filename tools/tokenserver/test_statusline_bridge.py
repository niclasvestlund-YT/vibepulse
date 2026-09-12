import io
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver import statusline_bridge as bridge


NOW = 1_800_000_000
FIVE = {"used_percentage": 42.0, "resets_at": NOW + 4 * 3600}
WEEK = {"used_percentage": 12.5, "resets_at": NOW + 5 * 86400}


def payload(**overrides):
    document = {
        "version": "2.1.0",
        "session_id": "abc",
        "transcript_path": "/Users/x/.claude/projects/y/z.jsonl",
        "rate_limits": {"five_hour": dict(FIVE), "seven_day": dict(WEEK)},
    }
    document.update(overrides)
    return json.dumps(document).encode("utf-8")


class ParsePayloadTests(unittest.TestCase):
    def test_keeps_only_the_two_windows_and_the_version(self):
        parsed = bridge.parse_payload(payload(), NOW)
        self.assertEqual(parsed["version"], "2.1.0")
        self.assertEqual(parsed["windows"], {
            "five_hour": {"pct": 42.0, "resets_at": NOW + 4 * 3600},
            "seven_day": {"pct": 12.5, "resets_at": NOW + 5 * 86400},
        })

    def test_missing_rate_limits_is_no_observation(self):
        parsed = bridge.parse_payload(payload(rate_limits=None), NOW)
        self.assertEqual(parsed["windows"], {})
        document = json.loads(payload())
        del document["rate_limits"]
        parsed = bridge.parse_payload(json.dumps(document).encode(), NOW)
        self.assertEqual(parsed["windows"], {})

    def test_one_window_absent_keeps_the_other(self):
        parsed = bridge.parse_payload(
            payload(rate_limits={"seven_day": dict(WEEK)}), NOW)
        self.assertEqual(list(parsed["windows"]), ["seven_day"])

    def test_malformed_window_rejects_the_whole_payload(self):
        cases = {
            "pct string": {"used_percentage": "42", "resets_at": FIVE["resets_at"]},
            "pct bool": {"used_percentage": True, "resets_at": FIVE["resets_at"]},
            "pct negative": {"used_percentage": -1, "resets_at": FIVE["resets_at"]},
            "pct over 100": {"used_percentage": 100.5, "resets_at": FIVE["resets_at"]},
            "pct nan": {"used_percentage": float("nan"), "resets_at": FIVE["resets_at"]},
            "reset past": {"used_percentage": 1, "resets_at": NOW},
            "reset too far": {"used_percentage": 1, "resets_at": NOW + 6 * 3600},
            "reset fractional": {"used_percentage": 1, "resets_at": NOW + 10.5},
            "reset huge": {"used_percentage": 1, "resets_at": 10 ** 400},
            "reset missing": {"used_percentage": 1},
            "not an object": [1, 2],
        }
        for label, window in cases.items():
            with self.subTest(label):
                raw = json.dumps({"rate_limits": {"five_hour": window,
                                                  "seven_day": dict(WEEK)}},
                                 allow_nan=True).encode()
                with self.assertRaises(bridge.RejectedPayload):
                    bridge.parse_payload(raw, NOW)

    def test_weekly_horizon_is_eight_days(self):
        ok = {"used_percentage": 1, "resets_at": NOW + 7 * 86400 + 3600}
        bad = {"used_percentage": 1, "resets_at": NOW + 9 * 86400}
        bridge.parse_payload(
            json.dumps({"rate_limits": {"seven_day": ok}}).encode(), NOW)
        with self.assertRaises(bridge.RejectedPayload):
            bridge.parse_payload(
                json.dumps({"rate_limits": {"seven_day": bad}}).encode(), NOW)

    def test_non_json_and_non_object_are_rejected(self):
        for raw in (b"", b"not json", b"[1]", b"\xff\xfe", b"42", b"null"):
            with self.subTest(raw):
                with self.assertRaises(bridge.RejectedPayload):
                    bridge.parse_payload(raw, NOW)
        with self.assertRaises(bridge.RejectedPayload):
            bridge.parse_payload(json.dumps({"rate_limits": 7}).encode(), NOW)

    def test_oversized_stdin_is_rejected(self):
        raw = b'{"pad":"' + b"x" * bridge.STDIN_MAX_BYTES + b'"}'
        with self.assertRaises(bridge.RejectedPayload):
            bridge.parse_payload(raw, NOW)

    def test_version_is_bounded_and_printable(self):
        for bad in ("", "x" * 65, "1.0\n", 7, None):
            with self.subTest(bad):
                parsed = bridge.parse_payload(payload(version=bad), NOW)
                self.assertIsNone(parsed["version"])


class MergeEntryTests(unittest.TestCase):
    def observed(self, **windows):
        return {"windows": windows, "version": "2.1.0"}

    def test_first_observation_stamps_at_and_seen(self):
        entry = bridge.merge_entry(None, self.observed(
            five_hour={"pct": 42.0, "resets_at": NOW + 100}), NOW)
        self.assertEqual(entry, {
            "five_hour": {"pct": 42.0, "resets_at": NOW + 100,
                          "at": NOW, "seen": NOW},
            "claude_code_version": "2.1.0",
        })

    def test_replay_keeps_value_and_advances_seen(self):
        stored = {"five_hour": {"pct": 42.0, "resets_at": NOW + 100,
                                "at": NOW - 60, "seen": NOW - 60}}
        for pct in (42.0, 30.0):
            with self.subTest(pct):
                entry = bridge.merge_entry(stored, self.observed(
                    five_hour={"pct": pct, "resets_at": NOW + 100}), NOW)
                self.assertEqual(entry["five_hour"], {
                    "pct": 42.0, "resets_at": NOW + 100,
                    "at": NOW - 60, "seen": NOW})

    def test_higher_pct_in_the_same_window_replaces(self):
        stored = {"five_hour": {"pct": 42.0, "resets_at": NOW + 100,
                                "at": NOW - 60, "seen": NOW - 60}}
        entry = bridge.merge_entry(stored, self.observed(
            five_hour={"pct": 43.0, "resets_at": NOW + 100}), NOW)
        self.assertEqual(entry["five_hour"],
                         {"pct": 43.0, "resets_at": NOW + 100,
                          "at": NOW, "seen": NOW})

    def test_newer_reset_replaces_even_with_lower_pct(self):
        stored = {"five_hour": {"pct": 90.0, "resets_at": NOW + 100,
                                "at": NOW - 60, "seen": NOW - 60}}
        entry = bridge.merge_entry(stored, self.observed(
            five_hour={"pct": 1.0, "resets_at": NOW + 3600}), NOW)
        self.assertEqual(entry["five_hour"]["pct"], 1.0)
        self.assertEqual(entry["five_hour"]["resets_at"], NOW + 3600)

    def test_older_reset_than_stored_is_a_replay(self):
        stored = {"five_hour": {"pct": 5.0, "resets_at": NOW + 3600,
                                "at": NOW - 60, "seen": NOW - 60}}
        entry = bridge.merge_entry(stored, self.observed(
            five_hour={"pct": 99.0, "resets_at": NOW + 100}), NOW)
        self.assertEqual(entry["five_hour"]["pct"], 5.0)
        self.assertEqual(entry["five_hour"]["seen"], NOW)

    def test_absent_window_keeps_stored_until_it_expires(self):
        stored = {"seven_day": {"pct": 12.5, "resets_at": NOW + 100,
                                "at": NOW - 60, "seen": NOW - 60},
                  "claude_code_version": "2.0.0"}
        entry = bridge.merge_entry(stored, self.observed(), NOW)
        self.assertEqual(entry["seven_day"], stored["seven_day"])
        entry = bridge.merge_entry(stored, self.observed(), NOW + 100)
        self.assertNotIn("seven_day", entry)

    def test_malformed_stored_window_is_treated_as_absent(self):
        for stored in ({"five_hour": "junk"},
                       {"five_hour": {"pct": 500, "resets_at": NOW + 1,
                                      "at": 1, "seen": 1}},
                       {"five_hour": {"pct": 5, "resets_at": NOW + 10 ** 9,
                                      "at": 1, "seen": 1}},
                       {"five_hour": {"pct": 5, "resets_at": NOW + 1}},
                       "not a dict", 7):
            with self.subTest(stored):
                entry = bridge.merge_entry(stored, self.observed(
                    five_hour={"pct": 1.0, "resets_at": NOW + 50}), NOW)
                self.assertEqual(entry["five_hour"]["pct"], 1.0)

    def test_version_carries_over_when_the_payload_has_none(self):
        stored = {"claude_code_version": "2.0.0"}
        entry = bridge.merge_entry(
            stored, {"windows": {}, "version": None}, NOW)
        self.assertEqual(entry["claude_code_version"], "2.0.0")
        entry = bridge.merge_entry(
            {"claude_code_version": 7}, {"windows": {}, "version": None}, NOW)
        self.assertNotIn("claude_code_version", entry)


class RecordSampleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "state"

    def read(self):
        return json.loads((self.dir / bridge.SAMPLE_NAME).read_text())

    def test_first_write_creates_a_private_v1_file(self):
        self.assertEqual(bridge.record_sample(payload(), now=NOW,
                                              directory=self.dir), "written")
        document = self.read()
        self.assertEqual(document["v"], 1)
        entry = document["accounts"]["single"]
        self.assertEqual(entry["five_hour"]["pct"], 42.0)
        self.assertEqual(entry["seven_day"]["resets_at"], WEEK["resets_at"])
        self.assertEqual(entry["claude_code_version"], "2.1.0")
        if os.name == "posix":
            mode = stat.S_IMODE((self.dir / bridge.SAMPLE_NAME).stat().st_mode)
            self.assertEqual(mode, 0o600)
        self.assertEqual(
            [p.name for p in self.dir.iterdir() if p.name.startswith(".")], [])

    def test_replay_at_the_same_second_is_unchanged(self):
        bridge.record_sample(payload(), now=NOW, directory=self.dir)
        self.assertEqual(bridge.record_sample(payload(), now=NOW,
                                              directory=self.dir), "unchanged")

    def test_replay_later_advances_seen_only(self):
        bridge.record_sample(payload(), now=NOW, directory=self.dir)
        self.assertEqual(bridge.record_sample(payload(), now=NOW + 30,
                                              directory=self.dir), "written")
        entry = self.read()["accounts"]["single"]
        self.assertEqual(entry["five_hour"]["at"], NOW)
        self.assertEqual(entry["five_hour"]["seen"], NOW + 30)

    def test_rejected_payload_leaves_the_file_untouched(self):
        bridge.record_sample(payload(), now=NOW, directory=self.dir)
        before = (self.dir / bridge.SAMPLE_NAME).read_bytes()
        bad = payload(rate_limits={
            "five_hour": {"used_percentage": 99, "resets_at": FIVE["resets_at"]},
            "seven_day": {"used_percentage": "junk", "resets_at": 1}})
        self.assertEqual(bridge.record_sample(bad, now=NOW + 1,
                                              directory=self.dir), "rejected")
        self.assertEqual((self.dir / bridge.SAMPLE_NAME).read_bytes(), before)

    def test_session_start_payload_without_limits_writes_nothing_new(self):
        raw = payload(rate_limits=None)
        self.assertEqual(bridge.record_sample(raw, now=NOW,
                                              directory=self.dir), "unchanged")
        self.assertFalse((self.dir / bridge.SAMPLE_NAME).exists())

    def test_expired_windows_are_dropped_and_empty_entry_removed(self):
        bridge.record_sample(payload(), now=NOW, directory=self.dir)
        later = WEEK["resets_at"] + 1
        raw = payload(rate_limits=None)
        self.assertEqual(bridge.record_sample(raw, now=later,
                                              directory=self.dir), "written")
        self.assertEqual(self.read(), {"v": 1, "accounts": {}})

    def test_corrupt_file_is_quarantined_not_overwritten(self):
        self.dir.mkdir(parents=True)
        target = self.dir / bridge.SAMPLE_NAME
        target.write_text("{not json")
        with self.assertLogs("tokenserver.state", level="WARNING"):
            outcome = bridge.record_sample(payload(), now=NOW,
                                           directory=self.dir)
        self.assertEqual(outcome, "written")
        quarantined = [p for p in self.dir.iterdir()
                       if ".corrupt-" in p.name]
        self.assertEqual(len(quarantined), 1)
        self.assertEqual(quarantined[0].read_text(), "{not json")
        self.assertEqual(self.read()["accounts"]["single"]["five_hour"]["pct"],
                         42.0)

    def test_wrong_shape_is_quarantined(self):
        self.dir.mkdir(parents=True)
        (self.dir / bridge.SAMPLE_NAME).write_text(json.dumps({"v": 2}))
        with self.assertLogs("tokenserver.state", level="WARNING"):
            bridge.record_sample(payload(), now=NOW, directory=self.dir)
        self.assertEqual(self.read()["v"], 1)

    def test_unreadable_file_skips_the_write(self):
        self.dir.mkdir(parents=True)
        target = self.dir / bridge.SAMPLE_NAME
        target.write_text("{}")
        with mock.patch.object(Path, "read_bytes",
                               side_effect=PermissionError("nope")):
            outcome = bridge.record_sample(payload(), now=NOW,
                                           directory=self.dir)
        self.assertEqual(outcome, "unreadable")
        self.assertEqual(target.read_text(), "{}")

    @unittest.skipIf(bridge.fcntl is None and bridge.msvcrt is None,
                     "no interprocess lock primitive")
    def test_held_lock_skips_the_write_within_the_bound(self):
        self.dir.mkdir(parents=True)
        lock = bridge._Lock(self.dir / bridge.LOCK_NAME, wait_s=0)
        with lock as held:
            self.assertTrue(held)
            with mock.patch.object(bridge, "LOCK_RETRY_S", 0):
                outcome = bridge.record_sample(payload(), now=NOW,
                                               directory=self.dir,
                                               lock_wait_s=0.05)
        self.assertEqual(outcome, "locked")
        self.assertFalse((self.dir / bridge.SAMPLE_NAME).exists())
        self.assertEqual(bridge.record_sample(payload(), now=NOW,
                                              directory=self.dir), "written")

    def test_unwritable_directory_is_reported_not_raised(self):
        with mock.patch.object(bridge, "atomic_write_private",
                               side_effect=OSError("disk full")):
            outcome = bridge.record_sample(payload(), now=NOW,
                                           directory=self.dir)
        self.assertEqual(outcome, "unreadable")


class SummarizeSampleTests(unittest.TestCase):
    def document(self, **entry):
        return {"v": 1, "accounts": {"single": entry}}

    def test_fresh_stale_and_empty(self):
        five = {"pct": 42.0, "resets_at": NOW + 100, "at": NOW - 10,
                "seen": NOW - 10}
        week = {"pct": 12.5, "resets_at": NOW + 86400, "at": NOW - 3000,
                "seen": NOW - 3000}
        summary = bridge.summarize_sample(
            self.document(five_hour=five, seven_day=week,
                          claude_code_version="2.1.0"), NOW)
        self.assertEqual(summary["status"], "fresh")
        self.assertEqual(summary["ageS"], 10)
        self.assertEqual(summary["claudeCodeVersion"], "2.1.0")
        self.assertEqual(set(summary["windows"]), {"five_hour", "seven_day"})
        later = NOW + bridge.FRESH_S + 1
        summary = bridge.summarize_sample(
            self.document(five_hour=five, seven_day=week), later)
        self.assertEqual(summary["status"], "stale")
        self.assertEqual(list(summary["windows"]), ["seven_day"])
        summary = bridge.summarize_sample(
            self.document(five_hour=five, seven_day=week), NOW + 86400)
        self.assertEqual(summary, {"status": "empty", "ageS": None,
                                   "claudeCodeVersion": None, "windows": {}})
        self.assertEqual(bridge.summarize_sample(None, NOW)["status"], "empty")
        self.assertEqual(bridge.summarize_sample({"v": 1, "accounts": "x"},
                                                 NOW)["status"], "empty")

    def test_bad_version_is_dropped(self):
        five = {"pct": 1, "resets_at": NOW + 100, "at": NOW, "seen": NOW}
        for version in (7, "", "a\nb", "x" * 200):
            summary = bridge.summarize_sample(
                self.document(five_hour=five, claude_code_version=version), NOW)
            self.assertIn(summary["claudeCodeVersion"], (None, "x" * 64))

    def test_peek_sample_never_quarantines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / bridge.SAMPLE_NAME
            self.assertEqual(bridge.peek_sample(path), ("missing", None))
            path.write_text("{oops")
            self.assertEqual(bridge.peek_sample(path), ("invalid", None))
            self.assertEqual(path.read_text(), "{oops")
            path.write_text(json.dumps({"v": 2, "accounts": {}}))
            self.assertEqual(bridge.peek_sample(path), ("invalid", None))
            path.write_text(json.dumps({"v": 1, "accounts": {}}))
            self.assertEqual(bridge.peek_sample(path),
                             ("ok", {"v": 1, "accounts": {}}))
            with mock.patch.object(Path, "read_bytes",
                                   side_effect=PermissionError("nope")):
                self.assertEqual(bridge.peek_sample(path),
                                 ("unreadable", None))


class ChainedCommandTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.config_dir = self.dir / "claude"

    def write_config(self, record):
        path = self.dir / bridge.CONFIG_NAME
        key = bridge.config_dir_key(self.config_dir)
        path.write_text(json.dumps({"v": 1, "dirs": {key: record}}))
        return path

    def test_reads_the_command_recorded_for_this_config_dir(self):
        path = self.write_config({"chained_command": "echo hi"})
        self.assertEqual(bridge.chained_command(self.config_dir, path),
                         "echo hi")
        other = self.dir / "other"
        self.assertIsNone(bridge.chained_command(other, path))

    def test_missing_or_malformed_config_means_no_command(self):
        self.assertIsNone(bridge.chained_command(
            self.config_dir, self.dir / "absent.json"))
        for record in ({"chained_command": ""}, {"chained_command": 7},
                       {"chained_command": "a\nb"}, {}, "junk"):
            with self.subTest(record):
                path = self.write_config(record)
                self.assertIsNone(bridge.chained_command(self.config_dir, path))
        path = self.dir / bridge.CONFIG_NAME
        path.write_text("{oops")
        self.assertIsNone(bridge.chained_command(self.config_dir, path))

    def test_config_dir_key_is_stable_and_path_independent(self):
        key = bridge.config_dir_key(self.config_dir)
        self.assertEqual(len(key), 16)
        self.assertEqual(key, bridge.config_dir_key(
            self.dir / "x" / ".." / "claude"))

    def test_claude_config_dir_honours_the_override(self):
        self.assertEqual(bridge.claude_config_dir({"CLAUDE_CONFIG_DIR": "/x"}),
                         Path("/x"))
        self.assertEqual(bridge.claude_config_dir({}), Path.home() / ".claude")


class RunChainedTests(unittest.TestCase):
    def test_no_command_prints_nothing_and_exits_zero(self):
        out = io.BytesIO()
        run = mock.Mock()
        self.assertEqual(bridge.run_chained(None, b"{}", stdout=out, run=run), 0)
        self.assertEqual(out.getvalue(), b"")
        run.assert_not_called()

    def test_stdin_stdout_and_exit_status_pass_through(self):
        out = io.BytesIO()
        completed = subprocess.CompletedProcess(["sh"], 3, stdout=b"line\n")
        run = mock.Mock(return_value=completed)
        code = bridge.run_chained("my-status", b'{"a":1}', stdout=out, run=run)
        self.assertEqual(code, 3)
        self.assertEqual(out.getvalue(), b"line\n")
        argv, kwargs = run.call_args
        self.assertEqual(argv[0][-1], "my-status")
        self.assertEqual(kwargs["input"], b'{"a":1}')
        self.assertEqual(kwargs["timeout"], bridge.CHAINED_TIMEOUT_S)

    def test_failed_or_timed_out_command_still_exits_zero(self):
        for error in (OSError("no shell"),
                      subprocess.TimeoutExpired("x", 10)):
            with self.subTest(error):
                out = io.BytesIO()
                run = mock.Mock(side_effect=error)
                self.assertEqual(
                    bridge.run_chained("slow", b"", stdout=out, run=run), 0)
                self.assertEqual(out.getvalue(), b"")

    @unittest.skipIf(sys.platform == "win32", "POSIX shell")
    def test_real_shell_round_trip(self):
        out = io.BytesIO()
        code = bridge.run_chained("cat; exit 4", b"payload", stdout=out)
        self.assertEqual(code, 4)
        self.assertEqual(out.getvalue(), b"payload")


class MainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name) / "state"
        self.config_dir = Path(self.tmp.name) / "claude"

    def test_records_then_runs_the_chained_command(self):
        self.dir.mkdir()
        key = bridge.config_dir_key(self.config_dir)
        (self.dir / bridge.CONFIG_NAME).write_text(json.dumps(
            {"v": 1, "dirs": {key: {"chained_command": "cat"}}}))
        out = io.BytesIO()
        raw = payload()
        code = bridge.main([], stdin=io.BytesIO(raw), stdout=out,
                           env={"CLAUDE_CONFIG_DIR": str(self.config_dir)},
                           now=NOW, directory=self.dir)
        self.assertEqual(code, 0)
        if sys.platform != "win32":
            self.assertEqual(out.getvalue(), raw)
        document = json.loads((self.dir / bridge.SAMPLE_NAME).read_text())
        self.assertEqual(document["accounts"]["single"]["five_hour"]["pct"],
                         42.0)

    def test_state_dir_argument_selects_the_directory(self):
        out = io.BytesIO()
        code = bridge.main(["--state-dir", str(self.dir)],
                           stdin=io.BytesIO(payload()), stdout=out,
                           env={"CLAUDE_CONFIG_DIR": str(self.config_dir)},
                           now=NOW)
        self.assertEqual(code, 0)
        self.assertTrue((self.dir / bridge.SAMPLE_NAME).is_file())
        self.assertIsNone(bridge._directory_from_argv(["--state-dir"]))
        self.assertIsNone(bridge._directory_from_argv(["--other", "x"]))
        self.assertIsNone(bridge._directory_from_argv(None))

    def test_bridge_prints_nothing_without_a_chained_command(self):
        out = io.BytesIO()
        code = bridge.main([], stdin=io.BytesIO(payload()), stdout=out,
                           env={"CLAUDE_CONFIG_DIR": str(self.config_dir)},
                           now=NOW, directory=self.dir)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue(), b"")

    def test_a_bridge_bug_never_takes_the_status_line_down(self):
        out = io.BytesIO()
        with mock.patch.object(bridge, "record_sample",
                               side_effect=RuntimeError("boom")):
            code = bridge.main([], stdin=io.BytesIO(payload()), stdout=out,
                               env={"CLAUDE_CONFIG_DIR": str(self.config_dir)},
                               now=NOW, directory=self.dir)
        self.assertEqual(code, 0)

    def test_stdin_read_error_is_an_empty_payload(self):
        class Broken(io.RawIOBase):
            def read(self, n=-1):
                raise OSError("closed")

        out = io.BytesIO()
        code = bridge.main([], stdin=Broken(), stdout=out,
                           env={"CLAUDE_CONFIG_DIR": str(self.config_dir)},
                           now=NOW, directory=self.dir)
        self.assertEqual(code, 0)
        self.assertFalse((self.dir / bridge.SAMPLE_NAME).exists())


if __name__ == "__main__":
    unittest.main()
