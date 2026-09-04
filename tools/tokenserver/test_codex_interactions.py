import dataclasses
import http.client
import json
import select
import socket
import threading
import time
import unittest
from unittest import mock

from tools.tokenserver import interactions
from tools.tokenserver.interaction_types import (
    InteractionProvider,
    InteractionResult,
)
from tools.tokenserver.codex_interactions import (
    codex_permission_response,
    codex_question_result,
    normalize_codex_permission,
    normalize_codex_question,
)
from tools.tokenserver.interactions import sign_answer_v2
from tools.tokenserver.test_interactions import (
    HttpEndToEndTests,
    SECRET,
    StubAgentStatus,
    approval_event,
    question_event,
)


class InteractionTypeTests(unittest.TestCase):
    def test_result_is_provider_neutral_and_immutable(self):
        result = InteractionResult(verdict="approve", option_index=1)
        self.assertEqual(result.verdict, "approve")
        self.assertEqual(result.option_index, 1)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.verdict = "deny"

    def test_provider_wire_values_are_stable(self):
        self.assertEqual(InteractionProvider.CLAUDE.value, "claude")
        self.assertEqual(InteractionProvider.CODEX.value, "codex")

    def test_result_rejects_unsupported_verdict(self):
        with self.assertRaisesRegex(ValueError, "unsupported verdict"):
            InteractionResult(verdict="maybe")

    def test_result_rejects_negative_option_index(self):
        with self.assertRaisesRegex(ValueError, "negative option index"):
            InteractionResult(verdict="approve", option_index=-1)


def codex_question(**overrides):
    payload = {
        "question": "Which auth approach?",
        "header": "Auth",
        "options": [
            {"label": "Keep existing auth", "description": "Smaller change"},
            {"label": "New auth layer", "description": "Cleaner architecture",
             "recommended": True},
        ],
    }
    payload.update(overrides)
    return payload


def codex_permission(**overrides):
    event = {
        "hook_event_name": "PermissionRequest",
        "session_id": "session-123",
        "turn_id": "turn-456",
        "cwd": "/Users/niclas/vibepulse",
        "tool_name": "Read",
        "tool_input": {"command": "cat README.md"},
    }
    event.update(overrides)
    return event


def codex_question_event(**overrides):
    event = {
        "question": "How should Codex handle approvals?",
        "header": "Approvals",
        "options": [
            {"label": "Use the trusted hook",
             "description": "Desktop + CLI, one setup",
             "recommended": True},
            {"label": "Keep computer only",
             "description": "No panel decisions"},
        ],
        "cwd": "/Users/niclas/vibepulse",
        "session_id": "session-123",
        "turn_id": "turn-456",
    }
    event.update(overrides)
    return event


