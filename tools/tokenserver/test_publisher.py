"""The relay publisher's two contracts: write economy, and honesty.

Write economy because the mailbox's free tier allows 1 000 writes/day and
a naive 30-second cadence would burn 2 880 -- and because "send only on
change" is not economy at all when the payload carries a countdown that
changes by itself once a minute (2026-08-21: half the day's allowance gone
by breakfast).  So the tests below hold three things still: what counts as
a change, how fast changes may go out, and the budget that bounds a day no
matter what the payloads do.  Honesty because every send must name its
publisher (the mailbox merges freshest-per-source) and wear the project's
own User-Agent, not somebody else's.
"""

import unittest
from collections import Counter

try:
    from .publisher import (BUDGET_BURST, CHECK_EVERY_S, DAILY_WRITE_BUDGET,
                            ENDPOINT_CADENCE, HEARTBEAT_EVERY_S,
                            MIN_SEND_INTERVAL_S, Publisher, SEND_CHANGE,
                            SEND_HEARTBEAT, SEND_START, USER_AGENT,
                            WriteBudget, payload_fingerprint, send_reason,
                            should_send, stable_payload)
except ImportError:
    from publisher import (BUDGET_BURST, CHECK_EVERY_S, DAILY_WRITE_BUDGET,
                           ENDPOINT_CADENCE, HEARTBEAT_EVERY_S,
                           MIN_SEND_INTERVAL_S, Publisher, SEND_CHANGE,
                           SEND_HEARTBEAT, SEND_START, USER_AGENT,
                           WriteBudget, payload_fingerprint, send_reason,
                           should_send, stable_payload)


class FingerprintTests(unittest.TestCase):
    def test_key_order_is_irrelevant(self):
        a = payload_fingerprint({"x": 1, "y": [1, 2]})
        b = payload_fingerprint({"y": [1, 2], "x": 1})
        self.assertEqual(a, b)

    def test_value_changes_are_visible(self):
        self.assertNotEqual(payload_fingerprint({"pct": 73.0}),
                            payload_fingerprint({"pct": 73.1}))

    def test_a_ticking_countdown_is_not_a_change(self):
        # The one that emptied the free tier: claudeWeekResetMin drops by
        # one every minute whether or not a single number moved, and each
        # drop used to buy a KV write.
        a = {"claudeWeekPct": 73.0, "claudeWeekResetMin": 600,
             "weekObservedAt": 1_700_000_000}
        b = {"claudeWeekPct": 73.0, "claudeWeekResetMin": 599,
             "weekObservedAt": 1_700_000_240}
        self.assertEqual(payload_fingerprint(a), payload_fingerprint(b))

    def test_a_moving_number_still_is(self):
        a = {"claudeWeekPct": 73.0, "claudeWeekResetMin": 600}
        b = {"claudeWeekPct": 73.1, "claudeWeekResetMin": 600}
        self.assertNotEqual(payload_fingerprint(a), payload_fingerprint(b))

    def test_stale_flags_are_never_derived_away(self):
        # A reading ageing out of live is real news, and the flag that says
        # so must survive the filter that drops the countdowns.
        a = {"claudeWeekPct": 73.0, "claudeWeekStale": False}
        b = {"claudeWeekPct": 73.0, "claudeWeekStale": True}
        self.assertNotEqual(payload_fingerprint(a), payload_fingerprint(b))

    def test_the_derived_fields_still_travel(self):
        # Dropped from the HASH, never from the wire: the mailbox needs the
        # stamps to merge and the countdowns to age.
        payload = {"claudeWeekResetMin": 600, "weekObservedAt": 17}
        self.assertEqual(stable_payload(payload), {})
        self.assertEqual(payload, {"claudeWeekResetMin": 600,
                                   "weekObservedAt": 17})


class ShouldSendTests(unittest.TestCase):
    def test_a_fresh_start_sends(self):
        self.assertTrue(should_send(None, 0.0, "abc", 1000.0))

    def test_unchanged_content_waits(self):
        self.assertFalse(should_send("abc", 1000.0, "abc", 1010.0))

    def test_a_change_waits_for_the_floor_then_sends(self):
        # Token volume moves every 30 s while you work.  Without the floor
        # that is 2 880 writes/day from one endpoint alone.
        inside = 1000.0 + MIN_SEND_INTERVAL_S - 1
        at = 1000.0 + MIN_SEND_INTERVAL_S
        self.assertFalse(should_send("abc", 1000.0, "def", inside))
        self.assertTrue(should_send("abc", 1000.0, "def", at))

    def test_the_heartbeat_bounds_staleness(self):
        just_before = 1000.0 + HEARTBEAT_EVERY_S - 1
        at = 1000.0 + HEARTBEAT_EVERY_S
        self.assertFalse(should_send("abc", 1000.0, "abc", just_before))
        self.assertTrue(should_send("abc", 1000.0, "abc", at))


