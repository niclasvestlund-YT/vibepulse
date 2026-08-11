#!/usr/bin/env python3
"""Serve the local, exact-size VibePulse Studio without a build step."""

import argparse
import base64
import hashlib
import hmac
import io
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import sys
import threading
import uuid
import weakref
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, urlsplit

from PIL import Image, UnidentifiedImageError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.hardware_registry import load_registry
from tools.vibepulse_studio.design import (
    DesignError,
    generate_header,
    validate_design,
)


MAX_REQUEST_BYTES = 1024 * 1024
JOURNAL_MAX_BYTES = 3 * MAX_REQUEST_BYTES
REQUEST_TIMEOUT_SECONDS = 10
MAX_WORKERS = 16
WORKER_ACQUIRE_TIMEOUT_SECONDS = 0.25
MUTATION_TOKEN_HEADER = "X-VibePulse-Studio-Token"
CANONICAL_LENGTH = re.compile(r"(?:0|[1-9][0-9]*)\Z")
APPROVED_EXPORT_STATES = frozenset({
    "claude-hero",
    "codex-hero",
    "claude-details",
    "overview",
    "claude-hero-stale",
    "codex-hero-stale",
    "claude-hero-missing",
    "codex-hero-missing",
})
STATIC_ASSETS = {
    "/": (("tools", "vibepulse_studio", "web", "index.html"),
          "text/html; charset=utf-8"),
    "/studio.css": (
        ("tools", "vibepulse_studio", "web", "studio.css"),
        "text/css; charset=utf-8",
    ),
    "/studio.js": (
        ("tools", "vibepulse_studio", "web", "studio.js"),
        "text/javascript; charset=utf-8",
    ),
}
FONT_ASSETS = {
    "/fonts/IBMPlexSans-Bold.woff2": (
        ("tools", "vibepulse_studio", "web", "fonts",
         "IBMPlexSans-Bold.woff2"),
        "font/woff2",
    ),
    "/fonts/IBMPlexSans-SemiBold.woff2": (
        ("tools", "vibepulse_studio", "web", "fonts",
         "IBMPlexSans-SemiBold.woff2"),
        "font/woff2",
    ),
    "/fonts/OFL.txt": (
        ("tools", "vibepulse_studio", "web", "fonts", "OFL.txt"),
        "text/plain; charset=utf-8",
    ),
}
HEADER_RELATIVE = (
    "components", "app_tokens", "vibepulse_layout.generated.h",
)
JOURNAL_RELATIVE = (
    "design", "vibepulse", ".studio-transaction.json",
)
EXPORT_DIRECTORY = ("design", "vibepulse", "exports")


class UnsafeRepositoryPath(OSError):
    """A repository path tried to cross or follow an unsafe object."""


class DesignPairError(DesignError):
    """The persisted JSON and generated C header do not match."""


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            + "\n").encode("utf-8")


