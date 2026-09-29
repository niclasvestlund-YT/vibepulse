import io
import http.client
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import Mock, patch

from tools.openpulse.client import Client, NoRedirect, SourceError
from tools.openpulse.credentials import SERVICE, load
from tools.openpulse.model import key_view, normalize_credits, normalize_key
from tools.openpulse.service import DEMO_KEY, EXAMPLE, Monitor, make_server, read_config, save_connection


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.config = read_config(EXAMPLE)["keys"][0]

    def test_separate_scopes(self):
        raw = dict(DEMO_KEY, byok_usage_monthly=100, include_byok_in_limit=True,
                   limit_remaining=3.21, label="secret upstream label")
        data = normalize_key(raw)
        result = key_view(data, 1000, 1000, self.config)
        self.assertEqual(result["month"], 38.42)
        self.assertEqual(result["byokMonth"], 100)
        self.assertEqual(result["limitRemaining"], 3.21)
        self.assertEqual(result["budgetLevel"], "warning")
        self.assertAlmostEqual(result["budgetPercent"], 76.84)
        self.assertNotIn("label", result)
        self.assertEqual(normalize_credits({"total_credits": 200, "total_usage": 138.42}),
                         {"accountBalance": 61.58000000000001})

    def test_missing_unlimited_zero_and_negative_balance(self):
        self.assertEqual(normalize_key({})["limitState"], "unknown")
        self.assertEqual(normalize_key({"limit": None})["limitState"], "unlimited")
        self.assertEqual(normalize_key({"limit": 0, "limit_remaining": 0})["limitRemaining"], 0)
        for value in (None, "0", True, float("nan"), float("inf"), -1, 1e100):
            self.assertIsNone(normalize_key({"usage_daily": value})["day"])
        self.assertIsNone(normalize_credits({})["accountBalance"])
        self.assertEqual(normalize_credits({"total_credits": 1, "total_usage": 2})["accountBalance"], -1)

    def test_thresholds_and_disabled_budget(self):
        for amount, expected in [(0, "normal"), (37.5, "warning"), (45, "critical"), (51, "critical")]:
            self.assertEqual(key_view(normalize_key({"usage_monthly": amount}), 100, 100,
                                     self.config)["budgetLevel"], expected)
        self.config["monthly_budget_usd"] = None
        self.assertIsNone(key_view(normalize_key(DEMO_KEY), 100, 100, self.config)["budgetPercent"])

    def test_utc_period_rollover(self):
        # Sunday Jan 31 to Monday Feb 1 2027: all three periods change.
        import datetime
        before = datetime.datetime(2027, 1, 31, 23, 59, 59, tzinfo=datetime.timezone.utc).timestamp()
        view = key_view(normalize_key(DEMO_KEY), before, before+2, self.config)
        for field in ("day", "week", "month", "byokDay", "byokWeek", "byokMonth", "budgetPercent"):
            self.assertIsNone(view[field])
        self.assertEqual(view["state"], "stale")
        self.assertEqual(view["limitRemaining"], 61.58)

    def test_stale_and_error_keep_observation(self):
        data = normalize_key(DEMO_KEY)
        self.assertEqual(key_view(data, 100, 279, self.config)["state"], "fresh")
        self.assertEqual(key_view(data, 100, 280, self.config)["state"], "stale")
        view = key_view(data, 100, 281, self.config, "auth")
        self.assertEqual(view["state"], "error")
        self.assertEqual(view["month"], 38.42)
        self.assertEqual(view["ageSeconds"], 181)


