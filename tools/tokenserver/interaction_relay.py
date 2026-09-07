"""Outbound-only adapter for the optional encrypted interaction relay.

The store hands immutable, already-bounded jobs to this module.  All HTTP,
AEAD work, retries, and logging happen outside the store lock; only the final
pure HMAC verification is called back while the store performs its atomic
consume transition.
"""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import math
import queue
import secrets
import threading
import time
from typing import Callable, Optional
from urllib.parse import quote, urlsplit

try:
    from .interaction_relay_crypto import (
        RelayRequest,
        RelayVerdict,
        MAX_STATUS_BYTES,
        b64url_decode,
        decode_device_key,
        decode_verdict,
        derive_keys,
        encode_request,
        encode_status,
        verify_verdict_mac,
    )
    from .interactions import RelayPublishJob, RelayResolution
except ImportError:  # pragma: no cover - direct script import convention
    from interaction_relay_crypto import (
        RelayRequest,
        RelayVerdict,
        MAX_STATUS_BYTES,
        b64url_decode,
        decode_device_key,
        decode_verdict,
        derive_keys,
        encode_request,
        encode_status,
        verify_verdict_mac,
    )
    from interactions import RelayPublishJob, RelayResolution


MAX_RESPONSE_BYTES = 4096
QUEUE_CAPACITY = 8
DELETE_CAPACITY = 8
MIN_BACKOFF_S = 0.5
MAX_BACKOFF_S = 5.0
POLL_INTERVAL_S = 0.5
STATUS_PUBLISH_INTERVAL_S = 10.0
STATUS_EXPIRY_S = 20


@dataclass(frozen=True)
class HttpResponse:
    status: int
    headers: tuple[tuple[str, str], ...]
    body: bytes


@dataclass
class _PublishState:
    job: RelayPublishJob
    envelope: bytes
    published: bool = False
    next_attempt: float = 0.0
    failures: int = 0


@dataclass
class _DeleteState:
    request_id: str
    next_attempt: float = 0.0
    failures: int = 0


@dataclass
class _StatusPublishState:
    envelope: bytes
    publication_id: int
    next_attempt: float
    failures: int = 0