class CodexNormalizationTests(unittest.TestCase):
    def normalize_question(self, payload=None):
        return normalize_codex_question(
            payload if payload is not None else codex_question(),
            cwd="/Users/niclas/vibepulse",
            session_id="session-123", turn_id="turn-456")

    def test_explicit_recommendation_has_a_safe_approvable_view(self):
        normalized = self.normalize_question()

        self.assertEqual(normalized["provider"], "codex")
        self.assertEqual(normalized["kind"], "question")
        self.assertEqual(normalized["project"], "vibepulse")
        self.assertEqual(normalized["recommended_index"], 1)
        self.assertEqual(normalized["view"], {
            "kind": "question", "options_total": 2, "marked": True,
            "prompt": "Which auth approach?", "title": "New auth layer",
            "subtitle": "Cleaner architecture", "can_approve": True,
        })
        self.assertNotIn("session_id", normalized["view"])
        self.assertNotIn("turn_id", normalized["view"])

    def test_unmarked_question_is_alert_only_without_a_guess(self):
        payload = codex_question(options=[
            {"label": "Keep existing auth", "description": "Smaller change"},
            {"label": "New auth layer", "description": "Cleaner architecture"},
        ])

        normalized = self.normalize_question(payload)

        self.assertIsNone(normalized["recommended_index"])
        self.assertEqual(normalized["view"], {
            "kind": "question", "options_total": 2, "marked": False,
            "prompt": "Which auth approach?", "can_approve": False,
        })

    def test_missing_description_is_accepted_as_no_subtitle(self):
        payload = codex_question(options=[
            {"label": "Keep existing auth"},
            {"label": "New auth layer", "recommended": True},
        ])

        normalized = self.normalize_question(payload)

        self.assertEqual(normalized["view"]["title"], "New auth layer")
        self.assertIsNone(normalized["view"]["subtitle"])

    def test_three_options_allow_one_explicit_recommendation(self):
        normalized = self.normalize_question(codex_question(options=[
            {"label": "first"},
            {"label": "second", "recommended": True},
            {"label": "third"},
        ]))

        self.assertEqual(normalized["recommended_index"], 1)
        self.assertEqual(normalized["view"]["options_total"], 3)
        self.assertTrue(normalized["view"]["can_approve"])

    def test_question_requires_question_and_options_separately(self):
        missing_question = codex_question()
        missing_question.pop("question")
        missing_options = codex_question()
        missing_options.pop("options")

        self.assertIsNone(self.normalize_question(missing_question))
        self.assertIsNone(self.normalize_question(missing_options))

    def test_optional_header_is_bounded_clean_text_when_present(self):
        self.assertIsNotNone(self.normalize_question(codex_question(header="Auth")))
        for header in (3, "Auth\x00", "x" * 65):
            with self.subTest(header=header):
                self.assertIsNone(self.normalize_question(
                    codex_question(header=header)))

    def test_options_reject_malformed_objects_labels_and_descriptions(self):
        invalid_options = (
            ["not an option", {"label": "b"}],
            [{}, {"label": "b"}],
            [{"label": 3}, {"label": "b"}],
            [{"label": "a", "description": 3}, {"label": "b"}],
            [{"label": "a", "description": "bad\x00"}, {"label": "b"}],
        )

        for options in invalid_options:
            with self.subTest(options=options):
                self.assertIsNone(self.normalize_question(
                    codex_question(options=options)))

    def test_display_text_uses_utf8_byte_boundaries_without_truncation(self):
        prompt_exact = "é" * 48
        prompt_over = prompt_exact + "a"
        label_exact = "é" * 32
        label_over = label_exact + "a"
        description_exact = "é" * 32
        description_over = description_exact + "a"
        exact_payloads = (
            codex_question(question=prompt_exact),
            codex_question(options=[{"label": label_exact}, {"label": "b"}]),
            codex_question(options=[
                {"label": "a", "description": description_exact}, {"label": "b"},
            ]),
        )
        over_payloads = (
            codex_question(question=prompt_over),
            codex_question(options=[{"label": label_over}, {"label": "b"}]),
            codex_question(options=[
                {"label": "a", "description": description_over}, {"label": "b"},
            ]),
        )

        for payload in exact_payloads:
            with self.subTest(exact=payload):
                self.assertIsNotNone(self.normalize_question(payload))
        for payload in over_payloads:
            with self.subTest(over=payload):
                self.assertIsNone(self.normalize_question(payload))

    def test_question_rejects_non_dict_payload(self):
        self.assertIsNone(self.normalize_question([]))

    def test_question_rejects_structural_and_option_shape_errors(self):
        cases = [
            codex_question(extra="unknown"),
            codex_question(options=[{"label": "only one"}]),
            codex_question(options=[{"label": str(i)} for i in range(4)]),
            codex_question(options=[
                {"label": "a", "recommended": True},
                {"label": "b", "recommended": True},
            ]),
            codex_question(options=[
                {"label": "a", "recommended": "yes"},
                {"label": "b"},
            ]),
            codex_question(options=[
                {"label": "a", "unknown": 1}, {"label": "b"},
            ]),
        ]

        for payload in cases:
            with self.subTest(payload=payload):
                self.assertIsNone(self.normalize_question(payload))

    def test_question_rejects_empty_controls_and_overlong_display_text(self):
        invalid_payloads = [
            codex_question(question="   "),
            codex_question(question="Question\x00"),
            codex_question(question="Question\u202e"),
            codex_question(question="x" * 97),
            codex_question(options=[{"label": "\x7f"}, {"label": "b"}]),
            codex_question(options=[{"label": "x" * 65}, {"label": "b"}]),
            codex_question(options=[
                {"label": "a", "description": "x" * 65}, {"label": "b"},
            ]),
        ]

        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                self.assertIsNone(self.normalize_question(payload))

    def test_unicode_controls_never_reach_an_approvable_permission_view(self):
        normalized = normalize_codex_permission(
            codex_permission(tool_input={"command": "cat \u202eREADME.md"}),
            reveal=True)

        self.assertIsNone(normalized)

    def test_permission_uses_the_documented_response_shapes(self):
        self.assertEqual(codex_permission_response("approve"), {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "allow"},
            },
        })
        self.assertIsNone(codex_permission_response("leave_it"))
        self.assertEqual(codex_permission_response("deny"), {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny", "message": "Denied from VibePulse",
                },
            },
        })
        with self.assertRaises(ValueError):
            codex_permission_response("maybe")

    def test_permission_applies_readability_and_tool_safety_gates(self):
        read_only = normalize_codex_permission(codex_permission(), reveal=True)
        hidden_read = normalize_codex_permission(codex_permission(), reveal=False)
        patch = normalize_codex_permission(
            codex_permission(tool_name="apply_patch", tool_input={}), reveal=True)

        self.assertTrue(read_only["view"]["can_approve"])
        self.assertFalse(hidden_read["view"]["can_approve"])
        self.assertFalse(patch["view"]["can_approve"])

    def test_permission_rejects_wrong_event_and_malformed_required_fields(self):
        invalid_events = [
            codex_permission(hook_event_name="PreToolUse"),
            codex_permission(session_id=""),
            codex_permission(turn_id=3),
            codex_permission(cwd=None),
            codex_permission(tool_name=[]),
            codex_permission(tool_input=[]),
        ]

        for event in invalid_events:
            with self.subTest(event=event):
                self.assertIsNone(normalize_codex_permission(event, reveal=True))

    def test_dangerous_shell_commands_are_never_approvable(self):
        commands = (
            "npm test; rm -rf build", "echo hi > notes.txt", "npm install",
            "./deploy.sh", "git push", "rm old.txt", "curl example.com",
        )

        for command in commands:
            with self.subTest(command=command):
                normalized = normalize_codex_permission(
                    codex_permission(tool_name="Shell",
                                     tool_input={"command": command}), reveal=True)
                self.assertFalse(normalized["view"]["can_approve"])

    def test_shell_command_families_are_strictly_positive(self):
        safe_commands = (
            "make", "make all", "make test",
            "ninja", "ninja all", "ninja test",
            "cmake --build build", "cmake --build build --parallel 4",
            "cmake --build build --target all", "npm test", "npm run test",
            "npm run build", "npm run build --silent", "pytest",
            "python -m unittest", "cargo test", "cargo build", "go test",
            "ctest", "./test/run.sh", "idf.py build", "git status", "git log",
            "git show install", "git branch", "git branch --show-current",
            "git diff", "git diff --stat", "ls", "cat installer-notes.txt",
            "head README", "tail README", "wc README", "grep value README",
            "rg value README",
        )
        unsafe_commands = (
            "npm run build -- --deploy", "npm run build -- --install",
            "npm run build -- --delete", "git branch new-feature",
            "git branch -D old-feature", "git diff --output=result.patch",
            "make install-strip", "make clean-all", "make deploy-prod",
            "ninja install/strip", "cmake --build b --target install/strip",
            "make npm up package", "make npm x package", "make uvx package",
            "make installer", "make -j4", "ninja -C build",
            "cmake --build build --output=result.patch", "npm install",
            "npm run deploy", "git diff --ext-diff", "git -c x=y status",
            "idf.py build flash",
            "./test/run.sh deploy", "./test/run.sh install",
            "./test/run.sh delete", "./test/run.sh push",
            "./test/run.sh publish", "./test/run.sh network",
            "./test/run.sh foo",
            "./TEST/RUN.SH", "./Test/run.sh",
            "make CC='touch /tmp/pwn' all",
            "make CC='rm -f /tmp/pwn' build",
            "make FETCH='curl https://example.test' test",
            "make ACTION=deploy all", "make TARGET=install build",
            "ninja CC='touch /tmp/pwn' all",
        )

        for command in safe_commands:
            with self.subTest(safe=command):
                normalized = normalize_codex_permission(
                    codex_permission(tool_name="Shell",
                                     tool_input={"command": command}), reveal=True)
                self.assertTrue(normalized["view"]["can_approve"])
        for command in unsafe_commands:
            with self.subTest(unsafe=command):
                normalized = normalize_codex_permission(
                    codex_permission(tool_name="Shell",
                                     tool_input={"command": command}), reveal=True)
                self.assertFalse(normalized["view"]["can_approve"])

    def test_question_result_only_answers_the_explicit_recommendation(self):
        normalized = self.normalize_question()

        self.assertEqual(codex_question_result("approve", normalized), {
            "status": "answered", "option_index": 1,
            "answer": "New auth layer",
        })
        self.assertEqual(codex_question_result("deny", normalized), {
            "status": "computer", "reason": "deny",
        })
        unmarked = self.normalize_question(codex_question(options=[
            {"label": "a"}, {"label": "b"},
        ]))
        self.assertEqual(codex_question_result("approve", unmarked), {
            "status": "computer", "reason": "approve",
        })


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class CodexInteractionStoreTests(unittest.TestCase):
    SECRET = "a" * 64

    def setUp(self):
        self.clock = Clock()
        self.wall = Clock(50_000.0)
        self.store = interactions.InteractionStore(
            secret=self.SECRET, reveal_detail=True, now=self.clock,
            wall=self.wall)

    def normalized_question(self):
        return normalize_codex_question(
            codex_question(), cwd="/Users/niclas/vibepulse",
            session_id="session-123", turn_id="turn-456")

    def answer_v2(self, entry, verdict="approve", *, provider=None,
                  digest=None, stamp=None, mac=None):
        provider = provider if provider is not None else entry.provider.value
        digest = digest if digest is not None else entry.view_sha256
        stamp = int(self.wall()) if stamp is None else stamp
        if mac is None:
            mac = interactions.sign_answer_v2(
                self.SECRET, provider, entry.request_id, digest, verdict,
                stamp)
        return self.store.resolve(
            entry.request_id, verdict, stamp, mac, provider=provider,
            view_sha256=digest)

    def test_codex_public_view_is_provider_bound_private_free_and_immutable(self):
        normalized = self.normalized_question()
        entry = self.store.park_normalized(normalized, 120)
        normalized["view"]["title"] = "tampered after park"
        public = self.store.pending_public()

        self.assertEqual(public["provider"], "codex")
        self.assertEqual(public["title"], "New auth layer")
        self.assertEqual(public["view_sha256"],
                         interactions.view_digest(public))
        serialized = json.dumps(public)
        for private in ("session-123", "turn-456", "session_id", "turn_id",
                        "event"):
            self.assertNotIn(private, serialized)
        with self.assertRaises(TypeError):
            entry.view["title"] = "mutate stored view"

    def test_v1_cannot_resolve_codex_and_does_not_consume_it(self):
        entry = self.store.park_normalized(self.normalized_question(), 120)
        stamp = int(self.wall())
        mac = interactions.sign_answer(
            self.SECRET, entry.request_id, "approve", stamp)

        ok, reason = self.store.resolve(
            entry.request_id, "approve", stamp, mac)

        self.assertEqual((ok, reason), (False, "v2 verdict required"))
        self.assertEqual(self.store.pending_public()["request_id"],
                         entry.request_id)
        for provider, digest in (("codex", None),
                                 (None, entry.view_sha256)):
            with self.subTest(provider=provider, digest=digest):
                ok, reason = self.store.resolve(
                    entry.request_id, "approve", stamp, "0" * 64,
                    provider=provider, view_sha256=digest)
                self.assertEqual((ok, reason),
                                 (False, "v2 verdict required"))
                self.assertEqual(self.store.pending_public()["request_id"],
                                 entry.request_id)

    def test_v2_exact_binding_returns_codex_recommended_option(self):
        entry = self.store.park_normalized(self.normalized_question(), 120)

        self.assertEqual(self.answer_v2(entry), (True, "ok"))
        result = self.store.await_result(entry)

        self.assertEqual(result, InteractionResult(
            verdict="approve", option_index=1))

    def test_codex_permission_keeps_raw_event_private_and_has_no_option(self):
        normalized = normalize_codex_permission(
            codex_permission(), reveal=True)
        entry = self.store.park_normalized(normalized, 120)
        public = self.store.pending_public()

        self.assertEqual(public["provider"], "codex")
        self.assertEqual(public["kind"], "approval")
        self.assertNotIn("session-123", json.dumps(public))
        self.assertNotIn("turn-456", json.dumps(public))
        self.assertEqual(self.answer_v2(entry), (True, "ok"))
        self.assertEqual(self.store.await_result(entry), InteractionResult(
            verdict="approve", option_index=None))

    def test_dangerous_codex_event_cannot_borrow_an_approvable_read_view(self):
        dangerous = normalize_codex_permission(
            codex_permission(
                tool_name="Bash", tool_input={"command": "rm -rf build"}),
            reveal=True)
        safe = normalize_codex_permission(codex_permission(), reveal=True)
        self.assertFalse(dangerous["view"]["can_approve"])
        self.assertTrue(safe["view"]["can_approve"])
        forged = {**dangerous, "view": dict(safe["view"])}

        entry = self.store.park_normalized(forged, 120)

        self.assertIsNone(entry)
        self.assertIsNone(self.store.pending_public())

    def test_wrong_v2_bindings_and_bad_signatures_never_consume(self):
        entry = self.store.park_normalized(self.normalized_question(), 600)
        stamp = int(self.wall())
        wrong_digest = "0" * 64
        attempts = [
            ("claude", entry.view_sha256, stamp,
             interactions.sign_answer_v2(
                 self.SECRET, "claude", entry.request_id,
                 entry.view_sha256, "approve", stamp)),
            ("codex", wrong_digest, stamp,
             interactions.sign_answer_v2(
                 self.SECRET, "codex", entry.request_id, wrong_digest,
                 "approve", stamp)),
            ("CODEX", entry.view_sha256, stamp, "0" * 64),
            ("codex", "short", stamp, "0" * 64),
            ("codex", entry.view_sha256, stamp, "0" * 64),
        ]
        for provider, digest, attempted_stamp, mac in attempts:
            with self.subTest(provider=provider, digest=digest, mac=mac):
                ok, _ = self.store.resolve(
                    entry.request_id, "approve", attempted_stamp, mac,
                    provider=provider, view_sha256=digest)
                self.assertFalse(ok)
                self.assertEqual(self.store.pending_public()["request_id"],
                                 entry.request_id)

        stale_mac = interactions.sign_answer_v2(
            self.SECRET, "codex", entry.request_id, entry.view_sha256,
            "approve", stamp)
        self.wall.advance(interactions.FRESHNESS_S + 1)
        ok, _ = self.store.resolve(
            entry.request_id, "approve", stamp, stale_mac,
            provider="codex", view_sha256=entry.view_sha256)
        self.assertFalse(ok)
        self.assertEqual(self.store.pending_public()["request_id"],
                         entry.request_id)

        self.assertEqual(self.answer_v2(entry), (True, "ok"))

    def test_codex_approve_still_requires_can_approve(self):
        normalized = normalize_codex_question(
            codex_question(options=[
                {"label": "first"}, {"label": "second"},
            ]), cwd="/Users/niclas/vibepulse",
            session_id="session-123", turn_id="turn-456")
        entry = self.store.park_normalized(normalized, 120)

        ok, reason = self.answer_v2(entry)

        self.assertFalse(ok)
        self.assertIn("terminal", reason)
        self.assertIsNotNone(self.store.pending_public())

    def test_await_verdict_refuses_codex_without_hook_output(self):
        entry = self.store.park_normalized(self.normalized_question(), 120)
        self.assertEqual(self.answer_v2(entry), (True, "ok"))

        self.assertIsNone(self.store.await_verdict(entry))

    def test_normalized_boundary_rejects_unvalidated_or_private_view_fields(self):
        valid = self.normalized_question()
        cases = [
            None,
            {**valid, "provider": "other"},
            {**valid, "recommended_index": 99},
            {**valid, "view": {**valid["view"],
                                "session_id": "must-not-publish"}},
            {**valid, "view": {**valid["view"], "event": {"raw": True}}},
        ]

        for normalized in cases:
            with self.subTest(normalized=normalized):
                self.assertIsNone(self.store.park_normalized(normalized, 120))

        self.assertIsNone(self.store.pending_public())

    def test_normalized_view_rejects_lone_surrogates_without_raising(self):
        cases = []
        for surrogate in ("\ud800", "\udc00"):
            invalid_view = self.normalized_question()
            invalid_view["view"]["prompt"] = f"bad{surrogate}"
            cases.append(("prompt", invalid_view))
        invalid_project = self.normalized_question()
        invalid_project["project"] = "bad\ud800"
        cases.append(("project", invalid_project))

        for field, normalized in cases:
            with self.subTest(field=field):
                try:
                    entry = self.store.park_normalized(normalized, 120)
                except UnicodeEncodeError as exc:
                    self.fail(
                        f"park_normalized leaked UnicodeEncodeError: {exc}")
                self.assertIsNone(entry)
        self.assertIsNone(self.store.pending_public())

    def test_normalized_boundary_rejects_control_and_format_text(self):
        cases = []
        for forbidden in ("\x00", "\u202e"):
            invalid_prompt = self.normalized_question()
            invalid_prompt["view"]["prompt"] = f"Approve{forbidden}this"
            cases.append(("prompt", ascii(forbidden), invalid_prompt))

            invalid_option = self.normalized_question()
            invalid_option["options"][0]["label"] = \
                f"Fallback{forbidden}option"
            cases.append(("option", ascii(forbidden), invalid_option))

            invalid_project = self.normalized_question()
            invalid_project["project"] = f"safe{forbidden}project"
            cases.append(("project", ascii(forbidden), invalid_project))

        for field, forbidden, normalized in cases:
            with self.subTest(field=field, forbidden=forbidden):
                self.assertIsNone(
                    self.store.park_normalized(normalized, 120))
                self.assertIsNone(self.store.pending_public())


