"""Tests for the Codex month-to-date value scan.

Most fixtures here are synthesised from shapes read out of the Codex source
rather than captured from a live rollout:

* ``codex-rs/history/src/rollout_payload.rs`` -- the wire tags, snake_case,
  ``turn_context`` and ``event_msg``.
* ``codex-rs/protocol/src/protocol.rs`` -- ``TurnContextItem.model``,
  ``TokenCountEvent {info, rate_limits}``, ``TokenUsageInfo
  {total_token_usage, last_token_usage, model_context_window}``.

That provenance is the reason those tests are worth having and also their
limit: they prove the reader handles the documented shape, not that a real
rollout matches it byte for byte -- which is exactly how the resume-replay bug
slipped through. So ``REAL_USAGE`` below is captured from a real ``~/.codex``
rollout and scrubbed to numeric usage + model id only (no timestamps, ids or
message text ever left the machine), and the tests that reproduce the
overcounting bug are built on it. The project's integrity contract -- no prompt
or message leaves the box -- holds for fixtures too.
"""

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from tools.tokenserver import codex_usage, value_meter


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


# 1M fresh input at the fixed fixture's $5.00/M rate.
ONE_MILLION_FRESH = {"input_tokens": 1_000_000, "cached_input_tokens": 0,
                     "cache_write_input_tokens": 0, "output_tokens": 0,
                     "reasoning_output_tokens": 0, "total_tokens": 1_000_000}


def session_meta(session_id, file_id=None, forked_from=None, stamp=None):
    """A rollout's opening ``session_meta`` line.

    A resume, fork or subagent spawn keeps the parent's ``session_id`` while
    taking a fresh file ``id`` (and, for a fork, a ``forked_from_id``). The
    value scan groups on ``session_id``, so that is the field that matters.
    """
    payload = {"session_id": session_id, "id": file_id or session_id,
               "cwd": "/w", "originator": "test", "cli_version": "0"}
    if forked_from is not None:
        payload["forked_from_id"] = forked_from
    return json.dumps({
        "type": "session_meta",
        "timestamp": (stamp or datetime.now().astimezone()).isoformat(),
        "payload": payload,
    }) + "\n"


# --- Real rollout usage, scrubbed to numbers + model id --------------------
# Captured from a real ~/.codex rollout (the first six ``last_token_usage``
# blocks of one gpt-5.6-sol conversation) and stripped to numeric usage fields
# only: no timestamps, no ids, no message text -- nothing that could carry a
# prompt off the machine. Real numbers are the point: they carry real
# ``cached_input_tokens``, and it was fixtures synthesised from the source --
# which never modelled resume replay -- that let the overcounting bug ship.
REAL_MODEL = "gpt-5.6-sol"
REAL_USAGE = [
    {"input_tokens": 21095, "cached_input_tokens": 11008,
     "cache_write_input_tokens": 0, "output_tokens": 489,
     "reasoning_output_tokens": 234, "total_tokens": 21584},
    {"input_tokens": 27213, "cached_input_tokens": 20224,
     "cache_write_input_tokens": 0, "output_tokens": 632,
     "reasoning_output_tokens": 191, "total_tokens": 27845},
    {"input_tokens": 32596, "cached_input_tokens": 26368,
     "cache_write_input_tokens": 0, "output_tokens": 496,
     "reasoning_output_tokens": 217, "total_tokens": 33092},
    {"input_tokens": 34031, "cached_input_tokens": 31488,
     "cache_write_input_tokens": 0, "output_tokens": 557,
     "reasoning_output_tokens": 62, "total_tokens": 34588},
    {"input_tokens": 47198, "cached_input_tokens": 33536,
     "cache_write_input_tokens": 0, "output_tokens": 455,
     "reasoning_output_tokens": 165, "total_tokens": 47653},
    {"input_tokens": 60629, "cached_input_tokens": 46848,
     "cache_write_input_tokens": 0, "output_tokens": 225,
     "reasoning_output_tokens": 102, "total_tokens": 60854},
]
# Priced once through the fixed August fixture (gpt-5.6-sol: $5 in / $30 out /
# $0.50 cache-read, cache_included_input). The cache-read rate matters: at full
# input price these same rows cost $1.20, 2.75x more -- so this also guards the
# cached discount. Tokens counted = sum(input + output + cache_write).
REAL_USAGE_USD = 0.436806
REAL_USAGE_TOKENS = 225_616


def real_rollout(session_id, file_id=None, forked_from=None, stamp=None):
    """A whole rollout for one conversation: session_meta + model + real usage.

    Passing ``file_id``/``forked_from`` makes it a *replay* of ``session_id`` --
    exactly what ``codex resume``/fork writes: same conversation, new file, the
    parent's token_count history re-emitted.
    """
    body = [session_meta(session_id, file_id, forked_from, stamp),
            turn_context(REAL_MODEL, stamp)]
    body += [token_count(usage, stamp=stamp) for usage in REAL_USAGE]
    return "".join(body)


