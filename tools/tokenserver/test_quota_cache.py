import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver.quota_cache import CachedQuota, QuotaCache


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
            )

            for record in invalid:
                with self.subTest(record=record):
                    self.assertFalse(cache.put(record))
            self.assertIsNone(cache.latest("codex", "general_weekly", now=1))

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
