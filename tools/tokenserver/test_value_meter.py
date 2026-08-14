"""Tests for the value multiple.

Every expected dollar figure here is computed by hand in the docstring or a
comment and written as a literal. That is deliberate: deriving the expectation
from the same rate table the implementation reads would make the test pass
even if the table were wrong, which is exactly the failure this feature
cannot afford.
"""

import json
import unittest

from tools.tokenserver import value_meter


# A real assistant record, copied from a live transcript. Its shape is the
# whole argument for the feature: 2 input and 4 output tokens against 8 246
# cache-write and 23 655 cache-read.
REAL_USAGE = {
    "cache_creation": {
        "ephemeral_1h_input_tokens": 0,
        "ephemeral_5m_input_tokens": 8246,
    },
    "cache_creation_input_tokens": 8246,
    "cache_read_input_tokens": 23655,
    "input_tokens": 2,
    "output_tokens": 4,
    "service_tier": "standard",
}

# Claude Opus 5 lists at $5.00 input / $25.00 output per million tokens.
#   input        2 x  5.00              =        10.00
#   output       4 x 25.00              =       100.00
#   5m write  8246 x  5.00 x 1.25       =    51 537.50
#   cache read 23655 x 5.00 x 0.10      =    11 827.50
#                                         -------------
#                                            63 475.00  per million
#                                       => $0.06347500
REAL_USAGE_USD = 63475.0 / 1_000_000.0


class PriceUsageTest(unittest.TestCase):

    def test_prices_a_real_record_including_cache(self):
        usd, unpriced = value_meter.price_usage(
            "claude-opus-5", REAL_USAGE)
        self.assertEqual(unpriced, 0)
        self.assertAlmostEqual(usd, REAL_USAGE_USD, places=10)

    def test_cache_dominates_input_and_output(self):
        """The reason this module exists, asserted as a number.

        Pricing only input+output would bill $0.00011 for a record actually
        worth $0.063 -- a 577x understatement. If someone ever 'simplifies'
        the cache terms away, this fails loudly.
        """
        usd, _ = value_meter.price_usage(
            "claude-opus-5", REAL_USAGE)
        naive = (2 * 5.00 + 4 * 25.00) / 1_000_000.0
        self.assertGreater(usd / naive, 500)

    def test_one_hour_cache_writes_cost_more_than_five_minute(self):
        five = value_meter.price_usage("claude-opus-5", {
            "cache_creation": {"ephemeral_5m_input_tokens": 1_000_000,
                               "ephemeral_1h_input_tokens": 0}})[0]
        hour = value_meter.price_usage("claude-opus-5", {
            "cache_creation": {"ephemeral_5m_input_tokens": 0,
                               "ephemeral_1h_input_tokens": 1_000_000}})[0]
        self.assertAlmostEqual(five, 6.25)   # 5.00 x 1.25
        self.assertAlmostEqual(hour, 10.00)  # 5.00 x 2.00

    def test_flat_cache_total_is_attributed_to_the_cheaper_bucket(self):
        """Without the TTL breakdown we must understate, never inflate."""
        usd, _ = value_meter.price_usage(
            "claude-opus-5", {"cache_creation_input_tokens": 1_000_000})
        self.assertAlmostEqual(usd, 6.25)

    def test_explicit_breakdown_wins_over_flat_total(self):
        usd, _ = value_meter.price_usage("claude-opus-5", {
            "cache_creation_input_tokens": 1_000_000,
            "cache_creation": {"ephemeral_5m_input_tokens": 0,
                               "ephemeral_1h_input_tokens": 1_000_000}})
        self.assertAlmostEqual(usd, 10.00)

    def test_per_model_cache_rates_are_used_not_inferred(self):
        """Cache rates come from the catalogue, not from multiplying input.

        Haiku's cache-read rate happens to equal 0.1x its input rate, so this
        uses a model where an override proves which path ran: state a cache
        rate that is NOT any multiple of input and check it is honoured.
        """
        import json, tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "p.json"
            path.write_text(json.dumps({"providers": {"anthropic": {"models": {
                "claude-opus-5": {"input": 5.0, "output": 25.0,
                                  "cache_read": 3.0}}}}}))
            table = value_meter.load_prices(path)
        usd, _ = table.price(
            "claude-opus-5", {"cache_read_input_tokens": 1_000_000})
        self.assertAlmostEqual(usd, 3.00)  # not 5.00 x 0.1

    def test_a_model_without_cache_rates_falls_back_to_the_multiplier(self):
        """An override that states only input/output still prices cache."""
        import json, tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "p.json"
            path.write_text(json.dumps({"providers": {"anthropic": {"models": {
                "claude-opus-9": {"input": 8.0, "output": 40.0}}}}}))
            table = value_meter.load_prices(path)
        read, _ = table.price(
            "claude-opus-9", {"cache_read_input_tokens": 1_000_000})
        write, _ = table.price(
            "claude-opus-9", {"cache_creation_input_tokens": 1_000_000})
        self.assertAlmostEqual(read, 0.80)   # 8.00 x 0.1
        self.assertAlmostEqual(write, 10.00)  # 8.00 x 1.25

    def test_batch_tier_is_half_price(self):
        usd, _ = value_meter.price_usage(
            "claude-opus-5",
            {"input_tokens": 1_000_000, "service_tier": "batch"})
        self.assertAlmostEqual(usd, 2.50)

    def test_unknown_model_reports_tokens_as_unpriced_not_free(self):
        usd, unpriced = value_meter.price_usage(
            "some-model-shipped-next-year", REAL_USAGE)
        self.assertEqual(usd, 0.0)
        self.assertEqual(unpriced, 2 + 4 + 8246 + 23655)

    def test_missing_model_is_unpriced(self):
        usd, unpriced = value_meter.price_usage(None, REAL_USAGE)
        self.assertEqual(usd, 0.0)
        self.assertEqual(unpriced, 31907)

    def test_empty_usage_is_neither_priced_nor_unpriced(self):
        for usage in ({}, {"input_tokens": 0}, None, "nonsense", []):
            usd, unpriced = value_meter.price_usage(
                "claude-opus-5", usage)
            self.assertEqual((usd, unpriced), (0.0, 0), usage)

    def test_malformed_counts_are_ignored_not_trusted(self):
        usd, unpriced = value_meter.price_usage("claude-opus-5", {
            "input_tokens": -5,
            "output_tokens": True,
            "cache_read_input_tokens": "12000",
            "cache_creation_input_tokens": float("inf"),
        })
        self.assertEqual((usd, unpriced), (0.0, 0))


