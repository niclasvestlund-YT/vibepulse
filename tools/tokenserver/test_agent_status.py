import json
import os
import tempfile
import threading
import unittest
from pathlib import Path

from tools.tokenserver import tokenserver
from tools.tokenserver.agent_status import (
    Event,
    AgentStatusStore,
    AgentStatusService,
    JsonlTailer,
    classify_claude,
    classify_codex,
    sanitize_project,
    stable_event_id,
)


def claude_event(entry_type, stop_reason=None, tool_name=None, tool_input=None):
    content = []
    if tool_name is not None:
        content.append({"type": "tool_use", "name": tool_name,
                        "input": tool_input or {}})
    return {
        "type": entry_type,
        "sessionId": "session-1",
        "uuid": "event-1",
        "cwd": "/Users/test/Torget",
        "timestamp": "2026-08-06T10:00:00Z",
        "message": {"stop_reason": stop_reason, "content": content},
    }


class ClassificationTests(unittest.TestCase):
    def test_claude_user_starts_work(self):
        event = classify_claude(claude_event("user"))

        self.assertEqual(event, Event(
            state="working",
            activity="thinking",
            task_id="session-1:event-1",
            source_id="event-1",
            project="Torget",
        ))

    def test_claude_end_turn_waits_but_does_not_finish(self):
        event = classify_claude(claude_event("assistant", stop_reason="end_turn"))

        self.assertEqual(event.state, "waiting")
        self.assertIsNone(event.activity)
        self.assertNotEqual(event.state, "done")

    def test_claude_success_result_finishes(self):
        entry = claude_event("result")
        entry["subtype"] = "success"

        event = classify_claude(entry)

        self.assertEqual(event.state, "done")
        self.assertIsNone(event.activity)

    def test_claude_success_with_explicit_error_does_not_false_finish(self):
        entry = claude_event("result")
        entry["subtype"] = "success"
        entry["error"] = {}

        event = classify_claude(entry)

        self.assertEqual(event.state, "error")

    def test_claude_ask_user_question_waits_for_input(self):
        event = classify_claude(claude_event(
            "assistant", tool_name="AskUserQuestion"))

        self.assertEqual((event.state, event.activity),
                         ("waiting", "waiting_input"))

    def test_claude_permission_tool_waits_for_approval(self):
        event = classify_claude(claude_event(
            "assistant", tool_name="RequestPermission"))

        self.assertEqual((event.state, event.activity),
                         ("waiting", "waiting_approval"))

    def test_claude_permission_status_waits_for_approval(self):
        entry = claude_event("system")
        entry["subtype"] = "permission_denied"

        event = classify_claude(entry)

        self.assertEqual((event.state, event.activity),
                         ("waiting", "waiting_approval"))

    def test_claude_editing_tools(self):
        for tool_name in ("Edit", "Write", "apply_patch"):
            with self.subTest(tool_name=tool_name):
                event = classify_claude(claude_event(
                    "assistant", tool_name=tool_name))
                self.assertEqual((event.state, event.activity),
                                 ("working", "editing"))

    def test_claude_read_tool(self):
        event = classify_claude(claude_event("assistant", tool_name="Read"))

        self.assertEqual((event.state, event.activity),
                         ("working", "reading"))

    def test_claude_search_tools(self):
        for tool_name in ("Glob", "Grep"):
            with self.subTest(tool_name=tool_name):
                event = classify_claude(claude_event(
                    "assistant", tool_name=tool_name))
                self.assertEqual((event.state, event.activity),
                                 ("working", "searching"))

    def test_claude_test_command(self):
        event = classify_claude(claude_event(
            "assistant", tool_name="Bash",
            tool_input={"command": "./test/run.sh"}))

        self.assertEqual((event.state, event.activity),
                         ("working", "testing"))

    def test_claude_build_command(self):
        event = classify_claude(claude_event(
            "assistant", tool_name="Bash",
            tool_input={"command": "cmake --build build"}))

        self.assertEqual((event.state, event.activity),
                         ("working", "building"))

    def test_claude_ordinary_command(self):
        event = classify_claude(claude_event(
            "assistant", tool_name="Bash",
            tool_input={"command": "git status --short"}))

        self.assertEqual((event.state, event.activity),
                         ("working", "running"))

    def test_claude_event_never_retains_a_command(self):
        secret_command = "printf private-prompt-and-command"
        event = classify_claude(claude_event(
            "assistant", tool_name="Bash",
            tool_input={"command": secret_command}))

        self.assertNotIn(secret_command, repr(event))

    def test_codex_task_started_uses_turn_as_task_identity(self):
        event = classify_codex({
            "type": "event_msg",
            "timestamp": "2026-08-06T10:00:00Z",
            "payload": {"type": "task_started", "turn_id": "turn-7"},
        })

        self.assertEqual(event, Event(
            state="working",
            activity="thinking",
            task_id="turn-7",
            source_id="turn-7",
            project=None,
        ))

    def test_codex_task_complete_finishes_same_task(self):
        event = classify_codex({
            "type": "event_msg",
            "timestamp": "2026-08-06T10:00:01Z",
            "payload": {"type": "task_complete", "turn_id": "turn-7"},
        })

        self.assertEqual((event.state, event.activity, event.task_id),
                         ("done", None, "turn-7"))

    def test_codex_task_complete_with_explicit_error_does_not_false_finish(self):
        event = classify_codex({
            "type": "event_msg",
            "payload": {
                "type": "task_complete",
                "turn_id": "turn-7",
                "error": {},
            },
        })

        self.assertEqual(event.state, "error")

    def test_malformed_and_unrelated_events_are_ignored(self):
        malformed_content = claude_event("assistant")
        malformed_content["message"]["content"] = [{"unexpected": True}]
        cases = (
            None,
            {},
            {"type": "assistant", "message": "not-a-dict"},
            {"type": "attachment", "sessionId": "s", "uuid": "u"},
            malformed_content,
        )
        for entry in cases:
            with self.subTest(entry=entry):
                self.assertIsNone(classify_claude(entry))

        self.assertIsNone(classify_codex({
            "type": "response_item", "payload": {"type": "message"},
        }))
        self.assertIsNone(classify_codex({
            "type": "event_msg", "payload": {"type": "task_started"},
        }))

    def test_project_sanitization_removes_controls_and_caps_characters(self):
        self.assertEqual(sanitize_project("Tor\x00get-med-ett-långt-namn"),
                         "Torget-med-ett-l")
        self.assertIsNone(sanitize_project(None))


