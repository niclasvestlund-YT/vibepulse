"""Local hook -> opaque mailbox -> fake panel integration tests."""

from __future__ import annotations

import http.client
import json
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import re
import threading
import time
import unittest
from urllib.parse import urlsplit

from tools.tokenserver.interaction_relay import HttpResponse, InteractionRelay
from tools.tokenserver.interaction_relay_crypto import (
    b64url_encode,
    decode_device_key,
    decode_request,
    derive_keys,
    encode_verdict,
)
from tools.tokenserver.interactions import InteractionStore, sign_answer_v2
from tools.tokenserver import tokenserver


DEVICE_KEY = (
    "000102030405060708090a0b0c0d0e0f"
    "101112131415161718191a1b1c1d1e1f"
)
MAILBOX = "vp_A1b2C3d4E5f6G7h8"
MAC_TOKEN = b64url_encode(b"\x11" * 32)
PANEL_TOKEN = b64url_encode(b"\x22" * 32)
_REQUEST = re.compile(
    rf"/v1/mailboxes/{MAILBOX}/requests/([A-Za-z0-9_-]{{22}})$")
_VERDICT = re.compile(
    rf"/v1/mailboxes/{MAILBOX}/requests/([A-Za-z0-9_-]{{22}})/verdict$")


class _WorkerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _auth(self, token):
        return self.headers.get("Authorization") == f"Bearer {token}"

    def _body(self):
        try:
            length = int(self.headers.get("Content-Length", "-1"))
        except ValueError:
            return None
        if not 0 < length <= 4096:
            return None
        value = self.rfile.read(length)
        return value if len(value) == length else None

    def _send(self, status, value=b"", content_type=None):
        self.send_response(status)
        self.send_header("Cache-Control", "no-store")
        if content_type is not None:
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(value)))
        self.end_headers()
        if value:
            self.wfile.write(value)

    def do_PUT(self):
        match = _REQUEST.fullmatch(self.path)
        if match is None or not self._auth(MAC_TOKEN) or \
                self.headers.get("Content-Type") != "application/json":
            self._send(404)
            return
        body = self._body()
        if body is None:
            self._send(400)
            return
        request_id = match.group(1)
        with self.server.state_lock:
            existing = self.server.requests_by_id.get(request_id)
            if existing is not None and existing != body:
                self._send(409)
                return
            self.server.requests_by_id.setdefault(request_id, body)
        self._send(200 if existing is not None else 201)

    def do_GET(self):
        next_path = f"/v1/mailboxes/{MAILBOX}/requests/next"
        verdicts_path = f"/v1/mailboxes/{MAILBOX}/verdicts"
        if self.path == next_path and self._auth(PANEL_TOKEN):
            with self.server.state_lock:
                item = next(iter(self.server.requests_by_id.items()), None)
            if item is None:
                self._send(204)
                return
            request_id, envelope = item
            body = json.dumps({
                "envelope": json.loads(envelope.decode("ascii")),
                "expiresAtMs": int(time.time() * 1000) + 120_000,
                "requestId": request_id,
            }, separators=(",", ":")).encode("ascii")
            self._send(200, body, "application/json")
            return
        if self.path == verdicts_path and self._auth(MAC_TOKEN):
            with self.server.state_lock:
                item = next(iter(self.server.verdicts_by_id.items()), None)
            if item is None:
                self._send(204)
                return
            request_id, envelope = item
            body = json.dumps({
                "verdicts": [{
                    "envelope": json.loads(envelope.decode("ascii")),
                    "requestId": request_id,
                    "verdictAtMs": int(time.time() * 1000),
                }],
            }, separators=(",", ":")).encode("ascii")
            self._send(200, body, "application/json")
            return
        self._send(404)

    def do_POST(self):
        match = _VERDICT.fullmatch(self.path)
        if match is None or not self._auth(PANEL_TOKEN) or \
                self.headers.get("Content-Type") != "application/json":
            self._send(404)
            return
        body = self._body()
        if body is None:
            self._send(400)
            return
        request_id = match.group(1)
        with self.server.state_lock:
            if request_id not in self.server.requests_by_id:
                self._send(404)
                return
            existing = self.server.verdicts_by_id.get(request_id)
            if existing is not None and existing != body:
                self._send(409)
                return
            self.server.verdicts_by_id.setdefault(request_id, body)
        self._send(200 if existing is not None else 201)

    def do_DELETE(self):
        match = _REQUEST.fullmatch(self.path)
        if match is None or not self._auth(MAC_TOKEN):
            self._send(404)
            return
        with self.server.state_lock:
            self.server.requests_by_id.pop(match.group(1), None)
            self.server.verdicts_by_id.pop(match.group(1), None)
        self._send(204)

    def log_message(self, _format, *_args):
        pass


