"""Tests for the price-table generator.

The generator is now the thing that decides what every model costs, so its
conversion rules are worth pinning. The failure it exists to prevent is a
silent one: a rate that is wrong by a factor of a million, or a model quietly
dropped, still produces a table that loads and prices happily.

No network here -- every test generates from an inline catalogue fragment
shaped like the real one.
"""

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.tokenserver import update_prices, value_meter


def catalogue(**models):
    return dict(models)


ANTHROPIC_ENTRY = {
    "litellm_provider": "anthropic",
    "mode": "chat",
    "input_cost_per_token": 5e-06,
    "output_cost_per_token": 2.5e-05,
    "cache_read_input_token_cost": 5e-07,
    "cache_creation_input_token_cost": 6.25e-06,
    "cache_creation_input_token_cost_above_1hr": 1e-05,
    "max_input_tokens": 200000,
    "supports_vision": True,
}


class ConvertTest(unittest.TestCase):

    def test_per_token_costs_become_per_million(self):
        out = update_prices.convert(catalogue(**{"m": dict(ANTHROPIC_ENTRY)}))
        self.assertEqual(out["anthropic"]["m"], {
            "input": 5.0, "output": 25.0, "cache_read": 0.5,
            "cache_write_5m": 6.25, "cache_write_1h": 10.0})

    def test_float_noise_is_rounded_away(self):
        """2e-07 x 1e6 is 0.19999999999999998 in binary floating point.

        Committing that would put noise in the file and in every future diff.
        """
        entry = dict(ANTHROPIC_ENTRY, input_cost_per_token=2e-07,
                     output_cost_per_token=1e-06)
        out = update_prices.convert(catalogue(**{"m": entry}))
        self.assertEqual(out["anthropic"]["m"]["input"], 0.2)

    def test_only_rate_fields_are_carried_over(self):
        """Context windows and capability flags are not this file's job."""
        out = update_prices.convert(catalogue(**{"m": dict(ANTHROPIC_ENTRY)}))
        self.assertEqual(set(out["anthropic"]["m"]),
                         {"input", "output", "cache_read",
                          "cache_write_5m", "cache_write_1h"})

    def test_a_model_priced_on_input_alone_is_dropped(self):
        """Half a price is worse than none: it would look ~5x too cheap."""
        entry = dict(ANTHROPIC_ENTRY)
        del entry["output_cost_per_token"]
        out = update_prices.convert(catalogue(**{"m": entry}))
        self.assertEqual(out["anthropic"], {})

    def test_non_chat_modes_are_skipped(self):
        for mode in ("embedding", "audio_transcription", "image_generation"):
            entry = dict(ANTHROPIC_ENTRY, mode=mode)
            out = update_prices.convert(catalogue(**{"m": entry}))
            self.assertEqual(out["anthropic"], {}, mode)

    def test_responses_mode_is_kept(self):
        """Codex models are mode 'responses', not 'chat'."""
        entry = dict(ANTHROPIC_ENTRY, litellm_provider="openai",
                     mode="responses")
        out = update_prices.convert(catalogue(**{"m": entry}))
        self.assertIn("m", out["openai"])

    def test_unsupported_providers_are_ignored(self):
        entry = dict(ANTHROPIC_ENTRY, litellm_provider="bedrock")
        out = update_prices.convert(catalogue(**{"m": entry}))
        self.assertEqual(out, {"anthropic": {}, "openai": {}})

    def test_junk_entries_do_not_crash_the_generator(self):
        out = update_prices.convert(catalogue(
            good=dict(ANTHROPIC_ENTRY), bad="not a dict",
            weird=dict(ANTHROPIC_ENTRY, input_cost_per_token="free"),
            negative=dict(ANTHROPIC_ENTRY, output_cost_per_token=-1)))
        self.assertEqual(list(out["anthropic"]), ["good"])

    def test_models_are_sorted_for_a_stable_diff(self):
        out = update_prices.convert(catalogue(
            zeta=dict(ANTHROPIC_ENTRY), alpha=dict(ANTHROPIC_ENTRY)))
        self.assertEqual(list(out["anthropic"]), ["alpha", "zeta"])


