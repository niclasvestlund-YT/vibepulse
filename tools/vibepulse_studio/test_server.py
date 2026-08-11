import copy
import hashlib
import io
import json
import os
import stat
import tempfile
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
    StudioApplication,
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
        bad = json.loads(original)
        bad["canvas"] = {"width": 1000, "height": 480}
        status, _, body = self.json_request("PUT", "/api/design", bad)
        self.assertEqual(status, 422)
        self.assertIn("error", json.loads(body))
        self.assertEqual(self.design_path.read_bytes(), original)
        self.assertFalse(self.header_path.exists())

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

        status, _, _ = self.request("PUT", "/api/design", b"{}")
        self.assertEqual(status, 415)

        status, _, _ = self.request(
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
        call_count = 0

        def fail_second_replace(source, target):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("simulated header replace failure")
            return real_replace(source, target)

        with mock.patch(
            "tools.vibepulse_studio.server.os.replace",
            side_effect=fail_second_replace,
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


if __name__ == "__main__":
    unittest.main()