class StableEventIdTests(unittest.TestCase):
    def test_stable_event_id_is_deterministic_and_sensitive_to_contract_fields(self):
        event = Event("working", "thinking", "task", "source", "project")

        first = stable_event_id("claude", event)

        self.assertEqual(first, stable_event_id("claude", event))
        self.assertEqual(len(first), 32)
        self.assertNotEqual(first, stable_event_id("codex", event))
        self.assertNotEqual(first, stable_event_id(
            "claude", Event("done", None, "task", "source", "project")))
        self.assertNotEqual(first, stable_event_id(
            "claude", Event("working", "thinking", "other", "source", "project")))
        self.assertNotEqual(first, stable_event_id(
            "claude", Event("working", "thinking", "task", "other", "project")))


class StoreTests(unittest.TestCase):
    def test_initial_snapshot_has_two_idle_agents_and_exact_shape(self):
        store = AgentStatusStore(now=lambda: 10.0)

        snapshot = store.snapshot()

        self.assertEqual(set(snapshot), {"v", "seq", "agents"})
        self.assertEqual(snapshot["v"], 1)
        self.assertEqual(snapshot["seq"], 0)
        self.assertEqual(set(snapshot["agents"]), {"claude", "codex"})
        for agent in snapshot["agents"].values():
            self.assertEqual(set(agent), {
                "task_id", "event_id", "state", "project", "activity",
                "updated_ms",
            })
            self.assertEqual(agent["state"], "idle")

    def test_apply_increments_only_for_changed_public_content(self):
        now = [10.0]
        store = AgentStatusStore(now=lambda: now[0])
        event = Event("working", "thinking", "task", "source", "Torget")

        self.assertTrue(store.apply("claude", event, observed_at=2.0))
        first = store.snapshot()
        now[0] = 20.0
        self.assertFalse(store.apply("claude", event, observed_at=15.0))
        second = store.snapshot()

        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 1)
        self.assertEqual(second["agents"]["claude"]["updated_ms"], 5000)

    def test_activity_task_event_and_project_changes_increment_sequence(self):
        store = AgentStatusStore(now=lambda: 0.0)
        events = (
            Event("working", "thinking", "task", "source", "Torget"),
            Event("working", "reading", "task", "source", "Torget"),
            Event("working", "reading", "task-2", "source", "Torget"),
            Event("working", "reading", "task-2", "source-2", "Torget"),
            Event("working", "reading", "task-2", "source-2", "Other"),
        )

        for event in events:
            self.assertTrue(store.apply("claude", event))

        self.assertEqual(store.snapshot()["seq"], len(events))

    def test_invalid_provider_state_and_activity_are_rejected(self):
        store = AgentStatusStore()

        with self.assertRaises(ValueError):
            store.apply("other", Event("working", None, "t", "s", None))
        with self.assertRaises(ValueError):
            store.apply("claude", Event("bogus", None, "t", "s", None))
        with self.assertRaises(ValueError):
            store.apply("claude", Event("working", "bogus", "t", "s", None))

    def test_old_working_entry_reads_as_unknown_without_mutation_or_seq_change(self):
        now = [121.0]
        store = AgentStatusStore(now=lambda: now[0])
        store.apply("claude", Event(
            "working", "testing", "task", "source", "Torget"),
            observed_at=0.0)

        first = store.snapshot()
        second = store.snapshot()

        self.assertEqual(first["agents"]["claude"]["state"], "unknown")
        self.assertIsNone(first["agents"]["claude"]["activity"])
        self.assertEqual(first["seq"], 1)
        self.assertEqual(second["seq"], 1)

    def test_snapshot_is_a_deep_copy(self):
        store = AgentStatusStore(now=lambda: 0.0)
        store.apply("claude", Event(
            "working", "reading", "task", "source", "Torget"))

        snapshot = store.snapshot()
        snapshot["agents"]["claude"]["state"] = "done"

        self.assertEqual(store.snapshot()["agents"]["claude"]["state"],
                         "working")

    def test_updated_ms_is_nonnegative_integer_and_clamped(self):
        now = [-1.0]
        store = AgentStatusStore(now=lambda: now[0])
        store.apply("claude", Event(
            "waiting", None, "task", "source", None), observed_at=0.0)
        self.assertEqual(store.snapshot()["agents"]["claude"]["updated_ms"], 0)

        now[0] = 10 ** 20
        age = store.snapshot()["agents"]["claude"]["updated_ms"]
        self.assertIs(type(age), int)
        self.assertEqual(age, 0xFFFFFFFF)

    def test_snapshot_exposes_only_allowed_sanitized_fields(self):
        secret = "private prompt and command"
        store = AgentStatusStore(now=lambda: 0.0)
        event = classify_claude(claude_event(
            "assistant", tool_name="Bash", tool_input={"command": secret}))
        store.apply("claude", event)

        snapshot = store.snapshot()

        self.assertNotIn(secret, repr(snapshot))
        self.assertNotIn("source_id", repr(snapshot))
        self.assertEqual(set(snapshot["agents"]["claude"]), {
            "task_id", "event_id", "state", "project", "activity",
            "updated_ms",
        })