class CodexRouteTests(unittest.TestCase):
    """Strict loopback Codex adapters around the provider-aware store."""

    request = HttpEndToEndTests.request
    raw_exchange = HttpEndToEndTests.raw_exchange
    pending = HttpEndToEndTests.pending
    wait_for_pending = HttpEndToEndTests.wait_for_pending

    def setUp(self):
        from tools.tokenserver import tokenserver as server_module
        self.module = server_module
        self.handler = server_module.Handler
        self._saved = {
            "store": self.handler.interaction_store,
            "timeout": self.handler.interaction_timeout_s,
            "agent_status": self.handler.agent_status,
            "claude": self.handler.claude_interactions,
            "codex": self.handler.codex_interactions,
            "detail": self.handler.interaction_detail,
            "legacy_claude_panel_v1": getattr(
                self.handler, "legacy_claude_panel_v1", False),
            "body_timeout": getattr(
                self.handler, "json_body_timeout_s", None),
            "had_body_timeout": hasattr(
                self.handler, "json_body_timeout_s"),
        }
        self.store = interactions.InteractionStore(
            secret=SECRET, reveal_detail=True)
        self.handler.interaction_store = self.store
        self.handler.interaction_timeout_s = 30.0
        self.handler.agent_status = StubAgentStatus()
        self.handler.claude_interactions = False
        self.handler.codex_interactions = True
        self.handler.interaction_detail = True
        self.handler.legacy_claude_panel_v1 = False
        self.handler.json_body_timeout_s = 0.1
        self.server = server_module.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), self.handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       daemon=True)
        self.thread.start()

    def tearDown(self):
        self.store.deny_all()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        self.handler.interaction_store = self._saved["store"]
        self.handler.interaction_timeout_s = self._saved["timeout"]
        self.handler.agent_status = self._saved["agent_status"]
        self.handler.claude_interactions = self._saved["claude"]
        self.handler.codex_interactions = self._saved["codex"]
        self.handler.interaction_detail = self._saved["detail"]
        self.handler.legacy_claude_panel_v1 = \
            self._saved["legacy_claude_panel_v1"]
        if self._saved["had_body_timeout"]:
            self.handler.json_body_timeout_s = self._saved["body_timeout"]
        else:
            del self.handler.json_body_timeout_s

    def answer_v2(self, shown, verdict="approve"):
        stamp = int(time.time())
        mac = sign_answer_v2(
            SECRET, "codex", shown["request_id"],
            shown["view_sha256"], verdict, stamp)
        return self.request(
            "POST", f"/api/interaction/{shown['request_id']}", {
                "provider": "codex",
                "view_sha256": shown["view_sha256"],
                "verdict": verdict,
                "ts": stamp,
                "hmac": mac,
            })

    def post_in_thread(self, path, payload):
        result = {}

        def post():
            result["status"], result["raw"] = self.request(
                "POST", path, payload)

        thread = threading.Thread(target=post, daemon=True)
        thread.start()
        return thread, result

    def direct_handler(self, path, host="192.168.1.20"):
        handler = self.handler.__new__(self.handler)
        handler.path = path
        handler.client_address = (host, 5000)
        handler._send = mock.Mock()
        handler._send_no_decision = mock.Mock()
        handler._read_json_body = mock.Mock()
        return handler

    def early_reject_exchange(self, path, payload, headers,
                              grace=0.3, timeout=10.0):
        """POST with the headers first and the body only if no reply came.

        The requests under test must be rejected on their headers alone,
        before the body is parsed or anything is parked. A client that
        writes headers and body in one go leaves that body unread in the
        server's socket when the server answers and closes; Windows then
        aborts the connection (WinError 10053) and discards the response
        that was already on its way, so the test sees no status at all.
        Sending the body only after a short grace period, and only when no
        response has arrived, keeps the assertion exact and the socket
        clean on every platform. A route that does need the body still
        gets it, well inside the server's JSON body deadline.
        """
        body = json.dumps(payload).encode()
        merged = {"Content-Type": "application/json"}
        merged.update(headers or {})
        if not any(name.lower() == "host" for name in merged):
            merged["Host"] = f"127.0.0.1:{self.port}"
        merged["Content-Length"] = str(len(body))
        merged["Connection"] = "close"
        head = (f"POST {path} HTTP/1.1\r\n" +
                "".join(f"{name}: {value}\r\n"
                        for name, value in merged.items()) + "\r\n")
        client = socket.create_connection(("127.0.0.1", self.port),
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

    def assert_wire_rejected(self, path, payload, headers, expected_status):
        result = {}

        def post():
            try:
                result["status"], result["raw"] = self.early_reject_exchange(
                    path, payload, headers)
            except OSError as exc:  # keep the failure legible, never silent
                result["error"] = repr(exc)

        thread = threading.Thread(target=post, daemon=True)
        thread.start()
        thread.join(timeout=0.5)
        shown = self.pending()
        if shown is not None:
            self.store.deny_all()
        thread.join(timeout=5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result.get("status"), expected_status,
                         result.get("error") or result.get("raw"))
        self.assertIsNone(shown)

    def test_codex_question_parks_and_returns_exact_structured_answer(self):
        thread, result = self.post_in_thread(
            "/api/codex/question", codex_question_event())

        shown = self.wait_for_pending()
        self.assertEqual(shown["provider"], "codex")
        self.assertEqual(shown["kind"], "question")
        self.assertEqual(shown["title"], "Use the trusted hook")
        self.assertEqual(self.answer_v2(shown)[0], 200)
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], 200)
        self.assertEqual(json.loads(result["raw"]), {
            "status": "answered",
            "option_index": 0,
            "answer": "Use the trusted hook",
        })

    def test_legacy_claude_mode_never_downgrades_codex(self):
        self.handler.legacy_claude_panel_v1 = True
        thread, result = self.post_in_thread(
            "/api/codex/question", codex_question_event())

        shown = self.wait_for_pending()
        self.assertEqual(shown["provider"], "codex")
        self.assertRegex(shown["view_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(self.answer_v2(shown)[0], 200)
        thread.join(timeout=10)

        self.assertFalse(thread.is_alive())
        self.assertEqual(result["status"], 200)

    def test_codex_question_deny_is_explicit_computer_fallback(self):
        thread, result = self.post_in_thread(
            "/api/codex/question", codex_question_event())
        shown = self.wait_for_pending()
        self.answer_v2(shown, "deny")
        thread.join(timeout=10)

        self.assertEqual(json.loads(result["raw"]), {
            "status": "computer", "reason": "deny",
        })

    def test_question_detail_off_publishes_no_question_or_option_text(self):
        secret_values = (
            "SECRET QUESTION 91d12a",
            "SECRET APPROVAL 57b0b9",
            "SECRET DESCRIPTION 61fc04",
            "SECRET FALLBACK b2a40e",
        )
        self.handler.interaction_detail = False
        payload = codex_question_event(
            question=secret_values[0],
            options=[
                {"label": secret_values[1],
                 "description": secret_values[2],
                 "recommended": True},
                {"label": secret_values[3]},
            ])
        thread, result = self.post_in_thread("/api/codex/question", payload)

        shown = self.wait_for_pending()
        serialized = json.dumps(shown)
        for secret in secret_values:
            self.assertNotIn(secret, serialized)
        self.assertFalse(shown["can_approve"])
        self.assertFalse(shown["marked"])
        self.assertNotIn("prompt", shown)
        self.assertNotIn("title", shown)
        self.assertNotIn("subtitle", shown)

        status, raw = self.answer_v2(shown, "approve")
        self.assertEqual(status, 409)
        self.assertFalse(json.loads(raw)["ok"])
        self.answer_v2(shown, "leave_it")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(result["raw"]), {
            "status": "computer", "reason": "leave_it",
        })

    def test_unmarked_question_cannot_accidentally_approve(self):
        payload = codex_question_event(options=[
            {"label": "Use the trusted hook"},
            {"label": "Keep computer only"},
        ])
        thread, result = self.post_in_thread("/api/codex/question", payload)
        shown = self.wait_for_pending()
        self.assertFalse(shown["can_approve"])

        status, raw = self.answer_v2(shown, "approve")
        self.assertEqual(status, 409)
        self.assertFalse(json.loads(raw)["ok"])
        self.answer_v2(shown, "leave_it")
        thread.join(timeout=10)
        self.assertEqual(json.loads(result["raw"]), {
            "status": "computer", "reason": "leave_it",
        })

    def test_codex_permission_allow_and_deny_use_documented_hook_json(self):
        for verdict, expected in (
                ("approve", {"behavior": "allow"}),
                ("deny", {"behavior": "deny",
                          "message": "Denied from VibePulse"})):
            with self.subTest(verdict=verdict):
                thread, result = self.post_in_thread(
                    "/api/codex/permission", codex_permission())
                shown = self.wait_for_pending()
                self.answer_v2(shown, verdict)
                thread.join(timeout=10)
                self.assertEqual(
                    json.loads(result["raw"])["hookSpecificOutput"], {
                        "hookEventName": "PermissionRequest",
                        "decision": expected,
                    })

    def test_codex_permission_leave_it_returns_empty_body(self):
        thread, result = self.post_in_thread(
            "/api/codex/permission", codex_permission())
        shown = self.wait_for_pending()
        self.answer_v2(shown, "leave_it")
        thread.join(timeout=10)
        self.assertEqual((result["status"], result["raw"]), (200, b""))

    def test_permission_detail_off_hides_command_and_cannot_approve(self):
        self.store.deny_all()
        self.store = interactions.InteractionStore(
            secret=SECRET, reveal_detail=False)
        self.handler.interaction_store = self.store
        self.handler.interaction_detail = False
        event = codex_permission(
            tool_input={"command": "cat SECRET_PERMISSION_43fca1"})
        thread, result = self.post_in_thread(
            "/api/codex/permission", event)

        shown = self.wait_for_pending()
        self.assertNotIn("SECRET_PERMISSION_43fca1", json.dumps(shown))
        self.assertFalse(shown["can_approve"])
        self.assertNotIn("title", shown)
        status, _ = self.answer_v2(shown, "approve")
        self.assertEqual(status, 409)
        self.answer_v2(shown, "leave_it")
        thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual((result["status"], result["raw"]), (200, b""))

    def test_invalid_flat_question_envelopes_fail_closed_without_parking(self):
        invalid = (
            codex_question_event(extra="unknown"),
            {"question": codex_question(), "cwd": "/tmp/project",
             "session_id": "s", "turn_id": "t"},
            codex_question_event(session_id=None),
            codex_question_event(options=[{"label": "only one"}]),
        )
        for payload in invalid:
            with self.subTest(payload=payload):
                started = time.monotonic()
                status, raw = self.request(
                    "POST", "/api/codex/question", payload)
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw), {
                    "status": "computer", "reason": "invalid",
                })
                self.assertIsNone(self.pending())
                self.assertLess(time.monotonic() - started, 5)

    def test_invalid_permission_returns_empty_body_without_parking(self):
        for payload in (None, [], {"hook_event_name": "PreToolUse"}):
            with self.subTest(payload=payload):
                status, raw = self.request(
                    "POST", "/api/codex/permission", payload)
                self.assertEqual((status, raw), (200, b""))
                self.assertIsNone(self.pending())

    def test_codex_timeouts_fail_closed_for_question_and_permission(self):
        self.handler.interaction_timeout_s = 1.0
        status, raw = self.request(
            "POST", "/api/codex/question", codex_question_event())
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {
            "status": "computer", "reason": "timeout",
        })

        status, raw = self.request(
            "POST", "/api/codex/permission", codex_permission())
        self.assertEqual((status, raw), (200, b""))

    def test_provider_switches_are_independent_and_disabled_routes_do_not_parse(self):
        cases = (
            (True, False, "/api/codex/question"),
            (False, True, "/api/hook/question"),
        )
        for claude, codex, path in cases:
            with self.subTest(claude=claude, codex=codex, path=path):
                self.handler.claude_interactions = claude
                self.handler.codex_interactions = codex
                handler = self.direct_handler(path, host="127.0.0.1")
                handler.do_POST()
                handler._send.assert_called_once_with(
                    404, {"error": "interactions are not enabled"})
                handler._read_json_body.assert_not_called()

    def test_injected_store_does_not_enable_claude_when_flag_is_off(self):
        self.handler.claude_interactions = False
        handler = self.direct_handler(
            "/api/hook/question", host="127.0.0.1")

        handler.do_POST()

        handler._send.assert_called_once_with(
            404, {"error": "interactions are not enabled"})
        handler._read_json_body.assert_not_called()

    def test_all_hook_and_codex_ingress_routes_reject_non_loopback_before_parsing(self):
        self.handler.claude_interactions = True
        for path in (
                "/api/hook/question", "/api/hook/permission",
                "/api/codex/question", "/api/codex/permission"):
            with self.subTest(path=path):
                handler = self.direct_handler(path)
                handler.do_POST()
                handler._send.assert_called_once_with(
                    403, {"error": "hooks must be local"})
                handler._read_json_body.assert_not_called()

    def test_hook_ingress_rejects_attacker_host_before_parking(self):
        self.handler.claude_interactions = True
        cases = (
            ("/api/hook/question", question_event()),
            ("/api/hook/permission", approval_event()),
            ("/api/codex/question", codex_question_event()),
            ("/api/codex/permission", codex_permission()),
        )
        for host in (
                "attacker.example", f"127.0.0.1:{self.port + 1}"):
            for path, payload in cases:
                with self.subTest(host=host, path=path):
                    self.assert_wire_rejected(
                        path, payload, {"Host": host}, 403)

    def test_hook_ingress_rejects_every_origin_before_parking(self):
        self.handler.claude_interactions = True
        cases = (
            ("/api/hook/question", question_event()),
            ("/api/hook/permission", approval_event()),
            ("/api/codex/question", codex_question_event()),
            ("/api/codex/permission", codex_permission()),
        )
        for origin in ("https://attacker.example", "null"):
            for path, payload in cases:
                with self.subTest(origin=origin, path=path):
                    self.assert_wire_rejected(
                        path, payload, {"Origin": origin}, 403)

    def test_every_json_post_route_rejects_text_plain_before_parsing(self):
        self.handler.claude_interactions = True
        cases = (
            ("/api/hook/question", question_event()),
            ("/api/hook/permission", approval_event()),
            ("/api/codex/question", codex_question_event()),
            ("/api/codex/permission", codex_permission()),
            ("/api/interaction/not-a-real-id", {}),
            ("/api/panic", {}),
        )
        for path, payload in cases:
            with self.subTest(path=path):
                self.assert_wire_rejected(
                    path, payload, {"Content-Type": "text/plain"}, 415)

    def test_recognized_loopback_host_forms_reach_the_json_route(self):
        for host in (
                "localhost", f"localhost.:{self.port}",
                f"127.0.0.1:{self.port}", f"127.42.7.9:{self.port}",
                f"[::1]:{self.port}"):
            with self.subTest(host=host):
                status, raw = self.request(
                    "POST", "/api/codex/question", {},
                    headers={"Host": host})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw), {
                    "status": "computer", "reason": "invalid",
                })
                self.assertIsNone(self.pending())

    def test_json_content_type_allows_only_an_optional_utf8_charset(self):
        accepted = (
            "application/json",
            "Application/JSON; charset=utf-8",
            'application/json; charset="UTF-8"',
        )
        for content_type in accepted:
            with self.subTest(accepted=content_type):
                status, raw = self.request(
                    "POST", "/api/codex/question", {},
                    headers={"Content-Type": content_type})
                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw)["reason"], "invalid")

        for content_type in (
                "application/json; charset=latin-1",
                "application/json; profile=hook",
                "application/json, text/plain"):
            with self.subTest(rejected=content_type):
                status, _ = self.request(
                    "POST", "/api/codex/question", {},
                    headers={"Content-Type": content_type})
                self.assertEqual(status, 415)
                self.assertIsNone(self.pending())

    def test_partial_advertised_body_hits_short_deadline_and_never_parks(self):
        request = (
            "POST /api/codex/question HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            "Content-Type: application/json\r\n"
            "Content-Length: 100\r\n"
            "Connection: close\r\n\r\n"
            "{"
        ).encode("ascii")
        started = time.monotonic()

        status, raw = self.raw_exchange(request)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {
            "status": "computer", "reason": "invalid",
        })
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNone(self.pending())

    def test_deep_and_huge_integer_json_fail_closed_without_traceback(self):
        adversarial = (
            b"[" * 10000 + b"0" + b"]" * 10000,
            b'{"value":' + b"9" * 5000 + b"}",
        )
        for body in adversarial:
            with self.subTest(size=len(body)):
                request = (
                    "POST /api/codex/question HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{self.port}\r\n"
                    "Content-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii") + body

                status, raw = self.raw_exchange(request)

                self.assertEqual(status, 200)
                self.assertEqual(json.loads(raw), {
                    "status": "computer", "reason": "invalid",
                })
                self.assertIsNone(self.pending())
