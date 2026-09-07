import json
import threading
import time
import unittest
from collections import OrderedDict, deque
from urllib.parse import urlsplit

from tools.tokenserver.interaction_relay import (
    MAX_RESPONSE_BYTES,
    HttpResponse,
    InteractionRelay,
)
from tools.tokenserver.interaction_relay_crypto import (
    RelayKeys,
    RelayRequest,
    b64url_encode,
    decode_device_key,
    decode_request,
    decode_status,
    derive_keys,
    encode_verdict,
)
from tools.tokenserver.interactions import InteractionStore


DEVICE_KEY_HEX = (
    "000102030405060708090a0b0c0d0e0f"
    "101112131415161718191a1b1c1d1e1f"
)
MAILBOX = "vp_A1b2C3d4E5f6G7h8"
MAC_TOKEN = b64url_encode(b"\x11" * 32)
BASE_URL = "https://relay.example"


class Clock:
    def __init__(self, value=1000.0):
        self.value = value

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class DeterministicRandom:
    def __init__(self):
        self.counter = 0

    def __call__(self, size):
        self.counter += 1
        return bytes([self.counter & 0xff]) * size


def approval_event(command="npm test"):
    return {
        "session_id": "private-session",
        "cwd": "/Users/niclas/vibepulse",
        "hook_event_name": "PermissionRequest",
        "tool_name": "Bash",
        "tool_input": {"command": command, "description": "Run tests"},
    }


class FakeMailboxTransport:
    def __init__(self):
        self.calls = []
        self.requests = OrderedDict()
        self.verdicts = OrderedDict()
        self.status_envelope = None
        self.forced = deque()

    def force(self, value):
        self.forced.append(value)

    def post_verdict(self, request_id, envelope):
        self.verdicts[request_id] = envelope

    def __call__(self, method, url, headers, body,
                 connect_timeout, read_timeout):
        self.calls.append({
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": body,
            "connect_timeout": connect_timeout,
            "read_timeout": read_timeout,
        })
        if self.forced:
            forced = self.forced.popleft()
            if isinstance(forced, BaseException):
                raise forced
            return forced

        path = urlsplit(url).path
        no_store = (("Cache-Control", "no-store"),)
        if method == "PUT" and path.endswith("/status"):
            self.status_envelope = body
            return HttpResponse(201, no_store, b"")
        if method == "PUT":
            request_id = path.rsplit("/", 1)[-1]
            existing = self.requests.get(request_id)
            if existing is None:
                self.requests[request_id] = body
                return HttpResponse(201, no_store, b"")
            return HttpResponse(
                200 if existing == body else 409,
                no_store,
                b"",
            )
        if method == "GET" and path.endswith("/verdicts"):
            if not self.verdicts:
                return HttpResponse(204, no_store, b"")
            request_id, envelope = next(iter(self.verdicts.items()))
            response = json.dumps({
                "verdicts": [{
                    "envelope": json.loads(envelope.decode("ascii")),
                    "requestId": request_id,
                    "verdictAtMs": 50_000,
                }],
            }, sort_keys=True, separators=(",", ":")).encode("ascii")
            return HttpResponse(
                200,
                no_store + (("Content-Type", "application/json"),),
                response,
            )
        if method == "DELETE":
            request_id = path.rsplit("/", 1)[-1]
            self.requests.pop(request_id, None)
            self.verdicts.pop(request_id, None)
            return HttpResponse(204, no_store, b"")
        raise AssertionError((method, path))


