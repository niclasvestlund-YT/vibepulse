#!/usr/bin/env python3
"""Serve the local, exact-size VibePulse Studio without a build step."""

import argparse
import hashlib
import io
import ipaddress
import json
import os
import socket
import stat
import sys
import tempfile
import threading
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from PIL import Image, UnidentifiedImageError

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from tools.hardware_registry import load_registry
from tools.vibepulse_studio.design import (
    DesignError,
    generate_header,
    load_design,
    validate_design,
)


MAX_REQUEST_BYTES = 1024 * 1024
REQUEST_TIMEOUT_SECONDS = 10
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
    "/": ("index.html", "text/html; charset=utf-8"),
    "/studio.css": ("studio.css", "text/css; charset=utf-8"),
    "/studio.js": ("studio.js", "text/javascript; charset=utf-8"),
}
FONT_ASSETS = {
    "/fonts/IBMPlexSans-Bold.woff2": (
        "IBMPlexSans-Bold.woff2", "font/woff2"
    ),
    "/fonts/IBMPlexSans-SemiBold.woff2": (
        "IBMPlexSans-SemiBold.woff2", "font/woff2"
    ),
    "/fonts/OFL.txt": ("OFL.txt", "text/plain; charset=utf-8"),
}


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
    display_facts = {
        "width": width,
        "height": height,
        "colorFormat": display.get("color_format"),
        "bus": display.get("bus"),
    }
    touch_facts = {
        "name": touch.get("name"),
        "firmwareEnabled": _capability_enabled(touch),
        "singlePoint": True,
    }
    states = display.get("states")
    amoled_facts = {
        "name": display.get("name"),
        "blackFirst": True,
        "unitVerified": (
            isinstance(states, dict) and states.get("unit_verified") == "yes"
        ),
    }
    return {
        "display": display_facts,
        "touch": touch_facts,
        "amoled": amoled_facts,
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


def _stage_bytes(destination, content, mode=0o644):
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
    )
    temporary = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), mode)
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return temporary


def _existing_regular_bytes(path):
    path = Path(path)
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(metadata.st_mode):
        raise OSError(f"refusing non-regular output: {path}")
    return path.read_bytes()


def _restore_file(path, previous):
    path = Path(path)
    if previous is None:
        try:
            metadata = path.lstat()
        except FileNotFoundError:
            return
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError(f"cannot safely roll back output: {path}")
        path.unlink()
        return
    rollback = _stage_bytes(path, previous)
    try:
        os.replace(rollback, path)
    finally:
        rollback.unlink(missing_ok=True)


def _read_fixed_asset(root, parts):
    root = Path(root)
    current = root
    try:
        if stat.S_ISLNK(current.lstat().st_mode):
            return None
        for part in parts:
            current = current / part
            metadata = current.lstat()
            if stat.S_ISLNK(metadata.st_mode):
                return None
        if not stat.S_ISREG(current.lstat().st_mode):
            return None
        return current.read_bytes()
    except (FileNotFoundError, NotADirectoryError, OSError):
        return None