class JsonlTailerTests(unittest.TestCase):
    def test_reads_only_complete_lines_then_finishes_partial_append(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_bytes(b'{"first":1}\n{"half":')
            tailer = JsonlTailer()

            self.assertEqual(tailer.read(path), [{"first": 1}])

            with path.open("ab") as stream:
                stream.write(b'true}\n{"second":2}\n')
            self.assertEqual(tailer.read(path), [
                {"half": True},
                {"second": 2},
            ])
            self.assertEqual(tailer.read(path), [])

    def test_invalid_complete_line_is_skipped_before_later_valid_line(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_bytes(b'not-json\n{"valid":true}\n')

            self.assertEqual(JsonlTailer().read(path), [{"valid": True}])

    def test_truncation_resets_only_that_files_offset_and_buffer(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            first = Path(temp_dir) / "first.jsonl"
            second = Path(temp_dir) / "second.jsonl"
            first.write_bytes(b'{"old_partial_with_padding":')
            second.write_bytes(b'{"kept":')
            tailer = JsonlTailer()
            self.assertEqual(tailer.read(first), [])
            self.assertEqual(tailer.read(second), [])

            first.write_bytes(b'{"new":true}\n')
            self.assertEqual(tailer.read(first), [{"new": True}])
            with second.open("ab") as stream:
                stream.write(b'true}\n')
            self.assertEqual(tailer.read(second), [{"kept": True}])

    def test_same_inode_truncate_and_regrow_resets_offset(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_bytes(b'{"old":1}\n')
            original_inode = path.stat().st_ino
            tailer = JsonlTailer()
            self.assertEqual(tailer.read(path), [{"old": 1}])

            path.write_bytes(b'{"replacement":true}\n')
            self.assertEqual(path.stat().st_ino, original_inode)

            self.assertEqual(tailer.read(path), [{"replacement": True}])

    def test_complete_line_content_is_not_retained_for_rewrite_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            secret = "synthetic-private-message-content"
            path.write_text(json.dumps({"message": secret}) + "\n",
                            encoding="utf-8")
            tailer = JsonlTailer()

            tailer.read(path)

            self.assertNotIn(secret, repr(tailer._files))

    def test_file_disappearance_is_tolerated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_text('{"event":1}\n', encoding="utf-8")
            tailer = JsonlTailer()
            self.assertEqual(tailer.read(path), [{"event": 1}])

            path.unlink()

            self.assertEqual(tailer.read(path), [])

    def test_temporarily_missing_file_resumes_without_replay(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            moved = Path(temp_dir) / "session.moved"
            path.write_text('{"event":1}\n', encoding="utf-8")
            tailer = JsonlTailer()
            self.assertEqual(tailer.read(path), [{"event": 1}])

            path.rename(moved)
            self.assertEqual(tailer.read(path), [])
            moved.rename(path)

            self.assertEqual(tailer.read(path), [])


class AgentStatusServiceTests(unittest.TestCase):
    @staticmethod
    def _write_line(path, event):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(event) + "\n", encoding="utf-8")

    def test_poll_discovers_claude_and_codex_and_counts_changes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            claude_path = root / "claude" / "project" / "session.jsonl"
            codex_path = (root / "codex" / "2026" / "08" / "06" /
                          "rollout-test.jsonl")
            self._write_line(claude_path, claude_event("user"))
            self._write_line(codex_path, {
                "type": "event_msg",
                "payload": {"type": "task_started", "turn_id": "turn-1"},
            })
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 10.0)

            self.assertEqual(service.poll_once(), 2)
            snapshot = service.snapshot()
            self.assertEqual(snapshot["agents"]["claude"]["state"],
                             "working")
            self.assertEqual(snapshot["agents"]["codex"]["state"],
                             "working")
            self.assertEqual(service.poll_once(), 0)

    def test_appended_unchanged_classified_event_counts_as_no_change(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "claude" / "session.jsonl"
            event = claude_event("user")
            self._write_line(path, event)
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 10.0)
            self.assertEqual(service.poll_once(), 1)

            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event) + "\n")

            self.assertEqual(service.poll_once(), 0)
            self.assertEqual(service.snapshot()["seq"], 1)

    def test_newest_file_rotation_starts_new_file_without_replaying_old(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "claude" / "old.jsonl"
            started = claude_event("user")
            self._write_line(old_path, started)
            os.utime(old_path, (1, 1))
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 10.0)
            self.assertEqual(service.poll_once(), 1)

            new_path = root / "claude" / "new.jsonl"
            completed = claude_event("result")
            completed["uuid"] = "event-2"
            completed["subtype"] = "success"
            self._write_line(new_path, completed)
            os.utime(new_path, (2, 2))

            self.assertEqual(service.poll_once(), 1)
            snapshot = service.snapshot()
            self.assertEqual(snapshot["agents"]["claude"]["state"], "done")
            self.assertEqual(snapshot["agents"]["claude"]["task_id"],
                             "session-1:event-2")
            self.assertEqual(service.poll_once(), 0)
            self.assertEqual(service.snapshot()["seq"], 2)

    def test_missing_roots_and_start_stop_are_idempotent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = AgentStatusService(root / "missing-claude",
                                         root / "missing-codex")
            self.assertEqual(service.poll_once(), 0)

            service.start()
            thread = service._thread
            service.start()
            self.assertIs(service._thread, thread)
            self.assertTrue(thread.daemon)
            service.stop()
            service.stop()

            self.assertFalse(thread.is_alive())

    def test_stop_signal_cannot_be_cleared_by_concurrent_start(self):
        class PausingClearEvent(threading.Event):
            def __init__(self):
                super().__init__()
                self.clear_entered = threading.Event()
                self.allow_clear = threading.Event()
                self.set_entered = threading.Event()
                self._pause_once = True

            def clear(self):
                if self._pause_once:
                    self._pause_once = False
                    self.clear_entered.set()
                    if not self.allow_clear.wait(timeout=2):
                        raise RuntimeError("test did not release clear")
                super().clear()

            def set(self):
                super().set()
                self.set_entered.set()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = AgentStatusService(root / "claude", root / "codex")
            coordinated_stop = PausingClearEvent()
            service._stop = coordinated_stop
            start_thread = threading.Thread(target=service.start)
            start_thread.start()
            self.assertTrue(coordinated_stop.clear_entered.wait(timeout=1))

            stop_invoked = threading.Event()

            def stop_service():
                stop_invoked.set()
                service.stop()

            stop_thread = threading.Thread(target=stop_service)
            stop_thread.start()
            self.assertTrue(stop_invoked.wait(timeout=1))
            coordinated_stop.set_entered.wait(timeout=0.1)
            coordinated_stop.allow_clear.set()
            start_thread.join(timeout=3)
            stop_thread.join(timeout=3)

            self.assertFalse(start_thread.is_alive())
            self.assertFalse(stop_thread.is_alive())
            poller = service._thread
            poller_was_alive = poller is not None and poller.is_alive()
            service.stop()
            self.assertFalse(poller_was_alive)

    def test_background_thread_survives_transient_poll_failure(self):
        class FlakyService(AgentStatusService):
            def __init__(self, root):
                super().__init__(root / "claude", root / "codex")
                self.poll_count = 0
                self.recovered = threading.Event()

            def poll_once(self):
                self.poll_count += 1
                if self.poll_count == 1:
                    raise OSError("synthetic transient failure")
                self.recovered.set()
                return 0

        with tempfile.TemporaryDirectory() as temp_dir:
            service = FlakyService(Path(temp_dir))
            service.start()
            try:
                self.assertTrue(service.recovered.wait(timeout=2))
                self.assertTrue(service._thread.is_alive())
            finally:
                service.stop()

    def test_snapshot_does_not_require_roots_to_exist(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            projects = root / "claude"
            projects.mkdir()
            service = AgentStatusService(projects, root / "codex")
            projects.rmdir()

            snapshot = service.snapshot()

            self.assertEqual(snapshot["v"], 1)
            self.assertEqual(set(snapshot["agents"]), {"claude", "codex"})


class HandlerTests(unittest.TestCase):
    @staticmethod
    def _request(path, agent_status=None):
        handler = object.__new__(tokenserver.Handler)
        handler.path = path
        handler.agent_status = agent_status
        sent = []
        handler._send = lambda code, payload: sent.append((code, payload))
        handler.do_GET()
        return sent

    def test_agent_status_endpoint_uses_service_snapshot(self):
        expected = {
            "v": 1,
            "seq": 0,
            "agents": {"claude": {}, "codex": {}},
        }

        class SnapshotOnly:
            def __init__(self):
                self.calls = 0

            def snapshot(self):
                self.calls += 1
                return expected

        service = SnapshotOnly()

        sent = self._request("/api/agent-status", service)

        self.assertEqual(sent, [(200, expected)])
        self.assertEqual(service.calls, 1)

    def test_root_lists_existing_and_agent_status_endpoints(self):
        sent = self._request("/")

        self.assertEqual(sent[0][0], 200)
        payload = sent[0][1]
        self.assertEqual(payload["endpoint"], "/api/tokens")
        self.assertIn("/api/tokens", payload["endpoints"])
        self.assertIn("/api/agent-status", payload["endpoints"])


if __name__ == "__main__":
    unittest.main()