class InteractionRelayTests(unittest.TestCase):
    def make_stack(self):
        clock = Clock()
        wall = Clock(50_000.0)
        transport = FakeMailboxTransport()
        audit = []
        store = InteractionStore(
            secret=DEVICE_KEY_HEX,
            reveal_detail=True,
            now=clock,
            wall=wall,
            relay_random_bytes=lambda size: b"\x42" * size,
        )
        relay = InteractionRelay(
            store=store,
            base_url=BASE_URL,
            mailbox=MAILBOX,
            mac_token=MAC_TOKEN,
            device_key_hex=DEVICE_KEY_HEX,
            transport=transport,
            now=clock,
            random_bytes=DeterministicRandom(),
            jitter=lambda: 0.0,
            sleeper=lambda _seconds: None,
            audit=lambda event, fields: audit.append((event, fields)),
        )
        return clock, wall, transport, audit, store, relay

    def publish(self, store, relay, transport, command="npm test", hold=30):
        entry = store.park("approval", approval_event(command), hold)
        self.assertIsNotNone(entry)
        relay.run_once()
        envelope = transport.requests[entry.request_id]
        keys = derive_keys(decode_device_key(DEVICE_KEY_HEX), MAILBOX)
        request = decode_request(keys, MAILBOX, entry.request_id, envelope)
        return entry, keys, request

    def test_is_idle_without_pending_and_publish_queue_is_nonblocking_bounded(self):
        _clock, _wall, transport, _audit, store, relay = self.make_stack()
        relay.run_once()
        self.assertEqual(transport.calls, [])

        entries = [store.park("approval", approval_event(), 30)
                   for _ in range(8)]
        self.assertTrue(all(entry is not None for entry in entries))
        self.assertEqual(relay.publish_queue_size, 8)
        relay.on_park(entries[0].relay_job)
        self.assertEqual(relay.publish_queue_size, 8)

    def test_valid_verdict_resolves_once_and_remote_row_is_deleted(self):
        clock, _wall, transport, _audit, store, relay = self.make_stack()
        entry, keys, request = self.publish(store, relay, transport)
        self.assertEqual(request.view_bytes, entry.relay_job.view_bytes)
        self.assertEqual(request.challenge, entry.relay_job.challenge)

        verdict = encode_verdict(keys, MAILBOX, request, "approve")
        transport.post_verdict(entry.request_id, verdict)
        clock.advance(0.5)
        relay.run_once()

        result = store.await_result(entry)
        self.assertEqual(result.verdict, "approve")
        relay.run_once()
        self.assertNotIn(entry.request_id, transport.requests)
        self.assertNotIn(entry.request_id, transport.verdicts)
        relay.run_once()
        self.assertIsNone(store.pending_public())

    def test_retry_reuses_exact_ciphertext_and_backoff_is_bounded(self):
        clock, _wall, transport, _audit, store, relay = self.make_stack()
        transport.force(OSError("offline"))
        entry = store.park("approval", approval_event(), 30)

        relay.run_once()
        first_put = [call for call in transport.calls
                     if call["method"] == "PUT"][0]
        relay.run_once()
        self.assertEqual(len([call for call in transport.calls
                              if call["method"] == "PUT"]), 1)
        clock.advance(0.5)
        relay.run_once()
        puts = [call for call in transport.calls if call["method"] == "PUT"]
        self.assertEqual(len(puts), 2)
        self.assertEqual(puts[0]["body"], puts[1]["body"])
        self.assertIn(entry.request_id, transport.requests)
        self.assertEqual(puts[0]["connect_timeout"], 2.0)
        self.assertEqual(puts[0]["read_timeout"], 5.0)
        self.assertEqual(
            puts[0]["headers"]["Authorization"], f"Bearer {MAC_TOKEN}")

    def test_wrong_hmac_wrong_key_and_tampering_never_resolve(self):
        cases = ("wrong-hmac", "wrong-key", "tampered")
        for case in cases:
            with self.subTest(case=case):
                clock, _wall, transport, _audit, store, relay = \
                    self.make_stack()
                entry, keys, request = self.publish(store, relay, transport)
                if case == "wrong-hmac":
                    bad_keys = RelayKeys(
                        request_aead=keys.request_aead,
                        verdict_aead=keys.verdict_aead,
                        verdict_mac=b"\xff" * 32,
                        status_aead=keys.status_aead,
                    )
                    verdict = encode_verdict(
                        bad_keys, MAILBOX, request, "approve")
                elif case == "wrong-key":
                    bad_keys = derive_keys(b"\xff" * 32, MAILBOX)
                    verdict = encode_verdict(
                        bad_keys, MAILBOX, request, "approve")
                else:
                    encoded = bytearray(encode_verdict(
                        keys, MAILBOX, request, "approve"))
                    encoded[len(encoded) // 2] ^= 1
                    verdict = bytes(encoded)
                transport.post_verdict(entry.request_id, verdict)
                clock.advance(0.5)
                relay.run_once()
                self.assertFalse(entry.done.is_set())
                self.assertIsNotNone(store.pending_public())

    def test_binding_expiry_unknown_and_disallowed_approve_fail_closed(self):
        for case in ("challenge", "digest", "expired", "unknown", "policy"):
            with self.subTest(case=case):
                clock, _wall, transport, _audit, store, relay = \
                    self.make_stack()
                command = "rm -rf important" if case == "policy" else "npm test"
                hold = 1 if case == "expired" else 30
                entry, keys, request = self.publish(
                    store, relay, transport, command=command, hold=hold)
                response_id = entry.request_id
                changed = request
                if case == "challenge":
                    changed = RelayRequest(
                        request.request_id, b"\x43" * 32,
                        request.expires_at, request.view_bytes,
                        request.view_sha256,
                    )
                elif case == "digest":
                    other_view = b'{"can_approve":false}'
                    import hashlib
                    changed = RelayRequest(
                        request.request_id, request.challenge,
                        request.expires_at, other_view,
                        hashlib.sha256(other_view).digest(),
                    )
                elif case == "unknown":
                    response_id = b64url_encode(b"\x99" * 16)
                    changed = RelayRequest(
                        response_id, request.challenge,
                        request.expires_at, request.view_bytes,
                        request.view_sha256,
                    )
                verdict = encode_verdict(
                    keys, MAILBOX, changed,
                    "approve" if case != "expired" else "deny",
                )
                transport.post_verdict(response_id, verdict)
                clock.advance(2.0 if case == "expired" else 0.5)
                relay.run_once()
                if case == "expired":
                    self.assertIsNone(store.await_result(entry))
                else:
                    self.assertFalse(entry.done.is_set())
                    self.assertIsNotNone(store.pending_public())

    def test_authenticated_panic_denies_only_the_current_snapshot(self):
        clock, _wall, transport, _audit, store, relay = self.make_stack()
        anchor, keys, request = self.publish(store, relay, transport)
        other = store.park("approval", approval_event(), 30)
        relay.run_once()
        transport.post_verdict(
            anchor.request_id,
            encode_verdict(keys, MAILBOX, request, "panic"),
        )
        clock.advance(0.5)
        relay.run_once()
        self.assertEqual(store.await_result(anchor).verdict, "deny")
        self.assertEqual(store.await_result(other).verdict, "deny")

    def test_outage_never_extends_deadline_or_suppresses_terminal_fallback(self):
        clock, _wall, transport, _audit, store, relay = self.make_stack()
        transport.force(OSError("offline"))
        entry = store.park("approval", approval_event(), 1)
        deadline = entry.expires_at
        relay.run_once()

        clock.advance(2)
        self.assertIsNone(store.pending_public())
        self.assertIsNone(store.await_result(entry))
        self.assertEqual(entry.expires_at, deadline)

    def test_direct_answer_enqueues_remote_delete_and_thread_is_daemon(self):
        _clock, wall, transport, _audit, store, relay = self.make_stack()
        entry, _keys, _request = self.publish(store, relay, transport)
        stamp = int(wall())
        from tools.tokenserver.interactions import sign_answer_v2
        mac = sign_answer_v2(
            DEVICE_KEY_HEX, "claude", entry.request_id,
            entry.view_sha256, "deny", stamp)
        self.assertEqual(store.resolve(
            entry.request_id, "deny", stamp, mac, provider="claude",
            view_sha256=entry.view_sha256), (True, "ok"))
        relay.run_once()
        self.assertNotIn(entry.request_id, transport.requests)

        relay.start()
        self.assertTrue(relay.thread.is_alive())
        self.assertTrue(relay.thread.daemon)
        relay.stop()
        self.assertFalse(relay.thread.is_alive())

    def test_full_queue_coalesces_remove_and_cleans_uncertain_publish(self):
        _clock, wall, transport, _audit, store, relay = self.make_stack()
        entries = [store.park("approval", approval_event(), 30)
                   for _ in range(8)]
        self.assertTrue(all(entry is not None for entry in entries))
        self.assertEqual(relay.publish_queue_size, 8)

        from tools.tokenserver.interactions import sign_answer_v2
        entry = entries[0]
        stamp = int(wall())
        mac = sign_answer_v2(
            DEVICE_KEY_HEX, "claude", entry.request_id,
            entry.view_sha256, "deny", stamp)
        self.assertEqual(store.resolve(
            entry.request_id, "deny", stamp, mac, provider="claude",
            view_sha256=entry.view_sha256), (True, "ok"))

        relay.run_once()
        methods_for_answered = [
            call["method"] for call in transport.calls
            if call["url"].endswith(entry.request_id)
        ]
        self.assertEqual(methods_for_answered, ["DELETE"])
        self.assertNotIn(entry.request_id, transport.requests)

        # A transport error can happen after the Worker committed the PUT.
        # Removal must still schedule an idempotent DELETE even though the
        # local adapter never observed a successful publish response.
        transport.force(OSError("response lost after commit"))
        uncertain = store.park("approval", approval_event(), 30)
        relay.run_once()
        self.assertFalse(relay._publishes[uncertain.request_id].published)
        relay.on_remove(uncertain.request_id, "resolved")
        relay.run_once()
        self.assertTrue(any(
            call["method"] == "DELETE" and
            call["url"].endswith(uncertain.request_id)
            for call in transport.calls))

    def test_logs_are_redacted_to_origin_and_route_kind(self):
        _clock, _wall, transport, audit, store, relay = self.make_stack()
        entry = store.park("approval", approval_event(), 30)
        relay.run_once()
        rendered = repr(audit)
        self.assertIn(BASE_URL, rendered)
        self.assertIn("put_request", rendered)
        self.assertNotIn(MAC_TOKEN, rendered)
        self.assertNotIn(MAILBOX, rendered)
        self.assertNotIn(entry.request_id, rendered)
        self.assertNotIn("npm test", rendered)

    def test_constructor_rejects_ambiguous_origins_tokens_and_timeouts(self):
        _clock, _wall, _transport, _audit, store, _relay = self.make_stack()
        invalid = (
            {"base_url": "http://relay.example"},
            {"base_url": "https://user@relay.example"},
            {"base_url": "https://relay.example/path"},
            {"base_url": "https://relay.example?x=1"},
            {"mac_token": b64url_encode(b"short")},
            {"connect_timeout": 0},
            {"read_timeout": float("inf")},
        )
        defaults = {
            "store": store,
            "base_url": BASE_URL,
            "mailbox": MAILBOX,
            "mac_token": MAC_TOKEN,
            "device_key_hex": DEVICE_KEY_HEX,
        }
        for change in invalid:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    InteractionRelay(**(defaults | change))

    def test_untrusted_or_oversize_http_response_never_resolves(self):
        no_store = (("Cache-Control", "no-store"),)
        bad_responses = (
            HttpResponse(200, (("Content-Type", "application/json"),), b"{}"),
            HttpResponse(200, no_store + no_store +
                         (("Content-Type", "application/json"),), b"{}"),
            HttpResponse(200, no_store +
                         (("Content-Type", "text/plain"),), b"{}"),
            HttpResponse(200, no_store +
                         (("Content-Type", "application/json"),),
                         b"x" * (MAX_RESPONSE_BYTES + 1)),
        )
        for response in bad_responses:
            with self.subTest(response=response):
                clock, _wall, transport, _audit, store, relay = \
                    self.make_stack()
                entry, keys, request = self.publish(store, relay, transport)
                transport.post_verdict(
                    entry.request_id,
                    encode_verdict(keys, MAILBOX, request, "approve"),
                )
                transport.force(response)
                clock.advance(0.5)
                relay.run_once()
                self.assertFalse(entry.done.is_set())
                self.assertIsNotNone(store.pending_public())

    def test_failed_delete_backlog_stays_bounded(self):
        _clock, _wall, transport, _audit, _store, relay = self.make_stack()
        for index in range(24):
            request_id = b64url_encode(index.to_bytes(16, "big"))
            transport.force(OSError("offline"))
            relay.on_remove(request_id, "timeout")
            relay.run_once()
        self.assertLessEqual(len(relay._deletes), 8)


class StatusPublisherTests(unittest.TestCase):
    def make_relay(self, *, transport=None, source=None, store=None,
                   publish_interactions=False):
        self.clock = Clock()
        self.wall = Clock(50_000.0)
        self.transport = transport or FakeMailboxTransport()
        self.audit = []
        self.source = source or (lambda: {
            "v": 2,
            "seq": 7,
            "agents": {
                "claude": {"active_count": 1, "jobs": []},
                "codex": {"active_count": 0, "jobs": []},
            },
            "pending": {"private": "must never leave the Mac"},
        })
        return InteractionRelay(
            store=store,
            publish_interactions=publish_interactions,
            publish_agent_status=True,
            status_source=self.source,
            base_url=BASE_URL,
            mailbox=MAILBOX,
            mac_token=MAC_TOKEN,
            device_key_hex=DEVICE_KEY_HEX,
            transport=self.transport,
            now=self.clock,
            wall=self.wall,
            random_bytes=DeterministicRandom(),
            jitter=lambda: 0.0,
            audit=lambda event, fields: self.audit.append((event, fields)),
        )

    def decoded_status(self, envelope):
        keys = derive_keys(decode_device_key(DEVICE_KEY_HEX), MAILBOX)
        return decode_status(keys, MAILBOX, envelope)

    def test_publishes_immediately_then_at_most_every_ten_seconds(self):
        relay = self.make_relay()
        relay.run_once()
        first = self.transport.status_envelope
        self.assertIsNotNone(first)
        decoded = self.decoded_status(first)
        self.assertEqual(decoded.publication_id, 50_000_000)
        self.assertEqual(decoded.expires_at, 50_020)
        snapshot = json.loads(decoded.status_bytes)
        self.assertEqual(snapshot["seq"], 7)
        self.assertNotIn("pending", snapshot)

        calls = len(self.transport.calls)
        self.clock.advance(9.999)
        self.wall.advance(9.999)
        relay.run_once()
        self.assertEqual(len(self.transport.calls), calls)
        self.clock.advance(0.001)
        self.wall.advance(0.001)
        relay.run_once()
        self.assertEqual(len(self.transport.calls), calls + 1)
        second = self.decoded_status(self.transport.status_envelope)
        self.assertGreater(second.publication_id, decoded.publication_id)
        self.assertNotEqual(first, self.transport.status_envelope)

    def test_retry_reuses_exact_ciphertext_then_rotates_after_success(self):
        self.transport = FakeMailboxTransport()
        self.transport.force(OSError("offline"))
        relay = self.make_relay(transport=self.transport)
        relay.run_once()
        first = self.transport.calls[-1]["body"]
        relay.run_once()
        self.assertEqual(len(self.transport.calls), 1)
        self.clock.advance(0.5)
        self.wall.advance(0.5)
        relay.run_once()
        self.assertEqual(self.transport.calls[-1]["body"], first)
        self.clock.advance(10.0)
        self.wall.advance(10.0)
        relay.run_once()
        self.assertNotEqual(self.transport.calls[-1]["body"], first)

    def test_status_failure_cannot_delay_an_interaction_publish(self):
        class StatusOffline(FakeMailboxTransport):
            def __call__(self, method, url, headers, body,
                         connect_timeout, read_timeout):
                if urlsplit(url).path.endswith("/status"):
                    self.calls.append({
                        "method": method, "url": url,
                        "headers": dict(headers), "body": body,
                        "connect_timeout": connect_timeout,
                        "read_timeout": read_timeout,
                    })
                    raise OSError("status offline")
                return super().__call__(method, url, headers, body,
                                        connect_timeout, read_timeout)

        transport = StatusOffline()
        store = InteractionStore(
            secret=DEVICE_KEY_HEX, reveal_detail=True,
            now=Clock(), wall=Clock(50_000.0),
            relay_random_bytes=lambda size: b"\x42" * size)
        relay = self.make_relay(
            transport=transport, store=store, publish_interactions=True)
        entry = store.park("approval", approval_event(), 30)
        relay.run_once()
        self.assertIn(entry.request_id, transport.requests)
        self.assertTrue(any(
            fields["route"] == "put_status" for _event, fields in self.audit))

    def test_blocked_status_http_cannot_hold_the_verdict_worker(self):
        status_entered = threading.Event()
        release_status = threading.Event()
        request_published = threading.Event()

        class BlockingStatus(FakeMailboxTransport):
            def __call__(self, method, url, headers, body,
                         connect_timeout, read_timeout):
                if urlsplit(url).path.endswith("/status"):
                    status_entered.set()
                    release_status.wait(2)
                response = super().__call__(
                    method, url, headers, body,
                    connect_timeout, read_timeout)
                if method == "PUT" and not urlsplit(url).path.endswith(
                        "/status"):
                    request_published.set()
                return response

        transport = BlockingStatus()
        store = InteractionStore(
            secret=DEVICE_KEY_HEX, reveal_detail=True,
            now=Clock(), wall=Clock(50_000.0),
            relay_random_bytes=lambda size: b"\x42" * size)
        relay = self.make_relay(
            transport=transport, store=store, publish_interactions=True)
        relay.start()
        try:
            self.assertTrue(status_entered.wait(1))
            entry = store.park("approval", approval_event(), 30)
            self.assertIsNotNone(entry)
            self.assertTrue(
                request_published.wait(0.5),
                "blocked status request delayed interaction publishing")
        finally:
            release_status.set()
            relay.stop()

    def test_status_only_requires_a_source_but_not_an_interaction_store(self):
        defaults = {
            "store": None,
            "publish_interactions": False,
            "publish_agent_status": True,
            "base_url": BASE_URL,
            "mailbox": MAILBOX,
            "mac_token": MAC_TOKEN,
            "device_key_hex": DEVICE_KEY_HEX,
        }
        with self.assertRaises(ValueError):
            InteractionRelay(**defaults)
        with self.assertRaises(ValueError):
            InteractionRelay(
                **{**defaults, "publish_interactions": True,
                   "status_source": lambda: {}})


if __name__ == "__main__":
    unittest.main()