class PublisherTests(unittest.TestCase):
    def _publisher(self, producers, results=None):
        sent = []
        outcomes = list(results) if results is not None else None

        def post(url, body):
            sent.append((url, body))
            return True if outcomes is None else outcomes.pop(0)

        clock = {"now": 1000.0}
        p = Publisher("https://relay.example/u/s3cret/", "test-machine",
                      producers, post=post, clock=lambda: clock["now"])
        return p, sent, clock

    def test_publishes_each_endpoint_to_its_path(self):
        p, sent, _ = self._publisher({
            "/api/tokens": lambda: {"weekPct": 73.0},
            "/api/github": lambda: {"stars": 5},
        })
        self.assertEqual(p.publish_once(), 2)
        urls = sorted(url for url, _ in sent)
        # Trailing slash on the configured URL must not double up.
        self.assertEqual(urls, ["https://relay.example/u/s3cret/api/github",
                                "https://relay.example/u/s3cret/api/tokens"])

    def test_unchanged_payloads_are_not_resent(self):
        p, sent, clock = self._publisher({"/api/tokens": lambda: {"pct": 73}})
        p.publish_once()
        clock["now"] += 30
        p.publish_once()
        self.assertEqual(len(sent), 1, "an unchanged payload was resent")

    def test_change_and_heartbeat_both_send(self):
        value = {"pct": 73}
        p, sent, clock = self._publisher({"/api/tokens": lambda: dict(value)})
        p.publish_once()
        value["pct"] = 74
        clock["now"] += 30
        p.publish_once()
        self.assertEqual(len(sent), 1, "a change inside the floor must wait")
        clock["now"] += MIN_SEND_INTERVAL_S
        p.publish_once()
        self.assertEqual(len(sent), 2, "a changed payload must send")
        clock["now"] += HEARTBEAT_EVERY_S
        p.publish_once()
        self.assertEqual(len(sent), 3, "the heartbeat must send")

    def test_a_ticking_countdown_alone_never_publishes(self):
        # End to end, the 2026-08-21 bug: nothing but the clock moves.
        minutes = {"left": 600}

        def tokens():
            return {"claudeWeekPct": 73.0,
                    "claudeWeekResetMin": minutes["left"]}

        p, sent, clock = self._publisher({"/api/tokens": tokens})
        p.publish_once()
        for _ in range(20):                      # ten minutes of ticking
            clock["now"] += CHECK_EVERY_S
            minutes["left"] -= CHECK_EVERY_S / 60.0
            p.publish_once()
        self.assertEqual(len(sent), 1,
                         "a countdown counting down is not news")

    def test_a_failed_send_retries_next_tick(self):
        p, sent, clock = self._publisher({"/api/tokens": lambda: {"a": 1}},
                                         results=[False, True])
        self.assertEqual(p.publish_once(), 0)
        clock["now"] += 30
        self.assertEqual(p.publish_once(), 1)
        self.assertEqual(len(sent), 2)

    def test_a_broken_producer_does_not_stop_the_others(self):
        def broken():
            raise RuntimeError("boom")

        p, sent, _ = self._publisher({"/api/tokens": broken,
                                      "/api/github": lambda: {"ok": 1}})
        self.assertEqual(p.publish_once(), 1)
        self.assertEqual(len(sent), 1)

    def test_the_budget_is_the_backstop_for_a_whole_day(self):
        # The guarantee the free tier actually needs: whatever the payloads
        # do, a day cannot cost more than the budget (plus the burst the
        # bucket starts full with).
        tick = {"n": 0}

        def churn():
            return {"pct": tick["n"]}

        p, sent, clock = self._publisher({
            "/api/tokens": churn,
            "/api/max-tracker": churn,
            "/api/github": churn,
        })
        for _ in range(int(86400 / CHECK_EVERY_S)):
            tick["n"] += 1
            p.publish_once()
            clock["now"] += CHECK_EVERY_S
        self.assertLessEqual(len(sent), DAILY_WRITE_BUDGET + BUDGET_BURST,
                             "a day must not outrun the budget")
        self.assertGreater(len(sent), DAILY_WRITE_BUDGET / 2,
                           "a throttle that never lets go is not a budget")

    def test_a_busy_endpoint_cannot_starve_the_slow_ones(self):
        # /api/tokens changes every tick; Max Tracker and the GitHub pulse
        # change almost never.  If the busy one could spend every token the
        # moment it is minted, the two quiet pages would sit stale in the
        # mailbox all day -- which is exactly what the glass shows when you
        # are away from home.
        tick = {"n": 0}
        p, sent, clock = self._publisher({
            "/api/tokens": lambda: {"pct": tick["n"]},
            "/api/max-tracker": lambda: {"peak": 71},
            "/api/github": lambda: {"stars": 5},
        })
        for _ in range(int(86400 / CHECK_EVERY_S)):
            tick["n"] += 1
            p.publish_once()
            clock["now"] += CHECK_EVERY_S

        per_path = Counter(url.rsplit("/", 1)[-1] for url, _ in sent)
        for path, (heartbeat, _) in ENDPOINT_CADENCE.items():
            if path == "/api/tokens":
                continue
            expected = int(86400 / heartbeat)
            self.assertGreaterEqual(
                per_path[path.rsplit("/", 1)[-1]], expected - 1,
                f"{path} lost its heartbeat to a busy neighbour")
        self.assertLessEqual(len(sent), DAILY_WRITE_BUDGET + BUDGET_BURST,
                             "and the day still fits the budget")

    def test_the_user_agent_is_our_own(self):
        # The Anthropic probe imitating claude-cli is a separate, deliberate
        # decision — but OUR mailbox gets OUR name.  If someone changes the
        # constant to imitate anything, this fails.
        self.assertTrue(USER_AGENT.startswith("vibepulse-publisher/"),
                        USER_AGENT)