class CodexUsageScanTest(unittest.TestCase):

    def setUp(self):
        codex_usage.reset_cache()
        fixture = Path(__file__).resolve().parents[2] / "test/fixtures/codex-prices.json"
        table = value_meter.PriceTable(json.loads(fixture.read_text()))
        patcher = mock.patch.object(value_meter, "_default_table", table)
        patcher.start()
        self.addCleanup(patcher.stop)
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

    def test_default_sessions_dir_honors_codex_home(self):
        sessions = self.root / "custom-codex-home" / "sessions"
        sessions.mkdir(parents=True)
        (sessions / "rollout-a.jsonl").write_text(
            turn_context() + token_count(ONE_MILLION_FRESH))

        with mock.patch.dict(
                os.environ, {"CODEX_HOME": str(sessions.parent)}):
            usd, priced, unpriced = codex_usage.month_value()

        self.assertAlmostEqual(usd, 5.00)
        self.assertEqual(priced, 1_000_000)
        self.assertEqual(unpriced, 0)

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

    # -- real data, and the resume-replay overcounting it exposes -----------

    def test_real_rollout_rows_price_with_the_cache_discount(self):
        """One real (scrubbed) conversation prices to its known figure.

        Real ``cached_input_tokens`` are the point: charged at the cache-read
        rate, not full input. This is the fixture the synthesised ones lacked.
        """
        self.write("rollout-real.jsonl", real_rollout("sess-A"))
        usd, priced, unpriced = codex_usage.month_value(self.root)
        self.assertEqual(unpriced, 0)
        self.assertEqual(priced, REAL_USAGE_TOKENS)
        self.assertAlmostEqual(usd, REAL_USAGE_USD, places=4)

    def test_a_resumed_session_replay_is_counted_once(self):
        """The shipped bug: a resume/fork replays the parent history under the
        same ``session_id`` and every replay was summed.

        The panel read $8 296 because one conversation's history lived across
        113 rollout files. Here the same conversation is replayed into three
        files (an original, a resume, a fork); the month must equal that
        conversation counted ONCE -- not three times.
        """
        self.write("2026/08/14/rollout-original.jsonl", real_rollout("sess-A"))
        self.write("2026/08/14/rollout-resume.jsonl",
                   real_rollout("sess-A", file_id="file-2"))
        self.write("2026/08/14/rollout-fork.jsonl",
                   real_rollout("sess-A", file_id="file-3", forked_from="sess-A"))
        usd, priced, unpriced = codex_usage.month_value(self.root)
        self.assertEqual(priced, REAL_USAGE_TOKENS)   # once, not 3x
        self.assertEqual(unpriced, 0)
        self.assertAlmostEqual(usd, REAL_USAGE_USD, places=4)

    def test_the_most_complete_rollout_of_a_session_wins(self):
        """A resume that replayed everything AND added a turn is the fuller
        file; the conversation is counted from it, not from the shorter one."""
        self.write("rollout-original.jsonl", real_rollout("sess-A"))
        fuller = real_rollout("sess-A", file_id="file-2") \
            + token_count(ONE_MILLION_FRESH)   # model still gpt-5.6-sol
        self.write("rollout-resume.jsonl", fuller)
        usd, priced, _ = codex_usage.month_value(self.root)
        self.assertEqual(priced, REAL_USAGE_TOKENS + 1_000_000)
        self.assertAlmostEqual(usd, REAL_USAGE_USD + 5.00, places=4)

    def test_distinct_sessions_are_both_counted(self):
        """Grouping must not over-reach: two different conversations stay two."""
        self.write("rollout-a.jsonl", real_rollout("sess-A"))
        self.write("rollout-b.jsonl", real_rollout("sess-B"))
        usd, priced, _ = codex_usage.month_value(self.root)
        self.assertEqual(priced, REAL_USAGE_TOKENS * 2)
        self.assertAlmostEqual(usd, REAL_USAGE_USD * 2, places=4)

    def test_rollouts_without_session_meta_each_stand_alone(self):
        """No ``session_meta`` -> no grouping key. A torn or pre-meta rollout
        must not collapse into another meta-less one: two are still two."""
        self.write("rollout-a.jsonl",
                   turn_context() + token_count(ONE_MILLION_FRESH))
        self.write("rollout-b.jsonl",
                   turn_context() + token_count(ONE_MILLION_FRESH))
        self.assertAlmostEqual(codex_usage.month_value(self.root)[0], 10.00)

    def test_session_id_persists_across_incremental_reads(self):
        """``session_meta`` is only at the top of the file. A token_count
        appended later must still attribute to the conversation, and its replay
        sibling must still be deduped, on the next incremental scan."""
        path = self.write("rollout-original.jsonl", real_rollout("sess-A"))
        codex_usage.month_value(self.root)
        # A sibling that replayed the same history under the same session_id.
        self.write("rollout-resume.jsonl",
                   real_rollout("sess-A", file_id="file-2"))
        # And one genuinely new turn appended to the original (no new meta).
        with path.open("a") as handle:
            handle.write(token_count(ONE_MILLION_FRESH))
        usd, priced, _ = codex_usage.month_value(self.root)
        self.assertEqual(priced, REAL_USAGE_TOKENS + 1_000_000)  # sibling dropped
        self.assertAlmostEqual(usd, REAL_USAGE_USD + 5.00, places=4)

    def test_session_id_helper_accepts_only_session_meta(self):
        from tools.tokenserver import codex_rollout
        meta = json.loads(session_meta("sess-A", file_id="file-2",
                                        forked_from="sess-A"))
        # A fork keeps the PARENT session_id, not the file id.
        self.assertEqual(codex_rollout.codex_rollout_session_id(meta), "sess-A")
        self.assertIsNone(
            codex_rollout.codex_rollout_session_id(json.loads(turn_context())))
        self.assertIsNone(codex_rollout.codex_rollout_session_id(
            {"type": "session_meta", "payload": {"id": "x"}}))
        self.assertIsNone(codex_rollout.codex_rollout_session_id("nope"))


if __name__ == "__main__":
    unittest.main()
