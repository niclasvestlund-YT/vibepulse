#!/usr/bin/env python3
"""Security and transcript tests for the local VibePulse Codex bridge."""

from __future__ import annotations

import importlib.util
import io
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / ".agents/plugins/plugins/vibepulse/scripts"
MAX_HOOK_INPUT = 64 * 1024
HOST_SOURCE_FINGERPRINT = "4c3dc7ddef6c"

PERMISSION = {
    "hook_event_name": "PermissionRequest",
    "session_id": "session-123",
    "turn_id": "turn-456",
    "cwd": "/tmp/project",
    "tool_name": "Read",
    "tool_input": {"path": "README.md"},
}
ALLOW = {
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {"behavior": "allow"},
    }
}
DENY = {
    "hookSpecificOutput": {
        "hookEventName": "PermissionRequest",
        "decision": {
            "behavior": "deny",
            "message": "Denied from VibePulse",
        },
    }
}
QUESTION = {
    "question": "How should Codex handle approvals?",
    "header": "Approvals",
    "options": [
        {
            "label": "Use the trusted hook",
            "description": "Desktop and CLI",
            "recommended": True,
        },
        {"label": "Keep computer only", "description": "No panel decisions"},
    ],
}


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def directory_symlink_or_skip(link, target):
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            raise unittest.SkipTest(
                "Windows directory symlinks require Developer Mode or "
                "SeCreateSymbolicLinkPrivilege") from exc
        raise


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _send_behavior(self, behavior):
        if "raw_chunks" in behavior:
            for chunk in behavior["raw_chunks"]:
                try:
                    self.connection.sendall(chunk)
                except OSError:
                    return
                time.sleep(behavior.get("raw_delay", 0))
            return
        if behavior.get("delay_headers"):
            time.sleep(behavior["delay_headers"])
        status = behavior.get("status", 200)
        payload = behavior.get("body", b"{}")
        self.send_response(status)
        if "location" in behavior:
            self.send_header("Location", behavior["location"])
        for content_type in behavior.get("content_types", ["application/json"]):
            self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        first = behavior.get("first_bytes")
        if first is not None:
            try:
                self.wfile.write(payload[:first])
                self.wfile.flush()
                time.sleep(behavior.get("delay_body", 0))
                self.wfile.write(payload[first:])
            except OSError:
                return
        else:
            if behavior.get("delay_body"):
                time.sleep(behavior["delay_body"])
            try:
                self.wfile.write(payload)
            except OSError:
                return

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append((self.path, dict(self.headers), body))
        self.server.request_times.append(time.monotonic())
        behavior = self.server.behavior
        if "sequence" in behavior:
            sequence = behavior["sequence"]
            behavior = sequence[min(len(self.server.requests) - 1,
                                    len(sequence) - 1)]
        self._send_behavior(behavior)

    def do_GET(self):
        self.server.requests.append((self.path, dict(self.headers), b""))
        self.server.request_times.append(time.monotonic())
        routes = self.server.behavior.get("routes", {})
        behavior = routes.get(self.path, {"status": 404, "body": b"{}"})
        self._send_behavior(behavior)

    def log_message(self, _format, *_args):
        pass


class LocalServer:
    def __init__(self, **behavior):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.behavior = behavior
        self.httpd.requests = []
        self.httpd.request_times = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self):
        return self.httpd.server_address[1]

    @property
    def requests(self):
        return self.httpd.requests

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def closed_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def run_script(name, stdin=b"", *, port=None, env=None, timeout=4):
    process_env = os.environ.copy()
    for key in tuple(process_env):
        if key.lower().endswith("_proxy") or key.lower() == "no_proxy":
            process_env.pop(key)
    for key in ("VIBEPULSE_PORT", "VIBEPULSE_CWD", "VIBEPULSE_SESSION_ID",
                "VIBEPULSE_TURN_ID", "_VIBEPULSE_TEST_READ_TIMEOUT"):
        process_env.pop(key, None)
    if port is not None:
        process_env["VIBEPULSE_PORT"] = str(port)
    if env:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / name)], input=stdin,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=process_env,
        cwd=ROOT, timeout=timeout, check=False,
    )


def rpc(method, request_id=1, params=None):
    value = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        value["params"] = params
    return value


def run_mcp(messages, *, port=None, env=None):
    wire = b"".join(
        message if isinstance(message, bytes)
        else compact(message).encode("utf-8") + b"\n"
        for message in messages
    )
    completed = run_script("mcp_server.py", wire, port=port, env=env)
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    return completed, responses


def load_loopback():
    path = SCRIPTS / "loopback.py"
    spec = importlib.util.spec_from_file_location("vibepulse_loopback_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_setup():
    path = ROOT / "tools/vibepulse_setup.py"
    spec = importlib.util.spec_from_file_location("vibepulse_setup_test", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def load_timeout_helper():
    path = ROOT / "tools/codex_mcp_timeout.py"
    spec = importlib.util.spec_from_file_location(
        "vibepulse_codex_mcp_timeout_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_macos_service():
    path = ROOT / "tools/vibepulse_macos_service.py"
    spec = importlib.util.spec_from_file_location(
        "vibepulse_macos_service_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeRunner:
    def __init__(self, responses=()):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, argv, **kwargs):
        self.calls.append((argv, kwargs))
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def result(returncode=0, stdout="", stderr=""):
    return SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=stderr)


def owned_mcp(*, repo=ROOT, python=Path(sys.executable), enabled=True,
              name="vibepulse", transport_extra=None, tool_timeout=130):
    transport = {
        "type": "stdio",
        "command": str(Path(python).resolve()),
        "args": [str(
            Path(repo).resolve() /
            ".agents/plugins/plugins/vibepulse/scripts/mcp_server.py")],
        "env": None,
        "env_vars": [],
        "cwd": None,
    }
    if transport_extra:
        transport.update(transport_extra)
    return {
        "name": name,
        "enabled": enabled,
        "disabled_reason": None,
        "transport": transport,
        "startup_timeout_sec": None,
        "tool_timeout_sec": tool_timeout,
        "auth_status": "unsupported",
    }


def plugin_listing(*, plugin_id="vibepulse@torget", name="vibepulse",
                   marketplace="torget", installed=True, enabled=True,
                   repo=ROOT, plugin_source="local", plugin_path=None,
                   marketplace_source_type="local", marketplace_root=None):
    root = Path(repo).resolve()
    if plugin_path is None:
        plugin_path = root / ".agents/plugins/plugins/vibepulse"
    if marketplace_root is None:
        marketplace_root = root
    return {
        "installed": [{
            "pluginId": plugin_id,
            "name": name,
            "marketplaceName": marketplace,
            "installed": installed,
            "enabled": enabled,
            "source": {
                "source": plugin_source,
                "path": str(plugin_path),
            },
            "marketplaceSource": {
                "sourceType": marketplace_source_type,
                "source": str(marketplace_root),
            },
        }],
        "available": [],
    }


def marketplace_listing(*, repo=ROOT, name="torget", root=None,
                        source_type="local", source=None):
    expected = Path(repo).resolve()
    return {
        "marketplaces": [{
            "name": name,
            "root": str(expected if root is None else root),
            "marketplaceSource": {
                "sourceType": source_type,
                "source": str(expected if source is None else source),
            },
        }],
    }


def install_preflight(*, repo=ROOT, python=Path(sys.executable), mcp=(),
                      plugin=None, marketplace=None):
    if plugin is None:
        plugin = {"installed": [], "available": []}
    if marketplace is None:
        marketplace = {"marketplaces": []}
    return [
        python_probe_ok(), codex_probe_ok(), json_result(list(mcp)),
        json_result(plugin), json_result(marketplace),
    ]


def uninstall_preflight(*, repo=ROOT, python=Path(sys.executable), mcp=(),
                        plugin=None, marketplace=None):
    if plugin is None:
        plugin = {"installed": [], "available": []}
    if marketplace is None:
        marketplace = {"marketplaces": []}
    return [json_result(list(mcp)), json_result(plugin),
            json_result(marketplace)]


def json_result(value):
    return result(stdout=json.dumps(value, separators=(",", ":")) + "\n")


def python_probe_ok():
    return result(stdout="vibepulse-python-3.11+\n")


def codex_probe_ok():
    return result(stdout="codex-cli 0.148.0-alpha.9\n")


class _Headers:
    def __init__(self, content_types):
        self.content_types = list(content_types)

    def get_all(self, name, default=None):
        if name.lower() == "content-type":
            return list(self.content_types)
        return default


class BytesResponse:
    def __init__(self, body, *, status=200,
                 content_types=("application/json",)):
        self.body = body
        self.limits = []
        self.status = status
        self.headers = _Headers(content_types)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, limit):
        self.limits.append(limit)
        return self.body


def healthy_diagnostics():
    return json.dumps({
        "service": "torget-tokenserver",
        "srcFingerprint": HOST_SOURCE_FINGERPRINT,
        "interactions": {
            "claude": False,
            "codex": True,
            "detail": False,
            "legacyClaudePanelV1": False,
            "relay": {"status": "off"},
            "agentStatusRelay": {"status": "off"},
            "transport": "lan",
        },
    }).encode()


def panel_diagnostics(status, **extra):
    payload = json.loads(healthy_diagnostics())
    payload["interactions"]["panel"] = {"status": status, **extra}
    return json.dumps(payload).encode()


class StatefulCodexRunner:
    """Small exact Codex model for transaction and reconciliation tests."""

    def __init__(self, *, repo=ROOT, python=Path(sys.executable),
                 mcp=False, plugin=False, marketplace=False, actions=None,
                 fail_inspection_rounds=()):
        self.repo = Path(repo).resolve()
        self.python = Path(python).resolve()
        self.state = {
            "mcp": mcp, "plugin": plugin, "marketplace": marketplace,
        }
        self.actions = {key: list(values)
                        for key, values in (actions or {}).items()}
        self.fail_inspection_rounds = set(fail_inspection_rounds)
        self.inspection_round = 0
        self.calls = []

    def _mutation(self, argv):
        words = tuple(argv[1:])
        if words[:3] == ("plugin", "marketplace", "add"):
            return "marketplace_add", "marketplace", True
        if words[:3] == ("plugin", "marketplace", "remove"):
            return "marketplace_remove", "marketplace", False
        if words[:2] == ("plugin", "add"):
            return "plugin_add", "plugin", True
        if words[:2] == ("plugin", "remove"):
            return "plugin_remove", "plugin", False
        if words[:2] == ("mcp", "add"):
            return "mcp_add", "mcp", True
        if words[:2] == ("mcp", "remove"):
            return "mcp_remove", "mcp", False
        return None

    def __call__(self, argv, **kwargs):
        argv = list(argv)
        self.calls.append((argv, kwargs))
        words = tuple(argv[1:])
        if len(argv) >= 3 and argv[1] == "-c":
            return python_probe_ok()
        if words == ("--version",):
            return codex_probe_ok()
        if words == ("mcp", "list", "--json"):
            self.inspection_round += 1
            if self.inspection_round in self.fail_inspection_rounds:
                return result(2, stderr="inspection unavailable")
            return json_result(
                [owned_mcp(repo=self.repo, python=self.python)]
                if self.state["mcp"] else [])
        if words == ("plugin", "list", "--json"):
            if self.inspection_round in self.fail_inspection_rounds:
                return result(2, stderr="inspection unavailable")
            return json_result(
                plugin_listing(repo=self.repo) if self.state["plugin"]
                else {"installed": [], "available": []})
        if words == ("plugin", "marketplace", "list", "--json"):
            if self.inspection_round in self.fail_inspection_rounds:
                return result(2, stderr="inspection unavailable")
            return json_result(
                marketplace_listing(repo=self.repo)
                if self.state["marketplace"] else {"marketplaces": []})

        mutation = self._mutation(argv)
        if mutation is None:
            return result()
        name, resource, target = mutation
        action = (self.actions.get(name) or ["commit_ok"]).pop(0)
        if name in self.actions and not self.actions[name]:
            del self.actions[name]
        if action in {"commit_ok", "commit_fail", "commit_interrupt"}:
            self.state[resource] = target
        if action == "commit_fail":
            return result(9, stderr="committed but failed")
        if action == "fail":
            return result(9, stderr="failed")
        if action == "commit_interrupt":
            raise KeyboardInterrupt()
        if action == "interrupt":
            raise KeyboardInterrupt()
        return result()


class LoopbackTests(unittest.TestCase):
    def test_url_parser_accepts_only_explicit_canonical_loopback_http(self):
        loopback = load_loopback()
        accepted = (
            "http://127.0.0.1:8737/api",
            "http://127.255.2.3:1/api",
            "http://localhost:65535/api",
            "http://localhost.:8737/api",
            "http://[::1]:8737/api",
        )
        rejected = (
            "https://127.0.0.1:8737/api",
            "http://127.0.0.1/api",
            "http://user@127.0.0.1:8737/api",
            "http://127.0.0.1:8737/api#fragment",
            "http://127.0.0.1:8737/api?query=1",
            "http://example.com:8737/api",
            "http://127.0.0.1.example:8737/api",
            "http://127.1:8737/api",
            "http://2130706433:8737/api",
            "http://0x7f000001:8737/api",
            "http://[::1%25lo0]:8737/api",
            "http://127.0.0.1:0/api",
            "http://127.0.0.1:65536/api",
            "http://127.0.0.1:notaport/api",
            "http:///api",
        )
        for url in accepted:
            with self.subTest(url=url):
                self.assertTrue(loopback.is_loopback_http_url(url))
        for url in rejected:
            with self.subTest(url=url):
                self.assertFalse(loopback.is_loopback_http_url(url))

    def test_post_uses_compact_json_content_type_and_no_proxy(self):
        loopback = load_loopback()
        response = compact({"ok": True}).encode()
        with LocalServer(body=response) as target, LocalServer(body=b"{}") as proxy:
            old_proxy = os.environ.get("HTTP_PROXY")
            os.environ["HTTP_PROXY"] = f"http://127.0.0.1:{proxy.port}"
            try:
                result = loopback.post_json(
                    f"http://127.0.0.1:{target.port}/api", {"word": "räv"})
            finally:
                if old_proxy is None:
                    os.environ.pop("HTTP_PROXY", None)
                else:
                    os.environ["HTTP_PROXY"] = old_proxy
        self.assertEqual(result, {"ok": True})
        self.assertEqual(len(proxy.requests), 0)
        path, headers, body = target.requests[0]
        self.assertEqual(path, "/api")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(body, compact({"word": "räv"}).encode("utf-8"))

    def test_request_and_response_caps_are_limit_plus_one(self):
        loopback = load_loopback()
        with LocalServer(body=b"{}") as server:
            base = f"http://127.0.0.1:{server.port}/api"
            self.assertEqual(loopback.post_json(base, {"x": "a" * 4088}), {})
            self.assertIsNone(loopback.post_json(base, {"x": "a" * 4089}))
            self.assertEqual(len(server.requests), 1)
        exact = compact({"x": "a" * 4088}).encode()
        oversized = compact({"x": "a" * 4089}).encode()
        self.assertEqual(len(exact), 4096)
        with LocalServer(body=exact) as server:
            self.assertEqual(loopback.post_json(
                f"http://127.0.0.1:{server.port}/api", {}),
                {"x": "a" * 4088})
        with LocalServer(body=oversized) as server:
            self.assertIsNone(loopback.post_json(
                f"http://127.0.0.1:{server.port}/api", {}))

    def test_connect_timeout_is_replaced_by_longer_read_timeout(self):
        loopback = load_loopback()
        with LocalServer(delay_headers=0.12, body=b'{"ok":true}') as server:
            started = time.monotonic()
            result = loopback.post_json(
                f"http://127.0.0.1:{server.port}/api", {},
                connect_timeout=0.03, read_timeout=0.5)
        self.assertEqual(result, {"ok": True})
        self.assertGreaterEqual(time.monotonic() - started, 0.1)

    def test_absolute_deadline_stops_drip_headers_and_body(self):
        loopback = load_loopback()
        header_chunks = [
            b"HTTP/1.1 200 OK\r\n",
            b"Content-Type: application/json\r\n",
            b"Content-Length: 2\r\n",
            b"\r\n",
            b"{}",
        ]
        body = b'{"ok":true}'
        body_chunks = [
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: 11\r\n\r\n",
            *[bytes((byte,)) for byte in body],
        ]
        for chunks in (header_chunks, body_chunks):
            with self.subTest(kind="headers" if chunks is header_chunks else "body"), \
                    LocalServer(raw_chunks=chunks, raw_delay=0.08) as server:
                started = time.monotonic()
                result = loopback.post_json(
                    f"http://127.0.0.1:{server.port}/api", {},
                    read_timeout=0.12)
                elapsed = time.monotonic() - started
            self.assertIsNone(result)
            # The result assertion proves the absolute deadline won instead of
            # accepting the complete drip-fed response.  Keep the wall-clock
            # guard generous enough for contended Windows CI runners.
            self.assertLess(elapsed, 0.75)

    def test_read_timeout_and_bad_responses_return_none(self):
        loopback = load_loopback()
        with LocalServer(body=b'{"ok":true}', first_bytes=1,
                         delay_body=0.2) as server:
            started = time.monotonic()
            result = loopback.post_json(
                f"http://127.0.0.1:{server.port}/api", {}, read_timeout=0.04)
        self.assertIsNone(result)
        self.assertLess(time.monotonic() - started, 1.5)
        for body in (b"\xff", b"{", b"[]", b'{"x":NaN}',
                     b'{"x":1,"x":2}'):
            with self.subTest(body=body), LocalServer(body=body) as server:
                self.assertIsNone(loopback.post_json(
                    f"http://127.0.0.1:{server.port}/api", {}))

    def test_foreign_redirect_is_rejected_without_dns_lookup(self):
        loopback = load_loopback()
        with LocalServer(status=302, body=b"", location="http://example.invalid/x") as server:
            self.assertIsNone(loopback.post_json(
                f"http://127.0.0.1:{server.port}/api", {}))
        self.assertEqual(len(server.requests), 1)

    def test_response_requires_one_clean_json_content_type(self):
        loopback = load_loopback()
        accepted = (
            ["application/json"],
            ["Application/JSON; charset=UTF-8"],
            ["application/json; charset=ascii"],
        )
        rejected = (
            [],
            ["text/plain"],
            ["application/json; charset=latin-1"],
            ["application/json; boundary=nope"],
            ["application/json", "application/json"],
            ["application/json", "text/plain"],
        )
        for content_types in accepted:
            with self.subTest(content_types=content_types), LocalServer(
                    body=b'{"ok":true}', content_types=content_types) as server:
                self.assertEqual(loopback.post_json(
                    f"http://127.0.0.1:{server.port}/api", {}), {"ok": True})
        for content_types in rejected:
            with self.subTest(content_types=content_types), LocalServer(
                    body=b'{"ok":true}', content_types=content_types) as server:
                self.assertIsNone(loopback.post_json(
                    f"http://127.0.0.1:{server.port}/api", {}))


class PermissionHookTests(unittest.TestCase):
    def test_allow_and_deny_are_forwarded_semantically_unchanged(self):
        for decision in (ALLOW, DENY):
            with self.subTest(decision=decision), LocalServer(
                    body=compact(decision).encode()) as server:
                completed = run_script(
                    "permission_hook.py", compact(PERMISSION).encode(),
                    port=server.port)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(json.loads(completed.stdout), decision)
                self.assertEqual(completed.stderr, b"")
                path, headers, body = server.requests[0]
                self.assertEqual(path, "/api/codex/permission")
                self.assertEqual(headers["Content-Type"], "application/json")
                self.assertEqual(json.loads(body), PERMISSION)

    def test_unicode_decision_is_emitted_as_utf8(self):
        decision = {
            "hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {
                    "behavior": "deny",
                    "message": "Nekad från VibePulse – försök på datorn",
                },
            },
        }
        with LocalServer(body=compact(decision).encode("utf-8")) as server:
            completed = run_script(
                "permission_hook.py", compact(PERMISSION).encode("utf-8"),
                port=server.port)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(
            json.loads(completed.stdout.decode("utf-8", errors="strict")),
            decision)
        self.assertEqual(completed.stderr, b"")

    def test_invalid_port_fails_silent_without_using_default(self):
        for port in ("", "0", "65536", "+8737", " 8737", "eight"):
            with self.subTest(port=port):
                completed = run_script(
                    "permission_hook.py", compact(PERMISSION).encode(),
                    env={"VIBEPULSE_PORT": port})
                self.assertEqual((completed.returncode, completed.stdout), (0, b""))

    def test_invalid_or_oversized_input_is_empty_success(self):
        invalid = (b"", b"\xff", b"{", b"[]", b"{} trailing",
                   b" " * (MAX_HOOK_INPUT + 1))
        for body in invalid:
            with self.subTest(size=len(body)):
                completed = run_script("permission_hook.py", body,
                                       port=closed_port())
                self.assertEqual((completed.returncode, completed.stdout), (0, b""))

    def test_nonstandard_or_duplicate_json_never_reaches_http(self):
        with LocalServer(body=compact(ALLOW).encode()) as server:
            for body in (b'{"value":NaN}', b'{"value":1,"value":2}'):
                with self.subTest(body=body):
                    completed = run_script("permission_hook.py", body,
                                           port=server.port)
                    self.assertEqual((completed.returncode, completed.stdout),
                                     (0, b""))
        self.assertEqual(server.requests, [])

    def test_transport_http_and_response_failures_are_empty_success(self):
        completed = run_script("permission_hook.py", compact(PERMISSION).encode(),
                               port=closed_port())
        self.assertEqual((completed.returncode, completed.stdout), (0, b""))
        bad_responses = (
            {"status": 500, "body": b"{}"},
            {"body": b"\xff"},
            {"body": b"{"},
            {"body": b"[]"},
            {"body": b"{" + b" " * 4096 + b"}"},
            {"body": compact({"decision": "allow"}).encode()},
            {"body": compact({"hookSpecificOutput": {
                "hookEventName": "PermissionRequest",
                "decision": {"behavior": "approve"}}}).encode()},
        )
        for behavior in bad_responses:
            with self.subTest(behavior=behavior), LocalServer(**behavior) as server:
                completed = run_script(
                    "permission_hook.py", compact(PERMISSION).encode(),
                    port=server.port)
                self.assertEqual((completed.returncode, completed.stdout), (0, b""))

    def test_timeout_and_foreign_redirect_are_empty_success(self):
        with LocalServer(delay_headers=0.2, body=compact(ALLOW).encode()) as server:
            completed = run_script(
                "permission_hook.py", compact(PERMISSION).encode(), port=server.port,
                env={"_VIBEPULSE_TEST_READ_TIMEOUT": "0.04"})
            self.assertEqual((completed.returncode, completed.stdout), (0, b""))

    def test_valid_decision_with_untrusted_content_type_is_empty_success(self):
        for content_types in ([], ["text/plain"],
                              ["application/json", "text/plain"]):
            with self.subTest(content_types=content_types), LocalServer(
                    body=compact(ALLOW).encode(),
                    content_types=content_types) as server:
                completed = run_script(
                    "permission_hook.py", compact(PERMISSION).encode(),
                    port=server.port)
                self.assertEqual((completed.returncode, completed.stdout),
                                 (0, b""))
        with LocalServer(status=302, body=b"",
                         location="http://example.invalid/decision") as server:
            completed = run_script(
                "permission_hook.py", compact(PERMISSION).encode(), port=server.port)
            self.assertEqual((completed.returncode, completed.stdout), (0, b""))