class PlanCostTest(unittest.TestCase):

    def setUp(self):
        self.table = value_meter.default_table()

    def test_override_wins_and_is_marked_configured(self):
        self.assertEqual(
            self.table.plan_cost("claude", "max5x", 137.5),
            (137.5, "configured"))

    def test_default_is_marked_as_such(self):
        cost, source = self.table.plan_cost("claude", "pro")
        self.assertEqual(source, "default")
        self.assertEqual(cost, 20.0)

    def test_codex_plans_resolve_from_their_own_table(self):
        cost, source = self.table.plan_cost("codex", "pro")
        self.assertEqual(source, "default")
        self.assertEqual(cost, 200.0)

    def test_unknown_plan_has_no_cost(self):
        self.assertEqual(self.table.plan_cost("claude", None),
                         (None, "unknown"))
        self.assertEqual(self.table.plan_cost("claude", "team"),
                         (None, "unknown"))

    def test_nonsense_overrides_fall_through_to_the_plan_default(self):
        for bad in (0, -10, float("nan"), float("inf"), True, "100", None):
            cost, source = self.table.plan_cost("claude", "pro", bad)
            self.assertEqual(source, "default", bad)
            self.assertEqual(cost, 20.0, bad)


class BuildPayloadTest(unittest.TestCase):

    def test_ok_state_reports_the_multiple(self):
        payload = value_meter.build_payload(
            312.0, unpriced_tokens=0, priced_tokens=5_000_000,
            claude_plan="max5x", plan_costs={"claude": 100.0})
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["multiple"], 3.12)
        self.assertEqual(payload["value_usd"], 312.0)
        self.assertEqual(payload["plan_usd"], 100.0)
        self.assertEqual(payload["cost_source"], "configured")

    def test_missing_plan_cost_dashes_the_multiple_but_keeps_the_dollars(self):
        payload = value_meter.build_payload(
            312.0, unpriced_tokens=0, priced_tokens=5_000_000, claude_plan=None)
        self.assertEqual(payload["state"], "no_plan_cost")
        self.assertIsNone(payload["multiple"])
        self.assertEqual(payload["value_usd"], 312.0)

    def test_too_many_unpriced_tokens_suppresses_the_multiple(self):
        payload = value_meter.build_payload(
            312.0, unpriced_tokens=500_000, priced_tokens=500_000,
            claude_plan="max5x", plan_costs={"claude": 100.0})
        self.assertEqual(payload["state"], "partial")
        self.assertIsNone(payload["multiple"])
        self.assertEqual(payload["unpriced_token_share"], 0.5)

    def test_a_trace_of_unpriced_tokens_is_tolerated(self):
        payload = value_meter.build_payload(
            312.0, unpriced_tokens=1_000, priced_tokens=1_000_000,
            claude_plan="max5x", plan_costs={"claude": 100.0})
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["multiple"], 3.12)

    def test_partial_outranks_missing_plan_cost(self):
        """Both wrong: report the one that makes the number meaningless."""
        payload = value_meter.build_payload(
            312.0, unpriced_tokens=500_000, priced_tokens=500_000, claude_plan=None)
        self.assertEqual(payload["state"], "partial")

    def test_zero_usage_is_ok_and_not_a_division_error(self):
        payload = value_meter.build_payload(
            0.0, unpriced_tokens=0, priced_tokens=0,
            claude_plan="pro", plan_costs={"claude": 20.0})
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["multiple"], 0.0)
        self.assertEqual(payload["unpriced_token_share"], 0.0)

    def test_payload_states_its_basis_and_price_date(self):
        payload = value_meter.build_payload(
            1.0, 0, 1000, claude_plan="pro", plan_costs={"claude": 20.0})
        self.assertEqual(payload["basis"], "list API prices")
        self.assertIsNotNone(payload["prices_as_of"])



