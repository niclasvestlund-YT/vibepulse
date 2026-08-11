import base64
import copy
import hashlib
import io
import json
import os
import socket
import stat
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from PIL import Image

from tools.vibepulse_studio.design import (
    generate_header,
    load_design,
    load_display,
    save_design,
)
from tools.vibepulse_studio.server import (
    MAX_REQUEST_BYTES,
    MUTATION_TOKEN_HEADER,
    DesignPairError,
    StudioApplication,
    create_server,
    safe_server_url,
    validate_bind,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
REPOSITORY_DESIGN = (
    REPOSITORY_ROOT / "design/vibepulse/studio-design.json"
)
APPROVED_STATES = (
    "claude-hero",
    "codex-hero",
    "claude-details",
    "overview",
    "claude-hero-stale",
    "codex-hero-stale",
    "claude-hero-missing",
    "codex-hero-missing",
)


def png_bytes(size=(480, 480), color="black"):
    payload = io.BytesIO()
    Image.new("RGB", size, color).save(payload, "PNG")
    return payload.getvalue()


class StudioApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.spec = REPOSITORY_ROOT / "spec"
        self.display = load_display(self.spec)
        self.design_path = self.repo / "design/vibepulse/studio-design.json"
        self.header_path = (
            self.repo / "components/app_tokens/vibepulse_layout.generated.h"
        )
        save_design(
            self.design_path,
            load_design(REPOSITORY_DESIGN, self.display),
            self.display,
        )
        self.app = StudioApplication(self.repo, self.design_path, self.spec)

    def tearDown(self):
        self.temp.cleanup()

    def request(self, method, path, body=b"", content_type=None):
        headers = {}
        if content_type is not None:
            headers["content-type"] = content_type
        return self.app.handle(method, path, body, headers)

    def json_request(self, method, path, value):
        return self.request(
            method,
            path,
            json.dumps(value).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def test_get_design_and_hardware_return_only_safe_json(self):
        status, headers, body = self.app.handle("GET", "/api/design", b"")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(int(headers["Content-Length"]), len(body))
        self.assertEqual(
            headers["X-VibePulse-Header-Digest"],
            hashlib.sha256(self.header_path.read_bytes()).hexdigest(),
        )
        self.assertNotIn("canvas", json.loads(body))

        status, _, body = self.app.handle("GET", "/api/hardware", b"")
        self.assertEqual(status, 200)
        hardware = json.loads(body)
        self.assertEqual(set(hardware), {"display", "touch", "amoled"})
        self.assertEqual(hardware["display"]["width"], 480)
        self.assertEqual(hardware["display"]["height"], 480)
        self.assertEqual(hardware["display"]["colorFormat"], "RGB565")
        self.assertTrue(hardware["touch"]["firmwareEnabled"])
        encoded = json.dumps(hardware).lower()
        for secret_name in ("secret", "password", "token", "ssid", "key"):
            self.assertNotIn(secret_name, encoded)

    def test_valid_save_persists_matching_design_and_header_digest(self):
        design = json.loads(self.design_path.read_text(encoding="utf-8"))
        design["hero"]["providerY"] = 24
        status, _, body = self.json_request("PUT", "/api/design", design)
        self.assertEqual(status, 200)
        response = json.loads(body)
        self.assertEqual(response["design"], design)

        saved = load_design(self.design_path, self.display)
        header = self.header_path.read_text(encoding="utf-8")
        self.assertEqual(saved, design)
        self.assertEqual(header, generate_header(saved, self.display))
        self.assertEqual(
            response["headerDigest"],
            hashlib.sha256(header.encode("utf-8")).hexdigest(),
        )

    def test_invalid_save_does_not_replace_design_or_header(self):
        original = self.design_path.read_bytes()
        original_header = self.header_path.read_bytes()
        bad = json.loads(original)
        bad["canvas"] = {"width": 1000, "height": 480}
        status, _, body = self.json_request("PUT", "/api/design", bad)
        self.assertEqual(status, 422)
        self.assertIn("error", json.loads(body))
        self.assertEqual(self.design_path.read_bytes(), original)
        self.assertEqual(self.header_path.read_bytes(), original_header)

    def test_invalid_json_type_and_oversized_bodies_are_json_errors(self):
        status, _, body = self.request(
            "PUT", "/api/design", b"{", "application/json"
        )
        self.assertEqual(status, 400)
        self.assertEqual(set(json.loads(body)), {"error"})

        status, _, _ = self.request(
            "PUT", "/api/design", b"{}", "text/plain"
        )
        self.assertEqual(status, 415)

        status, _, _ = self.app.handle("PUT", "/api/design", b"{}")
        self.assertEqual(status, 415)

        status, _, _ = self.app.handle(
            "POST", "/api/export/claude-hero", png_bytes()
        )
        self.assertEqual(status, 415)

        status, _, body = self.request(
            "PUT",
            "/api/design",
            b"x" * (MAX_REQUEST_BYTES + 1),
            "application/json",
        )
        self.assertEqual(status, 413)
        self.assertIn("error", json.loads(body))

        status, _, _ = self.request(
            "POST",
            "/api/export/claude-hero",
            b"x" * (MAX_REQUEST_BYTES + 1),
            "image/png",
        )
        self.assertEqual(status, 413)

    def test_second_replace_failure_rolls_back_both_outputs(self):
        design = json.loads(self.design_path.read_text(encoding="utf-8"))
        status, _, _ = self.json_request("PUT", "/api/design", design)
        self.assertEqual(status, 200)
        old_design = self.design_path.read_bytes()
        old_header = self.header_path.read_bytes()
        changed = copy.deepcopy(design)
        changed["hero"]["providerY"] = 24

        real_replace = os.replace
        failed = False

        def fail_header_replace(source, target, *args, **kwargs):
            nonlocal failed
            if target == self.header_path.name and not failed:
                failed = True
                raise OSError("simulated header replace failure")
            return real_replace(source, target, *args, **kwargs)

        with mock.patch(
            "tools.vibepulse_studio.server.os.replace",
            side_effect=fail_header_replace,
        ):
            status, _, body = self.json_request(
                "PUT", "/api/design", changed
            )
        self.assertEqual(status, 500)
        self.assertIn("save", json.loads(body)["error"].lower())
        self.assertEqual(self.design_path.read_bytes(), old_design)
        self.assertEqual(self.header_path.read_bytes(), old_header)
        self.assertEqual(list(self.design_path.parent.glob(".*.tmp")), [])
        self.assertEqual(list(self.header_path.parent.glob(".*.tmp")), [])

    def test_replace_that_commits_then_raises_restores_both_old_outputs(self):
        old_design = self.design_path.read_bytes()
        old_header = self.header_path.read_bytes()
        changed = json.loads(old_design)
        changed["hero"]["providerY"] = 24
        real_replace = os.replace
        failed = False

        def commit_design_then_raise(source, target, *args, **kwargs):
            nonlocal failed
            result = real_replace(source, target, *args, **kwargs)
            if target == self.design_path.name and not failed:
                failed = True
                raise OSError("replace committed then reported failure")
            return result

        with mock.patch(
            "tools.vibepulse_studio.server.os.replace",
            side_effect=commit_design_then_raise,
        ):
            status, _, _ = self.json_request("PUT", "/api/design", changed)
        self.assertEqual(status, 500)
        self.assertEqual(self.design_path.read_bytes(), old_design)
        self.assertEqual(self.header_path.read_bytes(), old_header)

    def test_startup_recovers_a_crash_left_between_pair_replaces(self):
        old_design = self.design_path.read_bytes()
        old_header = self.header_path.read_bytes()
        changed = json.loads(old_design)
        changed["hero"]["providerY"] = 24
        real_replace = os.replace
        crashed = False

        class SimulatedProcessCrash(BaseException):
            pass

        def crash_after_design_replace(source, target, *args, **kwargs):
            nonlocal crashed
            result = real_replace(source, target, *args, **kwargs)
            if target == self.design_path.name and not crashed:
                crashed = True
                raise SimulatedProcessCrash()
            return result

        with mock.patch(
            "tools.vibepulse_studio.server.os.replace",
            side_effect=crash_after_design_replace,
        ):
            with self.assertRaises(SimulatedProcessCrash):
                self.json_request("PUT", "/api/design", changed)

        recovered = StudioApplication(self.repo, self.design_path, self.spec)
        status, _, _ = recovered.handle("GET", "/api/design", b"")
        self.assertEqual(status, 200)
        self.assertEqual(self.design_path.read_bytes(), old_design)
        self.assertEqual(self.header_path.read_bytes(), old_header)
        self.assertFalse(
            (self.repo / "design/vibepulse/.studio-transaction.json").exists()
        )

    def test_recovery_accepts_journal_for_any_allowed_request_size(self):
        old_design = self.design_path.read_bytes()
        changed = json.loads(old_design)
        changed["fixtures"]["claude"]["reset"] = "R" * 800_000
        encoded = json.dumps(changed).encode("utf-8")
        self.assertLess(len(encoded), MAX_REQUEST_BYTES)
        real_replace = os.replace
        crashed = False

        class SimulatedProcessCrash(BaseException):
            pass

        def crash_after_design_replace(source, target, *args, **kwargs):
            nonlocal crashed
            result = real_replace(source, target, *args, **kwargs)
            if target == self.design_path.name and not crashed:
                crashed = True
                raise SimulatedProcessCrash()
            return result

        with mock.patch(
            "tools.vibepulse_studio.server.os.replace",
            side_effect=crash_after_design_replace,
        ):
            with self.assertRaises(SimulatedProcessCrash):
                self.json_request("PUT", "/api/design", changed)

        StudioApplication(self.repo, self.design_path, self.spec)
        self.assertEqual(
            json.loads(self.design_path.read_text(encoding="utf-8")), changed
        )
        self.assertFalse(
            (self.repo / "design/vibepulse/.studio-transaction.json").exists()
        )

    def test_mismatched_pair_is_never_served_without_a_valid_journal(self):
        self.header_path.write_text("corrupt\n", encoding="utf-8")
        status, _, body = self.app.handle("GET", "/api/design", b"")
        self.assertEqual(status, 500)
        self.assertIn("pair", json.loads(body)["error"])
        with self.assertRaisesRegex(Exception, "pair"):
            StudioApplication(self.repo, self.design_path, self.spec)

    def test_malformed_journal_types_fail_as_a_concise_pair_error(self):
        encoded = base64.b64encode(self.design_path.read_bytes()).decode("ascii")
        malformed_entry = {"sha256": 7, "base64": encoded}
        malformed = {
            "schemaVersion": 1,
            "old": {
                "design": malformed_entry,
                "header": None,
                "pairDigest": "0" * 64,
            },
            "new": {
                "design": malformed_entry,
                "header": malformed_entry,
                "pairDigest": "0" * 64,
            },
        }
        journal = self.repo / "design/vibepulse/.studio-transaction.json"
        journal.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaisesRegex(DesignPairError, "journal"):
            StudioApplication(self.repo, self.design_path, self.spec)

    def test_concurrent_saves_never_leave_a_mismatched_header(self):
        first = json.loads(self.design_path.read_text(encoding="utf-8"))
        second = copy.deepcopy(first)
        first["hero"]["providerY"] = 24
        second["hero"]["providerY"] = 25

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(
                pool.map(
                    lambda value: self.json_request(
                        "PUT", "/api/design", value
                    ),
                    (first, second),
                )
            )
        self.assertEqual([result[0] for result in results], [200, 200])
        saved = load_design(self.design_path, self.display)
        self.assertIn(saved, (first, second))
        self.assertEqual(
            self.header_path.read_text(encoding="utf-8"),
            generate_header(saved, self.display),
        )

    def test_each_approved_export_is_an_exact_atomic_png(self):
        payload = png_bytes()
        for state_name in APPROVED_STATES:
            with self.subTest(state=state_name):
                status, _, body = self.request(
                    "POST",
                    f"/api/export/{state_name}",
                    payload,
                    "image/png",
                )
                self.assertEqual(status, 201)
                response = json.loads(body)
                self.assertEqual(response["state"], state_name)
                target = (
                    self.repo
                    / "design/vibepulse/exports"
                    / f"{state_name}.png"
                )
                self.assertTrue(stat.S_ISREG(target.lstat().st_mode))
                with Image.open(target) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, (480, 480))
                self.assertEqual(
                    response["digest"], hashlib.sha256(payload).hexdigest()
                )
        self.assertEqual(
            list((self.repo / "design/vibepulse/exports").glob(".*.tmp")),
            [],
        )

    def test_exports_reject_bad_type_format_corruption_and_dimensions(self):
        status, _, _ = self.request(
            "POST", "/api/export/claude-hero", png_bytes(), "text/plain"
        )
        self.assertEqual(status, 415)

        jpeg = io.BytesIO()
        Image.new("RGB", (480, 480), "black").save(jpeg, "JPEG")
        status, _, _ = self.request(
            "POST", "/api/export/claude-hero", jpeg.getvalue(), "image/png"
        )
        self.assertEqual(status, 422)

        for payload in (b"not an image", png_bytes((960, 960))):
            with self.subTest(size=len(payload)):
                status, _, body = self.request(
                    "POST",
                    "/api/export/claude-hero",
                    payload,
                    "image/png",
                )
                self.assertEqual(status, 422)
                self.assertIn("error", json.loads(body))

    def test_export_rejects_unknown_state_traversal_and_nonregular_target(self):
        payload = png_bytes()
        for path in (
            "/api/export/claude",
            "/api/export/../../secrets",
            "/api/export/%2e%2e%2fsecrets",
            "/api/export/claude-hero/extra",
        ):
            with self.subTest(path=path):
                status, _, _ = self.request(
                    "POST", path, payload, "image/png"
                )
                self.assertEqual(status, 404)

        export_dir = self.repo / "design/vibepulse/exports"
        export_dir.mkdir(parents=True)
        target = export_dir / "claude-hero.png"
        target.mkdir()
        status, _, _ = self.request(
            "POST", "/api/export/claude-hero", payload, "image/png"
        )
        self.assertEqual(status, 422)

    def test_export_rejects_symlink_without_touching_its_victim(self):
        export_dir = self.repo / "design/vibepulse/exports"
        export_dir.mkdir(parents=True)
        victim = self.repo / "victim.txt"
        victim.write_text("keep me\n", encoding="utf-8")
        (export_dir / "claude-hero.png").symlink_to(victim)

        status, _, _ = self.request(
            "POST",
            "/api/export/claude-hero",
            png_bytes(),
            "image/png",
        )
        self.assertEqual(status, 422)
        self.assertEqual(victim.read_text(encoding="utf-8"), "keep me\n")
        self.assertTrue(
            (export_dir / "claude-hero.png").is_symlink()
        )

    def test_static_routes_are_fixed_allowlisted_and_do_not_follow_symlinks(self):
        web = self.repo / "tools/vibepulse_studio/web"
        fonts = web / "fonts"
        fonts.mkdir(parents=True)
        (web / "index.html").write_text("INDEX", encoding="utf-8")
        (web / "studio.css").write_text("CSS", encoding="utf-8")
        (web / "studio.js").write_text("JS", encoding="utf-8")
        for filename in (
            "IBMPlexSans-Bold.woff2",
            "IBMPlexSans-SemiBold.woff2",
            "OFL.txt",
        ):
            (fonts / filename).write_bytes(filename.encode("ascii"))

        expected = {
            "/": b"INDEX",
            "/studio.css": b"CSS",
            "/studio.js": b"JS",
            "/fonts/IBMPlexSans-Bold.woff2": b"IBMPlexSans-Bold.woff2",
            "/fonts/IBMPlexSans-SemiBold.woff2": (
                b"IBMPlexSans-SemiBold.woff2"
            ),
            "/fonts/OFL.txt": b"OFL.txt",
        }
        for path, expected_body in expected.items():
            with self.subTest(path=path):
                status, headers, body = self.app.handle("GET", path, b"")
                self.assertEqual(status, 200)
                self.assertEqual(body, expected_body)
                self.assertIn("Content-Type", headers)

        victim = self.repo / "private.txt"
        victim.write_text("PRIVATE", encoding="utf-8")
        (web / "studio.css").unlink()
        (web / "studio.css").symlink_to(victim)
        status, _, body = self.app.handle("GET", "/studio.css", b"")
        self.assertEqual(status, 404)
        self.assertNotIn(b"PRIVATE", body)

        for path in (
            "/../../secrets.h",
            "/studio.css/",
            "/not-allowlisted",
            "/fonts/not-a-font.woff2",
            "/fonts/../../secrets.h",
            "/fonts/%2e%2e%2fsecrets.h",
        ):
            with self.subTest(path=path):
                status, _, _ = self.app.handle("GET", path, b"")
                self.assertEqual(status, 404)

    def test_known_routes_reject_wrong_methods_and_all_errors_are_json(self):
        for method, path, allowed in (
            ("POST", "/api/design", "GET, PUT"),
            ("PUT", "/api/hardware", "GET"),
            ("POST", "/studio.css", "GET"),
        ):
            with self.subTest(method=method, path=path):
                status, headers, body = self.app.handle(method, path, b"")
                self.assertEqual(status, 405)
                self.assertEqual(headers["Allow"], allowed)
                self.assertEqual(set(json.loads(body)), {"error"})

        status, headers, body = self.app.handle("GET", "/missing", b"")
        self.assertEqual(status, 404)
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(set(json.loads(body)), {"error"})


class DescriptorSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.repo = self.base / "repo"
        self.repo.mkdir()
        self.spec = REPOSITORY_ROOT / "spec"
        self.display = load_display(self.spec)

    def tearDown(self):
        self.temp.cleanup()

    def seed(self, path):
        save_design(
            path,
            load_design(REPOSITORY_DESIGN, self.display),
            self.display,
        )

    def test_design_path_must_be_lexically_inside_repository(self):
        outside = self.base / "outside.json"
        self.seed(outside)
        with self.assertRaisesRegex(ValueError, "repository"):
            StudioApplication(self.repo, outside, self.spec)

    def test_design_and_header_symlinks_are_rejected_without_following(self):
        outside = self.base / "outside"
        outside.mkdir()
        victim_design = outside / "design.json"
        self.seed(victim_design)
        (self.repo / "design/vibepulse").mkdir(parents=True)
        design_path = self.repo / "design/vibepulse/studio-design.json"
        design_path.symlink_to(victim_design)
        with self.assertRaisesRegex(OSError, "regular|symlink"):
            StudioApplication(self.repo, design_path, self.spec)

        design_path.unlink()
        self.seed(design_path)
        header_dir = self.repo / "components/app_tokens"
        header_dir.mkdir(parents=True)
        victim_header = outside / "victim.h"
        victim_header.write_text("VICTIM\n", encoding="utf-8")
        (header_dir / "vibepulse_layout.generated.h").symlink_to(
            victim_header
        )
        with self.assertRaisesRegex(OSError, "regular|symlink"):
            StudioApplication(self.repo, design_path, self.spec)
        self.assertEqual(
            victim_header.read_text(encoding="utf-8"), "VICTIM\n"
        )

    def test_symlinked_intermediate_design_directory_is_rejected(self):
        outside = self.base / "outside"
        outside.mkdir()
        (self.repo / "design").symlink_to(outside, target_is_directory=True)
        design_path = self.repo / "design/vibepulse/studio-design.json"
        (outside / "vibepulse").mkdir()
        self.seed(outside / "vibepulse/studio-design.json")
        with self.assertRaisesRegex(OSError, "directory|symlink"):
            StudioApplication(self.repo, design_path, self.spec)

    def test_design_read_does_not_follow_a_late_symlink_swap(self):
        design_path = self.repo / "design/vibepulse/studio-design.json"
        self.seed(design_path)
        app = StudioApplication(self.repo, design_path, self.spec)
        victim = self.base / "victim.json"
        victim.write_text('{"private":"DO NOT SERVE"}\n', encoding="utf-8")
        design_path.unlink()
        design_path.symlink_to(victim)
        status, _, body = app.handle("GET", "/api/design", b"")
        self.assertEqual(status, 500)
        self.assertNotIn(b"DO NOT SERVE", body)


class BindValidationTests(unittest.TestCase):
    def test_loopback_is_default_and_lan_needs_explicit_opt_in(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            with self.subTest(host=host):
                self.assertEqual(validate_bind(host, False), host)
        with self.assertRaisesRegex(ValueError, "allow-lan"):
            validate_bind("0.0.0.0", False)
        with self.assertRaisesRegex(ValueError, "allow-lan"):
            validate_bind("192.168.1.40", False)
        self.assertEqual(validate_bind("0.0.0.0", True), "0.0.0.0")

    def test_every_dns_answer_must_be_loopback_without_lan_opt_in(self):
        answers = [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.0.2.8", 0)),
        ]
        with mock.patch(
            "tools.vibepulse_studio.server.socket.getaddrinfo",
            return_value=answers,
        ):
            with self.assertRaisesRegex(ValueError, "allow-lan"):
                validate_bind("studio.local", False)
            self.assertEqual(
                validate_bind("studio.local", True), "studio.local"
            )

    def test_server_resolves_once_then_binds_the_validated_numeric_address(self):
        real_getaddrinfo = socket.getaddrinfo
        with mock.patch(
            "tools.vibepulse_studio.server.socket.getaddrinfo",
            wraps=real_getaddrinfo,
        ) as resolver:
            server = create_server(mock.Mock(), "127.0.0.1", 0)
        try:
            self.assertEqual(resolver.call_count, 1)
            self.assertEqual(server.server_address[0], "127.0.0.1")
        finally:
            server.server_close()


class LiveHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.spec = REPOSITORY_ROOT / "spec"
        self.display = load_display(self.spec)
        self.design_path = self.repo / "design/vibepulse/studio-design.json"
        save_design(
            self.design_path,
            load_design(REPOSITORY_DESIGN, self.display),
            self.display,
        )
        self.app = StudioApplication(self.repo, self.design_path, self.spec)
        self.servers = []

    def tearDown(self):
        for server, thread in reversed(self.servers):
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
        self.temp.cleanup()

    def start_server(self, **kwargs):
        server = create_server(
            self.app,
            "127.0.0.1",
            0,
            request_timeout=kwargs.pop("request_timeout", 0.2),
            worker_acquire_timeout=kwargs.pop("worker_acquire_timeout", 0.05),
            **kwargs,
        )
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.servers.append((server, thread))
        return server

    def exchange(self, server, request, timeout=2):
        client = socket.create_connection(server.server_address, timeout=timeout)
        client.settimeout(timeout)
        try:
            client.sendall(request)
            chunks = []
            while True:
                chunk = client.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            client.close()

    def authority(self, server):
        return f"127.0.0.1:{server.server_address[1]}"

    def test_host_is_checked_against_bound_address_and_port(self):
        server = self.start_server()
        valid = self.exchange(
            server,
            (
                f"GET /api/hardware HTTP/1.1\r\n"
                f"Host: {self.authority(server)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"),
        )
        self.assertIn(b" 200 ", valid.split(b"\r\n", 1)[0])

        foreign = self.exchange(
            server,
            (
                "GET /api/hardware HTTP/1.1\r\n"
                f"Host: evil.example:{server.server_address[1]}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii"),
        )
        self.assertIn(b" 421 ", foreign.split(b"\r\n", 1)[0])
        self.assertIn(b"application/json", foreign)
        self.assertIn(b"Connection: close", foreign)

    def test_foreign_origin_is_rejected_before_mutation(self):
        server = self.start_server()
        design = self.design_path.read_bytes()
        response = self.exchange(
            server,
            (
                f"PUT /api/design HTTP/1.1\r\n"
                f"Host: {self.authority(server)}\r\n"
                "Origin: http://evil.example\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(design)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii") + design,
        )
        self.assertIn(b" 403 ", response.split(b"\r\n", 1)[0])

    def test_lan_mode_requires_ephemeral_token_and_uses_fragment_url(self):
        token = "ephemeral-test-token"
        server = self.start_server(allow_lan=True, mutation_token=token)
        design = self.design_path.read_bytes()
        common = (
            f"PUT /api/design HTTP/1.1\r\n"
            f"Host: {self.authority(server)}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(design)}\r\n"
            "Connection: close\r\n"
        )
        denied = self.exchange(
            server, (common + "\r\n").encode("ascii") + design
        )
        self.assertIn(b" 403 ", denied.split(b"\r\n", 1)[0])
        allowed = self.exchange(
            server,
            (
                common
                + f"{MUTATION_TOKEN_HEADER}: {token}\r\n\r\n"
            ).encode("ascii") + design,
        )
        self.assertIn(b" 200 ", allowed.split(b"\r\n", 1)[0])
        url = safe_server_url(server, "127.0.0.1", token)
        self.assertIn("#mutation-token=", url)
        self.assertNotIn("?mutation-token=", url)

    def test_strict_framing_rejects_transfer_duplicate_and_bad_lengths(self):
        server = self.start_server()
        bad_headers = (
            "Transfer-Encoding: chunked\r\n",
            "Content-Length: 0\r\nContent-Length: 0\r\n",
            "Content-Length: +1\r\n",
            "Content-Length: 01\r\n",
            "Content-Length: -1\r\n",
            f"Content-Length: {MAX_REQUEST_BYTES + 1}\r\n",
        )
        for framing in bad_headers:
            with self.subTest(framing=framing):
                response = self.exchange(
                    server,
                    (
                        "PUT /api/design HTTP/1.1\r\n"
                        f"Host: {self.authority(server)}\r\n"
                        "Content-Type: application/json\r\n"
                        f"{framing}"
                        "Connection: close\r\n\r\n"
                    ).encode("ascii"),
                )
                expected = (
                    413 if str(MAX_REQUEST_BYTES + 1) in framing else 400
                )
                self.assertIn(
                    f" {expected} ".encode("ascii"),
                    response.split(b"\r\n", 1)[0],
                )
                self.assertIn(b"Connection: close", response)
                self.assertIn(b"application/json", response)

    def test_incomplete_body_times_out_and_connection_is_closed(self):
        server = self.start_server(request_timeout=0.1)
        client = socket.create_connection(server.server_address, timeout=2)
        client.settimeout(2)
        started = time.monotonic()
        try:
            client.sendall(
                (
                    "PUT /api/design HTTP/1.1\r\n"
                    f"Host: {self.authority(server)}\r\n"
                    "Content-Type: application/json\r\n"
                    "Content-Length: 10\r\n\r\n"
                    "{"
                ).encode("ascii")
            )
            response = client.recv(65536)
            self.assertIn(b" 400 ", response.split(b"\r\n", 1)[0])
            self.assertIn(b"Connection: close", response)
            self.assertLess(time.monotonic() - started, 1.5)
            while client.recv(65536):
                pass
        finally:
            client.close()

    def test_worker_limit_returns_503_instead_of_unbounded_threads(self):
        server = self.start_server(
            request_timeout=0.5,
            max_workers=1,
            worker_acquire_timeout=0.05,
        )
        blocker = socket.create_connection(server.server_address, timeout=2)
        blocker.sendall(
            (
                "PUT /api/design HTTP/1.1\r\n"
                f"Host: {self.authority(server)}\r\n"
                "Content-Type: application/json\r\n"
                "Content-Length: 10\r\n\r\n"
                "{"
            ).encode("ascii")
        )
        try:
            response = self.exchange(
                server,
                (
                    "GET /api/hardware HTTP/1.1\r\n"
                    f"Host: {self.authority(server)}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii"),
            )
            self.assertIn(b" 503 ", response.split(b"\r\n", 1)[0])
            self.assertIn(b"Connection: close", response)
        finally:
            blocker.close()


if __name__ == "__main__":
    unittest.main()
