import hashlib
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver import agent_status, tokenserver
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


def claude_task_id(session_id, source_id):
    pair = json.dumps([session_id, source_id], ensure_ascii=True,
                      separators=(",", ":"))
    return hashlib.sha256(pair.encode()).hexdigest()


class ClassificationTests(unittest.TestCase):
    def test_claude_user_starts_work(self):
        event = classify_claude(claude_event("user"))

        self.assertEqual(event, Event(
            state="working",
            activity="thinking",
            task_id=claude_task_id("session-1", "event-1"),
            source_id="event-1",
            project="Torget",
        ))

    def test_claude_task_identity_is_uuid_safe_and_sensitive_to_both_ids(self):
        session_id = "123e4567-e89b-12d3-a456-426614174000"
        source_id = "123e4567-e89b-12d3-a456-426614174001"
        entry = claude_event("user")
        entry["sessionId"] = session_id
        entry["uuid"] = source_id

        event = classify_claude(entry)
        changed_session = dict(entry, sessionId=session_id[:-1] + "2")
        changed_source = dict(entry, uuid=source_id[:-1] + "3")

        self.assertEqual(len(event.task_id.encode("utf-8")), 64)
        self.assertEqual(
            event.task_id, claude_task_id(session_id, source_id))
        self.assertNotEqual(
            event.task_id, classify_claude(changed_session).task_id)
        self.assertNotEqual(
            event.task_id, classify_claude(changed_source).task_id)

    def test_claude_task_identity_pair_encoding_is_unambiguous(self):
        first = claude_event("user")
        first["sessionId"] = "session|part"
        first["uuid"] = "event"
        second = claude_event("user")
        second["sessionId"] = "session"
        second["uuid"] = "part|event"

        self.assertNotEqual(classify_claude(first).task_id,
                            classify_claude(second).task_id)

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

    def test_codex_overlong_turn_identity_is_hashed_without_collision_truncation(self):
        first_id = "å" * 40
        second_id = "å" * 39 + "ä"

        first = classify_codex({
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": first_id},
        })
        second = classify_codex({
            "type": "event_msg",
            "payload": {"type": "task_started", "turn_id": second_id},
        })

        self.assertEqual(first.task_id,
                         hashlib.sha256(first_id.encode()).hexdigest())
        self.assertEqual(len(first.task_id.encode("utf-8")), 64)
        self.assertNotEqual(first.task_id, second.task_id)

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

    def test_project_sanitization_caps_utf8_bytes_without_splitting(self):
        self.assertEqual(sanitize_project("å" * 16), "å" * 8)
        self.assertEqual(sanitize_project("Torget-" + "å" * 10),
                         "Torget-" + "å" * 4)
        self.assertLessEqual(
            len(sanitize_project("abc" + "😀" * 10).encode("utf-8")), 16)


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

    def test_store_hashes_any_overlong_task_id_before_output(self):
        store = AgentStatusStore(now=lambda: 0.0)
        long_task_id = "😀" * 17
        store.apply("claude", Event(
            "working", "thinking", long_task_id, "source", "Torget"))

        output_id = store.snapshot()["agents"]["claude"]["task_id"]

        self.assertEqual(output_id,
                         hashlib.sha256(long_task_id.encode()).hexdigest())
        self.assertLessEqual(len(output_id.encode("utf-8")), 64)

    def test_surrogate_ids_are_hashed_without_crashing_or_leaking(self):
        claude = claude_event("user")
        claude["uuid"] = "bad\ud800"
        cases = (
            ("claude", classify_claude(claude)),
            ("codex", classify_codex({
                "type": "event_msg",
                "payload": {
                    "type": "task_started",
                    "turn_id": "bad\ud800",
                },
            })),
        )
        store = AgentStatusStore(now=lambda: 0.0)

        for provider, event in cases:
            with self.subTest(provider=provider):
                self.assertTrue(store.apply(provider, event))
                output = store.snapshot()["agents"][provider]
                self.assertLessEqual(len(output["task_id"].encode("utf-8")),
                                     64)
                self.assertNotIn("\ud800", output["event_id"])


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

    def test_malformed_utf8_lines_are_skipped_before_valid_record(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_bytes(
                b'{"private":"\xff"}\n'
                b'{"private":"\xfe"}\n'
                b'{"valid":true}\n')

            self.assertEqual(JsonlTailer().read(path), [{"valid": True}])

    def test_oversized_unterminated_line_is_bounded_and_later_record_resumes(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            secret = b"synthetic-oversized-private-prompt"
            path.write_bytes(
                b'{"prompt":"' + secret +
                b"x" * (3 * 1024 * 1024))
            tailer = JsonlTailer()

            self.assertEqual(tailer.read(path), [])
            state = tailer._files[path]
            self.assertLessEqual(len(state["partial"]),
                                 tailer._MAX_LINE_BYTES)
            self.assertNotIn(secret.decode(), repr(state))
            self.assertLessEqual(state["offset"], tailer._READ_BYTES_PER_POLL)

            with path.open("ab") as stream:
                stream.write(b'"}\n{"valid":true}\n')
            records = []
            for _ in range(8):
                records.extend(tailer.read(path))
                if records:
                    break

            self.assertEqual(records, [{"valid": True}])

    def test_record_budget_drains_large_backlog_across_polls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            total = JsonlTailer._MAX_RECORDS_PER_POLL + 5
            path.write_bytes(b"".join(
                json.dumps({"record": index}).encode() + b"\n"
                for index in range(total)))
            tailer = JsonlTailer()

            first = tailer.read(path)
            second = tailer.read(path)

            self.assertEqual(len(first), tailer._MAX_RECORDS_PER_POLL)
            self.assertEqual(first + second,
                             [{"record": index} for index in range(total)])

    def test_reconciliation_bounds_cold_states_and_drops_cold_partial(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tailer = JsonlTailer()
            paths = []
            identities = {}
            for index in range(JsonlTailer._MAX_TRACKED_IDENTITIES + 5):
                path = root / f"session-{index:02d}.jsonl"
                path.write_text(
                    '{"private":"cold-secret-' + str(index),
                    encoding="utf-8")
                paths.append(path)
                tailer.read(path)
                stat = path.stat()
                identities[path] = (stat.st_dev, stat.st_ino)

            active = paths[-JsonlTailer._MAX_ACTIVE_IDENTITIES:]
            tailer.retain_paths(identities, active_paths=active)

            self.assertLessEqual(len(tailer._identities),
                                 tailer._MAX_TRACKED_IDENTITIES)
            self.assertLessEqual(len(tailer._files),
                                 tailer._MAX_TRACKED_IDENTITIES)
            for path in paths[:-JsonlTailer._MAX_ACTIVE_IDENTITIES]:
                state = tailer._identities.get(identities[path])
                if state is not None:
                    self.assertEqual(state["partial"], b"")
                    self.assertNotIn("cold-secret", repr(state))

    def test_inode_churn_enforces_identity_cap_before_next_discovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            tailer = JsonlTailer()
            paths = [root / f"active-{index:02d}.jsonl"
                     for index in range(12)]
            for index, path in enumerate(paths):
                path.write_text(
                    '{"private":"base-secret-' + str(index),
                    encoding="utf-8")
                tailer.read(path)

            for wave in range(4):
                for index, path in enumerate(paths):
                    replacement = root / f"replacement-{wave}-{index}.tmp"
                    replacement.write_text(
                        '{"private":"wave-' + str(wave) + "-secret-" +
                        str(index), encoding="utf-8")
                    os.replace(replacement, path)
                    tailer.read(path)

            self.assertLessEqual(len(tailer._identities),
                                 tailer._MAX_TRACKED_IDENTITIES)
            self.assertLessEqual(len(tailer._files), len(paths))
            self.assertNotIn("base-secret", repr(tailer._identities))

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

    def test_same_inode_rewrite_is_detected_when_final_boundary_is_unchanged(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            original = {"first": "A" * 80}
            replacement = {"first": "B" * 80}
            unchanged_tail = {"tail": "Z" * 100}
            path.write_text(
                json.dumps(original) + "\n" +
                json.dumps(unchanged_tail) + "\n",
                encoding="utf-8",
            )
            original_inode = path.stat().st_ino
            tailer = JsonlTailer()
            self.assertEqual(tailer.read(path), [original, unchanged_tail])

            path.write_text(
                json.dumps(replacement) + "\n" +
                json.dumps(unchanged_tail) + "\n",
                encoding="utf-8",
            )
            self.assertEqual(path.stat().st_ino, original_inode)

            self.assertEqual(tailer.read(path), [replacement, unchanged_tail])
            self.assertEqual(tailer.read(path), [])

    def test_rewrite_during_prefix_verification_does_not_poison_fast_path(self):
        class RewriteAfterDigestTailer(JsonlTailer):
            def __init__(self, path, replacement):
                super().__init__()
                self.path = path
                self.replacement = replacement
                self.rewrite_after_digest = False

            def _digest_prefix(self, stream, length):
                digest = super()._digest_prefix(stream, length)
                if self.rewrite_after_digest:
                    self.rewrite_after_digest = False
                    self.path.write_text(
                        json.dumps(self.replacement) + "\n",
                        encoding="utf-8")
                return digest

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            original = {"old": 1}
            replacement = {"new": 2}
            path.write_text(json.dumps(original) + "\n", encoding="utf-8")
            tailer = RewriteAfterDigestTailer(path, replacement)
            self.assertEqual(tailer.read(path), [original])
            stat = path.stat()
            os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            tailer.rewrite_after_digest = True

            self.assertEqual(tailer.read(path), [])

            self.assertEqual(tailer.read(path), [replacement])
            self.assertEqual(tailer.read(path), [])

    def test_complete_line_content_is_not_retained_for_rewrite_detection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            secret = "synthetic-private-message-content"
            path.write_text(json.dumps({"message": secret}) + "\n",
                            encoding="utf-8")
            tailer = JsonlTailer()

            tailer.read(path)

            self.assertNotIn(secret, repr(tailer._files))

    def test_full_prefix_verification_uses_bounded_cadence(self):
        class CountingTailer(JsonlTailer):
            def __init__(self, now):
                super().__init__(now=now)
                self.digest_calls = 0

            def _digest_prefix(self, stream, length):
                self.digest_calls += 1
                return super()._digest_prefix(stream, length)

        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            path.write_bytes(b'{"first":1}\n')
            now = [0.0]
            tailer = CountingTailer(now=lambda: now[0])
            self.assertEqual(tailer.read(path), [{"first": 1}])
            baseline = tailer.digest_calls

            now[0] = 0.5
            with path.open("ab") as stream:
                stream.write(b'{"second":2}\n')
            self.assertEqual(tailer.read(path), [{"second": 2}])
            self.assertEqual(tailer.digest_calls, baseline)

            now[0] = tailer._VERIFY_INTERVAL_S
            self.assertEqual(tailer.read(path), [])
            self.assertEqual(tailer.digest_calls, baseline + 1)

    def test_large_prefix_verification_is_budgeted_and_replays_mismatch_once(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "session.jsonl"
            padding_size = 2 * JsonlTailer._VERIFY_BYTES_PER_POLL + 200_000
            original = (b'{"first":1}\n' + b"x" * padding_size +
                        b'\n{"last":2}\n')
            path.write_bytes(original)
            now = [0.0]
            tailer = JsonlTailer(now=lambda: now[0])
            initial = []
            for _ in range(8):
                initial.extend(tailer.read(path))
                if tailer._files[path]["offset"] == len(original):
                    break
            self.assertEqual(initial, [{"first": 1}, {"last": 2}])

            replacement = bytearray(original)
            replacement[JsonlTailer._VERIFY_BYTES_PER_POLL + 100_000] = ord("y")
            path.write_bytes(replacement)
            now[0] = tailer._VERIFY_INTERVAL_S

            self.assertEqual(tailer.read(path), [])
            state = tailer._files[path]
            self.assertEqual(state["verify_offset"],
                             tailer._VERIFY_BYTES_PER_POLL)

            replayed = []
            for _ in range(8):
                replayed.extend(tailer.read(path))
                state = tailer._files[path]
                if (state["verify_offset"] is None and
                        state["offset"] == len(replacement)):
                    break

            self.assertEqual(replayed, [{"first": 1}, {"last": 2}])
            self.assertEqual(tailer.read(path), [])

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

    def test_replacement_path_read_before_rotated_inode_does_not_lose_state(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            current = Path(temp_dir) / "current.jsonl"
            rotated = Path(temp_dir) / "rotated.jsonl"
            old = {"old": 1}
            new = {"newfile": 1}
            current.write_text(json.dumps(old) + "\n", encoding="utf-8")
            tailer = JsonlTailer()
            self.assertEqual(tailer.read(current), [old])

            current.rename(rotated)
            current.write_text(json.dumps(new) + "\n", encoding="utf-8")

            self.assertEqual(tailer.read(current), [new])
            self.assertEqual(tailer.read(rotated), [])


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
            claude = claude_event("user")
            self._write_line(claude_path, claude)
            self._write_line(codex_path, {
                "type": "event_msg",
                "timestamp": claude["timestamp"],
                "payload": {"type": "task_started", "turn_id": "turn-1"},
            })
            wall_time = AgentStatusService._event_wall_time(claude)
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 10.0,
                                         _wall_time=lambda: wall_time)

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

    def test_fast_active_poll_does_not_repeat_recursive_discovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "claude" / "session.jsonl"
            first = claude_event("user")
            self._write_line(path, first)
            now = [0.0]
            wall_time = AgentStatusService._event_wall_time(first)
            service = AgentStatusService(
                root / "claude", root / "codex", now=lambda: now[0],
                _wall_time=lambda: wall_time)
            original_discover = service._discover_paths
            discovery_calls = []

            def counted_discovery(discovery_root, pattern):
                discovery_calls.append((discovery_root, pattern))
                return original_discover(discovery_root, pattern)

            service._discover_paths = counted_discovery
            self.assertEqual(service.poll_once(), 1)
            self.assertEqual(len(discovery_calls), 2)

            second = claude_event("assistant", stop_reason="end_turn")
            second["uuid"] = "event-2"
            with path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(second) + "\n")
            now[0] = 0.5
            self.assertEqual(service.poll_once(), 1)
            self.assertEqual(len(discovery_calls), 2)

            now[0] = service._DISCOVERY_INTERVAL_S
            self.assertEqual(service.poll_once(), 0)
            self.assertEqual(len(discovery_calls), 4)

    def test_newest_file_rotation_starts_new_file_without_replaying_old(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "claude" / "old.jsonl"
            started = claude_event("user")
            self._write_line(old_path, started)
            os.utime(old_path, (1, 1))
            now = [10.0]
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: now[0])
            self.assertEqual(service.poll_once(), 1)

            new_path = root / "claude" / "new.jsonl"
            completed = claude_event("result")
            completed["uuid"] = "event-2"
            completed["subtype"] = "success"
            self._write_line(new_path, completed)
            os.utime(new_path, (2, 2))
            now[0] += service._DISCOVERY_INTERVAL_S

            self.assertEqual(service.poll_once(), 1)
            snapshot = service.snapshot()
            self.assertEqual(snapshot["agents"]["claude"]["state"], "done")
            self.assertEqual(snapshot["agents"]["claude"]["task_id"],
                             claude_task_id("session-1", "event-2"))
            self.assertEqual(service.poll_once(), 0)
            self.assertEqual(service.snapshot()["seq"], 2)

    def test_renamed_active_file_transfers_offset_without_replaying_history(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            old_path = root / "claude" / "old.jsonl"
            first = claude_event("user")
            second = claude_event(
                "assistant", tool_name="Bash",
                tool_input={"command": "git status --short"})
            second["uuid"] = "event-2"
            old_path.parent.mkdir(parents=True)
            old_path.write_text(
                json.dumps(first) + "\n" + json.dumps(second) + "\n",
                encoding="utf-8",
            )
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 10.0)
            self.assertEqual(service.poll_once(), 2)

            new_path = old_path.with_name("renamed.jsonl")
            old_path.rename(new_path)
            completed = claude_event("result")
            completed["uuid"] = "event-3"
            completed["subtype"] = "success"
            with new_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(completed) + "\n")

            self.assertEqual(service.poll_once(), 1)
            self.assertEqual(service.snapshot()["seq"], 3)
            self.assertEqual(service.snapshot()["agents"]["claude"]["state"],
                             "done")
            self.assertNotIn(old_path, service._tailer._files)
            self.assertIn(new_path, service._tailer._files)
            self.assertEqual(service.poll_once(), 0)

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

    def test_start_during_stop_join_cannot_launch_replacement_worker(self):
        class JoinPausingThread:
            def __init__(self, thread):
                self.thread = thread
                self.worker_joined = threading.Event()
                self.allow_join_return = threading.Event()

            def is_alive(self):
                return self.thread.is_alive()

            def join(self, timeout=None):
                self.thread.join(timeout=timeout)
                if self.thread.is_alive():
                    return
                self.worker_joined.set()
                if not self.allow_join_return.wait(timeout=2):
                    raise RuntimeError("test did not release join")

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = AgentStatusService(root / "claude", root / "codex")
            service.start()
            original = service._thread
            pausing_thread = JoinPausingThread(original)
            with service._thread_lock:
                service._thread = pausing_thread

            stop_thread = threading.Thread(target=service.stop)
            stop_thread.start()
            self.assertTrue(pausing_thread.worker_joined.wait(timeout=2))

            service.start()
            pausing_thread.allow_join_return.set()
            stop_thread.join(timeout=2)
            self.assertFalse(stop_thread.is_alive())
            survivor = service._thread
            survivor_was_alive = (survivor is not None and
                                  survivor is not pausing_thread and
                                  survivor.is_alive())
            service.stop()

            self.assertFalse(survivor_was_alive)
            service.start()
            restarted = service._thread
            self.assertTrue(restarted.is_alive())
            service.stop()
            self.assertFalse(restarted.is_alive())

    def test_background_thread_survives_transient_poll_failure(self):
        class FlakyService(AgentStatusService):
            def __init__(self, root, diagnostics):
                super().__init__(
                    root / "claude", root / "codex",
                    _diagnostic=diagnostics.append)
                self.poll_count = 0
                self.recovered = threading.Event()

            def poll_once(self):
                self.poll_count += 1
                if self.poll_count == 1:
                    raise OSError("synthetic transient failure")
                self.recovered.set()
                return 0

        with tempfile.TemporaryDirectory() as temp_dir:
            diagnostics = []
            service = FlakyService(Path(temp_dir), diagnostics)
            service.start()
            try:
                self.assertTrue(service.recovered.wait(timeout=2))
                self.assertTrue(service._thread.is_alive())
            finally:
                service.stop()
            self.assertEqual(diagnostics,
                             ["agent-status poll: OSError"])

    def test_record_failures_are_isolated_and_diagnostics_are_sanitized(self):
        class ApplyFailsOnce:
            def __init__(self, delegate):
                self.delegate = delegate

            def apply(self, provider, event, observed_at=None, order_at=None):
                if event.source_id == "bad-apply":
                    raise LookupError("synthetic-private-apply-prompt")
                return self.delegate.apply(
                    provider, event, observed_at, order_at)

            def snapshot(self):
                return self.delegate.snapshot()

        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "claude" / "session.jsonl"
            records = []
            for source_id in (
                    "bad-classify-1", "bad-classify-2", "bad-apply",
                    "valid-final"):
                record = claude_event("user")
                record["uuid"] = source_id
                records.append(record)
            path.parent.mkdir(parents=True)
            path.write_text(
                "".join(json.dumps(record) + "\n" for record in records),
                encoding="utf-8")
            diagnostics = []
            wall_time = AgentStatusService._event_wall_time(records[0])
            service = AgentStatusService(
                root / "claude", root / "codex", now=lambda: 0.0,
                _wall_time=lambda: wall_time,
                _diagnostic=diagnostics.append)
            service._store = ApplyFailsOnce(service._store)
            original_classifier = agent_status.classify_claude

            def classify_with_failures(record):
                if record.get("uuid", "").startswith("bad-classify"):
                    raise RuntimeError("synthetic-private-classifier-command")
                return original_classifier(record)

            with mock.patch.object(
                    agent_status, "classify_claude",
                    side_effect=classify_with_failures):
                self.assertEqual(service.poll_once(), 1)

            snapshot = service.snapshot()["agents"]["claude"]
            self.assertEqual(snapshot["state"], "working")
            self.assertEqual(snapshot["task_id"],
                             claude_task_id("session-1", "valid-final"))
            self.assertEqual(diagnostics, [
                "agent-status classify: RuntimeError",
                "agent-status apply: LookupError",
            ])
            serialized = " ".join(diagnostics)
            self.assertNotIn("synthetic-private", serialized)
            self.assertNotIn(str(path), serialized)

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

    def test_startup_replay_uses_old_claude_and_codex_iso_timestamps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            claude_path = root / "claude" / "session.jsonl"
            codex_path = root / "codex" / "rollout-test.jsonl"
            claude = claude_event("user")
            claude["timestamp"] = "2000-01-01T00:00:00Z"
            codex = {
                "type": "event_msg",
                "timestamp": "2000-01-01T00:00:00Z",
                "payload": {"type": "task_started", "turn_id": "turn-old"},
            }
            self._write_line(claude_path, claude)
            self._write_line(codex_path, codex)
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 500.0)
            service._wall_time = lambda: 2_000_000_000.0

            self.assertEqual(service.poll_once(), 2)
            snapshot = service.snapshot()

            for provider in ("claude", "codex"):
                self.assertEqual(snapshot["agents"][provider]["state"],
                                 "unknown")
                self.assertIsNone(snapshot["agents"][provider]["activity"])

    def test_old_file_mtime_is_fallback_when_timestamp_is_missing(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "claude" / "session.jsonl"
            event = claude_event("user")
            del event["timestamp"]
            self._write_line(path, event)
            wall_time = 2_000_000_000.0
            os.utime(path, (wall_time - 121.0, wall_time - 121.0))
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 500.0)
            service._wall_time = lambda: wall_time

            self.assertEqual(service.poll_once(), 1)

            snapshot = service.snapshot()["agents"]["claude"]
            self.assertEqual(snapshot["state"], "unknown")
            self.assertIsNone(snapshot["activity"])

    def test_future_timestamp_falls_back_to_old_file_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "claude" / "session.jsonl"
            event = claude_event("user")
            event["timestamp"] = "2999-01-01T00:00:00Z"
            self._write_line(path, event)
            wall_time = 2_000_000_000.0
            os.utime(path, (wall_time - 121.0, wall_time - 121.0))
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: 500.0,
                                         _wall_time=lambda: wall_time)

            self.assertEqual(service.poll_once(), 1)

            snapshot = service.snapshot()["agents"]["claude"]
            self.assertEqual(snapshot["state"], "unknown")
            self.assertIsNone(snapshot["activity"])

    def test_task_complete_age_uses_completion_before_start_time(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "codex" / "rollout-test.jsonl"
            completion = "2026-08-06T10:00:00Z"
            completed_wall_time = AgentStatusService._event_wall_time(
                {"timestamp": completion})
            self._write_line(path, {
                "type": "event_msg",
                "payload": {
                    "type": "task_complete",
                    "turn_id": "turn-complete",
                    "started_at": "2000-01-01T00:00:00Z",
                    "completed_at": completion,
                },
            })
            service = AgentStatusService(
                root / "claude", root / "codex", now=lambda: 500.0,
                _wall_time=lambda: completed_wall_time + 2.0)

            self.assertEqual(service.poll_once(), 1)

            snapshot = service.snapshot()["agents"]["codex"]
            self.assertEqual(snapshot["state"], "done")
            self.assertEqual(snapshot["updated_ms"], 2000)

    def test_permanently_missing_partial_file_is_pruned_after_bounded_polls(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "claude" / "session.jsonl"
            secret = "synthetic-incomplete-private-prompt"
            path.parent.mkdir(parents=True)
            path.write_text('{"prompt":"' + secret, encoding="utf-8")
            service = AgentStatusService(root / "claude", root / "codex")
            self.assertEqual(service.poll_once(), 0)
            self.assertIn(secret, repr(service._tailer._files))

            path.unlink()
            for _ in range(4):
                self.assertEqual(service.poll_once(), 0)

            self.assertNotIn(secret, repr(service._tailer._files))
            self.assertNotIn(path, service._tailer._files)
            self.assertEqual(service._tailer._identities, {})

    def test_one_missing_service_poll_retains_identity_for_short_recovery(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            path = root / "claude" / "session.jsonl"
            moved = root / "session.moved"
            event = claude_event("user")
            self._write_line(path, event)
            wall_time = AgentStatusService._event_wall_time(event)
            service = AgentStatusService(
                root / "claude", root / "codex", now=lambda: 10.0,
                _wall_time=lambda: wall_time)
            self.assertEqual(service.poll_once(), 1)
            stat = path.stat()
            identity = (stat.st_dev, stat.st_ino)
            offset = service._tailer._identities[identity]["offset"]

            path.rename(moved)
            self.assertEqual(service.poll_once(), 0)

            self.assertIn(identity, service._tailer._identities)
            self.assertEqual(
                service._tailer._identities[identity]["offset"], offset)
            moved.rename(path)
            self.assertEqual(service.poll_once(), 0)

    def test_existing_file_outside_recent_limit_is_not_pruned_or_replayed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            target = root / "claude" / "target.jsonl"
            started = claude_event("user")
            self._write_line(target, started)
            os.utime(target, (1, 1))
            now = [10.0]
            service = AgentStatusService(root / "claude", root / "codex",
                                         now=lambda: now[0],
                                         _wall_time=lambda: 10.0)
            self.assertEqual(service.poll_once(), 1)

            for index in range(12):
                path = root / "claude" / f"newer-{index:02d}.jsonl"
                event = claude_event("user")
                event["uuid"] = f"crowd-{index:02d}"
                self._write_line(path, event)
                os.utime(path, (10 + index, 10 + index))
            now[0] += service._DISCOVERY_INTERVAL_S
            self.assertEqual(service.poll_once(), 12)
            self.assertEqual(service.poll_once(), 0)
            self.assertEqual(service.poll_once(), 0)

            completed = claude_event("result")
            completed["uuid"] = "target-complete"
            completed["subtype"] = "success"
            with target.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(completed) + "\n")
            now[0] += service._DISCOVERY_INTERVAL_S

            self.assertEqual(service.poll_once(), 1)
            self.assertEqual(service.snapshot()["agents"]["claude"]["state"],
                             "done")

    def test_older_event_in_newer_mtime_file_cannot_overwrite_newer_status(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            working_path = root / "claude" / "working.jsonl"
            touched_old_path = root / "claude" / "touched-old.jsonl"
            working = claude_event("user")
            working["timestamp"] = "2026-08-06T12:00:00Z"
            completed = claude_event("result")
            completed["uuid"] = "older-complete"
            completed["subtype"] = "success"
            completed["timestamp"] = "2026-08-06T11:00:00Z"
            self._write_line(working_path, working)
            self._write_line(touched_old_path, completed)
            os.utime(working_path, (1, 1))
            os.utime(touched_old_path, (2, 2))
            wall_time = AgentStatusService._event_wall_time(
                {"timestamp": "2026-08-06T12:00:30Z"})
            service = AgentStatusService(
                root / "claude", root / "codex", now=lambda: 500.0,
                _wall_time=lambda: wall_time)

            self.assertEqual(service.poll_once(), 1)

            snapshot = service.snapshot()
            self.assertEqual(snapshot["seq"], 1)
            self.assertEqual(snapshot["agents"]["claude"]["state"],
                             "working")
            self.assertEqual(snapshot["agents"]["claude"]["updated_ms"],
                             30000)


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

    def test_real_service_endpoint_is_exact_private_bounded_and_memory_only(self):
        markers = {
            "prompt": "synthetic-secret-prompt",
            "command": "synthetic-secret-command",
            "message": "synthetic-secret-message",
            "file": "synthetic-secret-file-content",
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            claude_path = root / "claude" / "session.jsonl"
            codex_path = root / "codex" / "rollout-test.jsonl"
            claude = claude_event(
                "assistant", tool_name="Bash", tool_input={
                    "command": markers["command"],
                    "prompt": markers["prompt"],
                    "file_path": markers["file"],
                    "message": markers["message"],
                })
            claude["sessionId"] = "123e4567-e89b-12d3-a456-426614174000"
            claude["uuid"] = "123e4567-e89b-12d3-a456-426614174001"
            claude["cwd"] = "/Users/test/" + "å" * 16
            codex = {
                "type": "event_msg",
                "timestamp": claude["timestamp"],
                "payload": {
                    "type": "task_started",
                    "turn_id": "turn-" + "x" * 100,
                    "prompt": markers["prompt"],
                    "command": markers["command"],
                    "message": markers["message"],
                    "file_content": markers["file"],
                },
            }
            AgentStatusServiceTests._write_line(claude_path, claude)
            AgentStatusServiceTests._write_line(codex_path, codex)
            wall_time = AgentStatusService._event_wall_time(claude)
            service = AgentStatusService(
                root / "claude", root / "codex", now=lambda: 10.0,
                _wall_time=lambda: wall_time)
            self.assertEqual(service.poll_once(), 2)

            def forbidden_disk_access(*args, **kwargs):
                raise AssertionError("request thread attempted filesystem I/O")

            service._discover_paths = forbidden_disk_access
            service._recent_paths = forbidden_disk_access
            service._tailer.read = forbidden_disk_access

            sent = self._request("/api/agent-status", service)
            self.assertEqual(sent[0][0], 200)
            payload = sent[0][1]
            serialized = json.dumps(payload, ensure_ascii=False)

            self.assertEqual(set(payload), {"v", "seq", "agents"})
            self.assertEqual(set(payload["agents"]), {"claude", "codex"})
            allowed = {
                "task_id", "event_id", "state", "project", "activity",
                "updated_ms",
            }
            for agent in payload["agents"].values():
                self.assertEqual(set(agent), allowed)
                self.assertLessEqual(len(agent["task_id"].encode("utf-8")),
                                     64)
                if agent["project"] is not None:
                    self.assertLessEqual(
                        len(agent["project"].encode("utf-8")), 16)
            for marker in markers.values():
                self.assertNotIn(marker, serialized)


if __name__ == "__main__":
    unittest.main()