class SessionStartTests(unittest.TestCase):
    def test_context_is_bounded_provider_correct_and_fail_safe(self):
        payload = {"hook_event_name": "SessionStart", "session_id": "s"}
        completed = run_script(
            "session_start.py", compact(payload).encode(), port=closed_port())
        self.assertEqual(completed.returncode, 0)
        body = json.loads(completed.stdout)
        self.assertEqual(body["hookSpecificOutput"]["hookEventName"], "SessionStart")
        context = body["hookSpecificOutput"]["additionalContext"]
        self.assertLessEqual(len(context), 1750)
        self.assertIn("mcp__vibepulse__ask", context)
        self.assertIn("2–3 option questions", context)
        self.assertIn("request_user_input", context)
        self.assertIn("unavailable, times out, or reports computer fallback", context)
        self.assertIn("Never treat silence, panel absence, or fallback as approval", context)
        self.assertIn("Permission decisions remain subject to Codex policy", context)
        self.assertIn("VibePulse startup health: SERVER UNAVAILABLE", context)
        self.assertNotIn(str(ROOT), context)

    def test_startup_health_separates_device_path_from_provider_stale(self):
        payload = {"hook_event_name": "SessionStart", "session_id": "s"}
        root = {
            "service": "torget-tokenserver",
            "srcFingerprint": HOST_SOURCE_FINGERPRINT,
            "claudeProbe": "usage_http_200 + ok",
            "claudeCredential": {"status": "ready", "expiresInMin": 480},
            "interactions": {
                "claude": True, "codex": True,
                "panel": {"status": "stale", "ageS": 60},
            },
        }
        fresh = {
            "claudeWeekStale": False,
            "claudeModelWeekStale": False,
            "codexWeekStale": False,
        }
        routes = {
            "/": {"body": compact(root).encode()},
            "/api/tokens": {"body": compact(fresh).encode()},
        }
        with LocalServer(routes=routes) as server:
            completed = run_script(
                "session_start.py", compact(payload).encode(), port=server.port)
        context = json.loads(completed.stdout)["hookSpecificOutput"][
            "additionalContext"]
        self.assertIn("DEVICE PATH STALE", context)
        self.assertIn("provider data is fresh", context)
        self.assertNotIn("ageS", context)

        stale = dict(fresh, claudeModelWeekStale=True)
        routes["/api/tokens"] = {"body": compact(stale).encode()}
        with LocalServer(routes=routes) as server:
            completed = run_script(
                "session_start.py", compact(payload).encode(), port=server.port)
        context = json.loads(completed.stdout)["hookSpecificOutput"][
            "additionalContext"]
        self.assertIn("PROVIDER DATA STALE (Claude)", context)
        self.assertIn("active Claude probe is live", context)
        self.assertNotIn("DEVICE PATH STALE", context)

    def test_startup_health_reports_ready_and_credential_risk_separately(self):
        payload = {"hook_event_name": "SessionStart", "session_id": "s"}
        root = {
            "service": "torget-tokenserver",
            "srcFingerprint": HOST_SOURCE_FINGERPRINT,
            "claudeProbe": "usage_http_200 + ok",
            "claudeCredential": {"status": "expired", "expiresInMin": 0},
            "interactions": {
                "claude": True, "codex": False,
                "panel": {"status": "ready", "ageS": 1},
            },
        }
        tokens = {
            "claudeWeekStale": False,
            "claudeModelWeekStale": False,
            "codexWeekStale": True,
        }
        routes = {
            "/": {"body": compact(root).encode()},
            "/api/tokens": {"body": compact(tokens).encode()},
        }
        with LocalServer(routes=routes) as server:
            completed = run_script(
                "session_start.py", compact(payload).encode(), port=server.port)
        context = json.loads(completed.stdout)["hookSpecificOutput"][
            "additionalContext"]
        self.assertIn("startup health: HEALTHY", context)
        self.assertIn("Saved Claude credential is expired", context)
        self.assertNotIn("PROVIDER DATA STALE", context)

    def test_startup_health_detects_plugin_host_version_drift(self):
        payload = {"hook_event_name": "SessionStart", "session_id": "s"}
        root = {
            "service": "torget-tokenserver",
            "srcFingerprint": "000000000000",
            "interactions": {"claude": True, "codex": True,
                             "panel": {"status": "ready"}},
        }
        routes = {
            "/": {"body": compact(root).encode()},
            "/api/tokens": {"body": compact({
                "claudeWeekStale": False,
                "claudeModelWeekStale": False,
                "codexWeekStale": False,
            }).encode()},
        }
        with LocalServer(routes=routes) as server:
            completed = run_script(
                "session_start.py", compact(payload).encode(), port=server.port)
        context = json.loads(completed.stdout)["hookSpecificOutput"][
            "additionalContext"]
        self.assertIn("SERVICE VERSION DRIFT", context)
        self.assertNotIn("000000000000", context)

    def test_startup_health_surfaces_confirmed_device_self_recovery(self):
        payload = {"hook_event_name": "SessionStart", "session_id": "s"}
        root = {
            "service": "torget-tokenserver",
            "srcFingerprint": HOST_SOURCE_FINGERPRINT,
            "claudeProbe": "usage_http_200 + ok",
            "claudeCredential": {"status": "ready", "expiresInMin": 480},
            "interactions": {
                "claude": True, "codex": True,
                "panel": {"status": "ready", "ageS": 1,
                          "httpStallRecoveryBoot": True},
            },
        }
        routes = {
            "/": {"body": compact(root).encode()},
            "/api/tokens": {"body": compact({
                "claudeWeekStale": False,
                "claudeModelWeekStale": False,
                "codexWeekStale": False,
            }).encode()},
        }
        with LocalServer(routes=routes) as server:
            completed = run_script(
                "session_start.py", compact(payload).encode(), port=server.port)
        context = json.loads(completed.stdout)["hookSpecificOutput"][
            "additionalContext"]
        self.assertIn("HEALTHY AFTER DEVICE SELF-RECOVERY", context)

    def test_plugin_expected_host_fingerprint_matches_checkout(self):
        script = (SCRIPTS / "session_start.py").read_text(encoding="utf-8")
        expected = re.search(
            r'EXPECTED_HOST_SOURCE_FINGERPRINT = "([0-9a-f]{12})"', script)
        self.assertIsNotNone(expected)
        digest = hashlib.sha256()
        source = ROOT / "tools/tokenserver"
        for path in sorted(source.glob("*.py")):
            if path.name.startswith("test_") or path.name == "smoke.py":
                continue
            digest.update(path.name.encode())
            digest.update(path.read_text(encoding="utf-8").encode("utf-8"))
        self.assertEqual(expected.group(1), digest.hexdigest()[:12])

    def test_invalid_or_oversized_input_is_empty_success(self):
        for body in (b"", b"\xff", b"[]", b"{", b'{"x":NaN}',
                     b'{"x":1,"x":2}', b" " * (MAX_HOOK_INPUT + 1)):
            with self.subTest(size=len(body)):
                completed = run_script("session_start.py", body)
                self.assertEqual((completed.returncode, completed.stdout), (0, b""))