class BlobShaTest(unittest.TestCase):

    def test_matches_git_hash_object(self):
        """The pin must be checkable by anyone with git and the URL."""
        raw = b'{"hello": "world"}\n'
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "f.json"
            path.write_bytes(raw)
            expected = subprocess.run(
                ["git", "hash-object", str(path)],
                capture_output=True, text=True, check=True).stdout.strip()
        self.assertEqual(update_prices.blob_sha(raw), expected)


class BuildTest(unittest.TestCase):

    def setUp(self):
        self.doc = update_prices.build(
            catalogue(**{"m": dict(ANTHROPIC_ENTRY)}), "a" * 40, "2026-08-14")

    def test_records_its_source(self):
        self.assertEqual(self.doc["source"]["blob_sha"], "a" * 40)
        self.assertEqual(self.doc["source"]["generated"], "2026-08-14")
        self.assertIn("litellm", self.doc["source"]["url"])

    def test_accounting_modes_are_declared_per_provider(self):
        """The one fact the catalogue cannot supply. Guessing mis-bills."""
        providers = self.doc["providers"]
        self.assertEqual(providers["anthropic"]["accounting"],
                         "cache_excluded_input")
        self.assertEqual(providers["openai"]["accounting"],
                         "cache_included_input")

    def test_output_loads_as_a_price_table(self):
        table = value_meter.PriceTable(self.doc)
        self.assertTrue(table.knows("m"))
        self.assertEqual(table.as_of(), "2026-08-14")

    def test_render_is_deterministic(self):
        again = update_prices.build(
            catalogue(**{"m": dict(ANTHROPIC_ENTRY)}), "a" * 40, "2026-08-14")
        self.assertEqual(update_prices.render(self.doc),
                         update_prices.render(again))


class CheckModeTest(unittest.TestCase):
    """--check is what stops the committed file drifting from the generator."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.source = Path(self.tmp.name) / "cat.json"
        self.source.write_text(json.dumps(
            catalogue(**{"m": dict(ANTHROPIC_ENTRY)})))
        self.out = Path(self.tmp.name) / "prices.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_tool(self, *extra):
        # The tool reports to stdout for humans; swallow it so "out of date"
        # does not read as a failure in the middle of a passing test run.
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return update_prices.main(
                ["--from", str(self.source), "--out", str(self.out), *extra])

    def test_generated_file_passes_its_own_check(self):
        self.assertEqual(self.run_tool(), 0)
        self.assertEqual(self.run_tool("--check"), 0)

    def test_check_fails_when_a_rate_was_hand_edited(self):
        self.run_tool()
        doc = json.loads(self.out.read_text())
        doc["providers"]["anthropic"]["models"]["m"]["input"] = 99.0
        # Rendered the same way the generator would, so the rate is the only
        # difference and the check cannot pass on formatting alone.
        self.out.write_text(update_prices.render(doc))
        self.assertEqual(self.run_tool("--check"), 1)

    def test_check_fails_when_the_catalogue_moved_on(self):
        self.run_tool()
        self.source.write_text(json.dumps(catalogue(**{
            "m": dict(ANTHROPIC_ENTRY, input_cost_per_token=9e-06)})))
        self.assertEqual(self.run_tool("--check"), 1)

    def test_check_does_not_fail_merely_because_a_day_passed(self):
        """Only rate changes should ever show up as drift."""
        self.run_tool()
        doc = json.loads(self.out.read_text())
        doc["source"]["generated"] = "2020-01-01"
        self.out.write_text(update_prices.render(doc))
        self.assertEqual(self.run_tool("--check"), 0)


if __name__ == "__main__":
    unittest.main()
