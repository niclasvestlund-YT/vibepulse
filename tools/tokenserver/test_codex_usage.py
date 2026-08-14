"""Tests for the Codex month-to-date value scan.

There is no Codex install on the build machine, so every fixture here is
synthesised from shapes read out of the Codex source rather than captured from
a live rollout:

* ``codex-rs/history/src/rollout_payload.rs`` -- the wire tags, snake_case,
  ``turn_context`` and ``event_msg``.
* ``codex-rs/protocol/src/protocol.rs`` -- ``TurnContextItem.model``,
  ``TokenCountEvent {info, rate_limits}``, ``TokenUsageInfo
  {total_token_usage, last_token_usage, model_context_window}``.

That provenance is the reason these tests are worth having and also their
limit: they prove the reader handles the documented shape, not that a real
rollout matches it byte for byte. Confirm against a real ~/.codex before
trusting the Codex half of the figure.
"""

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from tools.tokenserver import codex_usage


def turn_context(model="gpt-5.6-sol", stamp=None):
    return json.dumps({
        "type": "turn_context",
        "timestamp": (stamp or datetime.now().astimezone()).isoformat(),
        "payload": {"model": model, "cwd": "/w", "approval_policy": "on-request",
                    "sandbox_policy": "workspace-write"},
    }) + "\n"


def token_count(last, total=None, stamp=None):
    """A token_count event. ``total`` defaults to something LARGER than
    ``last`` so a reader that wrongly sums totals is caught by the numbers."""
    return json.dumps({
        "type": "event_msg",
        "timestamp": (stamp or datetime.now().astimezone()).isoformat(),
        "payload": {
            "type": "token_count",
            "info": {
                "last_token_usage": last,
                "total_token_usage": total or {k: v * 10 for k, v in last.items()},
                "model_context_window": 400_000,
            },
            "rate_limits": None,
        },
    }) + "\n"


# 1M fresh input on GPT-5.6 Sol at $5.00/M.
ONE_MILLION_FRESH = {"input_tokens": 1_000_000, "cached_input_tokens": 0,
                     "cache_write_input_tokens": 0, "output_tokens": 0,
                     "reasoning_output_tokens": 0, "total_tokens": 1_000_000}