# A Codex turn. Note input_tokens INCLUDES cached_input_tokens -- the opposite
# of Anthropic -- verified against codex-rs, whose own billing-shaped total is
# non_cached_input() + output_tokens where
# non_cached_input = input_tokens - cached_input_tokens.
CODEX_USAGE = {
    "input_tokens": 100_000,
    "cached_input_tokens": 80_000,
    "cache_write_input_tokens": 5_000,
    "output_tokens": 10_000,
    "reasoning_output_tokens": 7_000,   # a SUBSET of output_tokens
    "total_tokens": 110_000,            # context size, not a billing figure
}

# GPT-5.6 Sol lists at $5.00 input / $30.00 output, $0.50 cached input.
#   fresh input  (100000 - 80000) x  5.00 =   100 000
#   cached input        80000     x  0.50 =    40 000
#   cache write          5000 x 5.00x1.25 =    31 250
#   output              10000     x 30.00 =   300 000
#                                           ----------
#                                             471 250  per million
#                                          => $0.47125
CODEX_USAGE_USD = 471_250.0 / 1_000_000.0


class CodexPricingTest(unittest.TestCase):
    """Codex counts input differently; treating it like Anthropic overcharges."""

    def test_prices_a_codex_turn(self):
        usd, unpriced = value_meter.price_usage(
            "gpt-5.6-sol", CODEX_USAGE)
        self.assertEqual(unpriced, 0)
        self.assertAlmostEqual(usd, CODEX_USAGE_USD, places=10)

    def test_cached_tokens_are_subtracted_from_input_not_added(self):
        """The accounting trap, asserted directly.

        Reading Codex's input_tokens as fresh input -- the Anthropic
        convention -- bills 100k tokens at the full rate instead of 20k fresh
        plus 80k cached, overcharging the input side by 3.6x.
        """
        usd, _ = value_meter.price_usage(
            "gpt-5.6-sol", CODEX_USAGE)
        as_if_anthropic = (100_000 * 5.00 + 80_000 * 0.50
                           + 5_000 * 6.25 + 10_000 * 30.00) / 1_000_000.0
        self.assertLess(usd, as_if_anthropic)
        self.assertAlmostEqual(as_if_anthropic - usd,
                               80_000 * 5.00 / 1_000_000.0, places=10)

    def test_reasoning_tokens_are_not_billed_on_top_of_output(self):
        with_reasoning = value_meter.price_usage(
            "gpt-5.6-sol", CODEX_USAGE)[0]
        without = dict(CODEX_USAGE)
        without.pop("reasoning_output_tokens")
        self.assertAlmostEqual(
            with_reasoning,
            value_meter.price_usage("gpt-5.6-sol", without)[0])

    def test_total_tokens_is_context_size_and_never_billed(self):
        inflated = dict(CODEX_USAGE, total_tokens=99_000_000)
        self.assertAlmostEqual(
            value_meter.price_usage("gpt-5.6-sol", inflated)[0],
            CODEX_USAGE_USD, places=10)

    def test_cached_cannot_exceed_input(self):
        """A malformed record must not produce negative fresh input."""
        usd, _ = value_meter.price_usage("gpt-5.6-sol", {
            "input_tokens": 1_000, "cached_input_tokens": 50_000})
        self.assertAlmostEqual(usd, 1_000 * 0.50 / 1_000_000.0)