class McpServerTests(unittest.TestCase):
    def test_initialize_notification_ping_and_list_protocol(self):
        messages = [
            rpc("initialize", 1, {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "1"},
            }),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            rpc("ping", "ping-id"),
            rpc("tools/list", 3),
        ]
        completed, responses = run_mcp(messages)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stderr, b"")
        self.assertEqual([r["id"] for r in responses], [1, "ping-id", 3])
        initialized = responses[0]["result"]
        self.assertEqual(initialized["protocolVersion"], "2025-06-18")
        self.assertEqual(initialized["serverInfo"]["name"], "vibepulse")
        self.assertEqual(initialized["serverInfo"]["version"], "0.1.7")
        self.assertEqual(initialized["capabilities"], {"tools": {"listChanged": False}})
        self.assertEqual(responses[1]["result"], {})
        tools = responses[2]["result"]["tools"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["name"], "ask")
        schema = tools[0]["inputSchema"]
        self.assertEqual(schema["required"], ["question", "options"])
        self.assertFalse(schema["additionalProperties"])
        options = schema["properties"]["options"]
        self.assertEqual((options["minItems"], options["maxItems"]), (2, 3))
        item = options["items"]
        self.assertEqual(item["required"], ["label"])
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(item["properties"]["recommended"]["type"], "boolean")

    def test_initialize_rejects_unknown_or_control_bearing_fields(self):
        base = {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1"},
        }
        messages = [
            rpc("initialize", 1, {**base, "unknown": True}),
            rpc("initialize", 2, {**base,
                "clientInfo": {"name": "bad\u0001", "version": "1"}}),
        ]
        _, responses = run_mcp(messages)
        self.assertEqual([response["error"]["code"] for response in responses],
                         [-32602, -32602])

    def test_current_codex_initialize_with_title_and_elicitation_lists_tools(self):
        completed, responses = run_mcp([
            rpc("initialize", 1, {
                "protocolVersion": "2025-06-18",
                "capabilities": {"elicitation": {}},
                "clientInfo": {
                    "name": "codex-mcp-client",
                    "title": "Codex",
                    "version": "0.92.0",
                },
            }),
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            rpc("tools/list", 2, {"_meta": {}}),
        ])
        self.assertEqual(completed.returncode, 0)
        self.assertEqual([response["id"] for response in responses], [1, 2])
        self.assertEqual(responses[0]["result"]["protocolVersion"],
                         "2025-06-18")
        self.assertEqual([tool["name"] for tool in
                          responses[1]["result"]["tools"]], ["ask"])

    def test_list_and_ping_allow_only_bounded_request_metadata(self):
        completed, responses = run_mcp([
            rpc("tools/list", 1, {"_meta": {"progressToken": "list-1"}}),
            rpc("ping", 2, {"_meta": {}}),
            rpc("tools/list", 3, {"_meta": "not-an-object"}),
            rpc("tools/list", 4, {"_meta": {}, "cursor": None}),
        ])
        self.assertEqual(completed.returncode, 0)
        self.assertEqual([response["id"] for response in responses],
                         [1, 2, 3, 4])
        self.assertEqual([tool["name"] for tool in
                          responses[0]["result"]["tools"]], ["ask"])
        self.assertEqual(responses[1]["result"], {})
        self.assertEqual(responses[2]["error"]["code"], -32602)
        self.assertEqual(responses[3]["error"]["code"], -32602)

    def test_answered_call_returns_identical_text_and_structured_content(self):
        answered = {"status": "answered", "option_index": 0,
                    "answer": "Use the trusted hook"}
        with LocalServer(body=compact(answered).encode()) as server:
            completed, responses = run_mcp([
                rpc("tools/call", 7, {
                    "name": "ask", "arguments": QUESTION,
                    "_meta": {"progressToken": "ask-7"},
                })
            ], port=server.port, env={
                "VIBEPULSE_CWD": "/tmp/project",
                "VIBEPULSE_SESSION_ID": "session-123",
                "VIBEPULSE_TURN_ID": "turn-456",
            })
        self.assertEqual(completed.stderr, b"")
        result = responses[0]["result"]
        self.assertNotIn("isError", result)
        self.assertEqual(result["structuredContent"], answered)
        self.assertEqual(json.loads(result["content"][0]["text"]), answered)
        path, headers, body = server.requests[0]
        self.assertEqual(path, "/api/codex/question")
        self.assertEqual(headers["Content-Type"], "application/json")
        expected = dict(QUESTION)
        expected.update(cwd="/tmp/project", session_id="session-123",
                        turn_id="turn-456")
        self.assertEqual(json.loads(body), expected)

    def test_identity_is_automatic_stable_per_process_and_private(self):
        answered = {"status": "answered", "option_index": 0,
                    "answer": "Use the trusted hook"}
        with LocalServer(body=compact(answered).encode()) as server:
            completed, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION}),
                rpc("tools/call", 2, {"name": "ask", "arguments": QUESTION}),
            ], port=server.port)
        self.assertEqual([response["result"]["structuredContent"]
                          for response in responses], [answered, answered])
        posted = [json.loads(request[2]) for request in server.requests]
        self.assertEqual(len(posted), 2)
        for body in posted:
            for field in ("cwd", "session_id", "turn_id"):
                self.assertIsInstance(body[field], str)
                self.assertTrue(body[field])
                self.assertFalse(any(ord(char) < 32 for char in body[field]))
        self.assertEqual(posted[0]["cwd"], str(ROOT))
        self.assertEqual(posted[0]["session_id"], posted[1]["session_id"])
        self.assertNotEqual(posted[0]["turn_id"], posted[1]["turn_id"])
        for body in posted:
            self.assertNotIn(body["session_id"].encode(), completed.stdout)
            self.assertNotIn(body["turn_id"].encode(), completed.stdout)
            self.assertNotIn(body["cwd"].encode(), completed.stdout)

    def test_malformed_identity_environment_falls_back_to_safe_metadata(self):
        answered = {"status": "answered", "option_index": 0,
                    "answer": "Use the trusted hook"}
        with LocalServer(body=compact(answered).encode()) as server:
            _, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION})
            ], port=server.port, env={
                "VIBEPULSE_CWD": "bad\npath",
                "VIBEPULSE_SESSION_ID": "x" * 300,
                "VIBEPULSE_TURN_ID": "bad\u202eturn",
            })
        self.assertEqual(responses[0]["result"]["structuredContent"], answered)
        posted = json.loads(server.requests[0][2])
        self.assertEqual(posted["cwd"], str(ROOT))
        self.assertNotEqual(posted["session_id"], "x" * 300)
        self.assertNotEqual(posted["turn_id"], "bad\u202eturn")

    def test_transport_and_invalid_server_response_use_explicit_computer_fallback(self):
        completed, responses = run_mcp([
            rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION})
        ], port=closed_port())
        self.assertEqual(completed.returncode, 0)
        fallback = responses[0]["result"]["structuredContent"]
        self.assertEqual(fallback["status"], "computer")
        self.assertIn("request_user_input", fallback["instruction"])
        self.assertNotIn("approve", compact(fallback).lower())
        self.assertEqual(
            json.loads(responses[0]["result"]["content"][0]["text"]), fallback)
        for body in (b"[]", b"{", b"\xff", b"{" + b" " * 4096 + b"}"):
            with self.subTest(body=body), LocalServer(body=body) as server:
                _, responses = run_mcp([
                    rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION})
                ], port=server.port)
                self.assertEqual(
                    responses[0]["result"]["structuredContent"]["status"],
                    "computer")

    def test_held_response_timeout_returns_computer_fallback_quickly(self):
        with LocalServer(delay_headers=0.6, body=b'{}') as server:
            _, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION})
            ], port=server.port,
               env={"_VIBEPULSE_TEST_READ_TIMEOUT": "0.04"})
            finished = time.monotonic()
            self.assertEqual(len(server.httpd.request_times), 1)
            response_elapsed = finished - server.httpd.request_times[0]
        # Measure the transport deadline, not unrelated Python process startup.
        # Without the fixed response deadline this follows delay_headers (0.6 s).
        self.assertLess(response_elapsed, 0.25)
        self.assertEqual(
            responses[0]["result"]["structuredContent"]["status"], "computer")

    def test_mcp_recovers_after_absolute_drip_deadline(self):
        answered = {"status": "answered", "option_index": 0,
                    "answer": "Use the trusted hook"}
        slow_body = compact(answered).encode()
        slow_chunks = [
            (b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
             + f"Content-Length: {len(slow_body)}\r\n\r\n".encode()),
            *[bytes((byte,)) for byte in slow_body],
        ]
        with LocalServer(sequence=[
                {"raw_chunks": slow_chunks, "raw_delay": 0.04},
                {"body": compact(answered).encode()},
        ]) as server:
            started = time.monotonic()
            _, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION}),
                rpc("tools/call", 2, {"name": "ask", "arguments": QUESTION}),
            ], port=server.port,
               env={"_VIBEPULSE_TEST_READ_TIMEOUT": "0.12"})
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.6)
        self.assertEqual(responses[0]["result"]["structuredContent"]["status"],
                         "computer")
        self.assertEqual(responses[1]["result"]["structuredContent"], answered)

    def test_tool_schema_states_utf8_byte_limits_without_false_max_length(self):
        _, responses = run_mcp([rpc("tools/list", 1)])
        schema = responses[0]["result"]["tools"][0]["inputSchema"]
        question = schema["properties"]["question"]
        header = schema["properties"]["header"]
        option = schema["properties"]["options"]["items"]["properties"]
        for field, limit in ((question, 96), (header, 64),
                             (option["label"], 64),
                             (option["description"], 64)):
            self.assertNotIn("maxLength", field)
            self.assertEqual(field["minLength"], 1)
            self.assertEqual(field["x-vibepulse-maxUtf8Bytes"], limit)
            self.assertIn(f"{limit} UTF-8 bytes", field["description"])
        options = schema["properties"]["options"]
        self.assertEqual((options["minItems"], options["maxItems"]), (2, 3))

    def test_multibyte_text_obeys_runtime_utf8_byte_boundaries(self):
        question_ok = dict(QUESTION, question="å" * 48)
        question_bad = dict(QUESTION, question="å" * 49)
        with LocalServer(body=compact({
                "status": "answered", "option_index": 0,
                "answer": "Use the trusted hook",
        }).encode()) as server:
            _, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": question_ok}),
                rpc("tools/call", 2, {"name": "ask", "arguments": question_bad}),
            ], port=server.port)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(responses[0]["result"]["structuredContent"]["status"],
                         "answered")
        self.assertTrue(responses[1]["result"]["isError"])

        label_ok = dict(QUESTION)
        label_ok["options"] = [
            {"label": "å" * 32, "recommended": True}, {"label": "b"},
        ]
        label_bad = dict(label_ok)
        label_bad["options"] = [
            {"label": "å" * 33, "recommended": True}, {"label": "b"},
        ]
        with LocalServer(body=compact({
                "status": "answered", "option_index": 0,
                "answer": "å" * 32,
        }).encode()) as server:
            _, responses = run_mcp([
                rpc("tools/call", 3, {"name": "ask", "arguments": label_ok}),
                rpc("tools/call", 4, {"name": "ask", "arguments": label_bad}),
            ], port=server.port)
        self.assertEqual(len(server.requests), 1)
        self.assertEqual(responses[0]["result"]["structuredContent"]["answer"],
                         "å" * 32)
        self.assertTrue(responses[1]["result"]["isError"])

    def test_answered_json_with_untrusted_content_type_falls_back(self):
        answered = {"status": "answered", "option_index": 0,
                    "answer": "Use the trusted hook"}
        for content_types in ([], ["text/plain"],
                              ["application/json", "text/plain"]):
            with self.subTest(content_types=content_types), LocalServer(
                    body=compact(answered).encode(),
                    content_types=content_types) as server:
                _, responses = run_mcp([
                    rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION})
                ], port=server.port)
                result = responses[0]["result"]["structuredContent"]
                self.assertEqual(result["status"], "computer")
                self.assertIn("request_user_input", result["instruction"])

    def test_answered_result_must_match_the_sole_recommended_option(self):
        unmarked = dict(QUESTION)
        unmarked["options"] = [
            {"label": "First"}, {"label": "Second"},
        ]
        three = dict(QUESTION)
        three["options"] = [
            {"label": "First"},
            {"label": "Second"},
            {"label": "Third", "recommended": True},
        ]
        cases = (
            (QUESTION, {"status": "answered", "option_index": 0,
                        "answer": "Keep computer only"}),
            (QUESTION, {"status": "answered", "option_index": 2,
                        "answer": "Use the trusted hook"}),
            (QUESTION, {"status": "answered", "option_index": False,
                        "answer": "Use the trusted hook"}),
            (unmarked, {"status": "answered", "option_index": 0,
                        "answer": "First"}),
            (three, {"status": "answered", "option_index": 0,
                     "answer": "First"}),
        )
        for arguments, answered in cases:
            with self.subTest(arguments=arguments, answered=answered), LocalServer(
                    body=compact(answered).encode()) as server:
                _, responses = run_mcp([
                    rpc("tools/call", 1, {"name": "ask", "arguments": arguments})
                ], port=server.port)
                result = responses[0]["result"]["structuredContent"]
                self.assertEqual(result["status"], "computer")
                self.assertIn("request_user_input", result["instruction"])

    def test_three_option_answer_preserves_exact_recommended_payload(self):
        arguments = dict(QUESTION)
        arguments["options"] = [
            {"label": "First"}, {"label": "Second"},
            {"label": "Third", "recommended": True},
        ]
        answered = {"status": "answered", "option_index": 2,
                    "answer": "Third"}
        with LocalServer(body=compact(answered).encode()) as server:
            _, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": arguments})
            ], port=server.port)
        self.assertEqual(responses[0]["result"]["structuredContent"], answered)

    def test_invalid_tool_calls_are_errors_and_never_reach_http(self):
        bad_calls = (
            {"name": "other", "arguments": QUESTION},
            {"name": "ask", "arguments": []},
            {"name": "ask", "arguments": {**QUESTION, "extra": True}},
            {"name": "ask", "arguments": {**QUESTION, "question": " "}},
            {"name": "ask", "arguments": {**QUESTION, "question": "x" * 97}},
            {"name": "ask", "arguments": {**QUESTION, "question": "bad\u0001"}},
            {"name": "ask", "arguments": {**QUESTION, "options": [
                {"label": "Only"}]}},
            {"name": "ask", "arguments": {**QUESTION, "options": [
                {"label": "a", "recommended": True},
                {"label": "b", "recommended": True}]}},
            {"name": "ask", "arguments": {**QUESTION, "options": [
                {"label": "a", "unknown": 1}, {"label": "b"}]}},
            {"name": "ask", "arguments": {**QUESTION, "options": [
                {"label": "x" * 65}, {"label": "b"}]}},
        )
        with LocalServer(body=b'{}') as server:
            _, responses = run_mcp([
                rpc("tools/call", index, params)
                for index, params in enumerate(bad_calls)
            ], port=server.port)
        self.assertEqual(len(server.requests), 0)
        self.assertEqual(len(responses), len(bad_calls))
        self.assertTrue(all(response["result"]["isError"] for response in responses))

    def test_tool_call_rejects_invalid_or_unbounded_metadata(self):
        deep = {"value": True}
        for _ in range(20):
            deep = {"nested": deep}
        bad_calls = (
            {"name": "ask", "arguments": QUESTION, "_meta": "bad"},
            {"name": "ask", "arguments": QUESTION, "_meta": deep},
            {"name": "ask", "arguments": QUESTION, "unknown": {}},
        )
        with LocalServer(body=b'{}') as server:
            _, responses = run_mcp([
                rpc("tools/call", index, params)
                for index, params in enumerate(bad_calls)
            ], port=server.port)
        self.assertEqual(server.requests, [])
        self.assertTrue(all(response["result"]["isError"]
                            for response in responses))

    def test_bad_params_unknown_methods_and_notifications_handle_ids_exactly(self):
        messages = [
            rpc("tools/list", 1, {"cursor": "not-supported"}),
            rpc("unknown", 2),
            {"jsonrpc": "2.0", "method": "unknown"},
            {"jsonrpc": "2.0", "method": "ping"},
            {"jsonrpc": "1.0", "id": 3, "method": "ping"},
            {"jsonrpc": "2.0", "id": {"bad": "id"}, "method": "ping"},
            {"jsonrpc": "2.0", "id": "bad\u0001", "method": "ping"},
            {"jsonrpc": "2.0", "id": 4, "method": "bad\u0001"},
        ]
        _, responses = run_mcp(messages)
        self.assertEqual(len(responses), 6)
        self.assertEqual(responses[0]["error"]["code"], -32602)
        self.assertEqual(responses[1]["error"]["code"], -32601)
        self.assertEqual(responses[2]["error"]["code"], -32600)
        self.assertIsNone(responses[2]["id"])
        self.assertEqual(responses[3]["error"]["code"], -32600)
        self.assertEqual(responses[4]["error"]["code"], -32600)
        self.assertEqual(responses[5]["error"]["code"], -32600)

    def test_only_initialized_may_be_a_notification_and_ids_correlate(self):
        deep_params = {"value": True}
        for _ in range(20):
            deep_params = {"nested": deep_params}
        with LocalServer(body=compact({
                "status": "answered", "option_index": 0,
                "answer": "Use the trusted hook",
        }).encode()) as server:
            _, responses = run_mcp([
                {"jsonrpc": "2.0", "method": "tools/call", "params": {
                    "name": "ask", "arguments": QUESTION}},
                {"jsonrpc": "2.0", "method": "initialize", "params": {
                    "protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"}}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                rpc("notifications/initialized", 41),
                rpc("notifications/initialized", 43, deep_params),
                rpc("ping", None),
                {"jsonrpc": "2.0", "id": False, "method": "ping"},
                rpc("ping", 42),
            ], port=server.port)
        self.assertEqual(server.requests, [])
        self.assertEqual(len(responses), 5)
        self.assertEqual(responses[0]["id"], 41)
        self.assertEqual(responses[0]["error"]["code"], -32600)
        self.assertEqual(responses[1]["id"], 43)
        self.assertEqual(responses[1]["error"]["code"], -32600)
        self.assertEqual(responses[2], {"jsonrpc": "2.0", "id": None,
                                        "result": {}})
        self.assertEqual(responses[3]["id"], None)
        self.assertEqual(responses[3]["error"]["code"], -32600)
        self.assertEqual(responses[4], {"jsonrpc": "2.0", "id": 42,
                                        "result": {}})

    def test_bad_lines_are_bounded_and_server_survives_for_next_message(self):
        oversized = b'{' + b'"x":' + b'"' + b'a' * (MAX_HOOK_INPUT + 1) + b'"}\n'
        messages = [b"not json\n", b"\xff\n", b'{"jsonrpc":"2.0","id":1,"id":2,"method":"ping"}\n',
                    b'{"jsonrpc":"2.0","id":3,"method":"ping","params":NaN}\n',
                    oversized, rpc("ping", 9)]
        completed, responses = run_mcp(messages)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(len(responses), 6)
        self.assertTrue(all(r["error"]["code"] == -32600 for r in responses[:5]))
        self.assertEqual(responses[5], {"jsonrpc": "2.0", "id": 9, "result": {}})

    def test_deep_objects_context_overflow_and_secrets_never_reach_server_or_stdout(self):
        deep = {"label": "a"}
        for _ in range(20):
            deep = {"nested": deep}
        bad = dict(QUESTION)
        bad["options"] = [deep, {"label": "b"}]
        secret = "SECRET_MCP_NOISE_6f425c"
        with LocalServer(body=b"{}") as server:
            completed, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": bad}),
                rpc("tools/call", 2, {"name": "ask", "arguments": QUESTION}),
            ], port=server.port, env={
                "VIBEPULSE_CWD": "x" * 4097,
                "UNRELATED_SECRET": secret,
            })
        self.assertEqual(len(server.requests), 1)
        self.assertTrue(responses[0]["result"]["isError"])
        self.assertEqual(responses[1]["result"]["structuredContent"]["status"],
                         "computer")
        self.assertNotIn(secret.encode(), completed.stdout)
        self.assertEqual(completed.stderr, b"")

    def test_only_numeric_loopback_port_is_honored(self):
        for port in ("https://example.com", "0", "+8737", "65536"):
            completed, responses = run_mcp([
                rpc("tools/call", 1, {"name": "ask", "arguments": QUESTION})
            ], env={"VIBEPULSE_PORT": port})
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(
                responses[0]["result"]["structuredContent"]["status"],
                "computer")


