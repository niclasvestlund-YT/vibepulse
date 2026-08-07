import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver import tokenserver


class ClaudeLimitHeaderTests(unittest.TestCase):
    def _headers(self, model_bucket):
        return {
            "anthropic-ratelimit-unified-5h-utilization": "0.12",
            "anthropic-ratelimit-unified-5h-reset": "3600",
            "anthropic-ratelimit-unified-7d-utilization": "0.47",
            "anthropic-ratelimit-unified-7d-reset": "7200",
            f"anthropic-ratelimit-unified-{model_bucket}-utilization": "0.73",
            f"anthropic-ratelimit-unified-{model_bucket}-reset": "10800",
        }

    def test_named_model_week_buckets_keep_their_real_label(self):
        cases = {
            "7d_fable": "FABLE · VECKA",
            "7d_opus": "OPUS · VECKA",
            "7d_sonnet": "SONNET · VECKA",
        }

        for bucket, expected in cases.items():
            with self.subTest(bucket=bucket):
                parsed = tokenserver._parse_limit_headers(
                    self._headers(bucket), now_ts=1_800_000_000)
                self.assertEqual(parsed["modelLabel"], expected)
                self.assertEqual(parsed["modelPct"], 73.0)

    def test_generic_model_week_has_no_invented_model_label(self):
        parsed = tokenserver._parse_limit_headers(
            self._headers("7d_model"), now_ts=1_800_000_000)

        self.assertEqual(parsed["modelPct"], 73.0)
        self.assertNotIn("modelLabel", parsed)

    def test_generic_week_does_not_become_a_model_week(self):
        headers = {
            "anthropic-ratelimit-unified-5h-utilization": "12",
            "anthropic-ratelimit-unified-7d-utilization": "47",
        }

        parsed = tokenserver._parse_limit_headers(
            headers, now_ts=1_800_000_000)

        self.assertEqual(parsed["weekPct"], 47.0)
        self.assertNotIn("modelPct", parsed)
        self.assertNotIn("modelLabel", parsed)

    def test_snapshot_never_infers_label_from_active_agent_model(self):
        base = {
            "v": 1,
            "dayTokens": 0,
            "dayTokensPerHour": 0,
            "daySessions": 0,
            "monthTokens": 0,
            "at": "2026-08-07T10:00:00+02:00",
        }
        with mock.patch.object(tokenserver, "_compute", return_value=base), \
                mock.patch.object(tokenserver, "get_limits", return_value={
                    "sessionPct": 12.0,
                    "modelPct": 73.0,
                }), \
                mock.patch.object(tokenserver, "_read_codex_limits",
                                  return_value={}):
            tokenserver._last_result = None
            tokenserver._last_computed = 0.0
            snapshot = tokenserver.get_snapshot(Path("/unused"))

        self.assertEqual(snapshot["claudeModelWeekPct"], 73.0)
        self.assertIsNone(snapshot["claudeModelWeekLabel"])


if __name__ == "__main__":
    unittest.main()