class SendReasonTests(unittest.TestCase):
    """The reason matters, not just the yes: the budget gates changes and
    lets heartbeats through."""

    def test_it_names_why(self):
        self.assertIs(send_reason(None, 0.0, "abc", 1000.0), SEND_START)
        self.assertIs(
            send_reason("abc", 1000.0, "abc", 1000.0 + HEARTBEAT_EVERY_S),
            SEND_HEARTBEAT)
        self.assertIs(
            send_reason("abc", 1000.0, "def", 1000.0 + MIN_SEND_INTERVAL_S),
            SEND_CHANGE)
        self.assertIsNone(send_reason("abc", 1000.0, "abc", 1001.0))


class WriteBudgetTests(unittest.TestCase):
    def test_the_burst_is_available_at_once(self):
        budget = WriteBudget(per_day=400, burst=3)
        self.assertTrue(budget.spend(0.0))
        self.assertTrue(budget.spend(0.0))
        self.assertTrue(budget.spend(0.0))
        self.assertFalse(budget.spend(0.0))

    def test_it_refills_at_the_daily_rate(self):
        budget = WriteBudget(per_day=288, burst=1)   # one per five minutes
        self.assertTrue(budget.spend(0.0))
        self.assertFalse(budget.allows(299.0))
        self.assertTrue(budget.allows(300.0))

    def test_it_never_holds_more_than_the_burst(self):
        budget = WriteBudget(per_day=400, burst=2)
        budget.allows(0.0)
        for _ in range(2):
            self.assertTrue(budget.spend(86400.0), "a full day of refill")
        self.assertFalse(budget.spend(86400.0), "must still cap at burst")

    def test_a_forced_draw_overdraws_but_only_by_a_burst(self):
        budget = WriteBudget(per_day=400, burst=2)
        self.assertTrue(budget.spend(0.0))
        self.assertTrue(budget.spend(0.0))
        self.assertFalse(budget.spend(0.0), "the gate holds for changes")
        for _ in range(10):
            self.assertTrue(budget.spend(0.0, forced=True))
        # Ten forced draws, but the hole is capped at one burst, so the
        # bucket is back a burst's worth of refill later -- not ten.
        self.assertFalse(budget.allows(0.0))
        self.assertTrue(budget.allows(86400.0 * 3 / 400))

    def test_a_clock_that_jumps_backwards_drains_nothing(self):
        budget = WriteBudget(per_day=400, burst=2)
        self.assertTrue(budget.spend(1000.0))
        self.assertTrue(budget.allows(0.0))
        self.assertTrue(budget.spend(0.0))


class ArithmeticTests(unittest.TestCase):
    """The cadence table's own arithmetic, held still.

    These are the numbers the module docstring quotes and the free tier
    charges for.  If someone tightens a cadence, this fails before the
    account does.
    """

    FREE_TIER_WRITES_PER_DAY = 1000
    PUBLISHERS_SUPPORTED = 2

    def test_every_endpoint_has_a_floor_under_its_heartbeat(self):
        for path, (heartbeat, floor) in ENDPOINT_CADENCE.items():
            self.assertLess(floor, heartbeat, path)

    def test_a_silent_day_costs_well_under_the_budget(self):
        quiet = sum(86400.0 / heartbeat
                    for heartbeat, _ in ENDPOINT_CADENCE.values())
        self.assertLessEqual(quiet, DAILY_WRITE_BUDGET / 2,
                             "heartbeats alone must leave room for news")

    def test_two_publishers_fit_the_free_tier(self):
        self.assertLessEqual(
            DAILY_WRITE_BUDGET * self.PUBLISHERS_SUPPORTED,
            self.FREE_TIER_WRITES_PER_DAY,
            "the README advertises two machines on one mailbox")


if __name__ == "__main__":
    unittest.main()
