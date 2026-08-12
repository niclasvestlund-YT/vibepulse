import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver.quota_cache import CachedQuota, QuotaCache


HUGE_INT = 10 ** 400


class QuotaCacheTests(unittest.TestCase):
    def record(self, **overrides):
        values = {
            "provider": "codex",
            "scope": "general_weekly",
            "identity": "account-a",
            "pct": 46.0,
            "reset_at": 2_000,
            "observed_at": 1_000,
            "label": "Work account",
        }
        values.update(overrides)
        return CachedQuota(**values)

    def test_put_and_latest_returns_codex_general_weekly(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json", now=lambda: 1_100)

            self.assertTrue(cache.put(self.record()))

            self.assertEqual(
                cache.latest("codex", "general_weekly"), self.record())

    def test_multiple_identities_remain_isolated_and_newest_wins(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")
            first = self.record(identity="account-a", pct=46, observed_at=100)
            second = self.record(identity="account-b", pct=51, observed_at=200)

            self.assertTrue(cache.put(first))
            self.assertTrue(cache.put(second))

            self.assertEqual(cache.latest("codex", "general_weekly", now=300),
                             second)

    def test_latest_does_not_cross_provider_or_semantic_scope(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")
            self.assertTrue(cache.put(self.record()))
            self.assertTrue(cache.put(self.record(
                provider="claude", scope="general_session",
                identity="claude-account", pct=20)))
            self.assertTrue(cache.put(self.record(
                scope="general_session", identity="account-session", pct=30)))

            self.assertIsNone(cache.latest("claude", "general_weekly", now=1_100))
            self.assertIsNone(cache.latest("codex", "model_weekly", now=1_100))

    def test_restart_loads_persisted_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            record = self.record()
            self.assertTrue(QuotaCache(path).put(record))

            self.assertEqual(QuotaCache(path, now=lambda: 1_100).latest(
                "codex", "general_weekly"), record)

    def test_latest_expires_at_exact_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")
            self.assertTrue(cache.put(self.record(reset_at=1_000)))

            self.assertIsNone(cache.latest("codex", "general_weekly", now=1_000))

    def test_fresh_decrease_replaces_same_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")
            self.assertTrue(cache.put(self.record(pct=61, observed_at=100)))
            decreased = self.record(pct=46, observed_at=200)

            self.assertTrue(cache.put(decreased))

            self.assertEqual(cache.latest("codex", "general_weekly", now=300),
                             decreased)

    def test_load_ignores_malformed_sibling_record(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            valid = self.record()
            path.write_text(json.dumps({"v": 1, "records": [
                valid.__dict__, {"provider": "codex", "scope": "general_weekly"},
            ]}), encoding="utf-8")

            cache = QuotaCache(path, now=lambda: 1_100)

            self.assertEqual(cache.latest("codex", "general_weekly"), valid)

    def test_load_ignores_sibling_with_unhashable_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            valid = self.record()
            malformed = dict(valid.__dict__, provider=["codex"])
            path.write_text(json.dumps({"v": 1, "records": [
                valid.__dict__, malformed,
            ]}), encoding="utf-8")

            cache = QuotaCache(path, now=lambda: 1_100)

            self.assertEqual(cache.latest("codex", "general_weekly"), valid)

    def test_put_rejects_huge_integer_percentage(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")

            self.assertFalse(cache.put(self.record(pct=HUGE_INT)))

    def test_load_ignores_sibling_with_huge_integer_percentage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            valid = self.record()
            malformed = dict(valid.__dict__, pct=HUGE_INT)
            path.write_text(json.dumps({"v": 1, "records": [
                valid.__dict__, malformed,
            ]}), encoding="utf-8")

            cache = QuotaCache(path, now=lambda: 1_100)

            self.assertEqual(cache.latest("codex", "general_weekly"), valid)

    def test_latest_rejects_huge_integer_now(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")
            self.assertTrue(cache.put(self.record()))

            self.assertIsNone(cache.latest(
                "codex", "general_weekly", now=HUGE_INT))

    def test_rejects_invalid_values(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")
            invalid = (
                self.record(provider="unknown"),
                self.record(scope="weekly"),
                self.record(identity="bad\nidentity"),
                self.record(identity="x" * 129),
                self.record(pct=float("nan")),
                self.record(pct=101),
                self.record(reset_at=-1),
                self.record(observed_at=True),
                self.record(label=123),
                self.record(label="x" * 129),
                self.record(label="Work\naccount"),
            )

            for record in invalid:
                with self.subTest(record=record):
                    self.assertFalse(cache.put(record))
            self.assertIsNone(cache.latest("codex", "general_weekly", now=1))

    def test_label_accepts_printable_utf8(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")

            self.assertTrue(cache.put(self.record(label="FABLE · WEEK")))

    def test_expired_loaded_records_are_pruned_on_next_successful_put(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            expired = self.record(identity="expired", reset_at=1_000)
            fresh = self.record(identity="fresh", reset_at=2_000)
            original_text = json.dumps({"v": 1, "records": [
                expired.__dict__, fresh.__dict__,
            ]})
            path.write_text(original_text, encoding="utf-8")

            cache = QuotaCache(path, now=lambda: 1_000)

            self.assertEqual(path.read_text(encoding="utf-8"), original_text)
            self.assertTrue(cache.put(self.record(
                identity="new", reset_at=3_000, observed_at=1_100)))
            identities = {
                record["identity"]
                for record in json.loads(path.read_text(
                    encoding="utf-8"))["records"]
            }
            self.assertEqual(identities, {"fresh", "new"})

    def test_concurrent_puts_preserve_both_identity_snapshots(self):
        class CoordinatedRecords(dict):
            def __init__(self):
                super().__init__()
                self.guard = threading.Lock()
                self.first_get = threading.Event()
                self.second_get = threading.Event()

            def get(self, key, default=None):
                with self.guard:
                    if not self.first_get.is_set():
                        self.first_get.set()
                        wait_for_second = True
                    else:
                        self.second_get.set()
                        wait_for_second = False
                if wait_for_second:
                    self.second_get.wait(0.25)
                return super().get(key, default)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            cache = QuotaCache(path)
            cache._records = CoordinatedRecords()
            start = threading.Barrier(3)
            results = []

            def writer(record):
                start.wait()
                results.append(cache.put(record))

            threads = [
                threading.Thread(target=writer, args=(self.record(
                    identity="account-a", observed_at=100),)),
                threading.Thread(target=writer, args=(self.record(
                    identity="account-b", observed_at=200),)),
            ]
            for thread in threads:
                thread.start()
            start.wait()
            for thread in threads:
                thread.join(timeout=2)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(results, [True, True])
            self.assertEqual(
                {record.identity for record in cache._records.values()},
                {"account-a", "account-b"})
            persisted = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(
                {record["identity"] for record in persisted["records"]},
                {"account-a", "account-b"})
            restarted = QuotaCache(path, now=lambda: 300)
            self.assertEqual(
                {record.identity for record in restarted._records.values()},
                {"account-a", "account-b"})

    def test_failed_atomic_replace_restores_memory_and_keeps_disk_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            cache = QuotaCache(path)
            original = self.record(pct=40, observed_at=100)
            self.assertTrue(cache.put(original))
            on_disk = path.read_text(encoding="utf-8")

            with mock.patch("tools.tokenserver.quota_cache.os.replace",
                            side_effect=OSError("replace failed")):
                self.assertFalse(cache.put(self.record(pct=46, observed_at=200)))

            self.assertEqual(cache.latest("codex", "general_weekly", now=300),
                             original)
            self.assertEqual(path.read_text(encoding="utf-8"), on_disk)

    def test_fsyncs_temp_before_replace_and_directory_after(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            cache = QuotaCache(path)
            events = []
            directory_fds = set()
            real_fsync = os.fsync
            real_replace = os.replace
            real_close = os.close

            def observed_fsync(fd):
                is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                if is_directory:
                    directory_fds.add(fd)
                events.append("directory-fsync" if is_directory
                              else "temp-fsync")
                return real_fsync(fd)

            def observed_replace(source, destination):
                events.append("replace")
                return real_replace(source, destination)

            def observed_close(fd):
                if fd in directory_fds:
                    events.append("directory-close")
                return real_close(fd)

            with mock.patch("tools.tokenserver.quota_cache.os.fsync",
                            side_effect=observed_fsync), mock.patch(
                    "tools.tokenserver.quota_cache.os.replace",
                    side_effect=observed_replace), mock.patch(
                    "tools.tokenserver.quota_cache.os.close",
                    side_effect=observed_close):
                self.assertTrue(cache.put(self.record()))

            self.assertEqual(events, [
                "temp-fsync", "replace", "directory-fsync",
                "directory-close",
            ])

    def test_directory_fsync_failure_rolls_back_memory_and_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            cache = QuotaCache(path)
            original = self.record(pct=40, observed_at=100)
            self.assertTrue(cache.put(original))
            on_disk = path.read_text(encoding="utf-8")
            real_fsync = os.fsync
            failed_directory_fsync = False

            def fail_first_directory_fsync(fd):
                nonlocal failed_directory_fsync
                if (stat.S_ISDIR(os.fstat(fd).st_mode) and
                        not failed_directory_fsync):
                    failed_directory_fsync = True
                    raise OSError("directory fsync failed")
                return real_fsync(fd)

            with mock.patch("tools.tokenserver.quota_cache.os.fsync",
                            side_effect=fail_first_directory_fsync):
                self.assertFalse(cache.put(self.record(
                    pct=46, observed_at=200)))

            self.assertTrue(failed_directory_fsync)
            self.assertEqual(cache.latest("codex", "general_weekly", now=300),
                             original)
            self.assertEqual(path.read_text(encoding="utf-8"), on_disk)

    def test_directory_fsync_rollback_restores_exact_prior_file_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            original = self.record(pct=40, observed_at=100)
            prior_bytes = json.dumps(
                {"v": 1, "records": [original.__dict__]},
                indent=2).encode("utf-8")
            path.write_bytes(prior_bytes)
            cache = QuotaCache(path, now=lambda: 300)
            real_fsync = os.fsync
            failed_directory_fsync = False

            def fail_first_directory_fsync(fd):
                nonlocal failed_directory_fsync
                if (stat.S_ISDIR(os.fstat(fd).st_mode) and
                        not failed_directory_fsync):
                    failed_directory_fsync = True
                    raise OSError("directory fsync failed")
                return real_fsync(fd)

            with mock.patch("tools.tokenserver.quota_cache.os.fsync",
                            side_effect=fail_first_directory_fsync):
                self.assertFalse(cache.put(self.record(
                    pct=46, observed_at=200)))

            self.assertEqual(path.read_bytes(), prior_bytes)

    def test_failed_write_restores_memory_and_keeps_disk_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            cache = QuotaCache(path)
            original = self.record(pct=40, observed_at=100)
            self.assertTrue(cache.put(original))
            on_disk = path.read_text(encoding="utf-8")

            with mock.patch("tools.tokenserver.quota_cache.json.dump",
                            side_effect=OSError("write failed")):
                self.assertFalse(cache.put(self.record(pct=46, observed_at=200)))

            self.assertEqual(cache.latest("codex", "general_weekly", now=300),
                             original)
            self.assertEqual(path.read_text(encoding="utf-8"), on_disk)

    def test_unencodable_label_is_rejected_without_changing_state(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            cache = QuotaCache(path)
            original = self.record(pct=40, observed_at=100)
            self.assertTrue(cache.put(original))
            on_disk = path.read_text(encoding="utf-8")

            self.assertFalse(cache.put(self.record(
                pct=46, observed_at=200, label="bad\ud800label")))

            self.assertEqual(cache.latest("codex", "general_weekly", now=300),
                             original)
            self.assertEqual(path.read_text(encoding="utf-8"), on_disk)

    def test_serialization_failures_restore_memory_and_disk(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            cache = QuotaCache(path)
            original = self.record(pct=40, observed_at=100)
            self.assertTrue(cache.put(original))
            on_disk = path.read_text(encoding="utf-8")

            for error in (UnicodeError("encoding failed"),
                          TypeError("not serializable"),
                          ValueError("invalid value")):
                with self.subTest(error=type(error).__name__), mock.patch(
                        "tools.tokenserver.quota_cache.json.dump",
                        side_effect=error):
                    self.assertFalse(cache.put(self.record(
                        pct=46, observed_at=200)))
                    self.assertEqual(cache.latest(
                        "codex", "general_weekly", now=300), original)
                    self.assertEqual(path.read_text(encoding="utf-8"), on_disk)

    def test_latest_deterministically_uses_greatest_observed_at(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = QuotaCache(Path(directory) / "quota.json")
            older = self.record(identity="account-a", observed_at=100)
            newest = self.record(identity="account-b", observed_at=200)
            self.assertTrue(cache.put(newest))
            self.assertTrue(cache.put(older))

            self.assertEqual(cache.latest("codex", "general_weekly", now=300),
                             newest)

    def test_persists_only_allowlisted_privacy_safe_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "quota.json"
            self.assertTrue(QuotaCache(path).put(self.record()))

            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload), {"v", "records"})
            self.assertEqual(payload["v"], 1)
            self.assertEqual(set(payload["records"][0]), {
                "provider", "scope", "identity", "pct", "reset_at",
                "observed_at", "label",
            })
            self.assertNotIn("accessToken", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
