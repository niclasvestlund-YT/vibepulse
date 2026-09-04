"""The relay publisher's two contracts: write economy, and honesty.

Write economy because the mailbox's free tier allows 1 000 writes/day and
a naive 30-second cadence would burn 2 880.  Honesty because every send
must name its publisher (the mailbox merges freshest-per-source) and wear
the project's own User-Agent, not somebody else's.
"""

import threading
import time
import unittest

try:
    from .publisher import (Publisher, HEARTBEAT_EVERY_S,
                            MIN_SEND_INTERVAL_S, payload_fingerprint,
                            should_send, USER_AGENT)
except ImportError:
    from publisher import (Publisher, HEARTBEAT_EVERY_S,
                           MIN_SEND_INTERVAL_S, payload_fingerprint,
                           should_send, USER_AGENT)


class FingerprintTests(unittest.TestCase):
    def test_key_order_is_irrelevant(self):
        a = payload_fingerprint({"x": 1, "y": [1, 2]})
        b = payload_fingerprint({"y": [1, 2], "x": 1})
        self.assertEqual(a, b)

    def test_value_changes_are_visible(self):
        self.assertNotEqual(payload_fingerprint({"pct": 73.0}),
                            payload_fingerprint({"pct": 73.1}))


class ShouldSendTests(unittest.TestCase):
    def test_a_fresh_start_sends(self):
        self.assertTrue(should_send(None, 0.0, "abc", 1000.0))

    def test_unchanged_content_waits(self):
        self.assertFalse(should_send("abc", 1000.0, "abc", 1010.0))

    def test_changed_content_sends_immediately(self):
        self.assertTrue(should_send("abc", 1000.0, "def", 1001.0))

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

    def test_change_waits_for_the_endpoint_ceiling_then_sends(self):
        value = {"pct": 73}
        p, sent, clock = self._publisher({"/api/tokens": lambda: dict(value)})
        p.publish_once()
        value["pct"] = 74
        clock["now"] += 30
        p.publish_once()
        self.assertEqual(len(sent), 1, "a change must respect the write cap")
        clock["now"] += MIN_SEND_INTERVAL_S["/api/tokens"] - 30
        p.publish_once()
        self.assertEqual(len(sent), 2, "the bounded change must send")
        clock["now"] += MIN_SEND_INTERVAL_S["/api/tokens"]
        p.publish_once()
        self.assertEqual(len(sent), 3, "the heartbeat must still send")

    def test_a_failed_send_retries_next_tick(self):
        p, sent, clock = self._publisher({"/api/tokens": lambda: {"a": 1}},
                                         results=[False, True])
        self.assertEqual(p.publish_once(), 0)
        clock["now"] += 30
        self.assertEqual(p.publish_once(), 1)
        self.assertEqual(len(sent), 2)

    def test_failed_throttled_update_does_not_restart_the_ceiling(self):
        value = {"a": 1}
        p, sent, clock = self._publisher(
            {"/api/tokens": lambda: dict(value)},
            results=[True, False, True],
        )
        self.assertEqual(p.publish_once(), 1)
        value["a"] = 2
        clock["now"] += MIN_SEND_INTERVAL_S["/api/tokens"]
        self.assertEqual(p.publish_once(), 0)
        clock["now"] += 30
        self.assertEqual(p.publish_once(), 1)
        self.assertEqual(len(sent), 3)

    def test_a_broken_producer_does_not_stop_the_others(self):
        def broken():
            raise RuntimeError("boom")

        p, sent, _ = self._publisher({"/api/tokens": broken,
                                      "/api/github": lambda: {"ok": 1}})
        self.assertEqual(p.publish_once(), 1)
        self.assertEqual(len(sent), 1)

    def test_start_never_blocks_the_server_on_first_producer_pass(self):
        entered = threading.Event()
        release = threading.Event()

        def slow_first_payload():
            entered.set()
            release.wait(timeout=2)
            return {"ok": 1}

        publisher, _sent, _clock = self._publisher({
            "/api/tokens": slow_first_payload,
        })
        try:
            started = time.monotonic()
            publisher.start()
            self.assertLess(time.monotonic() - started, 0.2)
            self.assertTrue(entered.wait(timeout=1))
        finally:
            release.set()
            publisher.stop()

    def test_the_user_agent_is_our_own(self):
        # The Anthropic probe imitating claude-cli is a separate, deliberate
        # decision — but OUR mailbox gets OUR name.  If someone changes the
        # constant to imitate anything, this fails.
        self.assertTrue(USER_AGENT.startswith("vibepulse-publisher/"),
                        USER_AGENT)

    def test_two_publishers_fit_the_free_daily_write_budget(self):
        """Even continuously changing payloads remain below 1,000/day."""
        total_sends = 0
        for _publisher_number in range(2):
            generation = {"value": 0}
            p, _sent, clock = self._publisher({
                "/api/tokens": lambda g=generation: {"value": g["value"]},
                "/api/max-tracker": lambda g=generation: {
                    "value": g["value"],
                },
                "/api/github": lambda g=generation: {"value": g["value"]},
            })
            for _tick in range(int(24 * 60 * 60 / 30)):
                generation["value"] += 1
                total_sends += p.publish_once()
                clock["now"] += 30

        self.assertLessEqual(total_sends, 768)
        self.assertLess(total_sends, 1000)


if __name__ == "__main__":
    unittest.main()