class PlanCostFlagTest(unittest.TestCase):
    """--plan PROVIDER=USD. No allowlist: Team, Enterprise, annual billing,
    EDU and VAT-inclusive pricing all exist and none fit a fixed table."""

    def test_parses_one_entry_per_provider(self):
        self.assertEqual(
            value_meter.parse_plan_costs(["claude=200", "codex=20"]),
            {"claude": 200.0, "codex": 20.0})

    def test_accepts_a_provider_the_table_has_never_heard_of(self):
        self.assertEqual(value_meter.parse_plan_costs(["cursor=20"]),
                         {"cursor": 20.0})

    def test_the_deprecated_flag_still_means_claude(self):
        self.assertEqual(value_meter.parse_plan_costs([], legacy_claude=100.0),
                         {"claude": 100.0})

    def test_an_explicit_plan_wins_over_the_deprecated_flag(self):
        self.assertEqual(
            value_meter.parse_plan_costs(["claude=250"], legacy_claude=100.0),
            {"claude": 250.0})

    def test_malformed_entries_are_a_hard_error(self):
        """A mistyped denominator produces a confidently wrong multiple,
        which is exactly the failure this module exists to avoid."""
        for bad in ("claude", "claude=", "claude=abc", "claude=0",
                    "claude=-5", "=200"):
            with self.assertRaises(ValueError, msg=bad):
                value_meter.parse_plan_costs([bad])

    def test_each_provider_gets_its_own_cost_in_the_payload(self):
        payload = value_meter.build_payload(
            312.0, 0, 1000, plan_costs={"claude": 200.0, "codex": 20.0},
            claude_usd=280.0, codex_usd=32.0)
        self.assertEqual(payload["plan_usd"], 220.0)
        self.assertEqual(payload["claude_usd"], 280.0)
        self.assertEqual(payload["claude_plan_usd"], 200.0)
        self.assertEqual(payload["codex_usd"], 32.0)
        self.assertEqual(payload["codex_plan_usd"], 20.0)
        self.assertEqual(payload["cost_source"], "configured")

    def test_value_is_never_credited_against_another_plan(self):
        """The panel bug, as a test.

        Codex usage with no declared Codex plan turned a $100 subscription
        into 110x on the glass: both providers in the numerator, one cost in
        the denominator. Undeclared value is reported separately and left
        out of the ratio entirely.
        """
        payload = value_meter.build_payload(
            11014.0, 0, 1000, plan_costs={"claude": 100.0},
            claude_usd=2717.0, codex_usd=8296.0)
        self.assertEqual(payload["multiple"], 27.17)
        self.assertEqual(payload["value_usd"], 2717.0)
        self.assertEqual(payload["plan_usd"], 100.0)
        self.assertEqual(payload["undeclared_usd"], 8296.0)
        self.assertNotIn("codex_plan_usd", payload)

    def test_declaring_the_second_plan_brings_it_into_the_ratio(self):
        payload = value_meter.build_payload(
            11014.0, 0, 1000, plan_costs={"claude": 100.0, "codex": 20.0},
            claude_usd=2717.0, codex_usd=8296.0)
        self.assertEqual(payload["plan_usd"], 120.0)
        self.assertEqual(payload["value_usd"], 11013.0)
        self.assertNotIn("undeclared_usd", payload)

    def test_a_caller_with_only_a_total_keeps_its_ratio(self):
        """The strict pairing applies only where a breakdown exists."""
        payload = value_meter.build_payload(
            312.0, 0, 1000, plan_costs={"claude": 100.0})
        self.assertEqual(payload["state"], "ok")
        self.assertEqual(payload["multiple"], 3.12)

    def test_a_provider_that_spent_nothing_is_absent_not_zero(self):
        """An empty bar for an agent that is not installed is a lie."""
        payload = value_meter.build_payload(
            280.0, 0, 1000, plan_costs={"claude": 200.0},
            claude_usd=280.0, codex_usd=0.0)
        self.assertEqual(payload["claude_usd"], 280.0)
        self.assertNotIn("codex_usd", payload)
        self.assertNotIn("codex_plan_usd", payload)


