"""Tests for the read-only Lovable monitor. No network: fake MCP + fake store."""

import io
import json
import time
import unittest
import urllib.error

try:
    from . import lovable_monitor as lm
except ImportError:  # run directly
    import lovable_monitor as lm


SECRET = "tok_SECRET_do_not_leak"


class MemoryStore:
    def __init__(self, record=None):
        self.record = record
        self.saves = 0

    def load(self):
        return dict(self.record) if self.record else None

    def save(self, record):
        self.record = dict(record)
        self.saves += 1

    def clear(self):
        self.record = None


def good_record(**extra):
    record = {"client_id": "c1", "token_endpoint": "https://lovable.dev/oauth/token",
              "access_token": SECRET, "refresh_token": "r1",
              "expires_at": time.time() + 3600, "workspace_id": "ws1"}
    record.update(extra)
    return record


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def __call__(self, token):
        self.calls.append(token)
        return self

    def get_workspace(self, workspace_id):
        item = self.payloads.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


class ExtractTests(unittest.TestCase):
    def test_flat_fields(self):
        data = lm.extract({"id": "ws1", "name": "Niclas studio", "plan": "pro",
                           "credit_balance": 1240.5})
        self.assertEqual(data, {"plan": "PRO", "credits": 1240.5,
                                "workspace": "Niclas studio", "grant": None,
                                "reset_at": None, "start_at": None,
                                "daily_credits": None, "daily_grant": None})

    def test_nested_plan_and_credit_objects(self):
        data = lm.extract({"workspace": {
            "name": "Team", "subscription": {"plan": {"name": "Business"}},
            "credits": {"remaining": 42, "total": 100}}})
        self.assertEqual(data["plan"], "BUSINESS")
        self.assertEqual(data["credits"], 42.0)

    def test_unknown_fields_stay_unknown(self):
        data = lm.extract({"id": "ws1", "members": 3})
        self.assertEqual(data, {"plan": None, "credits": None, "workspace": None,
                                "grant": None, "reset_at": None,
                                "start_at": None, "daily_credits": None,
                                "daily_grant": None})

    def test_rejects_negative_bool_and_nan(self):
        for value in (-1, True, float("nan"), "abc"):
            self.assertIsNone(lm.extract({"credits": value})["credits"])

    def test_non_ascii_name_is_sanitised_for_the_panel_font(self):
        self.assertEqual(lm.extract({"name": "Värdera 🚀", "credits": 1})
                         ["workspace"], "Vrdera")

    def test_shape_prints_no_values(self):
        lines = lm.describe_shape({"credits": 12, "token": SECRET})
        self.assertNotIn(SECRET, "\n".join(lines))
        self.assertIn("credits: int", lines)

    def test_daily_pool_keeps_balance_and_allowance_separate(self):
        data = lm.extract({"credits": {"remaining": 81.5},
                           "daily_credits": {"remaining": 3,
                                             "allowance": 5}})
        self.assertEqual(data["daily_credits"], 3)
        self.assertEqual(data["daily_grant"], 5)

    def test_daily_credits_are_never_inferred_from_plan(self):
        data = lm.extract({"plan": "Pro", "credits": 100})
        self.assertIsNone(data["daily_credits"])
        self.assertIsNone(data["daily_grant"])


class ToolResultTests(unittest.TestCase):
    def test_structured_and_text(self):
        self.assertEqual(lm.tool_result_json({"structuredContent": {"a": 1}}),
                         {"a": 1})
        self.assertEqual(lm.tool_result_json(
            {"content": [{"type": "text", "text": '{"b": 2}'}]}), {"b": 2})

    def test_sse_body(self):
        raw = b'event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{}}\n\n'
        self.assertEqual(lm._parse_rpc_body(raw, "text/event-stream", 2)["id"], 2)