class _LocalWorker:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _WorkerHandler)
        self.server.requests_by_id = OrderedDict()
        self.server.verdicts_by_id = OrderedDict()
        self.server.state_lock = threading.Lock()
        self.thread = threading.Thread(
            target=lambda: self.server.serve_forever(poll_interval=0.02),
            daemon=True)

    @property
    def port(self):
        return self.server.server_address[1]

    def start(self):
        self.thread.start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)


class RelayIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.worker = _LocalWorker()
        self.worker.start()
        self.saved = {
            name: getattr(tokenserver.Handler, name)
            for name in (
                "interaction_store", "interaction_timeout_s",
                "agent_status", "claude_interactions",
                "codex_interactions", "interaction_detail",
                "legacy_claude_panel_v1", "json_body_timeout_s")
        }
        self.store = InteractionStore(
            secret=DEVICE_KEY, reveal_detail=True)
        self.relay = InteractionRelay(
            store=self.store,
            base_url="https://relay.integration.invalid",
            mailbox=MAILBOX,
            mac_token=MAC_TOKEN,
            device_key_hex=DEVICE_KEY,
            transport=self._transport,
        )
        self.relay.start()
        handler = tokenserver.Handler
        handler.interaction_store = self.store
        handler.interaction_timeout_s = 1.0
        handler.agent_status = type("Status", (), {
            "snapshot": lambda _self: {"agents": []}})()
        handler.claude_interactions = True
        handler.codex_interactions = True
        handler.interaction_detail = True
        handler.legacy_claude_panel_v1 = False
        handler.json_body_timeout_s = 0.2
        self.hook_server = tokenserver.BoundedThreadingHTTPServer(
            ("127.0.0.1", 0), handler)
        self.hook_port = self.hook_server.server_address[1]
        self.hook_thread = threading.Thread(
            target=lambda: self.hook_server.serve_forever(poll_interval=0.02),
            daemon=True)
        self.hook_thread.start()
        self.keys = derive_keys(decode_device_key(DEVICE_KEY), MAILBOX)

    def tearDown(self):
        self.relay.stop()
        self.store.deny_all()
        self.hook_server.shutdown()
        self.hook_server.server_close()
        self.hook_thread.join(timeout=2)
        self.worker.stop()
        for name, value in self.saved.items():
            setattr(tokenserver.Handler, name, value)

    def _transport(self, method, url, headers, body,
                   connect_timeout, read_timeout):
        path = urlsplit(url).path
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.worker.port, timeout=connect_timeout)
        try:
            connection.request(method, path, body=body or None,
                               headers=dict(headers))
            if connection.sock is not None:
                connection.sock.settimeout(read_timeout)
            response = connection.getresponse()
            raw = response.read(4097)
            return HttpResponse(
                response.status, tuple(response.getheaders()), raw)
        finally:
            connection.close()

    def _hook_request(self, method, path, payload):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.hook_port, timeout=4)
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        try:
            connection.request(method, path, body=body, headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            })
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def _hook_in_thread(self, path, payload):
        result = {}

        def request():
            result["value"] = self._hook_request("POST", path, payload)

        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        return thread, result

    def _wait_request(self, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.worker.server.state_lock:
                item = next(iter(
                    self.worker.server.requests_by_id.items()), None)
            if item is not None:
                return item
            time.sleep(0.01)
        self.fail("relay request was not published")

    def _panel_request(self):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.worker.port, timeout=2)
        try:
            connection.request(
                "GET", f"/v1/mailboxes/{MAILBOX}/requests/next",
                headers={"Authorization": f"Bearer {PANEL_TOKEN}"})
            response = connection.getresponse()
            self.assertEqual(response.status, 200)
            value = json.loads(response.read())
        finally:
            connection.close()
        envelope = json.dumps(
            value["envelope"], sort_keys=True,
            separators=(",", ":")).encode("ascii")
        return decode_request(
            self.keys, MAILBOX, value["requestId"], envelope)

    def _panel_verdict(self, request, verdict, keys=None, envelope=None):
        envelope = (encode_verdict(
            keys or self.keys, MAILBOX, request, verdict)
            if envelope is None else envelope)
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.worker.port, timeout=2)
        try:
            path = (f"/v1/mailboxes/{MAILBOX}/requests/"
                    f"{request.request_id}/verdict")
            connection.request("POST", path, body=envelope, headers={
                "Authorization": f"Bearer {PANEL_TOKEN}",
                "Content-Type": "application/json",
            })
            response = connection.getresponse()
            status = response.status
            response.read()
            return status
        finally:
            connection.close()

    def _wait_remote_empty(self, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.worker.server.state_lock:
                if not self.worker.server.requests_by_id and \
                        not self.worker.server.verdicts_by_id:
                    return
            time.sleep(0.01)
        self.fail("remote interaction row was not deleted")

    def test_codex_question_round_trip_matches_exact_store_view_and_deletes(self):
        payload = {
            "question": "How should Codex handle approvals?",
            "header": "Approvals",
            "options": [
                {"label": "Use the trusted hook",
                 "description": "Desktop + CLI, one setup",
                 "recommended": True},
                {"label": "Keep computer only",
                 "description": "No panel decisions"},
            ],
            "cwd": "/tmp/vibepulse",
            "session_id": "session-123",
            "turn_id": "turn-456",
        }
        thread, result = self._hook_in_thread("/api/codex/question", payload)
        request_id, _envelope = self._wait_request()
        request = self._panel_request()
        self.assertEqual(request.request_id, request_id)
        with self.store._lock:
            self.assertEqual(
                request.view_bytes,
                self.store._pending[request_id].relay_job.view_bytes)
        verdict = encode_verdict(
            self.keys, MAILBOX, request, "approve")
        self.assertEqual(self._panel_verdict(
            request, "approve", envelope=verdict), 201)
        # The Worker accepts an exact duplicate idempotently.
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.worker.port, timeout=2)
        try:
            connection.request(
                "POST", f"/v1/mailboxes/{MAILBOX}/requests/"
                f"{request.request_id}/verdict", body=verdict, headers={
                    "Authorization": f"Bearer {PANEL_TOKEN}",
                    "Content-Type": "application/json"})
            duplicate = connection.getresponse()
            self.assertEqual(duplicate.status, 200)
            duplicate.read()
        finally:
            connection.close()
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        status, raw = result["value"]
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw), {
            "status": "answered", "option_index": 0,
            "answer": "Use the trusted hook"})
        self._wait_remote_empty()

    def test_claude_permission_deny_returns_exact_hook_decision(self):
        payload = {
            "session_id": "claude-session",
            "cwd": "/tmp/vibepulse",
            "hook_event_name": "PermissionRequest",
            "tool_name": "Bash",
            "tool_input": {"command": "npm test",
                           "description": "Run tests"},
        }
        thread, result = self._hook_in_thread(
            "/api/hook/permission", payload)
        self._wait_request()
        request = self._panel_request()
        self.assertEqual(self._panel_verdict(request, "deny"), 201)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(result["value"][1]), {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": "Denied from VibePulse"}}})
        self._wait_remote_empty()

    def test_wrong_key_times_out_to_computer_and_cleans_remote_state(self):
        payload = {
            "question": "Approve?", "header": "Choice",
            "options": [
                {"label": "Yes", "recommended": True},
                {"label": "No"}],
            "cwd": "/tmp/vibepulse", "session_id": "s", "turn_id": "t",
        }
        thread, result = self._hook_in_thread("/api/codex/question", payload)
        self._wait_request()
        request = self._panel_request()
        wrong = derive_keys(b"\xff" * 32, MAILBOX)
        self.assertEqual(self._panel_verdict(request, "approve", wrong), 201)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(result["value"][1]), {
            "status": "computer", "reason": "timeout"})
        self._wait_remote_empty()

    def test_direct_lan_answer_wins_and_enqueues_remote_cleanup(self):
        payload = {
            "question": "Approve?", "header": "Choice",
            "options": [
                {"label": "Yes", "recommended": True},
                {"label": "No"}],
            "cwd": "/tmp/vibepulse", "session_id": "s2", "turn_id": "t2",
        }
        thread, result = self._hook_in_thread("/api/codex/question", payload)
        request_id, _envelope = self._wait_request()
        shown = self.store.pending_public()
        stamp = int(time.time())
        mac = sign_answer_v2(
            DEVICE_KEY, "codex", request_id, shown["view_sha256"],
            "deny", stamp)
        status, _raw = self._hook_request(
            "POST", f"/api/interaction/{request_id}", {
                "provider": "codex",
                "view_sha256": shown["view_sha256"],
                "verdict": "deny", "ts": stamp, "hmac": mac})
        self.assertEqual(status, 200)
        thread.join(timeout=3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(json.loads(result["value"][1]), {
            "status": "computer", "reason": "deny"})
        self._wait_remote_empty()


if __name__ == "__main__":
    unittest.main(verbosity=2)