class GeneratedTableTest(unittest.TestCase):
    """The shipped table is generated, so assert what generation guarantees.

    These are the invariants a bad refresh would break. They are cheap and
    they bite: an upstream rename, a dropped provider or a flipped accounting
    mode all fail here rather than silently mis-billing.
    """

    def setUp(self):
        self.table = value_meter.default_table()
        self.doc = json.loads(
            value_meter.DEFAULT_PRICES_PATH.read_text(encoding="utf-8"))

    def test_table_records_where_it_came_from(self):
        source = self.doc["source"]
        self.assertIn("litellm", source["catalogue"].lower())
        self.assertRegex(source["blob_sha"], r"^[0-9a-f]{40}$")
        self.assertRegex(source["generated"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(self.table.as_of(), source["generated"])

    def test_both_providers_survive_a_refresh(self):
        for provider, expected in (("anthropic", "cache_excluded_input"),
                                   ("openai", "cache_included_input")):
            spec = self.doc["providers"][provider]
            self.assertEqual(spec["accounting"], expected)
            self.assertGreater(len(spec["models"]), 10, provider)

    def test_the_models_actually_in_use_are_priced(self):
        for model in ("claude-opus-5", "claude-sonnet-5", "claude-fable-5",
                      "claude-haiku-4-5", "gpt-5.6-sol", "gpt-5.1-codex"):
            self.assertTrue(self.table.knows(model), model)

    def test_every_model_prices_both_sides_of_the_bill(self):
        """Input-only would look ~5x too cheap; worse than being unpriced."""
        for spec in self.doc["providers"].values():
            for model, rates in spec["models"].items():
                self.assertIn("input", rates, model)
                self.assertIn("output", rates, model)

    def test_rates_are_per_million_not_per_token(self):
        """A missed unit conversion is a 1,000,000x error that still 'works'."""
        usd, _ = self.table.price(
            "claude-opus-5", {"output_tokens": 1_000_000})
        self.assertAlmostEqual(usd, 25.00)

    def test_sonnet_5_matches_the_catalogue_not_the_old_hand_written_rate(self):
        """Regression: this was hand-written as $3/$15 and was simply wrong.

        It sat behind a 'verified: true' flag for a whole release, which is
        why rates are generated now instead of typed.
        """
        usd, _ = self.table.price(
            "claude-sonnet-5", {"input_tokens": 1_000_000})
        self.assertAlmostEqual(usd, 2.00)


class PriceTableTest(unittest.TestCase):

    def test_override_merges_over_a_single_rate(self):
        import json, tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prices.json"
            path.write_text(json.dumps({"providers": {"anthropic": {
                "models": {"claude-opus-5": {"input": 1.0, "output": 2.0}}}}}))
            table = value_meter.load_prices(path)
        usd, _ = table.price("claude-opus-5", {"input_tokens": 1_000_000})
        self.assertAlmostEqual(usd, 1.0)
        # An untouched model keeps the bundled rate.
        other, _ = table.price("claude-haiku-4-5", {"input_tokens": 1_000_000})
        self.assertAlmostEqual(other, 1.0)

    def test_override_can_add_a_model_without_code_changes(self):
        import json, tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prices.json"
            path.write_text(json.dumps({"providers": {"anthropic": {
                "models": {"claude-opus-6": {"input": 7.0, "output": 35.0}}}}}))
            table = value_meter.load_prices(path)
        self.assertTrue(table.knows("claude-opus-6"))
        usd, unpriced = table.price("claude-opus-6", {"output_tokens": 1_000_000})
        self.assertAlmostEqual(usd, 35.0)
        self.assertEqual(unpriced, 0)

    def test_unknown_accounting_mode_is_rejected_loudly(self):
        with self.assertRaises(ValueError):
            value_meter.PriceTable({"providers": {"weird": {
                "accounting": "vibes", "models": {"m": {"input": 1.0}}}}})

    def test_a_broken_override_file_raises_instead_of_falling_back(self):
        import tempfile, pathlib
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "prices.json"
            path.write_text("{ not json")
            with self.assertRaises(Exception):
                value_meter.load_prices(path)



if __name__ == "__main__":
    unittest.main()
