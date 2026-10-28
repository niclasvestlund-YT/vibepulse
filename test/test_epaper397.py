"""Portable e-paper source, rendering, identity and refresh regression tests."""

import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.epaper397 import usb_host as host

NOW = 1793196000


def source(value=14, observed=NOW, reset_minutes=1000, stale=False, snapshot_at=NOW):
    return {
        "tokens_snapshot_at": snapshot_at,
        "tokens": {
            "v": 2,
            "codexWeekPct": value,
            "codexWeekObservedAt": observed,
            "codexWeekResetMin": reset_minutes,
            "codexWeekStale": stale,
        },
        "agents": None,
    }


class SourcesAndRaster(unittest.TestCase):
    def test_missing_is_not_zero(self):
        self.assertIsNone(host.quota({}, "codex", NOW)[0])
        self.assertEqual(host.quota(source(0), "codex", NOW)[0], 0)

    def test_invalid_values_and_future_observation(self):
        for value in [True, -1, 101, float("nan"), float("inf"), "14"]:
            with self.subTest(value=value):
                self.assertIsNone(host.quota(source(value), "codex", NOW)[0])
        self.assertTrue(host.quota(source(observed=NOW + 1), "codex", NOW)[4])

    def test_expired_or_explicitly_stale_data(self):
        self.assertFalse(host.quota(source(), "codex", NOW + 180)[4])
        self.assertTrue(host.quota(source(), "codex", NOW + 181)[4])
        self.assertTrue(host.quota(source(stale=True), "codex", NOW)[4])

    def test_observation_clock_does_not_flash_unchanged_content(self):
        first = host.content_key(source(), "codex", NOW)
        # Same reading/reset, observed one minute later: no panel redraw.
        second = host.content_key(
            source(observed=NOW + 60, reset_minutes=999, snapshot_at=NOW + 60), "codex", NOW + 60
        )
        self.assertEqual(first, second)
        self.assertNotEqual(first, host.content_key(source(15), "codex", NOW))
        self.assertNotEqual(first, host.content_key(source(stale=True), "codex", NOW))

    def test_cached_quota_reset_uses_retained_snapshot_time(self):
        data = source(observed=NOW - 3600, snapshot_at=NOW)
        expected = NOW + 1000 * 60
        self.assertEqual(host.quota(data, "codex", NOW)[2], expected)
        # Turning pages / rendering later must not move the cached deadline.
        self.assertEqual(host.quota(data, "codex", NOW + 120)[2], expected)
        later = source(observed=NOW - 3600, reset_minutes=999,
                       snapshot_at=NOW + 60)
        self.assertEqual(host.quota(later, "codex", NOW + 60)[2], expected)
        self.assertEqual(host.content_key(data, "codex", NOW),
                         host.content_key(later, "codex", NOW + 60))

    def test_snapshot_anchor_precedes_the_agent_http_read(self):
        clock = [NOW]
        def read(url):
            if url.endswith("/api/tokens"):
                return source()["tokens"]
            clock[0] += 4
            return {"v": 2}
        with patch.object(host, "get_json", side_effect=read), \
             patch.object(host.time, "time", side_effect=lambda: clock[0]):
            data = host.snapshot()
        self.assertEqual(data["tokens_snapshot_at"], NOW)
        self.assertEqual(clock[0], NOW + 4)

    def test_four_pages_and_extremes_fit_native_geometry(self):
        for value in [None, 0, 14, 100]:
            for page in host.PAGES:
                with self.subTest(value=value, page=page):
                    im = host.render_page(source(value), page, NOW)
                    self.assertEqual(im.size, (800, 480))
                    self.assertEqual(im.mode, "1")
                    self.assertEqual(len(im.tobytes()), 48000)

    def test_http_errors_and_wrong_schema_are_unknown(self):
        for data in [b"[]", b'{"v":1}', b"not json", b"x" * 262145]:
            with self.subTest(data=data[:20]):
                response = io.BytesIO(data)
                with patch.object(
                    host.urllib.request, "urlopen", return_value=response
                ):
                    self.assertIsNone(host.get_json("http://127.0.0.1/"))


class FakeUSB:
    def __init__(self, **kwargs):
        self.lines = []
        self.frames = []
        self.closed = False

    def open(self):
        return None

    def close(self):
        self.closed = True

    def flush(self):
        return None

    def write(self, packet):
        if packet == b"?":
            self.lines.append(b"HELLO VPP1 waveshare_epaper_397 paper-0.2.0 800 480\n")
        elif packet.startswith(b"VPP1"):
            magic, seq, length, crc = struct.unpack("<4sIII", packet[:16])
            if magic != b"VPP1" or length != 48000 or zlib.crc32(packet[16:]) != crc:
                raise AssertionError("Invalid wire frame")
            self.frames.append(packet[16:])
            self.lines.append(f"ACK {seq} {crc:08x} 600ms partial\n".encode())
        return len(packet)

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