class ClientTests(unittest.TestCase):
    def test_correct_fixed_endpoint_and_header(self):
        opener = Mock()
        opener.open.return_value = io.BytesIO(b'{"data":{"usage_daily":4}}')
        self.assertEqual(Client(opener).get("key", "private-test-sentinel")["usage_daily"], 4)
        request = opener.open.call_args.args[0]
        self.assertEqual(request.full_url, "https://openrouter.ai/api/v1/key")
        self.assertEqual(request.get_header("Authorization"), "Bearer private-test-sentinel")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 8)
        with self.assertRaises(ValueError):
            Client(opener).get("activity", "unused")
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.test"))

    def test_safe_http_and_parse_errors(self):
        for code, expected in [(401,"auth"),(403,"auth"),(429,"rate_limited"),(500,"upstream"),(302,"upstream")]:
            opener = Mock()
            opener.open.side_effect = urllib.error.HTTPError("secret",code,"secret",{},None)
            with self.assertRaisesRegex(SourceError, "^" + expected + "$"):
                Client(opener).get("credits", "test")
        for body in (b'not-json', b'{"data":[]}', b'x'*65537, b'[]'):
            opener = Mock(); opener.open.return_value = io.BytesIO(body)
            with self.assertRaisesRegex(SourceError, "invalid_response"):
                Client(opener).get("key", "test")
        opener.open.side_effect = TimeoutError("private-test-sentinel")
        with self.assertRaisesRegex(SourceError, "^connection$"):
            Client(opener).get("key", "test")


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.config = read_config(EXAMPLE)
        self.clock = Mock(return_value=1000)
        self.client = Mock()
        self.client.get.return_value = DEMO_KEY
        self.loader = Mock(return_value="private-test-sentinel")
        self.monitor = Monitor(self.config, client=self.client, loader=self.loader,
                               clock=self.clock, monotonic=self.clock)

    def test_demo_never_reads_credentials_or_network(self):
        self.monitor.demo = True
        self.monitor.refresh("default")
        self.monitor.refresh("management")
        self.loader.assert_not_called(); self.client.get.assert_not_called()
        self.assertTrue(self.monitor.snapshot()["demo"])

    def test_failure_backoff_keeps_timestamp_then_recovers(self):
        self.monitor.refresh("default")
        original = self.monitor.snapshot()["updatedAt"]
        self.clock.return_value = 1060
        self.client.get.side_effect = SourceError("auth")
        self.monitor.refresh("default")
        self.assertEqual(self.monitor.snapshot()["updatedAt"], original)
        self.assertEqual(self.monitor.snapshot()["ageSeconds"], 60)
        self.assertEqual(self.monitor.snapshot()["state"], "error")
        self.assertEqual(self.monitor.slots["default"]["due"],1090)
        self.monitor.refresh("default")
        self.assertEqual(self.monitor.slots["default"]["due"],1120)
        self.client.get.side_effect = None
        self.clock.return_value = 1180
        self.monitor.refresh("default")
        self.assertEqual(self.monitor.snapshot()["state"], "fresh")
        self.assertEqual(self.monitor.snapshot()["ageSeconds"], 0)

    def test_optional_account_independent_failure(self):
        self.config["account_view"] = True
        self.monitor.refresh("default")
        self.client.get.side_effect = SourceError("auth")
        self.monitor.refresh("management")
        result = self.monitor.snapshot()
        self.assertEqual((result["state"], result["accountState"]), ("fresh", "error"))
        self.assertIsNone(result["accountBalance"])
        self.loader.assert_called_with("management")
        self.assertEqual(self.client.get.call_args.args[0], "credits")

    def test_cold_start_and_restart_have_no_fabricated_zero(self):
        data = self.monitor.snapshot()
        self.assertEqual(data["state"], "no_data")
        self.assertIsNone(data["month"])
        self.assertFalse(data["accountEnabled"])
        self.assertIsNone(data["accountAgeSeconds"])

    def test_untrusted_exception_does_not_leak(self):
        self.loader.side_effect = RuntimeError("private-test-sentinel")
        self.monitor.refresh("default")
        self.assertNotIn("private-test-sentinel", json.dumps(self.monitor.snapshot()))

    def test_http_contract_and_no_raw_provider_fields(self):
        self.monitor.refresh("default")
        server = make_server(self.monitor, "127.0.0.1", 0)
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        try:
            base = "http://127.0.0.1:" + str(server.server_port)
            with urllib.request.urlopen(base + "/api/openpulse") as response:
                body = response.read().decode()
                self.assertEqual(response.headers["Cache-Control"], "no-store")
                self.assertEqual(json.loads(body)["month"],38.42)
                self.assertNotIn("private-test-sentinel",body)
                self.assertNotIn("Authorization",body)
            for path in ("/api/openpulse?key=-1", "/api/openpulse?key=8", "/api/openpulse?key=bad"):
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(base+path)
                self.assertEqual(error.exception.code,400)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_config_rejects_secrets_and_invalid_thresholds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"config.json"
            for mutate in (lambda c:c.update(api_key="secret"),
                           lambda c:c["keys"][0].update(monthly_budget_usd=0),
                           lambda c:c["keys"][0].update(warning_at=.95),
                           lambda c:c["keys"][0].update(id="management"),
                           lambda c:c["keys"][0].update(credential_id="invalid/key"),
                           lambda c:c.update(management_credential_id=True)):
                config = read_config(EXAMPLE);mutate(config);path.write_text(json.dumps(config))
                with self.assertRaises(ValueError):
                    read_config(path)

    def test_local_connection_saves_only_to_keychain_and_config_has_no_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"config.json"
            server = make_server(self.monitor,"127.0.0.1",0,connect_path=path)
            thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            body="api_key=private-test-sentinel&management_key=&name=Test&budget=25"
            authority=f"127.0.0.1:{server.server_port}"
            def post(origin, host=authority):
                connection=http.client.HTTPConnection("127.0.0.1",server.server_port)
                connection.request("POST","/connect",body,{"Host":host,"Origin":origin,
                    "Content-Type":"application/x-www-form-urlencoded"})
                response=connection.getresponse();status=response.status;response.read();connection.close()
                return status
            try:
                with patch("tools.openpulse.credentials.keychain") as save:
                    self.assertEqual(post("https://attacker.test"),403)
                    self.assertEqual(post("http://"+authority,"attacker.test"),403)
                    save.assert_not_called()
                    self.assertEqual(post("http://"+authority),204)
                    saved_config = read_config(path)
                    ref = saved_config["keys"][0]["credential_id"]
                    self.assertNotEqual(ref, "default")
                    save.assert_called_once_with(ref,"private-test-sentinel")
                    self.assertNotIn("private-test-sentinel",path.read_text())
                    self.assertEqual(read_config(path)["keys"][0]["monthly_budget_usd"],25)
                    self.assertEqual(self.monitor.snapshot()["state"],"no_data")
                    self.assertEqual(path.stat().st_mode & 0o777,0o600)
                    self.monitor.demo=True
                    self.assertEqual(post("http://"+authority),403)
            finally:
                server.shutdown();server.server_close();thread.join()

    def test_failed_connection_never_replaces_active_credentials(self):
        for failure in ("key", "management", "config", "cleanup"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as directory:
                monitor = Monitor(read_config(EXAMPLE), client=self.client)
                path = Path(directory) / "config.json"
                path.write_text(json.dumps(monitor.config))
                original_config = path.read_bytes()
                store = {"default": "old-key", "management": "old-management"}
                monitor.loader = Mock(side_effect=store.get)
                monitor.refresh("default")
                original = monitor.snapshot()

                def keychain(account, secret=None, *, remove=False,
                             failure=failure, store=store, monitor=monitor):
                    self.assertNotIn(account, ("default", "management"))
                    if remove:
                        if failure == "cleanup":
                            raise RuntimeError("cleanup denied")
                        store.pop(account, None)
                    else:
                        if failure == "key" or (failure == "management" and account.startswith("mgmt_")):
                            raise RuntimeError("write denied")
                        store[account] = secret
                        # A concurrent poll still uses the old immutable reference.
                        monitor.refresh("default")
                        monitor.loader.assert_called_with("default")

                with patch("tools.openpulse.credentials.keychain", side_effect=keychain), \
                     patch("tools.openpulse.service.os.replace", side_effect=OSError("replace denied")):
                    with self.assertRaisesRegex(RuntimeError, "connection_not_saved"):
                        save_connection(monitor, path, "new-key", "new-management", "New account", 100)
                self.assertEqual(path.read_bytes(), original_config)
                self.assertEqual(store["default"], "old-key")
                self.assertEqual(store["management"], "old-management")
                self.assertEqual(monitor.snapshot()["name"], original["name"])
                self.assertEqual(monitor.snapshot()["budget"], original["budget"])
                self.assertEqual(monitor.snapshot()["month"], original["month"])
                self.assertNotIn("credential_id", monitor.config["keys"][0])
                self.assertEqual(monitor.generation, 0)
                self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_successful_connection_commits_both_references_and_discards_inflight_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            store = {"default": "old-key", "management": "old-management"}
            self.monitor.loader = Mock(side_effect=store.get)

            def stage(account, secret):
                self.assertNotIn(account, store)
                store[account] = secret

            def old_request(endpoint, token):
                self.assertEqual(token, "old-key")
                with patch("tools.openpulse.credentials.keychain", side_effect=stage):
                    save_connection(self.monitor, path, "new-key", "new-management", "New account", 100)
                return DEMO_KEY

            self.client.get.side_effect = old_request
            self.monitor.refresh("default")
            self.assertIsNone(self.monitor.snapshot()["month"])
            self.assertEqual(self.monitor.snapshot()["name"], "New account")
            config = read_config(path)
            self.assertEqual(config, self.monitor.config)
            self.assertEqual(store["default"], "old-key")
            self.assertEqual(store["management"], "old-management")
            self.client.get.side_effect = None
            self.client.get.return_value = DEMO_KEY
            self.monitor.refresh("default")
            self.client.get.assert_called_with("key", "new-key")
            self.monitor.loader.assert_called_with(config["keys"][0]["credential_id"])
            self.client.get.return_value = {"total_credits": 40, "total_usage": 3}
            self.monitor.refresh("management")
            self.client.get.assert_called_with("credits", "new-management")
            self.assertEqual(self.monitor.snapshot()["accountBalance"], 37)
            self.assertNotIn("new-key", path.read_text())
            self.assertNotIn("new-management", path.read_text())
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_keychain_namespace_and_env_are_isolated(self):
        self.assertEqual(SERVICE, b"org.openpulse.openrouter")
        with patch.dict("os.environ", {"OPENPULSE_KEY_DEFAULT":"test"}), patch("tools.openpulse.credentials.keychain") as kc:
            self.assertEqual(load("default"),"test");kc.assert_not_called()


if __name__ == "__main__":
    unittest.main()