class PluginPackageTests(unittest.TestCase):
    def read_json(self, relative):
        return json.loads((ROOT / relative).read_text(encoding="utf-8"))

    def test_marketplace_is_one_available_local_plugin_in_render_order(self):
        marketplace = self.read_json(".agents/plugins/marketplace.json")
        self.assertEqual(list(marketplace), ["name", "interface", "plugins"])
        self.assertEqual(marketplace["name"], "torget")
        self.assertEqual(
            marketplace["interface"], {"displayName": "Torget Plugins"})
        self.assertEqual(marketplace["plugins"], [{
            "name": "vibepulse",
            "source": {
                "source": "local",
                "path": "./.agents/plugins/plugins/vibepulse",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Developer Tools",
        }])
        source = marketplace["plugins"][0]["source"]["path"]
        self.assertEqual(
            (ROOT / source).resolve(),
            (ROOT / ".agents/plugins/plugins/vibepulse").resolve())
        self.assertTrue((ROOT / source / ".codex-plugin/plugin.json").is_file())
        self.assertNotIn("products", marketplace["plugins"][0]["policy"])

    def test_manifest_has_real_supported_metadata_and_no_phantom_assets(self):
        manifest = self.read_json(
            ".agents/plugins/plugins/vibepulse/.codex-plugin/plugin.json")
        self.assertEqual(manifest["name"], "vibepulse")
        self.assertRegex(
            manifest["version"], r"^(0|[1-9][0-9]*)\."
            r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
        self.assertEqual(manifest["version"], "0.1.7")
        self.assertEqual(manifest["author"]["name"], "Niclas Vestlund")
        self.assertNotIn("email", manifest["author"])
        self.assertEqual(manifest["license"], "MIT")
        repository = "https://github.com/niclasvestlund-YT/vibepulse"
        self.assertEqual(manifest["repository"], repository)
        self.assertTrue(manifest["homepage"].startswith(repository))
        self.assertEqual(manifest["skills"], "./skills/")
        for unsupported in ("hooks", "apps", "mcpServers"):
            self.assertNotIn(unsupported, manifest)
        interface = manifest["interface"]
        self.assertEqual(interface["displayName"], "VibePulse")
        self.assertEqual(interface["developerName"], "Niclas Vestlund")
        self.assertEqual(interface["category"], "Developer Tools")
        self.assertEqual(interface["capabilities"], ["Interactive"])
        self.assertEqual(interface["brandColor"], "#6F78FF")
        self.assertLessEqual(len(interface["defaultPrompt"]), 3)
        for key in ("shortDescription", "longDescription"):
            self.assertTrue(interface[key].strip())
        self.assertNotRegex(json.dumps(manifest), r"TODO|assets/")

    def test_default_discovered_hooks_have_exact_fail_safe_commands(self):
        hooks = self.read_json(
            ".agents/plugins/plugins/vibepulse/hooks/hooks.json")
        self.assertEqual(hooks, {
            "description": "Optional VibePulse Codex interactions",
            "hooks": {
                "SessionStart": [{
                    "matcher": "startup|resume|clear|compact",
                    "hooks": [{
                        "type": "command",
                        "command": "python3 \"$PLUGIN_ROOT/scripts/session_start.py\"",
                        "commandWindows":
                            "py -3 \"%PLUGIN_ROOT%\\scripts\\session_start.py\"",
                        "timeout": 3,
                        "additionalContextLimit": 1800,
                    }],
                }],
                "PermissionRequest": [{
                    "matcher": "*",
                    "hooks": [{
                        "type": "command",
                        "command": "python3 \"$PLUGIN_ROOT/scripts/permission_hook.py\"",
                        "commandWindows":
                            "py -3 \"%PLUGIN_ROOT%\\scripts\\permission_hook.py\"",
                        "timeout": 125,
                        "statusMessage":
                            "Waiting for VibePulse or this computer",
                    }],
                }],
            },
        })

    def test_skill_is_concise_local_only_and_never_invents_approval(self):
        path = (ROOT / ".agents/plugins/plugins/vibepulse/skills/vibepulse/"
                "SKILL.md")
        text = path.read_text(encoding="utf-8")
        frontmatter, body = text.split("---", 2)[1:]
        fields = [line.split(":", 1)[0] for line in frontmatter.splitlines()
                  if line.strip()]
        self.assertEqual(fields, ["name", "description"])
        self.assertLess(len(text.splitlines()), 100)
        for trigger in ("panel", "permission", "setup", "status", "doctor"):
            self.assertIn(trigger, frontmatter.lower())
        for safety in ("mcp__vibepulse__ask", "2-3", "request_user_input",
                       "secret", "silence", "approval", "local"):
            self.assertIn(safety, body.lower())
        self.assertIn("python3 tools/vibepulse_setup.py status", body)
        self.assertIn("python3 tools/vibepulse_setup.py doctor", body)
        for stale_guard in (
                "claudeProbe", "claudeCredential", "/api/tokens",
                "usage_http_200 + ok", "15 seconds",
                "do not prescribe a tokenserver restart first",
                "learn or update itself", "_vibepulse._tcp",
                "which computer owns the current question"):
            self.assertIn(stale_guard, body)
        self.assertRegex(body.lower(), r"relay (?:is|remains) not enabled")
        for physical_guard in (
                "Ser du APPROVE?", "SOMETHING IS WAITING",
                "status: answered", "option_index: 0",
                "git describe --tags --always --dirty", "otaAvailableVersion"):
            self.assertIn(physical_guard, body)

    def test_physical_smoke_contract_and_failure_record_are_documented(self):
        setup = (ROOT / "docs/agent-setup.md").read_text(encoding="utf-8")
        review = (ROOT / "docs/superpowers/reviews/"
                  "2026-08-27-vibepulse-codex-physical-end-to-end.md").read_text(
                      encoding="utf-8")
        lessons = (ROOT / "docs/lessons.md").read_text(encoding="utf-8")

        for text in (setup, review):
            for contract in (
                    "Ser du APPROVE?", "APPROVE syns", "APPROVE saknas",
                    "status: answered", "option_index: 0", "answer: Ja",
                    "SOMETHING IS WAITING", "computer fallback",
                    "git describe --tags --always --dirty", "otaAvailableVersion"):
                self.assertIn(contract, text)
        self.assertIn("v0.7.0-5-ge6feb29-dirty", review)
        self.assertIn("A green build from an old tree hid the panel test",
                      lessons)

    def test_v071_release_is_upgradeable_safe_and_evidence_backed(self):
        release = (ROOT / "docs/releases/"
                   "2026-08-27-health-and-panel-reliability.md").read_text(
                       encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        for required in (
                "VibePulse v0.7.1", "30 minutes", "every 15 seconds",
                "Ser du APPROVE?", "status: answered", "option_index: 0",
                "restart the tokenserver", "vibepulse@torget",
                "python3 tools/vibepulse_setup.py install",
                "green CI", "source-only", "Do not attach `torget.bin`"):
            self.assertIn(required, release)
        self.assertIn("v0.7.1 — health and panel reliability", changelog)
        self.assertIn("plugin is `0.1.1`", changelog)
        self.assertIn("## v0.7.1 — 2026-08-27", changelog)
        self.assertNotIn("eventual `v0.7.1` tag", release)

        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("## Latest release: v1.0.0", readme)
        self.assertIn("Compare v0.7.1...v1.0.0", readme)
        self.assertIn("### Windows v1 verification", readme)
        self.assertIn("788 tests, 11 named skips, 0 failures/errors", readme)
        self.assertIn("14/14 and 7/7 jobs", readme)
        self.assertIn("windows-v1-full-lifecycle.md", readme)
        self.assertNotIn(
            "latest sanitized checkpoint is explicitly\n"
            "  **[PARTIAL]", readme)

    def test_v100_release_is_major_windows_honest_and_source_only(self):
        release = (ROOT / "docs/releases/"
                   "2026-08-28-windows-joins-the-shelf.md").read_text(
                       encoding="utf-8")
        evidence = (ROOT / "docs/superpowers/reviews/"
                    "2026-08-28-windows-v1-core-physical.md").read_text(
                        encoding="utf-8")
        lifecycle = (ROOT / "docs/superpowers/reviews/"
                     "2026-08-28-windows-v1-full-lifecycle.md").read_text(
                         encoding="utf-8")
        changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertFalse(release.lstrip().startswith("# "))
        for required in (
                "VibePulse v1.0.0", "Why 1.0", "Windows, for real",
                "Ser du APPROVE?", "NEEDS YOU", "APPROVE",
                "answered / option_index 0 / Ja", "788-test",
                "Task Scheduler", "Private-profile-only", "source-only",
                "Do not attach `torget.bin`", "v0.7.1...v1.0.0",
                "Final Windows v1 verification ledger",
                "788 tests, 11 named skips, 0 failures/errors",
                "14/14 and 7/7 jobs", "12/12 fresh samples",
                "bee5d8c", "ab3ce92", "4d1c47d", "bc639eb",
                "33214257872", "33216669247"):
            self.assertIn(required, release)
        for image in (
                "vibepulse-codex-week.png",
                "vibepulse-codex-needs-you.png",
                "vibepulse-needs-you-codex-question.png"):
            self.assertIn(
                "https://raw.githubusercontent.com/"
                "niclasvestlund-YT/vibepulse/v1.0.0/docs/img/" + image,
                release)
        for boundary in (
                "PERSISTENT LIFECYCLE PARTIAL", "Sign-out/sign-in",
                "Sleep/resume", "Reboot", "NOT TESTED"):
            self.assertIn(boundary, evidence)
        for completed in (
                "FULL WINDOWS v1 PASS", "Sign-out/sign-in", "Sleep entered",
                "Full reboot occurred", "12 of 12", "Physical panel after reboot",
                "bee5d8c9c9b47b761b5970c346cc0e641ac82485",
                "ab3ce92a069b4cc66312b6043e3715829d1bb763"):
            self.assertIn(completed, lifecycle)
        self.assertIn("persistent lifecycle verified", release)
        self.assertIn("## v1.0.0 — 2026-08-28", changelog)
        self.assertLess(changelog.index("## Unreleased"),
                        changelog.index("## v1.0.0"))
        for forbidden in ("oauth token:", "refresh token:",
                          "account id:", "relay address:"):
            self.assertNotIn(forbidden, release.lower())
            self.assertNotIn(forbidden, lifecycle.lower())

    def test_default_runner_invokes_plugin_suite_once(self):
        runner = (ROOT / "test/run.sh").read_text(encoding="utf-8")
        self.assertEqual(runner.count("test_vibepulse_codex_plugin.py"), 1)

    def test_user_guides_pin_opt_in_trust_fallback_and_safe_uninstall(self):
        guides = {
            "agent setup": ROOT / "docs/agent-setup.md",
            "tokenserver README": ROOT / "tools/tokenserver/README.md",
        }
        commands = (
            "python3 tools/vibepulse_setup.py install",
            "python3 tools/vibepulse_setup.py status",
            "python3 tools/vibepulse_setup.py doctor",
            "python3 tools/vibepulse_setup.py disable codex",
            "python3 tools/vibepulse_setup.py uninstall codex",
        )
        for name, path in guides.items():
            text = path.read_text(encoding="utf-8")
            prose = " ".join(text.split())
            for command in commands:
                self.assertIn(command, text, f"{name} must show {command}")
            self.assertIn("/hooks", prose, f"{name} must require hook review")
            self.assertIn("Start a new Codex task", prose)
            self.assertIn("computer fallback", prose.lower())
            self.assertIn("safe-command tier", prose.lower())
            self.assertIn("legacy Claude v1 is insecure", prose)
            self.assertIn("off by default", prose.lower())
            self.assertIn("preserves Claude, relay, GitHub, device-key, and "
                          "unrelated Codex settings", prose)


class MacosServiceTests(unittest.TestCase):
    def test_validate_detects_old_checkout_without_mutating_private_options(self):
        service = load_macos_service()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plist_path = root / "Library/LaunchAgents/service.plist"
            plist_path.parent.mkdir(parents=True)
            old = {
                "Label": service.LABEL,
                "ProgramArguments": [
                    "/old/python", "-u", "tokenserver.py",
                    "--github-repo", "owner/repo",
                    "--publish", "https://secret.invalid/mailbox",
                ],
                "WorkingDirectory": "/old/worktree/tools/tokenserver",
                "EnvironmentVariables": {"PRIVATE_VALUE": "preserve-me"},
            }
            plist_path.write_bytes(plistlib.dumps(old))
            runner = FakeRunner([result()])
            venv_python = root / "checkout/.venv/bin/python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_bytes(b"")

            extras, environment = service._read_preserved(plist_path)
            payload = service.build_plist(
                ROOT, venv_python, root,
                extra_arguments=extras, environment=environment)
            self.assertEqual(payload["WorkingDirectory"], str(
                (ROOT / "tools/tokenserver").resolve()))
            self.assertEqual(payload["ProgramArguments"][:3], [
                str(venv_python.absolute()), "-u",
                str((ROOT / "tools/tokenserver/tokenserver.py").resolve()),
            ])
            self.assertEqual(payload["ProgramArguments"][3:],
                             old["ProgramArguments"][3:])
            self.assertEqual(payload["EnvironmentVariables"],
                             old["EnvironmentVariables"])
            with self.assertRaisesRegex(
                    service.ServiceConfigError, "does not match"):
                service.install(
                    repo_root=ROOT, python=venv_python,
                    plist_path=plist_path, home=root,
                    validate_only=True, run=runner)
            self.assertEqual(plistlib.loads(plist_path.read_bytes()), old)

    def test_install_atomically_reloads_cached_launchd_configuration(self):
        service = load_macos_service()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plist_path = root / "Library/LaunchAgents/service.plist"
            runner = FakeRunner([result(), result(returncode=3), result()])
            with mock.patch.object(
                    service, "_launchd_domain", return_value="gui/501"):
                service.install(
                    repo_root=ROOT, python=Path(sys.executable),
                    plist_path=plist_path, home=root,
                    validate_only=False, run=runner)

            payload = plistlib.loads(plist_path.read_bytes())
            self.assertEqual(payload["Label"], service.LABEL)
            self.assertEqual(runner.calls[1][0], [
                "launchctl", "bootout", "gui/501/se.torget.tokenserver"])
            self.assertEqual(runner.calls[2][0], [
                "launchctl", "bootstrap", "gui/501", str(plist_path)])
            if os.name != "nt":
                self.assertEqual(plist_path.stat().st_mode & 0o777, 0o644)

    def test_install_retries_transient_launchd_bootstrap_failure(self):
        service = load_macos_service()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plist_path = root / "Library/LaunchAgents/service.plist"
            runner = FakeRunner([
                result(), result(returncode=0), result(returncode=5),
                result(returncode=0)])
            delays = []
            with mock.patch.object(
                    service, "_launchd_domain", return_value="gui/501"):
                service.install(
                    repo_root=ROOT, python=Path(sys.executable),
                    plist_path=plist_path, home=root,
                    validate_only=False, run=runner, sleep=delays.append)

            bootstraps = [
                call[0] for call in runner.calls
                if call[0][:2] == ["launchctl", "bootstrap"]]
            self.assertEqual(len(bootstraps), 2)
            self.assertEqual(delays, [service.BOOTSTRAP_RETRY_DELAYS[0]])

    def test_failed_bootstrap_restores_previous_service(self):
        service = load_macos_service()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plist_path = root / "Library/LaunchAgents/service.plist"
            plist_path.parent.mkdir(parents=True)
            old = {
                "Label": service.LABEL,
                "ProgramArguments": [
                    "/old/python", "-u", "/old/tokenserver.py", "--port",
                    "9876"],
                "WorkingDirectory": "/old",
            }
            plist_path.write_bytes(plistlib.dumps(old))
            runner = FakeRunner([
                result(), result(returncode=0),
                result(returncode=5), result(returncode=5),
                result(returncode=5),
                result(returncode=0)])
            with mock.patch.object(
                    service, "_launchd_domain", return_value="gui/501"), \
                    self.assertRaisesRegex(
                        service.ServiceConfigError, "previous service was restored"):
                service.install(
                    repo_root=ROOT, python=Path(sys.executable),
                    plist_path=plist_path, home=root,
                    validate_only=False, run=runner, sleep=lambda _delay: None)
            self.assertEqual(plistlib.loads(plist_path.read_bytes()), old)
            self.assertEqual(runner.calls[5][0], [
                "launchctl", "bootstrap", "gui/501", str(plist_path)])

    def test_failed_first_bootstrap_removes_new_service_file(self):
        service = load_macos_service()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plist_path = root / "Library/LaunchAgents/service.plist"
            runner = FakeRunner([
                result(), result(returncode=0),
                result(returncode=5), result(returncode=5),
                result(returncode=5)])
            with mock.patch.object(
                    service, "_launchd_domain", return_value="gui/501"), \
                    self.assertRaisesRegex(
                        service.ServiceConfigError, "service file was removed"):
                service.install(
                    repo_root=ROOT, python=Path(sys.executable),
                    plist_path=plist_path, home=root,
                    validate_only=False, run=runner, sleep=lambda _delay: None)
            self.assertFalse(plist_path.exists())

    def test_foreign_or_unrecognized_existing_plist_is_never_overwritten(self):
        service = load_macos_service()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            plist_path = root / "service.plist"
            for payload in (
                    {"Label": "foreign", "ProgramArguments": ["x"]},
                    {"Label": service.LABEL,
                     "ProgramArguments": ["/bin/sh", "-c", "anything"]}):
                with self.subTest(payload=payload):
                    plist_path.write_bytes(plistlib.dumps(payload))
                    before = plist_path.read_bytes()
                    with self.assertRaises(service.ServiceConfigError):
                        service.install(
                            repo_root=ROOT, python=Path(sys.executable),
                            plist_path=plist_path, home=root,
                            validate_only=False, run=FakeRunner())
                    self.assertEqual(plist_path.read_bytes(), before)


class CodexMcpTimeoutConfigTests(unittest.TestCase):
    def test_adds_timeout_and_preserves_unrelated_toml(self):
        helper = load_timeout_helper()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            original = (
                "model = \"gpt-test\"\n\n"
                "[mcp_servers.peer]\ncommand = \"peer\"\n\n"
                "[mcp_servers.vibepulse]\n"
                "command = \"python\"\nargs = [\"mcp.py\"]\n")
            path.write_text(original, encoding="utf-8")

            helper.configure(path)

            updated = path.read_text(encoding="utf-8")
            self.assertIn("tool_timeout_sec = 130\n", updated)
            self.assertIn("[mcp_servers.peer]\ncommand = \"peer\"", updated)
            self.assertIn("model = \"gpt-test\"", updated)

    def test_replaces_legacy_timeout_and_is_idempotent(self):
        helper = load_timeout_helper()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "[mcp_servers.vibepulse]\ncommand = \"python\"\n"
                "tool_timeout_sec = 20\n",
                encoding="utf-8")

            helper.configure(path)
            once = path.read_bytes()
            helper.configure(path)

            self.assertEqual(path.read_bytes(), once)
            self.assertEqual(once.count(b"tool_timeout_sec = 130"), 1)

    def test_missing_owned_section_fails_closed(self):
        helper = load_timeout_helper()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "[mcp_servers.peer]\ncommand = \"peer\"\n",
                encoding="utf-8")
            before = path.read_bytes()

            with self.assertRaises(helper.TimeoutConfigError):
                helper.configure(path)

            self.assertEqual(path.read_bytes(), before)

    def test_config_symlink_is_refused(self):
        helper = load_timeout_helper()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / "target.toml"
            target.write_text(
                "[mcp_servers.vibepulse]\ncommand = \"python\"\n",
                encoding="utf-8")
            link = root / "config.toml"
            try:
                link.symlink_to(target)
            except OSError as exc:
                if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
                    self.skipTest("Windows symlinks require Developer Mode")
                raise

            with self.assertRaises(helper.TimeoutConfigError):
                helper.configure(link)


class SetupPlanTests(unittest.TestCase):
    def test_doctor_separates_live_source_from_expired_saved_fallback(self):
        setup = load_setup()
        output = io.StringIO()

        healthy = setup._doctor_claude_quota({
            "claudeProbe": "usage_http_200 + ok",
            "claudeCredential": {"status": "expired", "expiresInMin": 0},
        }, output)

        self.assertFalse(healthy)
        text = output.getvalue()
        self.assertIn("current quota source is live", text)
        self.assertIn("next client gap can make Fable stale", text)
        self.assertIn("does not need a restart", text)

    def test_doctor_never_calls_failed_probe_live_or_requires_restart(self):
        setup = load_setup()
        output = io.StringIO()

        healthy = setup._doctor_claude_quota({
            "claudeProbe": "token_expired_15:34",
            "claudeCredential": {"status": "expired", "expiresInMin": 0},
        }, output)

        self.assertFalse(healthy)
        text = output.getvalue()
        self.assertNotIn("source is live", text)
        self.assertIn("rechecks automatically", text)
        self.assertIn("does not need a restart", text)

    def test_legacy_missing_timeout_is_migratable_but_not_doctor_pass(self):
        setup = load_setup()
        legacy = compact([owned_mcp(tool_timeout=None)])
        self.assertIsNone(setup._owned_mcp_state(
            legacy, ROOT, Path(sys.executable)))
        self.assertTrue(setup._owned_mcp_state(
            legacy, ROOT, Path(sys.executable), allow_legacy_timeout=True))

    def test_auto_executable_resolution_uses_shared_codex_resolver(self):
        setup = load_setup()
        expected = Path(
            r"C:\Users\Tester\AppData\Local\Programs\OpenAI\Codex\bin\codex.exe")
        with mock.patch.object(
                setup, "resolve_codex_executable",
                return_value=str(expected)):
            python, codex = setup._resolve_executables(
                setup._AUTO, setup._AUTO)

        self.assertEqual(python, Path(sys.executable))
        self.assertEqual(codex, expected)

    def test_real_python_probe_has_the_exact_cross_platform_sentinel(self):
        """A stray space made healthy Python 3.12 fail doctor on Windows."""
        setup = load_setup()
        completed = subprocess.run(
            [sys.executable, "-c", setup._PYTHON_PROBE],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "vibepulse-python-3.11+\n")
        self.assertEqual(completed.stderr, "")
        self.assertTrue(setup._python_probe_ok(
            Path(sys.executable), setup._AUTO))

    def test_python_probe_accepts_only_native_line_endings(self):
        setup = load_setup()
        sentinel = "vibepulse-python-3.11+"
        for ending in ("\n", "\r\n"):
            with self.subTest(ending=repr(ending)):
                runner = FakeRunner([result(stdout=sentinel + ending)])
                self.assertTrue(setup._python_probe_ok(
                    Path(sys.executable), runner))

        for stdout in (
                sentinel + " \n", sentinel + "\nextra\n", sentinel, ""):
            with self.subTest(stdout=repr(stdout)):
                runner = FakeRunner([result(stdout=stdout)])
                self.assertFalse(setup._python_probe_ok(
                    Path(sys.executable), runner))

        self.assertFalse(setup._python_probe_ok(
            Path(sys.executable),
            FakeRunner([result(returncode=1, stdout=sentinel + "\n")])))
        self.assertFalse(setup._python_probe_ok(
            Path(sys.executable),
            FakeRunner([result(stdout=sentinel + "\n", stderr="warning")])))

    def test_disable_preserves_unrelated_switches_and_clears_legacy_with_claude(self):
        setup = load_setup()
        saved = setup.VibePulseConfig(
            claude_interactions=True, codex_interactions=True,
            interaction_detail=True, legacy_claude_panel_v1=True,
            interaction_relay=True,
            interaction_relay_url="https://relay.example",
            interaction_mailbox="vp_A1b2C3d4E5f6G7h8")

        self.assertEqual(
            setup._disabled_config(saved, "codex"),
            setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True,
                legacy_claude_panel_v1=True,
                interaction_relay=True,
                interaction_relay_url="https://relay.example",
                interaction_mailbox="vp_A1b2C3d4E5f6G7h8"))
        self.assertEqual(
            setup._disabled_config(saved, "claude"),
            setup.VibePulseConfig(
                codex_interactions=True, interaction_detail=True,
                interaction_relay=True,
                interaction_relay_url="https://relay.example",
                interaction_mailbox="vp_A1b2C3d4E5f6G7h8"))

    def test_setup_help_exposes_explicit_legacy_claude_opt_in_and_opt_out(self):
        setup = load_setup()
        help_text = setup._parser()._subparsers._group_actions[0].choices[
            "install"].format_help()

        self.assertIn("--legacy-claude-panel-v1", help_text)
        self.assertIn("--no-legacy-claude-panel-v1", help_text)
        self.assertIn("insecure", help_text.lower())

    def test_setup_exposes_explicit_relay_lifecycle(self):
        setup = load_setup()
        choices = setup._parser()._subparsers._group_actions[0].choices
        self.assertIn("relay", choices)
        relay_choices = choices["relay"]._subparsers._group_actions[0].choices
        self.assertEqual(set(relay_choices), {
            "install", "status", "doctor", "disable", "uninstall",
            "enable-status", "disable-status"})

    def test_install_plan_is_exact_and_paths_with_spaces_stay_one_argv(self):
        setup = load_setup()
        repo = Path("/repo with spaces")
        python = Path("/python with spaces")
        codex = Path("/codex")
        repo_path = str(repo.resolve())
        python_path = str(python.resolve())
        codex_path = str(codex.resolve())
        commands = setup.plan_codex_install(
            repo_root=repo, python=python, codex=codex,
            marketplace_name="torget")
        self.assertEqual(commands, [
            [codex_path, "plugin", "marketplace", "add", repo_path],
            [codex_path, "plugin", "add", "vibepulse@torget"],
            [codex_path, "mcp", "remove", "vibepulse"],
            [codex_path, "mcp", "add", "vibepulse", "--", python_path,
             str(repo.resolve() /
                 ".agents/plugins/plugins/vibepulse/scripts/mcp_server.py")],
            [python_path, str(repo.resolve() /
                              "tools/codex_mcp_timeout.py")],
        ])
        self.assertNotEqual(
            commands[0][-1], str(repo.resolve() / ".agents/plugins"))

    def test_provider_choices_and_detail_are_separate_explicit_opt_ins(self):
        setup = load_setup()
        expected = {
            "off": (False, False),
            "claude": (True, False),
            "codex": (False, True),
            "both": (True, True),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for providers, pair in expected.items():
                path = Path(tmp) / providers / "config.json"
                runner = StatefulCodexRunner()
                out = io.StringIO()
                code = setup.main(
                    ["install", "--providers", providers, "--no-detail"],
                    repo_root=ROOT, config_path=path,
                    python=Path(sys.executable), codex=Path("/codex"),
                    run=runner, stdout=out, stdin_isatty=False)
                self.assertEqual(code, 0)
                saved = setup.load_config(path)
                self.assertEqual((saved.claude_interactions,
                                  saved.codex_interactions), pair)
                self.assertFalse(saved.interaction_detail)
                self.assertEqual(len(runner.calls), 13)
                self.assertEqual(
                    [call[0] for call in runner.calls[5:10]],
                    setup.plan_codex_install(
                        ROOT, Path(sys.executable), Path("/codex")))
                self.assertTrue(all(isinstance(call[0], list)
                                    and call[1].get("shell") is False
                                    for call in runner.calls))
                self.assertIn("/hooks", out.getvalue())
                self.assertIn("computer fallback", out.getvalue().lower())

            path = Path(tmp) / "detail" / "config.json"
            setup.main(
                ["install", "--providers", "off", "--detail"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=StatefulCodexRunner(), stdout=io.StringIO(),
                stdin_isatty=False)
            self.assertTrue(setup.load_config(path).interaction_detail)

    def test_noninteractive_install_defaults_every_provider_and_detail_off(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            code = setup.main(
                ["install"], repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=StatefulCodexRunner(), stdout=io.StringIO(),
                stdin_isatty=False)
            self.assertEqual(code, 0)
            self.assertEqual(setup.load_config(path), setup.VibePulseConfig())

    def test_legacy_claude_panel_mode_is_explicit_saved_and_claude_only(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            code = setup.main(
                ["install", "--providers", "claude", "--no-detail",
                 "--legacy-claude-panel-v1"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=StatefulCodexRunner(), stdout=io.StringIO(),
                stdin_isatty=False)

            self.assertEqual(code, 0)
            self.assertEqual(setup.load_config(path), setup.VibePulseConfig(
                claude_interactions=True, legacy_claude_panel_v1=True))

            code = setup.main(
                ["install", "--providers", "claude", "--no-detail",
                 "--no-legacy-claude-panel-v1"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=StatefulCodexRunner(), stdout=io.StringIO(),
                stdin_isatty=False)
            self.assertEqual(code, 0)
            self.assertEqual(setup.load_config(path), setup.VibePulseConfig(
                claude_interactions=True))

            runner = StatefulCodexRunner()
            output = io.StringIO()
            code = setup.main(
                ["install", "--providers", "codex", "--no-detail",
                 "--legacy-claude-panel-v1"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=output, stdin_isatty=False)
            self.assertEqual(code, 1)
            self.assertEqual(runner.calls, [])
            self.assertIn("requires Claude", output.getvalue())

    def test_status_never_hides_insecure_legacy_claude_mode(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                claude_interactions=True,
                legacy_claude_panel_v1=True))
            output = io.StringIO()

            self.assertEqual(setup.main(
                ["status"], config_path=path, stdout=output), 0)

            self.assertIn(
                "Legacy Claude panel v1: ON (INSECURE COMPATIBILITY MODE)",
                output.getvalue())

    def test_install_stops_on_unexpected_command_failure_without_saving(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True)
            setup.save_config(path, original)
            runner = StatefulCodexRunner(actions={"plugin_add": ["fail"]})
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["install", "--providers", "codex", "--no-detail"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=output, stdin_isatty=False), 1)
            self.assertEqual(runner.state, {
                "mcp": False, "plugin": False, "marketplace": False})
            self.assertEqual(setup.load_config(path), original)
            self.assertNotIn("PASS", output.getvalue())

    def test_status_is_plain_and_does_not_expose_secrets(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True))
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["status"], config_path=path, stdout=output), 0)
            self.assertEqual(output.getvalue().splitlines(), [
                "Claude: ON", "Codex: OFF", "Detail: ON",
                "Legacy Claude panel v1: OFF",
                "Interaction relay: OFF", "Agent status relay: OFF"])
            self.assertNotIn("key", output.getvalue().lower())


class RelaySetupTests(unittest.TestCase):
    URL = "https://vibepulse-relay.example"
    MAILBOX_SUFFIX = "A1b2C3d4E5f6G7h8"
    MAC_TOKEN = "ERERERERERERERERERERERERERERERERERERERERERE"
    PANEL_TOKEN = "IiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiIiI"
    DEVICE_KEY = "ab" * 32

    def make_paths(self, root):
        root = Path(root)
        config = root / "state/config.json"
        token = root / "home/.vibepulse-interaction-relay-token"
        secrets = root / "repo/secrets.h"
        service = root / "repo/tools/interaction-relay"
        wrangler = service / "node_modules/.bin" / (
            "wrangler.cmd" if os.name == "nt" else "wrangler")
        wrangler.parent.mkdir(parents=True)
        wrangler.write_text("#!/bin/sh\nexit 99\n", encoding="utf-8")
        wrangler.chmod(0o700)
        secrets.parent.mkdir(parents=True, exist_ok=True)
        secrets.write_text(
            '#define TK_VIBEPULSE_DEVICE_KEY "' + self.DEVICE_KEY + '"\n'
            '#define UNRELATED_SETTING "keep-me"\n', encoding="utf-8")
        return config, token, secrets, service, wrangler

    def random_values(self):
        values = iter((self.MAILBOX_SUFFIX,
                       self.MAC_TOKEN, self.PANEL_TOKEN))

        def generate(_size):
            return next(values)
        return generate

    def test_relay_install_uses_private_ephemeral_secrets_and_never_prints(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-setup-") as tmp:
            config, token, secrets_h, service, wrangler = \
                self.make_paths(tmp)
            setup.save_config(config, setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True))
            observed = {}

            def run(argv, **kwargs):
                self.assertFalse(kwargs["shell"])
                if "bulk" in argv:
                    secret_file = Path(argv[-1])
                    observed["secret_path"] = secret_file
                    observed["secret_mode"] = (
                        secret_file.stat().st_mode & 0o777)
                    observed["secret_payload"] = json.loads(
                        secret_file.read_text(encoding="utf-8"))
                observed.setdefault("calls", []).append(argv)
                return result()

            output = io.StringIO()
            code = setup.main(
                ["relay", "install", "--url", self.URL,
                 "--yes-e2e-cloud"],
                config_path=config, relay_token_path=token,
                secrets_path=secrets_h, interaction_relay_dir=service,
                token_urlsafe=self.random_values(), run=run,
                stdout=output, stdin_isatty=False)

            self.assertEqual(code, 0, output.getvalue())
            saved = setup.load_config(config)
            self.assertTrue(saved.interaction_relay)
            self.assertEqual(saved.interaction_relay_url, self.URL)
            self.assertEqual(
                saved.interaction_mailbox, "vp_" + self.MAILBOX_SUFFIX)
            self.assertEqual(token.read_text(encoding="ascii"),
                             self.MAC_TOKEN + "\n")
            if os.name == "posix":
                self.assertEqual(token.stat().st_mode & 0o777, 0o600)
                self.assertEqual(observed["secret_mode"], 0o600)
            self.assertEqual(observed["secret_payload"], {
                "MAC_TOKEN": self.MAC_TOKEN,
                "PANEL_TOKEN": self.PANEL_TOKEN,
            })
            self.assertFalse(observed["secret_path"].exists())
            self.assertEqual(observed["calls"], [
                [str(wrangler), "--cwd", str(service), "secret", "bulk",
                 str(observed["secret_path"])],
                [str(wrangler), "--cwd", str(service), "deploy", "--var",
                 "MAILBOX_ID:vp_" + self.MAILBOX_SUFFIX],
            ])
            contents = secrets_h.read_text(encoding="utf-8")
            self.assertIn('UNRELATED_SETTING "keep-me"', contents)
            self.assertIn("VIBEPULSE INTERACTION RELAY BEGIN", contents)
            self.assertIn(self.PANEL_TOKEN, contents)
            backup = config.parent / "secrets.h.before-interaction-relay"
            self.assertTrue(backup.exists())
            self.assertNotIn(self.MAC_TOKEN, output.getvalue())
            self.assertNotIn(self.PANEL_TOKEN, output.getvalue())

    def test_status_relay_requires_consent_and_proves_mac_ownership(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-status-consent-") as tmp:
            config, token, secrets_h, service, _wrangler = self.make_paths(tmp)
            mailbox = "vp_" + self.MAILBOX_SUFFIX
            saved = setup.VibePulseConfig(
                claude_interactions=True,
                interaction_detail=True,
                interaction_relay=False,
                agent_status_relay=False,
                interaction_relay_url=self.URL,
                interaction_mailbox=mailbox)
            setup.save_config(config, saved)
            token.parent.mkdir(parents=True)
            token.write_text(self.MAC_TOKEN + "\n", encoding="ascii")
            token.chmod(0o600)
            secrets_h.write_text(
                secrets_h.read_text(encoding="utf-8") +
                setup._relay_secrets_block(
                    self.URL, mailbox, self.PANEL_TOKEN), encoding="utf-8")

            calls = []

            def open_url(request, **kwargs):
                calls.append((request, kwargs))
                return BytesResponse(b"", status=204, content_types=())

            output = io.StringIO()
            self.assertEqual(setup.main(
                ["relay", "enable-status"], config_path=config,
                relay_token_path=token, secrets_path=secrets_h,
                interaction_relay_dir=service, urlopen=open_url,
                stdout=output, stdin_isatty=False), 1)
            self.assertEqual(setup.load_config(config), saved)
            self.assertEqual(calls, [])
            self.assertIn("consent", output.getvalue().lower())

            output = io.StringIO()
            self.assertEqual(setup.main(
                ["relay", "enable-status", "--yes-e2e-cloud"],
                config_path=config, relay_token_path=token,
                secrets_path=secrets_h, interaction_relay_dir=service,
                urlopen=open_url, stdout=output, stdin_isatty=False), 0)
            enabled = setup.load_config(config)
            self.assertTrue(enabled.agent_status_relay)
            self.assertFalse(enabled.interaction_relay)
            self.assertTrue(enabled.claude_interactions)
            self.assertTrue(enabled.interaction_detail)
            self.assertEqual(enabled.interaction_relay_url, self.URL)
            self.assertEqual(enabled.interaction_mailbox, mailbox)
            self.assertEqual(len(calls), 1)
            request, kwargs = calls[0]
            self.assertEqual(
                request.full_url,
                f"{self.URL}/v1/mailboxes/{mailbox}/verdicts")
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(
                request.get_header("Authorization"),
                "Bearer " + self.MAC_TOKEN)
            self.assertEqual(kwargs["timeout"], setup.NETWORK_TIMEOUT_SECONDS)
            self.assertNotIn(self.MAC_TOKEN, output.getvalue())
            self.assertNotIn(self.PANEL_TOKEN, output.getvalue())

    def test_status_relay_probe_failure_and_disable_are_fail_closed(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-status-disable-") as tmp:
            config, token, secrets_h, service, _wrangler = self.make_paths(tmp)
            mailbox = "vp_" + self.MAILBOX_SUFFIX
            saved = setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True, interaction_relay=True,
                agent_status_relay=False,
                interaction_relay_url=self.URL,
                interaction_mailbox=mailbox)
            setup.save_config(config, saved)
            token.parent.mkdir(parents=True)
            token.write_text(self.MAC_TOKEN + "\n", encoding="ascii")
            token.chmod(0o600)
            secrets_h.write_text(
                secrets_h.read_text(encoding="utf-8") +
                setup._relay_secrets_block(
                    self.URL, mailbox, self.PANEL_TOKEN), encoding="utf-8")

            output = io.StringIO()
            self.assertEqual(setup.main(
                ["relay", "enable-status", "--yes-e2e-cloud"],
                config_path=config, relay_token_path=token,
                secrets_path=secrets_h, interaction_relay_dir=service,
                urlopen=lambda *_args, **_kwargs: BytesResponse(
                    b"", status=401, content_types=()),
                stdout=output, stdin_isatty=False), 1)
            self.assertEqual(setup.load_config(config), saved)
            self.assertIn("ownership", output.getvalue().lower())

            enabled = setup.VibePulseConfig(
                **{**saved.__dict__, "agent_status_relay": True})
            setup.save_config(config, enabled)
            self.assertEqual(setup.main(
                ["relay", "disable-status"], config_path=config,
                relay_token_path=token, secrets_path=secrets_h,
                interaction_relay_dir=service,
                stdout=io.StringIO()), 0)
            disabled = setup.load_config(config)
            self.assertFalse(disabled.agent_status_relay)
            self.assertTrue(disabled.interaction_relay)
            self.assertTrue(disabled.claude_interactions)
            self.assertTrue(disabled.codex_interactions)
            self.assertTrue(disabled.interaction_detail)
            self.assertEqual(disabled.interaction_relay_url, self.URL)
            self.assertEqual(disabled.interaction_mailbox, mailbox)
            self.assertTrue(token.exists())
            self.assertIn(self.PANEL_TOKEN,
                          secrets_h.read_text(encoding="utf-8"))

    def test_relay_install_requires_explicit_consent_provider_detail_and_key(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-consent-") as tmp:
            config, token, secrets_h, service, _wrangler = \
                self.make_paths(tmp)
            cases = (
                (setup.VibePulseConfig(
                    claude_interactions=True, interaction_detail=True),
                 ["relay", "install", "--url", self.URL], "consent"),
                (setup.VibePulseConfig(interaction_detail=True),
                 ["relay", "install", "--url", self.URL,
                  "--yes-e2e-cloud"], "provider"),
                (setup.VibePulseConfig(claude_interactions=True),
                 ["relay", "install", "--url", self.URL,
                  "--yes-e2e-cloud"], "detail"),
            )
            for saved, argv, word in cases:
                with self.subTest(word=word):
                    setup.save_config(config, saved)
                    out = io.StringIO()
                    self.assertEqual(setup.main(
                        argv, config_path=config, relay_token_path=token,
                        secrets_path=secrets_h,
                        interaction_relay_dir=service,
                        token_urlsafe=self.random_values(),
                        run=FakeRunner(), stdout=out,
                        stdin_isatty=False), 1)
                    self.assertIn(word, out.getvalue().lower())
                    self.assertEqual(setup.load_config(config), saved)

            setup.save_config(config, setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True))
            secrets_h.write_text(
                '#define UNRELATED_SETTING "keep-me"\n', encoding="utf-8")
            out = io.StringIO()
            self.assertEqual(setup.main(
                ["relay", "install", "--url", self.URL,
                 "--yes-e2e-cloud"], config_path=config,
                relay_token_path=token, secrets_path=secrets_h,
                interaction_relay_dir=service,
                token_urlsafe=self.random_values(), run=FakeRunner(),
                stdout=out, stdin_isatty=False), 1)
            self.assertIn("device key", out.getvalue().lower())

    def test_relay_disable_and_uninstall_preserve_every_unrelated_setting(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-remove-") as tmp:
            config, token, secrets_h, service, wrangler = \
                self.make_paths(tmp)
            original = setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True, legacy_claude_panel_v1=True,
                interaction_relay=True,
                interaction_relay_url=self.URL,
                interaction_mailbox="vp_" + self.MAILBOX_SUFFIX)
            setup.save_config(config, original)
            token.parent.mkdir(parents=True)
            token.write_text(self.MAC_TOKEN + "\n", encoding="ascii")
            token.chmod(0o600)
            secrets_h.write_text(
                secrets_h.read_text(encoding="utf-8") +
                setup._relay_secrets_block(
                    self.URL, "vp_" + self.MAILBOX_SUFFIX,
                    self.PANEL_TOKEN), encoding="utf-8")

            self.assertEqual(setup.main(
                ["relay", "disable"], config_path=config,
                relay_token_path=token, secrets_path=secrets_h,
                interaction_relay_dir=service,
                stdout=io.StringIO()), 0)
            disabled = setup.load_config(config)
            self.assertFalse(disabled.interaction_relay)
            self.assertEqual(disabled.interaction_relay_url, self.URL)
            self.assertTrue(token.exists())
            self.assertIn(self.PANEL_TOKEN,
                          secrets_h.read_text(encoding="utf-8"))

            setup.save_config(config, original)
            runner = FakeRunner([result()])
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["relay", "uninstall", "--delete-worker"],
                config_path=config, relay_token_path=token,
                secrets_path=secrets_h, interaction_relay_dir=service,
                run=runner, stdout=output, stdin_isatty=False), 0)
            self.assertEqual(runner.calls[0][0], [
                str(wrangler), "--cwd", str(service), "delete", "--force"])
            self.assertEqual(setup.load_config(config), setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True, legacy_claude_panel_v1=True))
            self.assertFalse(token.exists())
            contents = secrets_h.read_text(encoding="utf-8")
            self.assertIn('UNRELATED_SETTING "keep-me"', contents)
            self.assertNotIn("VIBEPULSE INTERACTION RELAY", contents)
            self.assertNotIn(self.MAC_TOKEN, output.getvalue())
            self.assertNotIn(self.PANEL_TOKEN, output.getvalue())

    def test_relay_install_refuses_preexisting_credential_artifacts(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-existing-") as tmp:
            config, token, secrets_h, service, _wrangler = \
                self.make_paths(tmp)
            saved = setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True)
            setup.save_config(config, saved)
            token.parent.mkdir(parents=True)
            token.write_text(self.MAC_TOKEN + "\n", encoding="ascii")
            token.chmod(0o600)
            before = token.read_bytes()
            runner = FakeRunner()
            output = io.StringIO()

            self.assertEqual(setup.main(
                ["relay", "install", "--url", self.URL,
                 "--yes-e2e-cloud"], config_path=config,
                relay_token_path=token, secrets_path=secrets_h,
                interaction_relay_dir=service,
                token_urlsafe=self.random_values(), run=runner,
                stdout=output, stdin_isatty=False), 1)
            self.assertEqual(token.read_bytes(), before)
            self.assertEqual(setup.load_config(config), saved)
            self.assertEqual(runner.calls, [])
            self.assertIn("existing", output.getvalue().lower())

    def test_relay_install_refuses_preexisting_panel_block(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-existing-block-") as tmp:
            config, token, secrets_h, service, _wrangler = \
                self.make_paths(tmp)
            saved = setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True)
            setup.save_config(config, saved)
            secrets_h.write_text(
                secrets_h.read_text(encoding="utf-8") +
                setup._relay_secrets_block(
                    self.URL, "vp_" + self.MAILBOX_SUFFIX,
                    self.PANEL_TOKEN), encoding="utf-8")
            before = secrets_h.read_bytes()
            runner = FakeRunner()
            output = io.StringIO()

            self.assertEqual(setup.main(
                ["relay", "install", "--url", self.URL,
                 "--yes-e2e-cloud"], config_path=config,
                relay_token_path=token, secrets_path=secrets_h,
                interaction_relay_dir=service,
                token_urlsafe=self.random_values(), run=runner,
                stdout=output, stdin_isatty=False), 1)
            self.assertEqual(secrets_h.read_bytes(), before)
            self.assertFalse(token.exists())
            self.assertEqual(setup.load_config(config), saved)
            self.assertEqual(runner.calls, [])
            self.assertIn("existing", output.getvalue().lower())

    def test_relay_uninstall_fails_closed_on_unreadable_secrets(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-bad-secrets-") as tmp:
            config, token, secrets_h, service, _wrangler = \
                self.make_paths(tmp)
            saved = setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True,
                interaction_relay=True,
                interaction_relay_url=self.URL,
                interaction_mailbox="vp_" + self.MAILBOX_SUFFIX)
            setup.save_config(config, saved)
            secrets_h.unlink()
            secrets_h.mkdir()
            output = io.StringIO()

            self.assertEqual(setup.main(
                ["relay", "uninstall", "--keep-worker"],
                config_path=config, relay_token_path=token,
                secrets_path=secrets_h,
                interaction_relay_dir=service, stdout=output,
                stdin_isatty=False), 1)
            self.assertTrue(secrets_h.is_dir())
            self.assertEqual(setup.load_config(config), saved)
            self.assertIn("could not safely", output.getvalue().lower())

    def test_relay_block_parser_rejects_reversed_markers(self):
        setup = load_setup()
        malformed = (
            setup._RELAY_BLOCK_END + "\n" +
            setup._RELAY_BLOCK_BEGIN + "\n")
        with self.assertRaises(setup.ConfigError):
            setup._without_relay_block(malformed)

    def test_relay_status_and_doctor_are_read_only_and_secret_free(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory(prefix="relay-doctor-") as tmp:
            config, token, secrets_h, service, _wrangler = \
                self.make_paths(tmp)
            saved = setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True,
                interaction_relay=True,
                interaction_relay_url=self.URL,
                interaction_mailbox="vp_" + self.MAILBOX_SUFFIX)
            setup.save_config(config, saved)
            token.parent.mkdir(parents=True)
            token.write_text(self.MAC_TOKEN + "\n", encoding="ascii")
            token.chmod(0o600)
            secrets_h.write_text(
                secrets_h.read_text(encoding="utf-8") +
                setup._relay_secrets_block(
                    self.URL, "vp_" + self.MAILBOX_SUFFIX,
                    self.PANEL_TOKEN), encoding="utf-8")
            snapshots = (config.read_bytes(), token.read_bytes(),
                         secrets_h.read_bytes())

            for command in ("status", "doctor"):
                with self.subTest(command=command):
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["relay", command], config_path=config,
                        relay_token_path=token, secrets_path=secrets_h,
                        interaction_relay_dir=service,
                        stdout=output), 0)
                    text = output.getvalue()
                    self.assertNotIn(self.MAC_TOKEN, text)
                    self.assertNotIn(self.PANEL_TOKEN, text)
            self.assertEqual(
                (config.read_bytes(), token.read_bytes(),
                 secrets_h.read_bytes()), snapshots)

    def test_doctor_uses_exact_json_evidence_and_never_claims_hook_trust(self):
        setup = load_setup()
        secret = "DO_NOT_PRINT_f4390c"

        class Response:
            status = 200
            headers = _Headers(["application/json; charset=utf-8"])

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

            def read(self, limit):
                self.limit = limit
                return json.dumps({
                    "service": "torget-tokenserver",
                    "srcFingerprint": HOST_SOURCE_FINGERPRINT,
                    "interactions": {"claude": False, "codex": True,
                                     "detail": False,
                                     "legacyClaudePanelV1": False,
                                     "relay": {"status": "off"},
                                     "agentStatusRelay": {"status": "off"},
                                     "transport": "lan"},
                    "secret": secret,
                }).encode()

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                codex_interactions=True))
            original = path.read_bytes()
            runner = FakeRunner([
                python_probe_ok(),
                codex_probe_ok(),
                json_result(plugin_listing()),
                json_result([owned_mcp()]),
            ])
            urls = []

            def open_local(request, timeout):
                urls.append((request.full_url, timeout))
                return Response()

            output = io.StringIO()
            self.assertEqual(setup.main(
                ["doctor"], config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, urlopen=open_local, stdout=output), 1)
            text = output.getvalue()
            self.assertIn("PASS Python", text)
            self.assertIn("PASS Codex plugin", text)
            self.assertIn("PASS Codex MCP", text)
            self.assertIn("PASS Tokenserver", text)
            self.assertIn("FIX hooks: open /hooks and review VibePulse", text)
            self.assertIn("not machine-readable", text)
            self.assertNotIn("PASS Hook", text)
            self.assertNotIn(secret, text)
            self.assertEqual(urls[0][0], "http://127.0.0.1:8737/")
            self.assertLessEqual(urls[0][1], 3)
            self.assertEqual(path.read_bytes(), original)

            setup.save_config(path, setup.VibePulseConfig())
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["doctor"], config_path=path,
                python=Path(sys.executable), codex=None,
                run=FakeRunner([python_probe_ok()]), stdout=output), 0)
            self.assertIn("OFF Codex executable", output.getvalue())
            self.assertIn("OFF Codex plugin", output.getvalue())
            self.assertIn("OFF Tokenserver", output.getvalue())

    def test_doctor_reports_legacy_claude_mode_as_an_insecure_fix(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                claude_interactions=True,
                legacy_claude_panel_v1=True))
            response = BytesResponse(json.dumps({
                "service": "torget-tokenserver",
                "srcFingerprint": HOST_SOURCE_FINGERPRINT,
                "interactions": {
                    "claude": True, "codex": False, "detail": False,
                    "legacyClaudePanelV1": True,
                    "relay": {"status": "off"},
                    "agentStatusRelay": {"status": "off"},
                    "transport": "lan",
                },
            }).encode())
            output = io.StringIO()

            code = setup.main(
                ["doctor"], config_path=path,
                python=Path(sys.executable), codex=None,
                run=FakeRunner([python_probe_ok()]),
                urlopen=lambda *_args, **_kwargs: response,
                stdout=output)

            self.assertEqual(code, 1)
            self.assertIn("FIX Legacy Claude panel v1", output.getvalue())
            self.assertIn("insecure", output.getvalue().lower())
            self.assertIn("--no-legacy-claude-panel-v1",
                          output.getvalue())
            self.assertIn("PASS Tokenserver", output.getvalue())

    def test_doctor_reports_panel_lan_contact_without_failing_relay_only_use(self):
        setup = load_setup()
        cases = (
            ("ready", {"route": "/api/tokens",
                       "httpStallRecoveryBoot": True},
             "PASS Panel LAN contact: recent confirmed poll via /api/tokens"),
            ("waiting", {},
             "WAIT Panel LAN contact: no confirmed direct poll"),
            ("stale", {"ageS": 90, "route": "/api/agent-status"},
             "WAIT Panel LAN contact: the last confirmed direct poll is stale"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                codex_interactions=True))
            for status, extra, expected in cases:
                with self.subTest(status=status):
                    output = io.StringIO()
                    body = panel_diagnostics(status, **extra)
                    code = setup.main(
                        ["doctor"], config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=FakeRunner([
                            python_probe_ok(), codex_probe_ok(),
                            json_result(plugin_listing()),
                            json_result([owned_mcp()]),
                        ]),
                        urlopen=lambda *_args, _body=body, **_kwargs:
                            BytesResponse(_body),
                        stdout=output)
                    # Hook trust is deliberately not machine-readable, but a
                    # waiting relay-only panel must not add another failure.
                    self.assertEqual(code, 1)
                    self.assertIn(expected, output.getvalue())
                    if status == "ready":
                        self.assertIn("PASS Panel self-recovery",
                                      output.getvalue())
                    if status != "ready":
                        self.assertIn("relay-only use may still be healthy",
                                      output.getvalue())

    def test_disable_and_uninstall_preserve_non_target_state(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = root / "state/config.json"
            key = root / "state/device.key"
            codex_config = root / "home/.codex/config.toml"
            key.parent.mkdir(parents=True)
            codex_config.parent.mkdir(parents=True)
            key.write_text("DEVICE_KEY_1f733d", encoding="utf-8")
            codex_config.write_text("unrelated = true\n", encoding="utf-8")
            setup.save_config(config, setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True))

            self.assertEqual(setup.main(
                ["disable", "codex"], config_path=config,
                stdout=io.StringIO()), 0)
            self.assertEqual(setup.load_config(config), setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=False,
                interaction_detail=True))

            setup.save_config(config, setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True))
            runner = StatefulCodexRunner()
            self.assertEqual(setup.main(
                ["uninstall", "codex"], config_path=config,
                codex=Path("/codex"), run=runner,
                stdout=io.StringIO()), 0)
            codex_path = str(Path("/codex").resolve())
            self.assertEqual([call[0] for call in runner.calls[3:6]], [
                [codex_path, "mcp", "remove", "vibepulse"],
                [codex_path, "plugin", "remove", "vibepulse@torget"],
                [codex_path, "plugin", "marketplace", "remove", "torget"],
            ])
            self.assertEqual(setup.load_config(config), setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=False,
                interaction_detail=True))
            self.assertEqual(key.read_text(encoding="utf-8"),
                             "DEVICE_KEY_1f733d")
            self.assertEqual(codex_config.read_text(encoding="utf-8"),
                             "unrelated = true\n")

    def test_uninstall_is_idempotent_but_surfaces_unknown_failures(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True))
            runner = StatefulCodexRunner()
            self.assertEqual(setup.main(
                ["uninstall", "codex"], config_path=path,
                codex=Path("/codex"), run=runner,
                stdout=io.StringIO()), 0)
            self.assertEqual(setup.main(
                ["uninstall", "codex"], config_path=path,
                codex=Path("/codex"), run=runner,
                stdout=io.StringIO()), 0)
            self.assertEqual(setup.load_config(path), setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=False,
                interaction_detail=True))

            setup.save_config(path, setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True))
            failed = StatefulCodexRunner(actions={
                "marketplace_remove": ["fail"],
            })
            self.assertEqual(setup.main(
                ["uninstall", "codex"], config_path=path,
                codex=Path("/codex"), run=failed,
                stdout=io.StringIO()), 1)
            self.assertIn(
                [str(Path("/codex").resolve()), "plugin", "marketplace",
                 "remove", "torget"],
                [call[0] for call in failed.calls])
            self.assertTrue(setup.load_config(path).codex_interactions)

    def test_malformed_config_causes_zero_external_mutation(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            for argv in (
                    ["install", "--providers", "codex", "--no-detail"],
                    ["uninstall", "codex"]):
                with self.subTest(argv=argv):
                    path = Path(tmp) / (argv[0] + ".json")
                    path.write_text(
                        '{"codex_interactions":"yes"}\n', encoding="utf-8")
                    before = path.read_bytes()
                    runner = FakeRunner()
                    self.assertEqual(setup.main(
                        argv, repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner, stdout=io.StringIO(),
                        stdin_isatty=False), 1)
                    self.assertEqual(runner.calls, [])
                    self.assertEqual(path.read_bytes(), before)

    def test_install_saves_only_after_preflight_and_all_plan_commands(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = setup.VibePulseConfig(
                claude_interactions=True, interaction_detail=True)
            setup.save_config(path, original)
            seen = []
            stateful = StatefulCodexRunner()

            def runner(argv, **kwargs):
                seen.append((list(argv), setup.load_config(path), kwargs))
                return stateful(argv, **kwargs)

            self.assertEqual(setup.main(
                ["install", "--providers", "codex", "--no-detail"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=io.StringIO(), stdin_isatty=False), 0)
            self.assertEqual(len(seen), 13)
            self.assertTrue(all(saved == original for _, saved, _ in seen))
            self.assertEqual(setup.load_config(path), setup.VibePulseConfig(
                codex_interactions=True))
            self.assertEqual([entry[0] for entry in seen[5:10]],
                             setup.plan_codex_install(
                                 ROOT, Path(sys.executable), Path("/codex")))
            self.assertTrue(all(entry[2].get("shell") is False for entry in seen))

    def test_foreign_or_malformed_existing_mcp_aborts_before_plan(self):
        setup = load_setup()
        cases = [
            [owned_mcp(python=Path("/foreign/python"))],
            [owned_mcp(repo=Path("/foreign/repo"))],
            [owned_mcp(enabled=False)],
            [owned_mcp(transport_extra={"env": {"SECRET": "hidden"}})],
            [{"name": "vibepulse", "enabled": True,
              "transport": {"type": "http", "url": "https://example.com"}}],
            {"servers": []},
            "not json",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, payload in enumerate(cases):
                with self.subTest(index=index):
                    path = Path(tmp) / f"config-{index}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    response = (result(stdout=payload) if isinstance(payload, str)
                                else json_result(payload))
                    runner = FakeRunner([
                        python_probe_ok(), codex_probe_ok(), response,
                        json_result({"installed": [], "available": []}),
                        json_result({"marketplaces": []}),
                    ])
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["install", "--providers", "codex", "--no-detail"],
                        repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner, stdout=output, stdin_isatty=False), 1)
                    self.assertEqual(len(runner.calls), 5)
                    self.assertEqual(setup.load_config(path), original)
                    self.assertNotIn("hidden", output.getvalue())

    def test_owned_mcp_is_restored_when_final_add_fails(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            for rollback_ok in (True, False):
                with self.subTest(rollback_ok=rollback_ok):
                    path = Path(tmp) / f"config-{rollback_ok}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    runner = StatefulCodexRunner(
                        mcp=True, plugin=True, marketplace=True,
                        actions={
                            "mcp_add": (["fail", "commit_ok"] if rollback_ok
                                        else ["fail", "noop_ok", "noop_ok"]),
                        })
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["install", "--providers", "codex", "--no-detail"],
                        repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner, stdout=output, stdin_isatty=False), 1)
                    self.assertEqual(setup.load_config(path), original)
                    self.assertIn("MCP add", output.getvalue())
                    if rollback_ok:
                        self.assertIn("restored", output.getvalue())
                    else:
                        self.assertIn("irrecoverable divergence",
                                      output.getvalue())
                    self.assertNotIn("SECRET", output.getvalue())

    def test_doctor_executes_bounded_python_and_codex_probes(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                codex_interactions=True))
            runner = FakeRunner([
                result(2, stderr="/bin/sh: syntax error"),
                result(stdout="not really codex\n"),
                json_result(plugin_listing()),
                json_result([owned_mcp(python=Path("/bin/sh"))]),
            ])
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["doctor"], repo_root=ROOT, config_path=path,
                python=Path("/bin/sh"), codex=Path("/codex"), run=runner,
                urlopen=lambda *_args, **_kwargs:
                    BytesResponse(healthy_diagnostics()),
                stdout=output), 1)
            # The doctor probes the interpreter through Path.resolve(), so
            # the expectation must resolve too: /bin/sh is itself on macOS
            # but a symlink to dash on Debian-family CI runners.
            self.assertEqual(runner.calls[0][0][0:2],
                             [str(Path("/bin/sh").resolve()), "-c"])
            self.assertEqual(
                runner.calls[1][0],
                [str(Path("/codex").resolve()), "--version"])
            self.assertTrue(all(call[1]["timeout"] <= 15
                                and call[1]["shell"] is False
                                for call in runner.calls))
            self.assertIn("FIX Python executable", output.getvalue())
            self.assertIn("FIX Codex executable", output.getvalue())

    def test_strict_json_never_leaks_deep_parser_recursion(self):
        setup = load_setup()
        deep = "[" * 5000 + "]" * 5000
        try:
            parsed = setup._strict_json(deep)
        except RecursionError as exc:
            self.fail(f"deep bounded JSON leaked RecursionError: {exc}")
        except ValueError:
            pass
        else:
            self.assertIsInstance(parsed, list)

    def test_plugin_provenance_requires_exact_unaliased_real_paths(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            plugin = repo / ".agents/plugins/plugins/vibepulse"
            plugin.mkdir(parents=True)
            (repo / "spelling").mkdir()
            (plugin.parent / "spelling").mkdir()

            repo_alias = base / "repo-alias"
            directory_symlink_or_skip(repo_alias, repo)
            plugin_alias = base / "plugin-alias"
            directory_symlink_or_skip(plugin_alias, plugin)
            parent_alias = base / "parent-alias"
            directory_symlink_or_skip(parent_alias, base)
            parent_repo = parent_alias / "repo"
            parent_plugin = (
                parent_repo / ".agents/plugins/plugins/vibepulse")

            exact = json.dumps(plugin_listing(repo=repo))
            self.assertTrue(setup._plugin_installed(exact, repo))

            bad = {
                "marketplace final symlink": plugin_listing(
                    repo=repo, marketplace_root=repo_alias),
                "plugin final symlink": plugin_listing(
                    repo=repo, plugin_path=plugin_alias),
                "marketplace parent symlink": plugin_listing(
                    repo=repo, marketplace_root=parent_repo),
                "plugin parent symlink": plugin_listing(
                    repo=repo, plugin_path=parent_plugin),
                "marketplace dotdot": plugin_listing(
                    repo=repo, marketplace_root=repo / "spelling" / ".."),
                "plugin dotdot": plugin_listing(
                    repo=repo,
                    plugin_path=plugin.parent / "spelling" / ".." /
                    "vibepulse"),
                # Do not use relpath here: Windows CI can put TEMP and the
                # checkout on different drive letters, where relpath raises.
                "marketplace relative": plugin_listing(
                    repo=repo, marketplace_root=Path("repo")),
                "plugin relative": plugin_listing(
                    repo=repo,
                    plugin_path=Path(".agents/plugins/plugins/vibepulse")),
                "marketplace trailing separator": plugin_listing(
                    repo=repo, marketplace_root=str(repo) + os.sep),
                "plugin trailing separator": plugin_listing(
                    repo=repo, plugin_path=str(plugin) + os.sep),
            }
            for label, listing in bad.items():
                with self.subTest(label=label):
                    self.assertFalse(setup._plugin_installed(
                        json.dumps(listing), repo))

    def test_plugin_provenance_accepts_codex_0150_cached_marketplace(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            plugin = repo / ".agents/plugins/plugins/vibepulse"
            plugin.mkdir(parents=True)
            cache = base / "codex-marketplace-cache"
            cache.mkdir()
            # Hosted Windows runners may spell TEMP through an 8.3 alias;
            # model the CLI's canonical emitted path, not tempfile's input.
            listing = plugin_listing(
                repo=repo, marketplace_root=cache.resolve())
            listing["installed"][0].update({
                "version": "1.0.0",
                "installPolicy": "project",
                "authPolicy": "none",
            })

            self.assertTrue(setup._plugin_installed(
                json.dumps(listing), repo))

            listing["installed"][0]["source"]["path"] = str(
                base / "foreign-plugin")
            self.assertFalse(setup._plugin_installed(
                json.dumps(listing), repo))

    def test_marketplace_state_accepts_codex_0150_root_only_schema(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            current = {"marketplaces": [{
                "name": "torget", "root": str(repo.resolve()),
            }]}
            foreign = {"marketplaces": [{
                "name": "torget", "root": str(repo.parent.resolve()),
            }]}
            extra = {"marketplaces": [{
                "name": "torget", "root": str(repo.resolve()),
                "unexpected": True,
            }]}

            self.assertIs(setup._marketplace_state(
                json.dumps(current), repo), True)
            self.assertFalse(setup._marketplace_state(
                json.dumps(foreign), repo))
            self.assertIsNone(setup._marketplace_state(
                json.dumps(extra), repo))

    def test_doctor_rejects_every_plugin_false_green_transcript(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            foreign = Path(tmp) / "foreign"
            foreign_plugin = foreign / "plugin"
            foreign_plugin.mkdir(parents=True)
            foreign_link = Path(tmp) / "foreign-link"
            directory_symlink_or_skip(foreign_link, foreign)
            foreign_plugin_link = Path(tmp) / "foreign-plugin-link"
            directory_symlink_or_skip(foreign_plugin_link, foreign_plugin)

            missing_source = plugin_listing()
            missing_source["installed"][0].pop("source")
            missing_marketplace_source = plugin_listing()
            missing_marketplace_source["installed"][0].pop(
                "marketplaceSource")
            malformed_source = plugin_listing()
            malformed_source["installed"][0]["source"] = "local"
            malformed_marketplace_source = plugin_listing()
            malformed_marketplace_source["installed"][0][
                "marketplaceSource"] = []
            extra_source = plugin_listing()
            extra_source["installed"][0]["source"]["unexpected"] = True
            extra_marketplace_source = plugin_listing()
            extra_marketplace_source["installed"][0][
                "marketplaceSource"]["unexpected"] = True
            bad_plugins = [
                "vibepulse@torget installed enabled trusted\n",
                json.dumps([]),
                json.dumps({
                    "installed": "vibepulse@torget", "available": []}),
                json.dumps(plugin_listing(
                    plugin_id="vibepulse-extra@torget")),
                json.dumps(plugin_listing(installed=False)),
                json.dumps(plugin_listing(enabled=False)),
                json.dumps(plugin_listing(marketplace="torget-lookalike")),
                json.dumps(missing_source),
                json.dumps(missing_marketplace_source),
                json.dumps(malformed_source),
                json.dumps(malformed_marketplace_source),
                json.dumps(extra_source),
                json.dumps(extra_marketplace_source),
                json.dumps(plugin_listing(plugin_source="git")),
                json.dumps(plugin_listing(
                    plugin_path=foreign_plugin)),
                json.dumps(plugin_listing(
                    plugin_path=ROOT.parent / (ROOT.name + "-lookalike"))),
                json.dumps(plugin_listing(
                    plugin_path=foreign_plugin_link)),
                json.dumps(plugin_listing(
                    marketplace_source_type="git")),
                json.dumps(plugin_listing(marketplace_root=foreign)),
                json.dumps(plugin_listing(
                    marketplace_root=ROOT.parent /
                    (ROOT.name + "-lookalike"))),
                json.dumps(plugin_listing(marketplace_root=foreign_link)),
                json.dumps({
                    "installed": [42, plugin_listing()["installed"][0]],
                    "available": [],
                }),
                json.dumps({
                    "installed": plugin_listing()["installed"],
                    "available": "not-a-list",
                }),
                '{"installed":[],"installed":[]}',
                '{"installed":NaN,"available":[]}',
                "[" * 5000 + "]" * 5000,
                "{",
                json.dumps(plugin_listing()) + " " * (16 * 1024),
            ]
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                codex_interactions=True))
            for transcript in bad_plugins:
                with self.subTest(transcript=transcript[:30]):
                    runner = FakeRunner([
                        python_probe_ok(), codex_probe_ok(),
                        result(stdout=transcript),
                        json_result([owned_mcp()]),
                    ])
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["doctor"], repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner,
                        urlopen=lambda *_args, **_kwargs:
                            BytesResponse(healthy_diagnostics()),
                        stdout=output), 1)
                    self.assertIn("FIX Codex plugin", output.getvalue())

    def test_doctor_rejects_every_mcp_false_green_transcript(self):
        setup = load_setup()
        bad_mcp = [
            "vibepulse running\n",
            {"name": "vibepulse"},
            [owned_mcp(name="vibepulse-extra")],
            [owned_mcp(enabled=False)],
            [owned_mcp(python=Path("/wrong/python"))],
            [owned_mcp(repo=Path("/wrong/root"))],
            [owned_mcp(transport_extra={"env_vars": ["SECRET"]})],
            [owned_mcp(transport_extra={"unexpected": True})],
            "[" * 5000 + "]" * 5000,
            "{",
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                codex_interactions=True))
            for transcript in bad_mcp:
                with self.subTest(transcript=str(transcript)[:40]):
                    wire = (transcript if isinstance(transcript, str)
                            else json.dumps(transcript))
                    runner = FakeRunner([
                        python_probe_ok(), codex_probe_ok(),
                        json_result(plugin_listing()), result(stdout=wire),
                    ])
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["doctor"], repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner,
                        urlopen=lambda *_args, **_kwargs:
                            BytesResponse(healthy_diagnostics()),
                        stdout=output), 1)
                    self.assertIn("FIX Codex MCP", output.getvalue())
                    self.assertNotIn("SECRET", output.getvalue())

    def test_doctor_rejects_nonobject_and_wrong_loopback_diagnostics(self):
        setup = load_setup()
        bad_bodies = [
            "not bytes", None,
            b"[]", b"null", b"1", b'"text"', b"{", b"{}",
            json.dumps({
                "service": "torget-tokenserver", "interactions": []}).encode(),
            json.dumps({
                "service": "torget-tokenserver",
                "interactions": {
                    "claude": False, "codex": True, "detail": False,
                    "transport": "relay",
                },
            }).encode(),
            healthy_diagnostics() + b" " * (16 * 1024),
            (b"[" * 5000) + (b"]" * 5000),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                codex_interactions=True))
            for body in bad_bodies:
                with self.subTest(body=repr(body)[:30]):
                    response = BytesResponse(body)
                    runner = FakeRunner([
                        python_probe_ok(), codex_probe_ok(),
                        json_result(plugin_listing()),
                        json_result([owned_mcp()]),
                    ])
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["doctor"], repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner,
                        urlopen=lambda *_args, **_kwargs: response,
                        stdout=output), 1)
                    self.assertIn("FIX Tokenserver", output.getvalue())
                    self.assertEqual(response.limits,
                                     [setup.MAX_DIAGNOSTIC_BYTES + 1])

    def test_uninstall_preflights_all_resources_and_refuses_foreign_ownership(self):
        setup = load_setup()
        foreign_mcp = [owned_mcp(python=Path("/bin/echo"))]
        foreign_plugin = plugin_listing(
            repo=ROOT, plugin_path=ROOT.parent / "foreign-plugin")
        foreign_marketplace = marketplace_listing(
            repo=ROOT, root=ROOT.parent, source=ROOT.parent)
        cases = {
            "mcp": uninstall_preflight(
                mcp=foreign_mcp, plugin=plugin_listing(),
                marketplace=marketplace_listing()),
            "plugin": uninstall_preflight(
                mcp=[owned_mcp()], plugin=foreign_plugin,
                marketplace=marketplace_listing()),
            "marketplace": uninstall_preflight(
                mcp=[owned_mcp()], plugin=plugin_listing(),
                marketplace=foreign_marketplace),
            "malformed": [json_result([owned_mcp()]),
                          result(stdout="not-json\n"),
                          json_result(marketplace_listing())],
        }
        codex_path = str(Path("/codex").resolve())
        expected_preflight = [
            [codex_path, "mcp", "list", "--json"],
            [codex_path, "plugin", "list", "--json"],
            [codex_path, "plugin", "marketplace", "list", "--json"],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for label, responses in cases.items():
                with self.subTest(label=label):
                    path = Path(tmp) / f"{label}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, codex_interactions=True,
                        interaction_detail=True)
                    setup.save_config(path, original)
                    runner = FakeRunner(responses)
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["uninstall", "codex"], repo_root=ROOT,
                        config_path=path, python=Path(sys.executable),
                        codex=Path("/codex"), run=runner, stdout=output), 1)
                    self.assertEqual([call[0] for call in runner.calls],
                                     expected_preflight)
                    self.assertEqual(setup.load_config(path), original)
                    self.assertNotIn("PASS", output.getvalue())

    def test_install_runtime_probes_abort_before_resource_or_mutation_calls(self):
        setup = load_setup()
        cases = [
            [result(stdout="unsupported\n")],
            [python_probe_ok(), result(stdout="not-codex 0.148.0\n")],
            [result(1, stderr="invalid utf8 boundary")],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, responses in enumerate(cases):
                with self.subTest(index=index):
                    path = Path(tmp) / f"runtime-{index}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    runner = FakeRunner(responses)
                    self.assertEqual(setup.main(
                        ["install", "--providers", "codex", "--no-detail"],
                        repo_root=ROOT, config_path=path,
                        python=Path("/bin/sh"), codex=Path("/codex"),
                        run=runner, stdout=io.StringIO(),
                        stdin_isatty=False), 1)
                    self.assertLessEqual(len(runner.calls), 2)
                    self.assertFalse(any("list" in call[0]
                                         for call in runner.calls))
                    self.assertEqual(setup.load_config(path), original)

    def test_install_rolls_back_every_new_resource_on_each_failure(self):
        setup = load_setup()
        mutations = setup.plan_codex_install(
            ROOT, Path(sys.executable), Path("/codex"))
        action_names = (
            "marketplace_add", "plugin_add", "mcp_remove", "mcp_add")
        with tempfile.TemporaryDirectory() as tmp:
            for failed_index in range(4):
                with self.subTest(failed_index=failed_index):
                    path = Path(tmp) / f"install-{failed_index}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    runner = StatefulCodexRunner(actions={
                        action_names[failed_index]: ["fail"],
                    })
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["install", "--providers", "codex", "--no-detail"],
                        repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner, stdout=output,
                        stdin_isatty=False), 1)
                    calls = [call[0] for call in runner.calls]
                    self.assertEqual(calls[5:5 + failed_index + 1],
                                     mutations[:failed_index + 1])
                    self.assertEqual(runner.state, {
                        "mcp": False, "plugin": False,
                        "marketplace": False})
                    self.assertEqual(setup.load_config(path), original)
                    self.assertNotIn("SECRET", output.getvalue())

    def test_install_restores_owned_mcp_and_reports_rollback_divergence(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = setup.VibePulseConfig(codex_interactions=True)
            setup.save_config(path, original)
            runner = StatefulCodexRunner(
                mcp=True, plugin=True, marketplace=True,
                actions={"mcp_add": ["fail", "noop_ok", "noop_ok"]})
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["install", "--providers", "codex", "--no-detail"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=output, stdin_isatty=False), 1)
            self.assertIn("irrecoverable divergence", output.getvalue().lower())
            self.assertNotIn("SECRET", output.getvalue())
            self.assertEqual(setup.load_config(path), original)

    def test_uninstall_compensates_in_reverse_at_each_failure(self):
        setup = load_setup()
        removes = setup.plan_codex_uninstall(Path("/codex"))
        actions = ("mcp_remove", "plugin_remove", "marketplace_remove")
        with tempfile.TemporaryDirectory() as tmp:
            for failed_index in range(3):
                with self.subTest(failed_index=failed_index):
                    path = Path(tmp) / f"uninstall-{failed_index}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, codex_interactions=True,
                        interaction_detail=True)
                    setup.save_config(path, original)
                    runner = StatefulCodexRunner(
                        mcp=True, plugin=True, marketplace=True,
                        actions={actions[failed_index]: ["fail"]})
                    self.assertEqual(setup.main(
                        ["uninstall", "codex"], repo_root=ROOT,
                        config_path=path, python=Path(sys.executable),
                        codex=Path("/codex"), run=runner,
                        stdout=io.StringIO()), 1)
                    calls = [call[0] for call in runner.calls]
                    self.assertEqual(calls[3:3 + failed_index + 1],
                                     removes[:failed_index + 1])
                    self.assertEqual(runner.state, {
                        "mcp": True, "plugin": True,
                        "marketplace": True})
                    self.assertEqual(setup.load_config(path), original)

    def test_config_conflict_after_install_rolls_back_external_state(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = setup.VibePulseConfig(claude_interactions=True)
            changed = setup.VibePulseConfig(interaction_detail=True)
            setup.save_config(path, original)
            base = StatefulCodexRunner()

            def runner(argv, **kwargs):
                answer = base(argv, **kwargs)
                if argv[1:4] == ["mcp", "add", "vibepulse"]:
                    setup.save_config(path, changed)
                return answer

            output = io.StringIO()
            self.assertEqual(setup.main(
                ["install", "--providers", "codex", "--no-detail"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=output, stdin_isatty=False), 1)
            self.assertEqual(setup.load_config(path), changed)
            self.assertEqual(base.state, {
                "mcp": False, "plugin": False, "marketplace": False})
            self.assertIn("config", output.getvalue().lower())

    def test_disable_is_not_blocked_by_slow_install_external_command(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True))
            entered = threading.Event()
            release = threading.Event()
            responses = install_preflight() + [result()] * 4 + [result()] * 3
            base = FakeRunner(responses)

            def slow_runner(argv, **kwargs):
                if not entered.is_set():
                    entered.set()
                    release.wait(3)
                return base(argv, **kwargs)

            install_result = []
            thread = threading.Thread(target=lambda: install_result.append(
                setup.main(
                    ["install", "--providers", "codex", "--no-detail"],
                    repo_root=ROOT, config_path=path,
                    python=Path(sys.executable), codex=Path("/codex"),
                    run=slow_runner, stdout=io.StringIO(),
                    stdin_isatty=False)))
            thread.start()
            self.assertTrue(entered.wait(1))
            started = time.monotonic()
            self.assertEqual(setup.main(
                ["disable", "codex"], config_path=path,
                stdout=io.StringIO()), 0)
            elapsed = time.monotonic() - started
            release.set()
            thread.join(timeout=4)
            self.assertFalse(thread.is_alive())
            self.assertLess(elapsed, 1.0)
            self.assertEqual(install_result, [1])
            self.assertFalse(setup.load_config(path).codex_interactions)

    def test_doctor_default_opener_ignores_proxy_and_rejects_forged_pass(self):
        setup = load_setup()

        class ProxyHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.server.requests.append(self.path)
                body = healthy_diagnostics()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format, *_args):
                pass

        proxy = ThreadingHTTPServer(("127.0.0.1", 0), ProxyHandler)
        proxy.requests = []
        thread = threading.Thread(target=proxy.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "config.json"
                setup.save_config(path, setup.VibePulseConfig(
                    claude_interactions=True))
                unavailable = closed_port()
                proxy_url = f"http://127.0.0.1:{proxy.server_address[1]}"
                with mock.patch.object(
                        setup, "TOKEN_SERVER_URL",
                        f"http://127.0.0.1:{unavailable}/"), mock.patch.dict(
                            os.environ, {
                                "HTTP_PROXY": proxy_url,
                                "HTTPS_PROXY": proxy_url,
                                "ALL_PROXY": proxy_url,
                                "NO_PROXY": "",
                            }, clear=False):
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["doctor"], config_path=path,
                        python=Path(sys.executable), codex=None,
                        run=FakeRunner([python_probe_ok()]),
                        stdout=output), 1)
                self.assertEqual(proxy.requests, [])
                self.assertIn("FIX Tokenserver", output.getvalue())
        finally:
            proxy.shutdown()
            proxy.server_close()
            thread.join(timeout=2)

    def test_production_process_boundary_drains_caps_utf8_and_recovers(self):
        setup = load_setup()
        noisy = [
            sys.executable, "-c",
            "import os; b=b'x'*20000; os.write(1,b); os.write(2,b)",
        ]
        invalid = [sys.executable, "-c", "import os; os.write(1,b'\\xff')"]
        valid = [sys.executable, "-c", "print('ok')"]
        self.assertIsNone(setup._invoke(noisy, setup._AUTO))
        self.assertIsNone(setup._invoke(invalid, setup._AUTO))
        completed = setup._invoke(valid, setup._AUTO)
        self.assertIsNotNone(completed)
        self.assertEqual(completed.stdout, "ok" + os.linesep)
        self.assertFalse(any(thread.name.startswith("vibepulse-drain-")
                             for thread in threading.enumerate()))

    def test_loopback_requires_status_and_one_utf8_json_content_type(self):
        setup = load_setup()
        bad_responses = [
            BytesResponse(healthy_diagnostics(), status=201),
            BytesResponse(healthy_diagnostics(), content_types=()),
            BytesResponse(healthy_diagnostics(),
                          content_types=("application/json", "application/json")),
            BytesResponse(healthy_diagnostics(),
                          content_types=("application/json; charset=latin-1",)),
            BytesResponse(healthy_diagnostics(),
                          content_types=("text/plain",)),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            setup.save_config(path, setup.VibePulseConfig(
                claude_interactions=True))
            for response in bad_responses:
                with self.subTest(status=response.status,
                                  types=response.headers.content_types):
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["doctor"], config_path=path,
                        python=Path(sys.executable), codex=None,
                        run=FakeRunner([python_probe_ok()]),
                        urlopen=lambda *_args, **_kwargs: response,
                        stdout=output), 1)
                    self.assertIn("FIX Tokenserver", output.getvalue())

    def test_install_refuses_foreign_plugin_or_marketplace_before_mutation(self):
        setup = load_setup()
        cases = [
            install_preflight(
                plugin=plugin_listing(
                    repo=ROOT, plugin_path=ROOT.parent / "foreign")),
            install_preflight(
                marketplace=marketplace_listing(
                    repo=ROOT, root=ROOT.parent, source=ROOT.parent)),
            [python_probe_ok(), codex_probe_ok(), json_result([]),
             result(stdout="{\"installed\":[]}"),
             json_result({"marketplaces": []})],
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for index, responses in enumerate(cases):
                with self.subTest(index=index):
                    path = Path(tmp) / f"foreign-{index}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    runner = FakeRunner(responses)
                    self.assertEqual(setup.main(
                        ["install", "--providers", "codex", "--no-detail"],
                        repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner, stdout=io.StringIO(),
                        stdin_isatty=False), 1)
                    self.assertEqual(len(runner.calls), 5)
                    self.assertEqual(setup.load_config(path), original)

    def test_config_save_failure_restores_full_external_prestate(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            original = setup.VibePulseConfig(
                claude_interactions=True, codex_interactions=True,
                interaction_detail=True)

            install_path = root / "install.json"
            setup.save_config(install_path, original)
            install_runner = StatefulCodexRunner()
            install_output = io.StringIO()
            with mock.patch.object(
                    setup, "save_config",
                    side_effect=setup.ConfigError("atomic save failed")):
                self.assertEqual(setup.main(
                    ["install", "--providers", "codex", "--no-detail"],
                    repo_root=ROOT, config_path=install_path,
                    python=Path(sys.executable), codex=Path("/codex"),
                    run=install_runner, stdout=install_output,
                    stdin_isatty=False), 1)
            self.assertEqual(install_runner.state, {
                "mcp": False, "plugin": False, "marketplace": False})
            self.assertEqual(setup.load_config(install_path), original)

            uninstall_path = root / "uninstall.json"
            setup.save_config(uninstall_path, original)
            uninstall_runner = StatefulCodexRunner(
                mcp=True, plugin=True, marketplace=True)
            uninstall_output = io.StringIO()
            with mock.patch.object(
                    setup, "save_config",
                    side_effect=setup.ConfigError("atomic save failed")):
                self.assertEqual(setup.main(
                    ["uninstall", "codex"], repo_root=ROOT,
                    config_path=uninstall_path,
                    python=Path(sys.executable), codex=Path("/codex"),
                    run=uninstall_runner, stdout=uninstall_output), 1)
            self.assertEqual(uninstall_runner.state, {
                "mcp": True, "plugin": True, "marketplace": True})
            self.assertEqual(setup.load_config(uninstall_path), original)
            self.assertNotIn("PASS", install_output.getvalue() +
                             uninstall_output.getvalue())

    def test_post_commit_config_error_restores_snapshot_or_reports_divergence(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            for restore_ok in (True, False):
                with self.subTest(restore_ok=restore_ok):
                    path = Path(tmp) / f"post-commit-{restore_ok}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    target = setup.VibePulseConfig(codex_interactions=True)
                    setup.save_config(path, original)
                    real_save = setup.save_config
                    calls = []

                    def post_commit_failure(save_path, config):
                        calls.append(config)
                        if len(calls) == 1:
                            real_save(save_path, config)
                            raise setup.ConfigError("failure after replace")
                        if restore_ok:
                            real_save(save_path, config)
                        else:
                            raise setup.ConfigError("restore failed")

                    runner = StatefulCodexRunner()
                    output = io.StringIO()
                    with mock.patch.object(
                            setup, "save_config", side_effect=post_commit_failure):
                        self.assertEqual(setup.main(
                            ["install", "--providers", "codex", "--no-detail"],
                            repo_root=ROOT, config_path=path,
                            python=Path(sys.executable), codex=Path("/codex"),
                            run=runner, stdout=output,
                            stdin_isatty=False), 1)
                    self.assertEqual(calls, [target, original])
                    self.assertEqual(setup.load_config(path),
                                     original if restore_ok else target)
                    if restore_ok:
                        self.assertNotIn("irrecoverable configuration",
                                         output.getvalue().lower())
                    else:
                        self.assertIn("irrecoverable configuration",
                                      output.getvalue().lower())

    def test_failed_forward_command_is_reconciled_from_observed_state(self):
        setup = load_setup()
        cases = [
            ("marketplace_add", {"mcp": False, "plugin": False,
                                  "marketplace": False}),
            ("plugin_add", {"mcp": False, "plugin": False,
                             "marketplace": False}),
            ("mcp_add", {"mcp": False, "plugin": False,
                          "marketplace": False}),
            ("mcp_remove", {"mcp": True, "plugin": True,
                             "marketplace": True}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            for action, initial in cases:
                with self.subTest(action=action):
                    path = Path(tmp) / f"{action}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    runner = StatefulCodexRunner(
                        **initial, actions={action: ["commit_fail"]})
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["install", "--providers", "codex", "--no-detail"],
                        repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner, stdout=output,
                        stdin_isatty=False), 1)
                    self.assertEqual(runner.state, initial)
                    self.assertEqual(setup.load_config(path), original)
                    self.assertIn("external state restored", output.getvalue())
                    self.assertNotIn("PASS", output.getvalue())

    def test_uninstall_commit_then_failure_reconciles_every_resource(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            for action in ("mcp_remove", "plugin_remove",
                           "marketplace_remove"):
                with self.subTest(action=action):
                    path = Path(tmp) / f"{action}.json"
                    original = setup.VibePulseConfig(
                        codex_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    runner = StatefulCodexRunner(
                        mcp=True, plugin=True, marketplace=True,
                        actions={action: ["commit_fail"]})
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["uninstall", "codex"], repo_root=ROOT,
                        config_path=path, python=Path(sys.executable),
                        codex=Path("/codex"), run=runner, stdout=output), 1)
                    self.assertEqual(runner.state, {
                        "mcp": True, "plugin": True, "marketplace": True})
                    self.assertEqual(setup.load_config(path), original)
                    self.assertIn("external state restored", output.getvalue())

    def test_reconciliation_never_trusts_noop_or_uninspectable_compensation(self):
        setup = load_setup()
        cases = {
            "noop": StatefulCodexRunner(actions={
                "plugin_add": ["commit_fail"],
                "plugin_remove": ["noop_ok", "noop_ok"],
            }),
            "inspection": StatefulCodexRunner(
                actions={"marketplace_add": ["commit_fail"]},
                fail_inspection_rounds={2}),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for label, runner in cases.items():
                with self.subTest(label=label):
                    path = Path(tmp) / f"{label}.json"
                    original = setup.VibePulseConfig(
                        claude_interactions=True, interaction_detail=True)
                    setup.save_config(path, original)
                    output = io.StringIO()
                    self.assertEqual(setup.main(
                        ["install", "--providers", "codex", "--no-detail"],
                        repo_root=ROOT, config_path=path,
                        python=Path(sys.executable), codex=Path("/codex"),
                        run=runner, stdout=output,
                        stdin_isatty=False), 1)
                    self.assertIn("irrecoverable divergence",
                                  output.getvalue().lower())
                    self.assertNotIn("external state restored",
                                     output.getvalue())
                    self.assertEqual(setup.load_config(path), original)
                    if label == "noop":
                        self.assertEqual(runner.state, {
                            "mcp": False, "plugin": True,
                            "marketplace": False})
                    else:
                        self.assertEqual(runner.state, {
                            "mcp": False, "plugin": False,
                            "marketplace": True})

    def test_reconciliation_dependency_order_is_exact(self):
        setup = load_setup()
        absent = setup._ExternalState(
            mcp=False, plugin=False, marketplace=False)
        present = setup._ExternalState(
            mcp=True, plugin=True, marketplace=True)
        self.assertEqual(setup._state_reconciliation_commands(
            present, absent, repo_root=ROOT, python=Path(sys.executable),
            codex=Path("/codex")), setup.plan_codex_uninstall(Path("/codex")))
        install = setup.plan_codex_install(
            ROOT, Path(sys.executable), Path("/codex"))
        self.assertEqual(setup._state_reconciliation_commands(
            absent, present, repo_root=ROOT, python=Path(sys.executable),
            codex=Path("/codex")),
            [install[0], install[1], install[3], install[4]])

    def test_success_requires_strict_observed_desired_external_state(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = setup.VibePulseConfig(claude_interactions=True)
            setup.save_config(path, original)
            runner = StatefulCodexRunner(actions={
                "marketplace_add": ["noop_ok"],
            })
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["install", "--providers", "codex", "--no-detail"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=output, stdin_isatty=False), 1)
            self.assertEqual(runner.state, {
                "mcp": False, "plugin": False, "marketplace": False})
            self.assertEqual(setup.load_config(path), original)
            self.assertNotIn("PASS", output.getvalue())

    def assert_process_tree_killed(self, pid):
        # SIGKILL lands immediately, but reaping belongs to the reaper: on
        # Linux an orphan killed via killpg stays a kill-proof zombie until
        # pid 1 (or the nearest subreaper) collects it, and os.kill(pid, 0)
        # succeeds on zombies. Killed therefore means: gone, or a zombie
        # that can never run again — never a schedulable process.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                return
            try:
                stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
                state = stat.rsplit(")", 1)[1].split()[0]
            except (OSError, IndexError):
                state = ""
            if state == "Z":
                return
            time.sleep(0.05)
        self.fail(f"descendant {pid} still schedulable after tree kill")

    @unittest.skipUnless(os.name == "posix", "POSIX process-group behavior")
    def test_timeout_kills_descendant_that_inherits_output_pipes(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "descendant.pid"
            code = (
                "import pathlib,subprocess,sys,time; "
                "p=subprocess.Popen([sys.executable,'-c',"
                "'import time; time.sleep(10)']); "
                "pathlib.Path(sys.argv[1]).write_text(str(p.pid)); "
                "time.sleep(10)"
            )
            # Leave enough time for a fresh Python interpreter to start and
            # write the child PID; the assertion below still catches waiting
            # for the descendant's inherited pipes (10 seconds).
            with mock.patch.object(setup, "COMMAND_TIMEOUT_SECONDS", 0.5):
                started = time.monotonic()
                self.assertIsNone(setup._invoke(
                    [sys.executable, "-c", code, str(pid_path)], setup._AUTO))
                elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.0)
            descendant_pid = int(pid_path.read_text(encoding="utf-8"))
            self.assert_process_tree_killed(descendant_pid)
            recovered = setup._invoke(
                [sys.executable, "-c", "print('after-tree-kill')"],
                setup._AUTO)
            self.assertIsNotNone(recovered)
            self.assertEqual(recovered.stdout, "after-tree-kill\n")
            self.assertFalse(any(thread.name.startswith("vibepulse-drain-")
                                 for thread in threading.enumerate()))

    @unittest.skipUnless(os.name == "posix", "POSIX detached-pipe behavior")
    def test_detached_descendant_cannot_extend_process_capture_deadline(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            pid_path = Path(tmp) / "detached.pid"
            descendant = (
                "import os,pathlib,sys,time; "
                "os.setsid(); "
                "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                "time.sleep(4)"
            )
            parent = (
                "import pathlib,subprocess,sys,time; "
                "subprocess.Popen([sys.executable,'-c',sys.argv[1],"
                "sys.argv[2]]); "
                "p=pathlib.Path(sys.argv[2]); "
                "exec('while not p.exists():\\n time.sleep(.01)'); "
                "time.sleep(10)"
            )
            detached_pid = None
            try:
                with mock.patch.object(
                        setup, "COMMAND_TIMEOUT_SECONDS", 0.5), \
                        mock.patch.object(
                            setup, "PIPE_JOIN_TIMEOUT_SECONDS", 0.05):
                    started = time.monotonic()
                    self.assertIsNone(setup._invoke([
                        sys.executable, "-c", parent, descendant,
                        str(pid_path)], setup._AUTO))
                    elapsed = time.monotonic() - started
                self.assertLess(elapsed, 1.5)
                detached_pid = int(pid_path.read_text(encoding="utf-8"))
                os.kill(detached_pid, 0)
                recovered = setup._invoke(
                    [sys.executable, "-c", "print('after-detach')"],
                    setup._AUTO)
                self.assertIsNotNone(recovered)
                self.assertEqual(recovered.stdout, "after-detach\n")
                self.assertFalse(any(
                    thread.name.startswith("vibepulse-drain-")
                    for thread in threading.enumerate()))
            finally:
                if detached_pid is not None:
                    try:
                        os.kill(detached_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

    def test_keyboard_interrupt_compensates_and_does_not_escape_cli(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = setup.VibePulseConfig(claude_interactions=True)
            setup.save_config(path, original)
            runner = StatefulCodexRunner(actions={
                "plugin_add": ["commit_interrupt"],
            })
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["install", "--providers", "codex", "--no-detail"],
                repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=output, stdin_isatty=False), 1)
            self.assertEqual(runner.state, {
                "mcp": False, "plugin": False, "marketplace": False})
            self.assertEqual(setup.load_config(path), original)
            self.assertNotIn("PASS", output.getvalue())

    def test_uninstall_reports_irrecoverable_divergence_on_restore_failure(self):
        setup = load_setup()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            original = setup.VibePulseConfig(codex_interactions=True)
            setup.save_config(path, original)
            runner = FakeRunner(uninstall_preflight(
                mcp=[owned_mcp()], plugin=plugin_listing(),
                marketplace=marketplace_listing()) + [
                    result(), result(3, stderr="remove failure"),
                    result(4, stderr="restore failure SECRET")])
            output = io.StringIO()
            self.assertEqual(setup.main(
                ["uninstall", "codex"], repo_root=ROOT, config_path=path,
                python=Path(sys.executable), codex=Path("/codex"),
                run=runner, stdout=output), 1)
            self.assertIn("irrecoverable divergence", output.getvalue().lower())
            self.assertNotIn("SECRET", output.getvalue())
            self.assertEqual(setup.load_config(path), original)

    def test_production_process_timeout_kills_reaps_and_recovers(self):
        setup = load_setup()
        sleeping = [sys.executable, "-c", "import time; time.sleep(10)"]
        with mock.patch.object(setup, "COMMAND_TIMEOUT_SECONDS", 0.1):
            started = time.monotonic()
            self.assertIsNone(setup._invoke(sleeping, setup._AUTO))
            self.assertLess(time.monotonic() - started, 2)
        completed = setup._invoke(
            [sys.executable, "-c", "print('recovered')"], setup._AUTO)
        self.assertIsNotNone(completed)
        self.assertEqual(completed.stdout, "recovered" + os.linesep)
        self.assertFalse(any(thread.name.startswith("vibepulse-drain-")
                             for thread in threading.enumerate()))

    @unittest.skipUnless(os.name == "posix", "POSIX raw-pipe behavior")
    def test_closed_output_does_not_end_a_still_running_process_early(self):
        setup = load_setup()
        closes_then_sleeps = [
            sys.executable, "-c",
            "import os,time; os.close(1); os.close(2); time.sleep(3)",
        ]
        with mock.patch.object(setup, "COMMAND_TIMEOUT_SECONDS", 0.15), \
                mock.patch.object(
                    setup, "PIPE_JOIN_TIMEOUT_SECONDS", 0.05):
            started = time.monotonic()
            self.assertIsNone(setup._invoke(
                closes_then_sleeps, setup._AUTO))
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, 0.1)
        self.assertLess(elapsed, 1.0)

    def test_process_interrupt_terminates_tree_and_windows_uses_taskkill(self):
        setup = load_setup()

        class InterruptProcess:
            pid = 424242
            stdout = io.BytesIO()
            stderr = io.BytesIO()

            def wait(self, timeout=None):
                raise KeyboardInterrupt()

            def poll(self):
                raise KeyboardInterrupt()

        process = InterruptProcess()
        with mock.patch.object(
                setup.subprocess, "Popen", return_value=process), \
                mock.patch.object(setup, "_terminate_process_tree") as stop:
            with self.assertRaises(KeyboardInterrupt):
                setup._bounded_process(["/fake/python", "--version"])
        stop.assert_called_with(process)
        self.assertFalse(any(thread.name.startswith("vibepulse-drain-")
                             for thread in threading.enumerate()))

        class WindowsProcess:
            pid = 31337

            def poll(self):
                return None

            def wait(self, timeout=None):
                return 1

        windows_process = WindowsProcess()
        with mock.patch.object(setup.os, "name", "nt"), \
                mock.patch.object(setup.subprocess, "run") as taskkill:
            setup._terminate_process_tree(windows_process)
        taskkill.assert_called_once()
        argv, kwargs = taskkill.call_args
        self.assertEqual(argv[0], [
            "taskkill", "/PID", "31337", "/T", "/F"])
        self.assertFalse(kwargs["shell"])
        self.assertLessEqual(kwargs["timeout"], 2)

    def test_capture_setup_and_windows_fallback_release_owned_handles(self):
        setup = load_setup()
        opened = []
        real_pipe = os.pipe

        def tracked_pipe():
            pair = real_pipe()
            opened.extend(pair)
            return pair

        with mock.patch.object(setup.os, "pipe", side_effect=tracked_pipe), \
                mock.patch.object(
                    setup.subprocess, "Popen", side_effect=OSError("nope")):
            self.assertIsNone(setup._bounded_posix_process(["/missing"]))
        for fd in opened:
            with self.subTest(fd=fd), self.assertRaises(OSError):
                os.fstat(fd)

        release = threading.Event()

        class HeldPipe:
            def __init__(self):
                self.closed = False

            def read(self, _size):
                release.wait(2)
                return b""

            def close(self):
                self.assert_reader_released()
                self.closed = True

            def assert_reader_released(self):
                if not release.is_set():
                    raise AssertionError("closed while reader was active")

        class TimeoutProcess:
            pid = 8181

            def __init__(self):
                self.stdout = HeldPipe()
                self.stderr = HeldPipe()

            def wait(self, timeout=None):
                raise subprocess.TimeoutExpired("fake", timeout)

            def poll(self):
                return None

        process = TimeoutProcess()
        try:
            with mock.patch.object(
                    setup.subprocess, "Popen", return_value=process), \
                    mock.patch.object(setup, "_terminate_process_tree"), \
                    mock.patch.object(
                        setup, "PIPE_JOIN_TIMEOUT_SECONDS", 0.02):
                started = time.monotonic()
                self.assertIsNone(setup._bounded_thread_process(["fake.exe"]))
                self.assertLess(time.monotonic() - started, 0.3)
            self.assertFalse(process.stdout.closed)
            self.assertFalse(process.stderr.closed)
        finally:
            release.set()
            deadline = time.monotonic() + 1
            while (any(thread.name.startswith("vibepulse-drain-")
                       for thread in threading.enumerate()) and
                   time.monotonic() < deadline):
                time.sleep(0.01)
        self.assertTrue(process.stdout.closed)
        self.assertTrue(process.stderr.closed)
        self.assertFalse(any(thread.name.startswith("vibepulse-drain-")
                             for thread in threading.enumerate()))

    def test_absence_allowlist_is_exact_anchored_and_command_scoped(self):
        setup = load_setup()
        accepted = "Error: marketplace `torget` is not configured or installed\n"
        rejected = [
            "marketplace `torget` is not configured or installed",
            "Error: marketplace 'torget' is not configured or installed",
            accepted + "\n",
            accepted.strip() + "; ignoring unrelated failure",
        ]
        marketplace_remove = [
            "/codex", "plugin", "marketplace", "remove", "torget"]
        self.assertTrue(setup._known_absent(marketplace_remove, accepted))
        for message in rejected:
            with self.subTest(message=message):
                self.assertFalse(setup._known_absent(
                    marketplace_remove, message))
        self.assertFalse(setup._known_absent(
            ["/codex", "mcp", "remove", "vibepulse"], accepted))


if __name__ == "__main__":
    unittest.main()