class RestartingUSB(FakeUSB):
    def write(self, packet):
        count = super().write(packet)
        if packet.startswith(b"VPP1"):
            if len(self.frames) == 1:
                self.lines.append((host.HELLO + "\n").encode())
            else:
                raise StopIteration("second raster transferred")
        return count

    def readline(self):
        if self.frames and not self.lines:
            raise AssertionError("Boot greeting did not trigger an unchanged-page repaint")
        return super().readline()


class LiveBridge(unittest.TestCase):
    def test_device_restart_repaints_even_the_constant_device_page(self):
        usb = RestartingUSB()
        with (
            tempfile.TemporaryDirectory() as state,
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(sys, "argv", ["paper", "--state-dir", state]),
            patch.object(host, "PAGES", ("device",)),
            patch.object(host, "check_identity", return_value="/dev/synthetic"),
            patch.object(host.serial, "Serial", return_value=usb),
            patch.object(host.time, "monotonic", return_value=0.0),
            patch.object(host.time, "time", return_value=NOW),
            patch.object(host, "snapshot", return_value=source()),
            self.assertRaises(StopIteration),
        ):
            host.main()
        self.assertEqual(len(usb.frames), 2)
        self.assertEqual(usb.frames[0], usb.frames[1])
        self.assertTrue(usb.closed)

    def test_restart_during_transfer_requires_a_new_identity_handshake(self):
        class RestartDuringTransfer(FakeUSB):
            def write(self, packet):
                count = super().write(packet)
                if packet.startswith(b"VPP1"):
                    self.lines = [(host.HELLO + "\n").encode()]
                return count
        usb = RestartDuringTransfer()
        with (
            tempfile.TemporaryDirectory() as state,
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(sys, "argv", ["paper", "--once", "--state-dir", state]),
            patch.object(host, "check_identity", return_value="/dev/synthetic"),
            patch.object(host.serial, "Serial", return_value=usb),
            patch.object(host.time, "monotonic", return_value=0.0),
            patch.object(host.time, "time", return_value=NOW),
            patch.object(host, "snapshot", return_value=source()),
            self.assertRaisesRegex(RuntimeError, "restarted during transfer"),
        ):
            host.main()
        self.assertEqual(len(usb.frames), 1)
        self.assertTrue(usb.closed)

    def test_startup_noise_does_not_queue_duplicate_identity_replies(self):
        class NoisyHandshake(FakeUSB):
            queries = 0
            def write(self, packet):
                if packet == b"?":
                    self.queries += 1
                    if self.queries > 1:
                        raise AssertionError("Repeated identity query queued a false boot greeting")
                return super().write(packet)
        usb = NoisyHandshake()
        usb.lines = [b"ACK old-frame\n", b"PMU ready\n", b""]
        with (
            tempfile.TemporaryDirectory() as state,
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(sys, "argv", ["paper", "--once", "--state-dir", state]),
            patch.object(host, "check_identity", return_value="/dev/synthetic"),
            patch.object(host.serial, "Serial", return_value=usb),
            patch.object(host.time, "monotonic", return_value=0.0),
            patch.object(host.time, "time", return_value=NOW),
            patch.object(host, "snapshot", return_value=source()),
        ):
            host.main()
        self.assertEqual(usb.queries, 1)
        self.assertEqual(len(usb.frames), 1)
        self.assertTrue(usb.closed)

    def test_first_transfer_reads_source_even_when_monotonic_starts_at_zero(self):
        usb = FakeUSB()
        with (
            tempfile.TemporaryDirectory() as state,
            contextlib.redirect_stdout(io.StringIO()),
            patch.object(sys, "argv", ["paper", "--once", "--state-dir", state]),
            patch.object(host, "check_identity", return_value="/dev/synthetic"),
            patch.object(host.serial, "Serial", return_value=usb),
            patch.object(host.time, "monotonic", return_value=0.0),
            patch.object(host.time, "time", return_value=NOW),
            patch.object(host, "snapshot", return_value=source()) as read_source,
        ):
            host.main()
            read_source.assert_called_once_with("http://127.0.0.1:8737")
            self.assertEqual(
                usb.frames, [host.render_page(source(), "codex", NOW).tobytes()]
            )
            saved = json.loads((Path(state) / "usb-status.json").read_text())
            self.assertEqual(saved["source_status"]["codex"]["value"], 14)
        self.assertTrue(usb.closed)


if __name__ == "__main__":
    unittest.main()
