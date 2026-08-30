#!/usr/bin/env python3
"""Small fail-closed JSON client for explicit HTTP loopback URLs."""

from __future__ import annotations

import http.client
import ipaddress
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


MAX_BODY_BYTES = 4096
CONNECT_TIMEOUT_SECONDS = 0.75
READ_TIMEOUT_SECONDS = 125.0
_IPV4_TEXT = re.compile(r"[0-9]+(?:\.[0-9]+){3}\Z")
_JSON_CONTENT_TYPE = re.compile(
    r'[ \t]*application/json[ \t]*'
    r'(?:;[ \t]*charset[ \t]*=[ \t]*(?:"(?:utf-8|ascii)"|utf-8|ascii)'
    r'[ \t]*)?\Z', re.IGNORECASE | re.ASCII)


def _reject_json_constant(value):
    raise ValueError(f"non-standard JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object field")
        result[key] = value
    return result


def strict_json_loads(text):
    """Decode RFC JSON while rejecting duplicate fields and NaN values."""
    return json.loads(text, parse_constant=_reject_json_constant,
                      object_pairs_hook=_unique_object)


def is_loopback_http_url(url: object) -> bool:
    """Accept only canonical, explicit HTTP loopback URLs with a valid port."""
    if not isinstance(url, str) or not url or any(ord(char) < 32 for char in url):
        return False
    try:
        parsed = urllib.parse.urlsplit(url)
        port = parsed.port
        hostname = parsed.hostname
    except (TypeError, ValueError):
        return False
    if parsed.scheme != "http" or not parsed.netloc or not hostname:
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    if parsed.fragment or parsed.query or port is None or not 1 <= port <= 65535:
        return False
    if not parsed.path.startswith("/"):
        return False
    host = hostname.casefold()
    if host in {"localhost", "localhost."}:
        return True
    if host == "::1":
        return True
    if not _IPV4_TEXT.fullmatch(host):
        return False
    parts = host.split(".")
    if any(str(int(part)) != part for part in parts):
        return False
    try:
        address = ipaddress.IPv4Address(host)
    except ipaddress.AddressValueError:
        return False
    return address.is_loopback


def _has_json_content_type(headers) -> bool:
    values = headers.get_all("Content-Type", [])
    if len(values) != 1:
        return False
    value = values[0]
    try:
        value.encode("ascii", errors="strict")
    except (AttributeError, UnicodeEncodeError):
        return False
    return _JSON_CONTENT_TYPE.fullmatch(value) is not None


class _ResponseDeadline:
    """Close this request's active connection at one absolute deadline."""
    def __init__(self, seconds: float):
        self._seconds = seconds
        self._lock = threading.Lock()
        self._socket = None
        self._timer = None
        self._deadline = None
        self._expired = False
        self._finished = False

    @staticmethod
    def _close_socket(sock):
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def attach(self, sock):
        with self._lock:
            if self._finished:
                close_now = True
            else:
                self._socket = sock
                close_now = self._expired
                if self._timer is None:
                    self._deadline = time.monotonic() + self._seconds
                    remaining = max(0.0, self._deadline - time.monotonic())
                    self._timer = threading.Timer(remaining, self._expire)
                    self._timer.daemon = True
                    self._timer.start()
        if close_now:
            self._close_socket(sock)

    def _expire(self):
        with self._lock:
            if self._finished:
                return
            self._expired = True
            sock = self._socket
        if sock is not None:
            self._close_socket(sock)

    def finish(self):
        with self._lock:
            self._finished = True
            self._socket = None
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()


class _SplitTimeoutHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, read_timeout: float, deadline, **kwargs):
        self._read_timeout = read_timeout
        self._deadline = deadline
        super().__init__(*args, **kwargs)

    def connect(self):
        super().connect()
        if self.sock is not None:
            self.sock.settimeout(self._read_timeout)
            self._deadline.attach(self.sock)


class _SplitTimeoutHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, read_timeout: float, deadline):
        super().__init__()
        self._read_timeout = read_timeout
        self._deadline = deadline

    def http_open(self, request):
        def connection(host, **kwargs):
            return _SplitTimeoutHTTPConnection(
                host, read_timeout=self._read_timeout,
                deadline=self._deadline, **kwargs)

        return self.do_open(connection, request)


class _LoopbackRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not is_loopback_http_url(newurl):
            raise urllib.error.HTTPError(
                request.full_url, code, "redirect left loopback", headers, fp)
        return super().redirect_request(request, fp, code, msg, headers, newurl)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def get_json(url: str, *,
             connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
             read_timeout: float = CONNECT_TIMEOUT_SECONDS):
    """GET a bounded loopback JSON object without following redirects.

    This is intentionally short-lived for startup diagnostics. Interactive
    requests use :func:`post_json` and its much longer response deadline.
    """
    if not is_loopback_http_url(url):
        return None
    if not isinstance(connect_timeout, (int, float)) or connect_timeout <= 0:
        return None
    if not isinstance(read_timeout, (int, float)) or read_timeout <= 0:
        return None
    deadline = _ResponseDeadline(float(read_timeout))
    try:
        request = urllib.request.Request(
            url, method="GET", headers={"Accept": "application/json"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _SplitTimeoutHTTPHandler(float(read_timeout), deadline),
            _NoRedirectHandler(),
        )
        with opener.open(request, timeout=float(connect_timeout)) as response:
            if response.status != 200:
                return None
            if not _has_json_content_type(response.headers):
                return None
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_BODY_BYTES:
                return None
            received = response.read(MAX_BODY_BYTES + 1)
        if len(received) > MAX_BODY_BYTES:
            return None
        decoded = received.decode("utf-8", errors="strict")
        result = strict_json_loads(decoded)
        return result if isinstance(result, dict) else None
    except Exception:
        return None
    finally:
        deadline.finish()


def post_json(url: str, value: object, *,
              connect_timeout: float = CONNECT_TIMEOUT_SECONDS,
              read_timeout: float = READ_TIMEOUT_SECONDS):
    """POST a bounded object and return a bounded JSON object, or ``None``."""
    if not is_loopback_http_url(url) or not isinstance(value, dict):
        return None
    if not isinstance(connect_timeout, (int, float)) or connect_timeout <= 0:
        return None
    if not isinstance(read_timeout, (int, float)) or read_timeout <= 0:
        return None
    deadline = _ResponseDeadline(float(read_timeout))
    try:
        encoded = json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
        if len(encoded) > MAX_BODY_BYTES:
            return None
        request = urllib.request.Request(
            url, data=encoded, method="POST",
            headers={"Content-Type": "application/json"})
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            _SplitTimeoutHTTPHandler(float(read_timeout), deadline),
            _LoopbackRedirectHandler(),
        )
        with opener.open(request, timeout=float(connect_timeout)) as response:
            if response.status != 200:
                return None
            if not _has_json_content_type(response.headers):
                return None
            length = response.headers.get("Content-Length")
            if length is not None and int(length) > MAX_BODY_BYTES:
                return None
            received = response.read(MAX_BODY_BYTES + 1)
        if len(received) > MAX_BODY_BYTES:
            return None
        decoded = received.decode("utf-8", errors="strict")
        result = strict_json_loads(decoded)
        return result if isinstance(result, dict) else None
    except Exception:
        return None
    finally:
        deadline.finish()