class StudioApplication:
    """Pure request boundary shared by tests and the HTTP adapter."""

    def __init__(self, repository_root, design_path, spec_dir):
        self.repository_root = Path(repository_root).resolve()
        self.design_path = Path(design_path).resolve()
        self.header_path = (
            self.repository_root
            / "components/app_tokens/vibepulse_layout.generated.h"
        )
        self.export_dir = self.repository_root / "design/vibepulse/exports"
        self.web_root = self.repository_root / "tools/vibepulse_studio/web"
        registry = load_registry(spec_dir)
        self.hardware = _safe_hardware(registry)
        display = self.hardware["display"]
        self.display = {"width": display["width"], "height": display["height"]}
        load_design(self.design_path, self.display)
        self._mutation_lock = threading.RLock()

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
            filename, mime_type = STATIC_ASSETS[path]
            return self._static((filename,), mime_type)
        filename, mime_type = FONT_ASSETS[path]
        return self._static(("fonts", filename), mime_type)

    def _get_design(self):
        try:
            with self._mutation_lock:
                design = load_design(self.design_path, self.display)
            return _json_response(200, design)
        except (DesignError, OSError, UnicodeError):
            return _error(500, "stored design is unavailable")

    def _put_design(self, body, headers):
        supplied_type = _content_type(headers)
        if headers is not None and supplied_type != "application/json":
            return _error(415, "Content-Type must be application/json")
        try:
            value = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _error(400, "request body must be valid UTF-8 JSON")
        try:
            validate_design(value, self.display)
            header = generate_header(value, self.display)
        except DesignError as error:
            return _error(422, str(error))
        design_content = (
            json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        header_content = header.encode("utf-8")
        try:
            with self._mutation_lock:
                self._commit_design_and_header(design_content, header_content)
        except (OSError, UnicodeError):
            return _error(500, "could not save design and generated header")
        return _json_response(200, {
            "design": value,
            "headerDigest": hashlib.sha256(header_content).hexdigest(),
        })

    def _commit_design_and_header(self, design_content, header_content):
        previous_design = _existing_regular_bytes(self.design_path)
        previous_header = _existing_regular_bytes(self.header_path)
        staged_design = _stage_bytes(self.design_path, design_content)
        staged_header = None
        design_installed = False
        try:
            staged_header = _stage_bytes(self.header_path, header_content)
            os.replace(staged_design, self.design_path)
            design_installed = True
            os.replace(staged_header, self.header_path)
        except BaseException as commit_error:
            if design_installed:
                try:
                    _restore_file(self.design_path, previous_design)
                except BaseException as rollback_error:
                    raise OSError(
                        "design transaction and rollback both failed"
                    ) from rollback_error
            raise commit_error
        finally:
            staged_design.unlink(missing_ok=True)
            if staged_header is not None:
                staged_header.unlink(missing_ok=True)

    def _export(self, state_name, body, headers):
        supplied_type = _content_type(headers)
        if headers is not None and supplied_type != "image/png":
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
        try:
            with self._mutation_lock:
                target = self._write_export(state_name, body)
        except ValueError as error:
            return _error(422, str(error))
        except OSError:
            return _error(500, "could not save PNG export")
        return _json_response(201, {
            "state": state_name,
            "path": target.relative_to(self.repository_root).as_posix(),
            "width": self.display["width"],
            "height": self.display["height"],
            "digest": hashlib.sha256(body).hexdigest(),
        })

    def _write_export(self, state_name, body):
        self.export_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        export_root = self.export_dir.resolve()
        try:
            export_root.relative_to(self.repository_root)
        except ValueError as error:
            raise OSError("export directory leaves repository") from error
        if export_root != self.export_dir or not self.export_dir.is_dir():
            raise OSError("export directory must be a real directory")

        filename = f"{state_name}.png"
        directory_flags = os.O_RDONLY
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        directory = os.open(self.export_dir, directory_flags)
        temporary = f".{filename}.{uuid.uuid4().hex}.tmp"
        descriptor = None
        try:
            try:
                metadata = os.stat(
                    filename, dir_fd=directory, follow_symlinks=False
                )
            except FileNotFoundError:
                metadata = None
            if metadata is not None and not stat.S_ISREG(metadata.st_mode):
                raise ValueError("export target must be a regular file")
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                temporary, flags, 0o600, dir_fd=directory
            )
            with os.fdopen(descriptor, "wb") as output:
                descriptor = None
                os.fchmod(output.fileno(), 0o644)
                output.write(body)
                output.flush()
                os.fsync(output.fileno())
            os.replace(
                temporary,
                filename,
                src_dir_fd=directory,
                dst_dir_fd=directory,
            )
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)
        return self.export_dir / filename

    def _static(self, parts, mime_type):
        body = _read_fixed_asset(self.web_root, parts)
        if body is None:
            return _error(404, "not found")
        return _response(200, body, mime_type)


def validate_bind(host, allow_lan):
    """Reject public/LAN exposure unless it was deliberately enabled."""
    if not isinstance(host, str) or not host.strip():
        raise ValueError("host must be nonempty")
    host = host.strip()
    loopback = host.lower() == "localhost"
    if not loopback:
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = False
    if not loopback and not allow_lan:
        raise ValueError("non-loopback bind requires --allow-lan")
    return host


class StudioRequestHandler(BaseHTTPRequestHandler):
    """Small HTTP adapter; all behavior remains in StudioApplication."""

    protocol_version = "HTTP/1.1"
    application = None

    def setup(self):
        super().setup()
        self.request.settimeout(REQUEST_TIMEOUT_SECONDS)

    def _dispatch(self):
        try:
            content_length_text = self.headers.get("Content-Length", "0")
            content_length = int(content_length_text)
            if content_length < 0:
                raise ValueError
        except ValueError:
            response = _error(400, "invalid Content-Length")
        else:
            if content_length > MAX_REQUEST_BYTES:
                self.close_connection = True
                response = _error(413, "request body exceeds 1 MiB")
            else:
                try:
                    body = self.rfile.read(content_length)
                except (OSError, socket.timeout):
                    self.close_connection = True
                    response = _error(400, "could not read request body")
                else:
                    if len(body) != content_length:
                        self.close_connection = True
                        response = _error(400, "incomplete request body")
                    else:
                        headers = {
                            name.lower(): value
                            for name, value in self.headers.items()
                        }
                        response = self.application.handle(
                            self.command, self.path, body, headers
                        )
        status_code, headers, response_body = response
        self.send_response(status_code)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(response_body)
            except (BrokenPipeError, ConnectionResetError):
                pass

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


def _handler_for(application):
    class BoundStudioRequestHandler(StudioRequestHandler):
        pass

    BoundStudioRequestHandler.application = application
    return BoundStudioRequestHandler


def _server_class(host):
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and address.version == 6:
        class IPv6ThreadingHTTPServer(ThreadingHTTPServer):
            address_family = socket.AF_INET6

        return IPv6ThreadingHTTPServer
    return ThreadingHTTPServer


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
    try:
        host = validate_bind(arguments.host, arguments.allow_lan)
    except ValueError as error:
        parser.error(str(error))

    design_path = REPOSITORY_ROOT / "design/vibepulse/studio-design.json"
    application = StudioApplication(
        REPOSITORY_ROOT,
        design_path,
        REPOSITORY_ROOT / "spec",
    )
    server_type = _server_class(host)
    server = server_type(
        (host, arguments.port), _handler_for(application)
    )
    display_host = f"[{host}]" if ":" in host else host
    url = f"http://{display_host}:{server.server_address[1]}/"
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