def _response(status_code, body, content_type="application/json",
              extra_headers=None):
    if not isinstance(body, bytes):
        body = bytes(body)
    headers = {
        "Content-Type": content_type,
        "Content-Length": str(len(body)),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    if extra_headers:
        headers.update(extra_headers)
    return status_code, headers, body


def _json_response(status_code, value, extra_headers=None):
    return _response(
        status_code,
        _json_bytes(value),
        "application/json",
        extra_headers,
    )


def _error(status_code, message, extra_headers=None):
    return _json_response(status_code, {"error": message}, extra_headers)


def _is_integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _capability_enabled(capability):
    states = capability.get("states") if isinstance(capability, dict) else None
    return isinstance(states, dict) and states.get("firmware_enabled") == "yes"


def _safe_hardware(registry):
    display = registry.capabilities.get("display.amoled")
    touch = registry.capabilities.get("touch.controller")
    if not isinstance(display, dict):
        raise DesignError("display.amoled is missing from the hardware registry")
    width = display.get("width")
    height = display.get("height")
    if (not _is_integer(width) or not _is_integer(height)
            or width <= 0 or height <= 0):
        raise DesignError("display dimensions must be positive integers")
    if not isinstance(touch, dict):
        raise DesignError("touch.controller is missing from the hardware registry")
    states = display.get("states")
    return {
        "display": {
            "width": width,
            "height": height,
            "colorFormat": display.get("color_format"),
            "bus": display.get("bus"),
        },
        "touch": {
            "name": touch.get("name"),
            "firmwareEnabled": _capability_enabled(touch),
            "singlePoint": True,
        },
        "amoled": {
            "name": display.get("name"),
            "blackFirst": True,
            "unitVerified": (
                isinstance(states, dict)
                and states.get("unit_verified") == "yes"
            ),
        },
    }


def _content_type(headers):
    if not headers:
        return None
    value = headers.get("content-type")
    if value is None:
        value = headers.get("Content-Type")
    if not isinstance(value, str):
        return None
    return value.split(";", 1)[0].strip().lower()


class RepositoryStore:
    """Descriptor-anchored access to a trusted repository directory."""

    def __init__(self, repository_root):
        self.root_path = Path(os.path.abspath(os.fspath(repository_root)))
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            self.root_fd = os.open(self.root_path, flags)
        except OSError as error:
            raise UnsafeRepositoryPath(
                "repository root must be a real directory, not a symlink"
            ) from error
        if not stat.S_ISDIR(os.fstat(self.root_fd).st_mode):
            os.close(self.root_fd)
            raise UnsafeRepositoryPath("repository root is not a directory")
        self._finalizer = weakref.finalize(self, os.close, self.root_fd)

    def relative_from_supplied(self, path):
        supplied = Path(os.path.abspath(os.fspath(path)))
        try:
            relative = supplied.relative_to(self.root_path)
        except ValueError as error:
            raise ValueError("design path must stay inside repository") from error
        if not relative.parts:
            raise ValueError("design path must name a repository file")
        return relative.parts

    def display_path(self, parts):
        return self.root_path.joinpath(*parts)

    def _directory_fd(self, parts, create=False, mode=0o755):
        current = os.dup(self.root_fd)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            for part in parts:
                if part in ("", ".", "..") or "/" in part:
                    raise UnsafeRepositoryPath("invalid repository path part")
                try:
                    following = os.open(part, flags, dir_fd=current)
                except FileNotFoundError:
                    if not create:
                        raise
                    os.mkdir(part, mode, dir_fd=current)
                    following = os.open(part, flags, dir_fd=current)
                except OSError as error:
                    raise UnsafeRepositoryPath(
                        "repository path component is not a real directory "
                        "or is a symlink"
                    ) from error
                os.close(current)
                current = following
            return current
        except BaseException:
            os.close(current)
            raise

    def _parent_fd(self, parts, create=False):
        if not parts or parts[-1] in ("", ".", ".."):
            raise UnsafeRepositoryPath("invalid repository file path")
        return self._directory_fd(parts[:-1], create=create)

    def _open_regular(self, parent, name):
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_CLOEXEC", 0)
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
        except OSError as error:
            if isinstance(error, FileNotFoundError):
                raise
            raise UnsafeRepositoryPath(
                "repository file is not a regular file or is a symlink"
            ) from error
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise UnsafeRepositoryPath("repository target is not a regular file")
        return descriptor

    def read(self, parts, *, missing_ok=False, limit=MAX_REQUEST_BYTES):
        try:
            parent = self._parent_fd(parts)
        except FileNotFoundError:
            if missing_ok:
                return None
            raise
        try:
            try:
                descriptor = self._open_regular(parent, parts[-1])
            except FileNotFoundError:
                if missing_ok:
                    return None
                raise
            with os.fdopen(descriptor, "rb") as source:
                data = source.read(limit + 1)
            if len(data) > limit:
                raise OSError("repository file exceeds safe size")
            return data
        finally:
            os.close(parent)

    def _target_is_safe(self, parent, name):
        try:
            descriptor = self._open_regular(parent, name)
        except FileNotFoundError:
            return
        else:
            os.close(descriptor)

    def atomic_write(self, parts, content, mode=0o644):
        parent = self._parent_fd(parts, create=True)
        temporary = f".{parts[-1]}.{uuid.uuid4().hex}.tmp"
        descriptor = None
        try:
            self._target_is_safe(parent, parts[-1])
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            descriptor = os.open(
                temporary, flags, 0o600, dir_fd=parent
            )
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                os.fchmod(output.fileno(), mode)
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            os.replace(
                temporary,
                parts[-1],
                src_dir_fd=parent,
                dst_dir_fd=parent,
            )
            os.fsync(parent)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=parent)
            except FileNotFoundError:
                pass
            os.close(parent)

    def remove(self, parts, *, missing_ok=False):
        try:
            parent = self._parent_fd(parts)
        except FileNotFoundError:
            if missing_ok:
                return
            raise
        try:
            try:
                self._target_is_safe(parent, parts[-1])
            except FileNotFoundError:
                if missing_ok:
                    return
                raise
            os.unlink(parts[-1], dir_fd=parent)
            os.fsync(parent)
        finally:
            os.close(parent)