def _json_pairs(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _canonical_object(value: dict) -> bytes:
    return json.dumps(
        value, ensure_ascii=True, allow_nan=False, sort_keys=True,
        separators=(",", ":"),
    ).encode("ascii")


def _strict_json(raw: bytes):
    if not isinstance(raw, bytes) or not raw or len(raw) > MAX_RESPONSE_BYTES:
        raise ValueError("invalid response body")
    try:
        return json.loads(
            raw.decode("utf-8", "strict"),
            object_pairs_hook=_json_pairs,
            parse_constant=lambda _value: (_ for _ in ()).throw(
                ValueError("invalid JSON number")),
        )
    except (UnicodeError, json.JSONDecodeError, ValueError, RecursionError,
            OverflowError) as exc:
        raise ValueError("invalid response JSON") from exc


def _header_values(response: HttpResponse, name: str) -> list[str]:
    if not isinstance(response, HttpResponse) or \
            not isinstance(response.headers, tuple):
        raise ValueError("invalid HTTP response")
    values = []
    for item in response.headers:
        if not isinstance(item, tuple) or len(item) != 2 or \
                not all(isinstance(part, str) for part in item):
            raise ValueError("invalid HTTP response headers")
        if item[0].lower() == name.lower():
            values.append(item[1].strip())
    return values


def _validate_response(response: HttpResponse, *, json_body: bool) -> None:
    if type(response.status) is not int or \
            not isinstance(response.body, bytes) or \
            len(response.body) > MAX_RESPONSE_BYTES:
        raise ValueError("invalid HTTP response")
    if _header_values(response, "Cache-Control") != ["no-store"]:
        raise ValueError("relay response is cacheable")
    content_types = _header_values(response, "Content-Type")
    if json_body:
        if len(content_types) != 1:
            raise ValueError("missing response media type")
        media_type = [part.strip().lower()
                      for part in content_types[0].split(";")]
        if media_type[0] != "application/json" or any(
                part not in ("charset=utf-8", 'charset="utf-8"')
                for part in media_type[1:]):
            raise ValueError("untrusted response media type")
    elif content_types:
        raise ValueError("unexpected response media type")


def _origin(base_url: str) -> tuple[str, str, int]:
    if not isinstance(base_url, str):
        raise ValueError("relay URL must be HTTPS")
    parsed = urlsplit(base_url)
    if parsed.scheme != "https" or not parsed.hostname or \
            parsed.username is not None or parsed.password is not None or \
            parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("relay URL must be an HTTPS origin")
    try:
        port = parsed.port or 443
    except ValueError as exc:
        raise ValueError("invalid relay port") from exc
    if not (1 <= port <= 65535):
        raise ValueError("invalid relay port")
    host = parsed.hostname
    authority = f"[{host}]" if ":" in host else host
    if port != 443:
        authority = f"{authority}:{port}"
    return f"https://{authority}", host, port


def _default_transport(method: str, url: str,
                       headers: tuple[tuple[str, str], ...], body: bytes,
                       connect_timeout: float,
                       read_timeout: float) -> HttpResponse:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.query or \
            parsed.fragment or parsed.username is not None or \
            parsed.password is not None:
        raise ValueError("invalid relay request URL")
    connection = http.client.HTTPSConnection(
        parsed.hostname, parsed.port or 443, timeout=connect_timeout)
    try:
        connection.connect()
        if connection.sock is None:
            raise OSError("relay connection has no socket")
        connection.sock.settimeout(read_timeout)
        path = parsed.path or "/"
        connection.request(method, path, body=body or None,
                           headers=dict(headers))
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise ValueError("relay response too large")
        return HttpResponse(
            status=response.status,
            headers=tuple((name, value) for name, value
                          in response.getheaders()),
            body=raw,
        )
    finally:
        connection.close()


class InteractionRelay:
    """Fail-closed host adapter with independent interaction/status workers."""

    def __init__(
            self, *, store, base_url: str, mailbox: str, mac_token: str,
            device_key_hex: str, transport=None,
            publish_interactions: bool = True,
            publish_agent_status: bool = False,
            status_source=None,
            now: Callable[[], float] = time.monotonic,
            wall: Callable[[], float] = time.time,
            random_bytes: Callable[[int], bytes] = secrets.token_bytes,
            jitter: Callable[[], float] = secrets.SystemRandom().random,
            sleeper: Callable[[float], None] = time.sleep,
            audit: Optional[Callable[[str, dict], None]] = None,
            connect_timeout: float = 2.0, read_timeout: float = 5.0):
        self._origin, _host, _port = _origin(base_url)
        token = b64url_decode(mac_token)
        if len(token) != 32:
            raise ValueError("relay token must encode 32 bytes")
        if not isinstance(connect_timeout, (int, float)) or \
                not isinstance(read_timeout, (int, float)) or \
                isinstance(connect_timeout, bool) or \
                isinstance(read_timeout, bool) or \
                not math.isfinite(connect_timeout) or \
                not math.isfinite(read_timeout) or \
                connect_timeout <= 0 or read_timeout <= 0:
            raise ValueError("invalid relay timeout")
        if type(publish_interactions) is not bool or \
                type(publish_agent_status) is not bool or \
                not (publish_interactions or publish_agent_status):
            raise ValueError("at least one relay feature must be enabled")
        if publish_interactions and store is None:
            raise ValueError("interaction relay requires a store")
        if publish_agent_status and not callable(status_source):
            raise ValueError("status relay requires a source")
        self._store = store
        self._publish_interactions = publish_interactions
        self._publish_agent_status = publish_agent_status
        self._status_source = status_source
        self._mailbox = mailbox
        self._mac_token = mac_token
        self._keys = derive_keys(decode_device_key(device_key_hex), mailbox)
        self._transport = transport or _default_transport
        self._now = now
        self._wall = wall
        self._random_bytes = random_bytes
        self._jitter = jitter
        self._sleeper = sleeper
        self._audit = audit
        self._connect_timeout = float(connect_timeout)
        self._read_timeout = float(read_timeout)
        self._events = queue.Queue(maxsize=QUEUE_CAPACITY)
        self._publishes: dict[str, _PublishState] = {}
        self._deletes: dict[str, _DeleteState] = {}
        self._next_poll = 0.0
        self._poll_failures = 0
        self._status_state: Optional[_StatusPublishState] = None
        self._next_status = 0.0
        self._last_publication_id = 0
        self._status_prepare_failures = 0
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._status_thread: Optional[threading.Thread] = None
        if self._publish_interactions:
            store.set_relay_listener(self)

    @property
    def publish_queue_size(self) -> int:
        return self._events.qsize()

    @property
    def thread(self) -> Optional[threading.Thread]:
        return self._thread

    def on_park(self, job: RelayPublishJob) -> None:
        if not self._publish_interactions or \
                not isinstance(job, RelayPublishJob):
            return
        try:
            self._events.put_nowait(("park", job))
        except queue.Full:
            self._audit_event("queue_full", "put_request", None)

    def on_remove(self, request_id: str, reason: str) -> None:
        if not self._publish_interactions or \
                not isinstance(request_id, str) or not isinstance(reason, str):
            return
        event = ("remove", (request_id, reason))
        try:
            self._events.put_nowait(event)
        except queue.Full:
            # MAX_PENDING is also eight, so a full queue can contain the
            # still-undrained park for the entry being removed.  Replace that
            # park in place rather than dropping the authoritative removal
            # and later publishing a request that is already gone locally.
            replaced = False
            with self._events.mutex:
                for index, queued in enumerate(self._events.queue):
                    queued_kind, queued_value = queued
                    queued_id = queued_value.request_id \
                        if queued_kind == "park" else queued_value[0]
                    if queued_id == request_id:
                        self._events.queue[index] = event
                        replaced = True
                        break
                if not replaced and \
                        self._events._qsize() < self._events.maxsize:
                    self._events._put(event)
                    self._events.unfinished_tasks += 1
                    self._events.not_empty.notify()
                    replaced = True
            if replaced:
                return
            self._audit_event("queue_full", "delete_request", None)

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        target = (self._run_interactions if self._publish_interactions
                  else self._run_status)
        name = ("vibepulse-interaction-relay" if self._publish_interactions
                else "vibepulse-agent-status-relay")
        self._thread = threading.Thread(
            target=target, name=name, daemon=True)
        self._thread.start()
        if self._publish_interactions and self._publish_agent_status:
            self._status_thread = threading.Thread(
                target=self._run_status,
                name="vibepulse-agent-status-relay", daemon=True)
            self._status_thread.start()

    def stop(self) -> None:
        if self._publish_interactions:
            self._store.set_relay_listener(None)
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, self._connect_timeout +
                                          self._read_timeout + 0.5))
        if self._status_thread is not None:
            self._status_thread.join(
                timeout=max(1.0, self._connect_timeout +
                            self._read_timeout + 0.5))

    def _run_interactions(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_interactions_once()
            except Exception:
                self._audit_event(
                    "cycle_failed", "interaction_worker", None)
            self._stop.wait(0.05)

    def _run_status(self) -> None:
        while not self._stop.is_set():
            try:
                self._run_status_once()
            except Exception:
                self._audit_event("cycle_failed", "status_worker", None)
            self._stop.wait(0.05)

    def _backoff(self, failures: int) -> float:
        base = min(MAX_BACKOFF_S,
                   MIN_BACKOFF_S * (2 ** max(0, failures - 1)))
        try:
            sample = float(self._jitter())
        except Exception:
            sample = 0.0
        if not math.isfinite(sample):
            sample = 0.0
        sample = max(0.0, min(1.0, sample))
        return min(MAX_BACKOFF_S, base * (1.0 + sample * 0.2))

    def _drain_events(self, now: float) -> None:
        while True:
            try:
                kind, value = self._events.get_nowait()
            except queue.Empty:
                return
            try:
                if kind == "park":
                    job = value
                    try:
                        envelope = encode_request(
                            self._keys, self._mailbox, job.request_id,
                            job.challenge, job.expires_at, job.view_bytes,
                            nonce=self._random_bytes(12),
                            padding=self._random_bytes,
                        )
                    except Exception:
                        self._audit_event(
                            "encode_failed", "put_request", None)
                        continue
                    self._deletes.pop(job.request_id, None)
                    self._publishes[job.request_id] = _PublishState(
                        job=job, envelope=envelope, next_attempt=now)
                elif kind == "remove":
                    request_id, _reason = value
                    self._publishes.pop(request_id, None)
                    # DELETE is intentionally unconditional.  A timed-out PUT
                    # may have committed at the Worker before its response was
                    # lost, and the Worker's DELETE is idempotent.
                    self._schedule_delete(request_id, now)
            finally:
                self._events.task_done()

    def _schedule_delete(self, request_id: str, now: float = 0.0) -> None:
        if request_id in self._deletes:
            return
        if len(self._deletes) >= DELETE_CAPACITY:
            # Worker TTL remains the final cleanup boundary.  Dropping the
            # oldest local retry keeps a prolonged outage from growing host
            # memory without bound.
            oldest = next(iter(self._deletes))
            del self._deletes[oldest]
            self._audit_event("backlog_full", "delete_request", None)
        self._deletes[request_id] = _DeleteState(
            request_id=request_id, next_attempt=now)

    def _cycle_time(self) -> Optional[float]:
        now = self._now()
        if not isinstance(now, (int, float)) or isinstance(now, bool) or \
                not math.isfinite(now):
            return None
        return float(now)

    def _run_interactions_once(self) -> None:
        now = self._cycle_time()
        if now is None:
            return
        self._drain_events(now)
        self._process_deletes(now)
        self._process_publishes(now)
        if self._publish_interactions:
            if any(state.published for state in self._publishes.values()) and \
                    now >= self._next_poll:
                self._poll(now)

    def _run_status_once(self) -> None:
        now = self._cycle_time()
        if now is not None:
            self._process_status(now)

    def run_once(self) -> None:
        """Run both enabled paths synchronously for deterministic tests."""
        if self._publish_interactions:
            self._run_interactions_once()
        if self._publish_agent_status:
            self._run_status_once()

    def _prepare_status(self, now: float) -> None:
        if self._status_state is not None or now < self._next_status:
            return
        try:
            wall = self._wall()
            if not isinstance(wall, (int, float)) or isinstance(wall, bool) or \
                    not math.isfinite(wall) or wall <= 0:
                raise ValueError("invalid wall clock")
            snapshot = self._status_source()
            if not isinstance(snapshot, dict):
                raise ValueError("invalid status snapshot")
            public = {key: value for key, value in snapshot.items()
                      if key != "pending"}
            status_bytes = _canonical_object(public)
            if not status_bytes or len(status_bytes) > MAX_STATUS_BYTES:
                raise ValueError("status snapshot is too large")
            milliseconds = int(wall * 1000)
            publication_id = max(self._last_publication_id + 1, milliseconds)
            expires_at = int(wall) + STATUS_EXPIRY_S
            if publication_id > 0xFFFFFFFFFFFFFFFF or \
                    expires_at > 0xFFFFFFFF:
                raise ValueError("status clock is out of range")
            envelope = encode_status(
                self._keys, self._mailbox, publication_id, expires_at,
                status_bytes, nonce=self._random_bytes(12),
                padding=self._random_bytes)
            self._last_publication_id = publication_id
            self._status_state = _StatusPublishState(
                envelope=envelope, publication_id=publication_id,
                next_attempt=now)
            self._status_prepare_failures = 0
        except Exception:
            self._status_prepare_failures += 1
            self._next_status = now + self._backoff(
                self._status_prepare_failures)
            self._audit_event("encode_failed", "put_status", None)

    def _process_status(self, now: float) -> None:
        self._prepare_status(now)
        state = self._status_state
        if state is None or now < state.next_attempt:
            return
        route = f"/v1/mailboxes/{quote(self._mailbox, safe='')}/status"
        try:
            response = self._request("PUT", route, state.envelope)
            _validate_response(response, json_body=False)
            if response.status != 201 or response.body:
                raise ValueError("relay status publish rejected")
            self._status_state = None
            self._next_status = now + STATUS_PUBLISH_INTERVAL_S
            self._audit_event("ok", "put_status", response.status)
        except Exception:
            state.failures += 1
            state.next_attempt = now + self._backoff(state.failures)
            self._audit_event("failed", "put_status", None)

    def _request(self, method: str, route: str,
                 body: bytes = b"") -> HttpResponse:
        headers = (
            ("Authorization", f"Bearer {self._mac_token}"),
            ("Accept", "application/json"),
        )
        if body:
            headers += (("Content-Type", "application/json"),)
        response = self._transport(
            method, self._origin + route, headers, body,
            self._connect_timeout, self._read_timeout)
        if not isinstance(response, HttpResponse):
            raise ValueError("invalid HTTP response")
        return response

    def _process_publishes(self, now: float) -> None:
        for request_id, state in list(self._publishes.items()):
            if state.published or now < state.next_attempt:
                continue
            route = (f"/v1/mailboxes/{quote(self._mailbox, safe='')}/"
                     f"requests/{quote(request_id, safe='')}")
            try:
                response = self._request("PUT", route, state.envelope)
                _validate_response(response, json_body=False)
                if response.status not in (200, 201) or response.body:
                    raise ValueError("relay publish rejected")
                state.published = True
                state.failures = 0
                self._next_poll = min(self._next_poll, now)
                self._audit_event("ok", "put_request", response.status)
            except Exception:
                state.failures += 1
                state.next_attempt = now + self._backoff(state.failures)
                self._audit_event("failed", "put_request", None)

    def _process_deletes(self, now: float) -> None:
        for request_id, state in list(self._deletes.items()):
            if now < state.next_attempt:
                continue
            route = (f"/v1/mailboxes/{quote(self._mailbox, safe='')}/"
                     f"requests/{quote(request_id, safe='')}")
            try:
                response = self._request("DELETE", route)
                _validate_response(response, json_body=False)
                if response.status != 204 or response.body:
                    raise ValueError("relay delete rejected")
                del self._deletes[request_id]
                self._audit_event("ok", "delete_request", response.status)
            except Exception:
                state.failures += 1
                state.next_attempt = now + self._backoff(state.failures)
                self._audit_event("failed", "delete_request", None)

    def _poll(self, now: float) -> None:
        route = (f"/v1/mailboxes/{quote(self._mailbox, safe='')}/verdicts")
        try:
            response = self._request("GET", route)
            if response.status == 204:
                _validate_response(response, json_body=False)
                if response.body:
                    raise ValueError("unexpected relay response body")
            elif response.status == 200:
                _validate_response(response, json_body=True)
                self._consume_verdict_response(response.body)
            else:
                raise ValueError("relay poll rejected")
            self._poll_failures = 0
            self._next_poll = now + POLL_INTERVAL_S
            self._audit_event("ok", "list_verdicts", response.status)
        except Exception:
            self._poll_failures += 1
            self._next_poll = now + self._backoff(self._poll_failures)
            self._audit_event("failed", "list_verdicts", None)

    def _consume_verdict_response(self, raw: bytes) -> None:
        value = _strict_json(raw)
        if not isinstance(value, dict) or set(value) != {"verdicts"} or \
                not isinstance(value["verdicts"], list) or \
                len(value["verdicts"]) != 1:
            raise ValueError("invalid verdict list")
        item = value["verdicts"][0]
        if not isinstance(item, dict) or set(item) != {
                "envelope", "requestId", "verdictAtMs"} or \
                not isinstance(item["envelope"], dict) or \
                not isinstance(item["requestId"], str) or \
                type(item["verdictAtMs"]) is not int or \
                not (0 <= item["verdictAtMs"] <= 0x1FFFFFFFFFFFFF):
            raise ValueError("invalid verdict item")
        request_id = item["requestId"]
        try:
            request_bytes = b64url_decode(request_id)
        except ValueError as exc:
            raise ValueError("invalid verdict request id") from exc
        if len(request_bytes) != 16:
            raise ValueError("invalid verdict request id")
        state = self._publishes.get(request_id)
        envelope = _canonical_object(item["envelope"])
        if state is None or not state.published:
            self._schedule_delete(request_id)
            return
        try:
            verdict = decode_verdict(
                self._keys, self._mailbox, request_id, envelope)
        except ValueError:
            self._schedule_delete(request_id)
            return
        result = RelayResolution(
            request_id=verdict.request_id,
            challenge=verdict.challenge,
            view_sha256=verdict.view_sha256,
            verdict=verdict.verdict,
            mac=verdict.mac,
        )
        accepted, _reason = self._store.resolve_relay(
            result, self._verify_resolution)
        if not accepted:
            self._schedule_delete(request_id)

    def _verify_resolution(self, job: RelayPublishJob,
                           result: RelayResolution) -> bool:
        try:
            request = RelayRequest(
                request_id=job.request_id,
                challenge=job.challenge,
                expires_at=job.expires_at,
                view_bytes=job.view_bytes,
                view_sha256=job.view_sha256,
            )
            verdict = RelayVerdict(
                request_id=result.request_id,
                challenge=result.challenge,
                view_sha256=result.view_sha256,
                verdict=result.verdict,
                mac=result.mac,
            )
            return verify_verdict_mac(
                self._keys, self._mailbox, request, verdict)
        except Exception:
            return False

    def _audit_event(self, event: str, route: str,
                     status: Optional[int]) -> None:
        if self._audit is None:
            return
        fields = {"origin": self._origin, "route": route}
        if status is not None:
            fields["status"] = status
        try:
            self._audit(event, fields)
        except Exception:
            pass