class CodexUsageScanTest(unittest.TestCase):

    def setUp(self):
        codex_usage.reset_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        codex_usage.reset_cache()
        self.tmp.cleanup()

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_missing_sessions_dir_is_zero_not_an_error(self):
        self.assertEqual(
            codex_usage.month_value(self.root / "nope"), (0.0, 0, 0))

    def test_prices_a_turn_against_the_preceding_turn_context(self):
        self.write("rollout-a.jsonl",
                   turn_context() + token_count(ONE_MILLION_FRESH))
        usd, priced, unpriced = codex_usage.month_value(self.root)
        self.assertAlmostEqual(usd, 5.00)
        self.assertEqual(priced, 1_000_000)
        self.assertEqual(unpriced, 0)

    def test_totals_are_never_summed_only_last_usage(self):
        """total_token_usage is cumulative; summing it multiplies the bill.

        Each event's total here is 10x its last usage, so a reader that used
        totals would report $50 for three events instead of $15.
        """
        self.write("rollout-a.jsonl", turn_context()
                   + token_count(ONE_MILLION_FRESH)
                   + token_count(ONE_MILLION_FRESH)
                   + token_count(ONE_MILLION_FRESH))
        usd, _, _ = codex_usage.month_value(self.root)
        self.assertAlmostEqual(usd, 15.00)

    def test_usage_before_any_turn_context_is_unpriced_not_dropped(self):
        self.write("rollout-a.jsonl", token_count(ONE_MILLION_FRESH))
        usd, priced, unpriced = codex_usage.month_value(self.root)
        self.assertEqual(usd, 0.0)
        self.assertEqual(priced, 0)
        self.assertEqual(unpriced, 1_000_000)

    def test_a_later_turn_context_switches_the_model(self):
        self.write("rollout-a.jsonl",
                   turn_context("gpt-5.6-sol") + token_count(ONE_MILLION_FRESH)
                   + turn_context("gpt-5.6-luna") + token_count(ONE_MILLION_FRESH))
        usd, _, _ = codex_usage.month_value(self.root)
        self.assertAlmostEqual(usd, 5.00 + 0.20)  # Sol then Luna

    def test_rows_older_than_the_month_are_skipped(self):
        old = datetime.now().astimezone().replace(day=1) - timedelta(days=5)
        self.write("rollout-a.jsonl",
                   turn_context(stamp=old)
                   + token_count(ONE_MILLION_FRESH, stamp=old))
        usd, _, _ = codex_usage.month_value(self.root)
        self.assertEqual(usd, 0.0)

    def test_unknown_model_is_unpriced(self):
        self.write("rollout-a.jsonl",
                   turn_context("gpt-9-unreleased")
                   + token_count(ONE_MILLION_FRESH))
        usd, priced, unpriced = codex_usage.month_value(self.root)
        self.assertEqual((usd, priced), (0.0, 0))
        self.assertEqual(unpriced, 1_000_000)

    def test_rate_limit_only_events_contribute_nothing(self):
        """The shape the server already reads for quota carries no info."""
        line = json.dumps({
            "type": "event_msg",
            "timestamp": datetime.now().astimezone().isoformat(),
            "payload": {"type": "token_count", "rate_limits": {"primary": {}}},
        }) + "\n"
        self.write("rollout-a.jsonl", turn_context() + line)
        self.assertEqual(codex_usage.month_value(self.root), (0.0, 0, 0))

    def test_incremental_read_keeps_the_model_across_chunks(self):
        """A turn_context in an earlier chunk still names the later model.

        Without carrying it, every appended turn after the first read would
        price as unknown and the multiple would decay as the session grew.
        """
        path = self.write("rollout-a.jsonl", turn_context())
        codex_usage.month_value(self.root)
        with path.open("a") as handle:
            handle.write(token_count(ONE_MILLION_FRESH))
        usd, priced, unpriced = codex_usage.month_value(self.root)
        self.assertAlmostEqual(usd, 5.00)
        self.assertEqual(unpriced, 0)

    def test_a_half_written_row_is_reread_not_lost(self):
        path = self.write("rollout-a.jsonl", turn_context())
        codex_usage.month_value(self.root)
        complete = token_count(ONE_MILLION_FRESH)
        with path.open("a") as handle:
            handle.write(complete[:20])          # torn write
        self.assertEqual(codex_usage.month_value(self.root)[0], 0.0)
        with path.open("a") as handle:
            handle.write(complete[20:])          # writer finishes the row
        self.assertAlmostEqual(codex_usage.month_value(self.root)[0], 5.00)

    def test_malformed_lines_are_skipped(self):
        self.write("rollout-a.jsonl",
                   "{ not json\n" + turn_context()
                   + "\n" + token_count(ONE_MILLION_FRESH))
        self.assertAlmostEqual(codex_usage.month_value(self.root)[0], 5.00)

    def test_nested_session_directories_are_found(self):
        self.write("2026/08/14/rollout-a.jsonl",
                   turn_context() + token_count(ONE_MILLION_FRESH))
        self.assertAlmostEqual(codex_usage.month_value(self.root)[0], 5.00)

    def test_deleted_files_leave_the_cache(self):
        path = self.write("rollout-a.jsonl",
                          turn_context() + token_count(ONE_MILLION_FRESH))
        self.assertAlmostEqual(codex_usage.month_value(self.root)[0], 5.00)
        path.unlink()
        self.assertEqual(codex_usage.month_value(self.root), (0.0, 0, 0))


if __name__ == "__main__":
    unittest.main()