def _decode_design(content, display):
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DesignPairError("stored design/header pair is invalid") from error
    return validate_design(value, display)


def _canonical_design(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(
        "utf-8"
    )


def _digest(content):
    return hashlib.sha256(content).hexdigest() if content is not None else None


def _pair_digest(design_content, header_content):
    marker = f"{_digest(design_content)}:{_digest(header_content)}"
    return hashlib.sha256(marker.encode("ascii")).hexdigest()


def _journal_entry(content):
    if content is None:
        return None
    return {
        "sha256": _digest(content),
        "base64": base64.b64encode(content).decode("ascii"),
    }


def _decode_entry(value):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {"sha256", "base64"}:
        raise DesignPairError("transaction journal entry is invalid")
    encoded = value.get("base64")
    expected_digest = value.get("sha256")
    if not isinstance(encoded, str) or not isinstance(expected_digest, str):
        raise DesignPairError("transaction journal entry types are invalid")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (TypeError, ValueError) as error:
        raise DesignPairError("transaction journal encoding is invalid") from error
    if not hmac.compare_digest(_digest(content), expected_digest):
        raise DesignPairError("transaction journal digest is invalid")
    return content


def _journal_document(old_pair, new_pair):
    return {
        "schemaVersion": 1,
        "old": {
            "design": _journal_entry(old_pair[0]),
            "header": _journal_entry(old_pair[1]),
            "pairDigest": _pair_digest(*old_pair),
        },
        "new": {
            "design": _journal_entry(new_pair[0]),
            "header": _journal_entry(new_pair[1]),
            "pairDigest": _pair_digest(*new_pair),
        },
    }


def _decode_journal(content, display):
    try:
        value = json.loads(content.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DesignPairError("transaction journal is invalid") from error
    if (not isinstance(value, dict)
            or set(value) != {"schemaVersion", "old", "new"}
            or not _is_integer(value.get("schemaVersion"))
            or value["schemaVersion"] != 1):
        raise DesignPairError("transaction journal shape is invalid")
    pairs = []
    for label in ("old", "new"):
        record = value[label]
        if (not isinstance(record, dict)
                or set(record) != {"design", "header", "pairDigest"}):
            raise DesignPairError("transaction journal pair is invalid")
        pair = (_decode_entry(record["design"]),
                _decode_entry(record["header"]))
        expected_pair_digest = record.get("pairDigest")
        if (not isinstance(expected_pair_digest, str)
                or not hmac.compare_digest(
                    _pair_digest(*pair), expected_pair_digest)):
            raise DesignPairError("transaction pair digest is invalid")
        if pair[0] is None:
            raise DesignPairError("transaction design is missing")
        design = _decode_design(pair[0], display)
        expected_header = generate_header(design, display).encode("utf-8")
        if pair[1] is not None and not hmac.compare_digest(
                pair[1], expected_header):
            raise DesignPairError("transaction design/header pair mismatches")
        if label == "new" and pair[1] is None:
            raise DesignPairError("new transaction header is missing")
        pairs.append(pair)
    return tuple(pairs)


class StudioApplication:
    """Pure request boundary shared by tests and the HTTP adapter."""

    def __init__(self, repository_root, design_path, spec_dir):
        self.store = RepositoryStore(repository_root)
        self.repository_root = self.store.root_path
        self.design_relative = self.store.relative_from_supplied(design_path)
        self.design_path = self.store.display_path(self.design_relative)
        self.header_path = self.store.display_path(HEADER_RELATIVE)
        self.export_dir = self.store.display_path(EXPORT_DIRECTORY)
        registry = load_registry(spec_dir)
        self.hardware = _safe_hardware(registry)
        display = self.hardware["display"]
        self.display = {"width": display["width"], "height": display["height"]}
        self._mutation_lock = threading.RLock()
        with self._mutation_lock:
            self._recover_transaction_locked()
            design_content = self.store.read(self.design_relative)
            design = _decode_design(design_content, self.display)
            header_content = self.store.read(HEADER_RELATIVE, missing_ok=True)
            expected_header = generate_header(design, self.display).encode("utf-8")
            if header_content is None:
                self._transaction_locked(
                    (design_content, None),
                    (design_content, expected_header),
                )
            self._verified_pair_locked()

    def _route(self, method, path):
        if path == "/api/design":
            allowed = ("GET", "PUT")
            action = "design"
        elif path == "/api/hardware":
            allowed = ("GET",)
            action = "hardware"
        elif path in STATIC_ASSETS:
            allowed = ("GET",)
            action = "static"
        elif path in FONT_ASSETS:
            allowed = ("GET",)
            action = "font"
        elif path.startswith("/api/export/"):
            state_name = path.removeprefix("/api/export/")
            if state_name not in APPROVED_EXPORT_STATES:
                return None, None, None
            allowed = ("POST",)
            action = "export"
        else:
            return None, None, None
        if method not in allowed:
            return "method", allowed, None
        return action, allowed, (
            path.removeprefix("/api/export/") if action == "export" else None
        )

    def handle(self, method, path, body, headers=None):
        """Return ``(status, headers, body)`` without opening a socket."""
        if not isinstance(method, str) or not isinstance(path, str):
            return _error(400, "invalid request")
        method = method.upper()
        action, allowed, state_name = self._route(method, path)
        if action is None:
            return _error(404, "not found")
        if action == "method":
            return _error(
                405,
                "method not allowed",
                {"Allow": ", ".join(allowed)},
            )
        if not isinstance(body, (bytes, bytearray, memoryview)):
            return _error(400, "request body must be bytes")
        body = bytes(body)
        if len(body) > MAX_REQUEST_BYTES:
            return _error(413, "request body exceeds 1 MiB")

        if action == "design" and method == "GET":
            return self._get_design()
        if action == "design":
            return self._put_design(body, headers)
        if action == "hardware":
            return _json_response(200, self.hardware)
        if action == "export":
            return self._export(state_name, body, headers)
        if action == "static":
            parts, mime_type = STATIC_ASSETS[path]
            return self._static(parts, mime_type)
        parts, mime_type = FONT_ASSETS[path]
        return self._static(parts, mime_type)

    def _read_pair_locked(self):
        return (
            self.store.read(self.design_relative),
            self.store.read(HEADER_RELATIVE, missing_ok=True),
        )

    def _verified_pair_locked(self):
        design_content, header_content = self._read_pair_locked()
        design = _decode_design(design_content, self.display)
        expected = generate_header(design, self.display).encode("utf-8")
        if header_content is None or not hmac.compare_digest(
                header_content, expected):
            raise DesignPairError("stored design/header pair does not match")
        return design, design_content, header_content

    def _restore_pair_locked(self, pair):
        errors = []
        for parts, content in (
            (self.design_relative, pair[0]),
            (HEADER_RELATIVE, pair[1]),
        ):
            try:
                if content is None:
                    self.store.remove(parts, missing_ok=True)
                else:
                    self.store.atomic_write(parts, content)
            except Exception as error:
                errors.append(error)
        if errors:
            raise OSError("could not restore complete design/header pair") \
                from errors[0]

    def _transaction_locked(self, old_pair, new_pair):
        journal = _json_bytes(_journal_document(old_pair, new_pair))
        try:
            self.store.atomic_write(JOURNAL_RELATIVE, journal, mode=0o600)
            self.store.atomic_write(self.design_relative, new_pair[0])
            self.store.atomic_write(HEADER_RELATIVE, new_pair[1])
            actual = self._read_pair_locked()
            if actual != new_pair:
                raise DesignPairError("saved design/header pair does not match")
            self._verified_pair_locked()
            self.store.remove(JOURNAL_RELATIVE)
        except Exception as transaction_error:
            try:
                self._restore_pair_locked(old_pair)
                self.store.remove(JOURNAL_RELATIVE, missing_ok=True)
            except Exception as rollback_error:
                raise OSError(
                    "design/header transaction failed; recovery journal retained"
                ) from rollback_error
            raise transaction_error

    def _recover_transaction_locked(self):
        content = self.store.read(
            JOURNAL_RELATIVE,
            missing_ok=True,
            limit=JOURNAL_MAX_BYTES,
        )
        if content is None:
            return
        old_pair, new_pair = _decode_journal(content, self.display)
        actual_pair = self._read_pair_locked()
        if actual_pair == new_pair:
            self._verified_pair_locked()
            self.store.remove(JOURNAL_RELATIVE)
            return
        if actual_pair == old_pair:
            self.store.remove(JOURNAL_RELATIVE)
            return
        self._restore_pair_locked(old_pair)
        restored = self._read_pair_locked()
        if restored != old_pair:
            raise DesignPairError("transaction recovery could not restore pair")
        self.store.remove(JOURNAL_RELATIVE)

    def _get_design(self):
        try:
            with self._mutation_lock:
                self._recover_transaction_locked()
                design, _, header = self._verified_pair_locked()
            return _json_response(200, design, {
                "X-VibePulse-Header-Digest": _digest(header),
            })
        except (DesignError, OSError, UnicodeError):
            return _error(500, "stored design/header pair is unavailable")

    def _put_design(self, body, headers):
        if _content_type(headers) != "application/json":
            return _error(415, "Content-Type must be application/json")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error(400, "request body must be valid UTF-8 JSON")
        try:
            validate_design(value, self.display)
            design_content = _canonical_design(value)
            header_content = generate_header(value, self.display).encode("utf-8")
        except DesignError as error:
            return _error(422, str(error))
        try:
            with self._mutation_lock:
                self._recover_transaction_locked()
                _, old_design, old_header = self._verified_pair_locked()
                self._transaction_locked(
                    (old_design, old_header),
                    (design_content, header_content),
                )
        except (DesignError, OSError, UnicodeError):
            return _error(500, "could not save design/header pair")
        return _json_response(200, {
            "design": value,
            "headerDigest": _digest(header_content),
        })

    def _export(self, state_name, body, headers):
        if _content_type(headers) != "image/png":
            return _error(415, "Content-Type must be image/png")
        try:
            with Image.open(io.BytesIO(body)) as image:
                if image.format != "PNG":
                    raise ValueError("image is not PNG")
                if image.size != (
                    self.display["width"], self.display["height"]
                ):
                    raise ValueError("PNG dimensions do not match the display")
                image.verify()
            with Image.open(io.BytesIO(body)) as image:
                image.load()
                if image.format != "PNG":
                    raise ValueError("image is not PNG")
        except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
            return _error(422, "export must be a valid exact-size PNG")
        relative = EXPORT_DIRECTORY + (f"{state_name}.png",)
        try:
            with self._mutation_lock:
                self._recover_transaction_locked()
                self._verified_pair_locked()
                self.store.atomic_write(relative, body)
        except UnsafeRepositoryPath as error:
            return _error(422, str(error))
        except (DesignError, OSError):
            return _error(500, "could not save PNG export")
        return _json_response(201, {
            "state": state_name,
            "path": "/".join(relative),
            "width": self.display["width"],
            "height": self.display["height"],
            "digest": _digest(body),
        })

    def _static(self, parts, mime_type):
        try:
            body = self.store.read(parts, missing_ok=True)
        except OSError:
            body = None
        if body is None:
            return _error(404, "not found")
        return _response(200, body, mime_type)


def _resolved_addresses(host):
    try:
        answers = socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM)
    except socket.gaierror as error:
        raise ValueError(f"cannot resolve bind host: {host}") from error
    addresses = set()
    for answer in answers:
        address = answer[4][0].split("%", 1)[0]
        try:
            addresses.add(ipaddress.ip_address(address))
        except ValueError as error:
            raise ValueError(f"invalid bind address: {address}") from error
    if not addresses:
        raise ValueError(f"bind host has no addresses: {host}")
    return addresses


def validate_bind(host, allow_lan):
    """Require every DNS answer to be loopback unless LAN is deliberate."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be nonempty")
    host = host.strip()
    addresses = _resolved_addresses(host)
    if not allow_lan and any(not address.is_loopback for address in addresses):
        raise ValueError("non-loopback bind requires --allow-lan")
    return host


def _parse_authority(value):
    if not isinstance(value, str) or not value or any(
            character.isspace() for character in value):
        return None
    try:
        parsed = urlsplit(f"//{value}")
        port = parsed.port
    except ValueError:
        return None
    if (parsed.username is not None or parsed.password is not None
            or not parsed.hostname or parsed.path
            or parsed.query or parsed.fragment or port is None):
        return None
    return parsed.hostname.rstrip(".").lower(), port


def _origin_matches(value, authority):
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    return (
        parsed.scheme == "http"
        and parsed.username is None
        and parsed.password is None
        and parsed.hostname is not None
        and parsed.hostname.rstrip(".").lower() == authority[0]
        and port == authority[1]
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment
    )


class StudioRequestHandler(BaseHTTPRequestHandler):
    """HTTP framing and browser-origin boundary for StudioApplication."""

    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.request.settimeout(self.server.request_timeout)

    def _framing(self):
        transfer = self.headers.get_all("Transfer-Encoding") or []
        if transfer:
            return None, _error(400, "Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length") or []
        if not lengths:
            if self.command in ("PUT", "POST"):
                return None, _error(411, "Content-Length is required")
            return 0, None
        if len(lengths) != 1 or CANONICAL_LENGTH.fullmatch(lengths[0]) is None:
            return None, _error(400, "invalid Content-Length")
        content_length = int(lengths[0])
        if content_length > MAX_REQUEST_BYTES:
            return None, _error(413, "request body exceeds 1 MiB")
        return content_length, None

    def _browser_boundary(self):
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1:
            return None, _error(421, "Host does not match Studio")
        authority = _parse_authority(hosts[0])
        if (authority is None
                or authority[1] != self.server.server_address[1]
                or authority[0] not in self.server.allowed_hosts):
            return None, _error(421, "Host does not match Studio")
        if self.command in ("PUT", "POST"):
            origins = self.headers.get_all("Origin") or []
            if len(origins) > 1 or (
                    origins and not _origin_matches(origins[0], authority)):
                return None, _error(403, "Origin does not match Studio")
            if self.server.allow_lan:
                tokens = self.headers.get_all(MUTATION_TOKEN_HEADER) or []
                if (len(tokens) != 1 or not hmac.compare_digest(
                        tokens[0], self.server.mutation_token)):
                    return None, _error(403, "LAN mutation token is required")
        return authority, None

    def _send(self, response, force_close=False):
        status_code, headers, response_body = response
        if force_close:
            self.close_connection = True
            headers = dict(headers)
            headers["Connection"] = "close"
        self.send_response(status_code)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(response_body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _dispatch(self):
        content_length, framing_error = self._framing()
        if framing_error is not None:
            self._send(framing_error, force_close=True)
            return
        _, browser_error = self._browser_boundary()
        if browser_error is not None:
            self._send(browser_error, force_close=True)
            return
        try:
            body = self.rfile.read(content_length)
        except (OSError, socket.timeout):
            self._send(
                _error(400, "could not read request body"),
                force_close=True,
            )
            return
        if len(body) != content_length:
            self._send(_error(400, "incomplete request body"), force_close=True)
            return
        headers = {
            name.lower(): value for name, value in self.headers.items()
        }
        response = self.server.application.handle(
            self.command, self.path, body, headers
        )
        self._send(response)

    do_GET = _dispatch
    do_PUT = _dispatch
    do_POST = _dispatch
    do_HEAD = _dispatch
    do_DELETE = _dispatch
    do_PATCH = _dispatch
    do_OPTIONS = _dispatch

    def log_message(self, _format, *args):
        del args

    def log_request(self, code="-", size="-"):
        del size
        print(f"VibePulse Studio response {code}", file=sys.stderr)


class BoundedThreadingHTTPServer(ThreadingHTTPServer):
    """ThreadingHTTPServer with a hard cap on active request threads."""

    daemon_threads = True

    def __init__(self, server_address, handler, *, max_workers=MAX_WORKERS,
                 worker_acquire_timeout=WORKER_ACQUIRE_TIMEOUT_SECONDS):
        if not _is_integer(max_workers) or max_workers < 1:
            raise ValueError("max_workers must be a positive integer")
        self.max_workers = max_workers
        self.worker_acquire_timeout = worker_acquire_timeout
        self._workers = threading.BoundedSemaphore(max_workers)
        super().__init__(server_address, handler)

    def process_request(self, request, client_address):
        if not self._workers.acquire(timeout=self.worker_acquire_timeout):
            self._reject_overload(request)
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self._workers.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self._workers.release()

    def _reject_overload(self, request):
        body = _json_bytes({"error": "Studio is busy"})
        response = (
            f"HTTP/1.1 503 {HTTPStatus.SERVICE_UNAVAILABLE.phrase}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Cache-Control: no-store\r\n"
            "Connection: close\r\n\r\n"
        ).encode("ascii") + body
        try:
            request.settimeout(self.worker_acquire_timeout)
            request.recv(8192)
            request.sendall(response)
        except OSError:
            pass


def _server_class(address):
    if address.version == 6:
        class IPv6BoundedThreadingHTTPServer(BoundedThreadingHTTPServer):
            address_family = socket.AF_INET6

        return IPv6BoundedThreadingHTTPServer
    return BoundedThreadingHTTPServer


def create_server(application, host, port, *, allow_lan=False,
                  mutation_token=None,
                  request_timeout=REQUEST_TIMEOUT_SECONDS,
                  max_workers=MAX_WORKERS,
                  worker_acquire_timeout=WORKER_ACQUIRE_TIMEOUT_SECONDS):
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be nonempty")
    host = host.strip()
    addresses = _resolved_addresses(host)
    if not allow_lan and any(not address.is_loopback for address in addresses):
        raise ValueError("non-loopback bind requires --allow-lan")
    bind_address = sorted(
        addresses, key=lambda address: (address.version != 4, int(address))
    )[0]
    server_type = _server_class(bind_address)
    server = server_type(
        (str(bind_address), port),
        StudioRequestHandler,
        max_workers=max_workers,
        worker_acquire_timeout=worker_acquire_timeout,
    )
    allowed_hosts = {str(address).lower() for address in addresses}
    allowed_hosts.add(host.rstrip(".").lower())
    if any(address.is_unspecified for address in addresses):
        allowed_hosts.update({"localhost", "127.0.0.1", "::1"})
    server.application = application
    server.allowed_hosts = frozenset(allowed_hosts)
    server.allow_lan = bool(allow_lan)
    server.mutation_token = (
        mutation_token or secrets.token_urlsafe(32) if allow_lan else None
    )
    server.request_timeout = request_timeout
    return server


def safe_server_url(server, configured_host, mutation_token=None):
    host = configured_host
    if host == "0.0.0.0":
        host = "127.0.0.1"
    elif host == "::":
        host = "::1"
    display_host = f"[{host}]" if ":" in host else host
    url = f"http://{display_host}:{server.server_address[1]}/"
    if mutation_token:
        url += f"#mutation-token={quote(mutation_token, safe='')}"
    return url


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=64942)
    parser.add_argument("--allow-lan", action="store_true")
    opening = parser.add_mutually_exclusive_group()
    opening.add_argument("--open", dest="open_browser", action="store_true")
    opening.add_argument("--no-open", dest="open_browser", action="store_false")
    parser.set_defaults(open_browser=False)
    arguments = parser.parse_args(argv)
    if not 1 <= arguments.port <= 65535:
        parser.error("--port must be between 1 and 65535")

    design_path = REPOSITORY_ROOT / "design/vibepulse/studio-design.json"
    try:
        application = StudioApplication(
            REPOSITORY_ROOT,
            design_path,
            REPOSITORY_ROOT / "spec",
        )
        server = create_server(
            application,
            arguments.host,
            arguments.port,
            allow_lan=arguments.allow_lan,
        )
    except (DesignError, OSError, ValueError) as error:
        parser.error(str(error))
    url = safe_server_url(
        server, arguments.host, server.mutation_token
    )
    print(f"VibePulse Studio: {url}")
    if arguments.open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