class MonitorTests(unittest.TestCase):
    def make(self, payloads, record=None):
        clock = Clock()
        store = MemoryStore(record if record is not None else good_record())
        client = FakeClient(payloads)
        monitor = lm.LovableMonitor(store=store, clock=clock,
                                    client_factory=client, poll_seconds=300)
        return monitor, clock, store, client

    def test_live_snapshot_is_numbers_only(self):
        monitor, clock, _, _ = self.make(
            [{"name": "Niclas", "plan": "Pro", "credits_remaining": 12.5}])
        self.assertTrue(monitor.poll_once())
        clock.now += 90
        snap = monitor.snapshot()
        self.assertEqual(snap, {"v": 1, "enabled": True, "stale": False,
                                "creditsTenths": 125, "ageSeconds": 90,
                                "plan": "PRO", "workspace": "Niclas"})
        self.assertNotIn(SECRET, json.dumps(snap))

    def test_percentage_is_never_published(self):
        monitor, _, _, _ = self.make([{"credits": 5, "percent": 50}])
        monitor.poll_once()
        snap = monitor.snapshot()
        self.assertFalse({"percent", "grantTenths", "resetSeconds"} & set(snap))

    def test_daily_pool_is_published_only_when_named(self):
        monitor, _, _, _ = self.make([{
            "credits": 81.5,
            "daily_credits": {"remaining": 3, "allowance": 5},
        }])
        monitor.poll_once()
        snap = monitor.snapshot()
        self.assertEqual(snap["dailyCreditsTenths"], 30)
        self.assertEqual(snap["dailyGrantTenths"], 50)

    def test_grant_and_reset_only_when_named(self):
        now = 1_790_000_000.0
        monitor = lm.LovableMonitor(
            store=MemoryStore(good_record()), clock=Clock(),
            client_factory=FakeClient([{"credits": {
                "remaining": 81.5, "total_granted": 100,
                "period_start": now - 10 * 86400,
                "expires_at": "2026-10-21T00:00:00Z"}}]),
            wall_clock=lambda: now)
        self.assertTrue(monitor.poll_once())
        snap = monitor.snapshot()
        self.assertEqual(snap["creditsTenths"], 815)
        self.assertEqual(snap["grantTenths"], 1000)
        self.assertEqual(snap["resetSeconds"],
                         int(lm._timestamp("2026-10-21T00:00:00Z") - now))
        self.assertEqual(snap["periodSeconds"],
                         snap["resetSeconds"] + 10 * 86400)

    def test_past_or_absurd_reset_is_dropped(self):
        for when in ("2001-01-01T00:00:00Z", "2999-01-01T00:00:00Z", "soon"):
            monitor = lm.LovableMonitor(
                store=MemoryStore(good_record()), clock=Clock(),
                client_factory=FakeClient([{"credits": 3, "resetAt": when}]),
                wall_clock=lambda: 1_790_000_000.0)
            monitor.poll_once()
            self.assertNotIn("resetSeconds", monitor.snapshot())

    def test_waiting_without_login(self):
        monitor, _, _, _ = self.make([], record={})
        self.assertFalse(monitor.poll_once())
        snap = monitor.snapshot()
        self.assertTrue(snap["stale"])
        self.assertTrue(snap["login"])
        self.assertNotIn("creditsTenths", snap)

    def test_failure_keeps_last_value_as_cached(self):
        monitor, clock, _, _ = self.make([{"credits": 7}, RuntimeError("boom")])
        monitor.poll_once()
        clock.now += 400
        self.assertFalse(monitor.poll_once())
        snap = monitor.snapshot()
        self.assertTrue(snap["stale"])
        self.assertEqual(snap["creditsTenths"], 70)
        self.assertEqual(snap["ageSeconds"], 400)

    def test_missing_credit_field_is_an_error_not_a_zero(self):
        monitor, _, _, _ = self.make([{"plan": "Pro"}])
        self.assertFalse(monitor.poll_once())
        self.assertNotIn("creditsTenths", monitor.snapshot())

    def test_expired_token_is_refreshed_and_saved(self):
        record = good_record(expires_at=time.time() - 10)
        monitor, _, store, client = self.make([{"credits": 1}], record=record)

        def opener(request, timeout):
            body = json.dumps({"access_token": "new_access",
                               "expires_in": 3600}).encode()

            class Response(io.BytesIO):
                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False
            return Response(body)
        monitor._opener = opener
        self.assertTrue(monitor.poll_once())
        self.assertEqual(client.calls, ["new_access"])
        self.assertEqual(store.record["access_token"], "new_access")
        self.assertEqual(store.record["refresh_token"], "r1")

    def test_revoked_refresh_asks_for_login(self):
        record = good_record(expires_at=0)
        monitor, _, _, _ = self.make([], record=record)

        def opener(request, timeout):
            raise urllib.error.HTTPError(request.full_url, 400, "bad", {}, None)
        monitor._opener = opener
        self.assertFalse(monitor.poll_once())
        self.assertTrue(monitor.snapshot()["login"])

    def test_token_never_logged(self):
        monitor, _, _, _ = self.make([RuntimeError("upstream 500")])
        with self.assertLogs("tokenserver.lovable", level="WARNING") as logs:
            monitor.poll_once()
        self.assertNotIn(SECRET, "\n".join(logs.output))


class TokenStoreTests(unittest.TestCase):
    def test_file_store_is_private(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = lm.TokenStore(path=os.path.join(tmp, "x.json"),
                                  platform="linux")
            store.save({"access_token": "a"})
            mode = os.stat(os.path.join(tmp, "x.json")).st_mode & 0o777
            self.assertEqual(mode, 0o600)
            self.assertEqual(store.load(), {"access_token": "a"})
            store.clear()
            self.assertIsNone(store.load())

    def test_keychain_store_uses_security_tool(self):
        calls = []

        class Result:
            returncode = 0
            stdout = '{"access_token": "a"}'

        def runner(cmd, **kw):
            calls.append(cmd)
            return Result()
        store = lm.TokenStore(runner=runner, platform="darwin")
        self.assertEqual(store.load(), {"access_token": "a"})
        store.save({"access_token": "b"})
        self.assertEqual(calls[0][:2], ["security", "find-generic-password"])
        self.assertEqual(calls[1][:2], ["security", "add-generic-password"])


if __name__ == "__main__":
    unittest.main()
