"""The smoke test's own verdict: every comb step against rigged servers
and files.

No test class touches the network except through a local canned HTTP
server on port 0, and none touches the home directory -- all state lives
in temporary directories.
"""

import contextlib
import io
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __package__:
    from . import smoke
else:
    import smoke


HEALTHY_ROOT = {
    "service": "torget-tokenserver",
    "rev": "abc1234",
    "startedAt": "2026-08-13T20:00:00+02:00",
    "claudeProbe": "usage_http_200 + ok",
    "claudeCredential": {"status": "ready", "expiresInMin": 480},
    "ratelimitHeaders": [],
    "unknownRateLimitBuckets": [],
    "usageComputeOk": True,
    "usageComputeFailingForS": None,
}
# The healthy answers ARE the sim fixtures -- the same payloads the
# firmware's parsers demonstrably accept (they are fed through exactly
# those parsers in the simulator and the C tests). If the contract drifts
# these tests fail instead of the smoke test lying green.
_FIXTURES = Path(__file__).resolve().parents[2] / "sim-fixtures"
HEALTHY_TOKENS = json.loads((_FIXTURES / "tokens.json").read_text())
HEALTHY_AGENTS = json.loads(
    (_FIXTURES / "agent-status-idle.json").read_text())
HEALTHY_TRACKER = json.loads(
    (_FIXTURES / "max-tracker-live-shape.json").read_text())


@contextlib.contextmanager
def canned_server(payloads, status=200):
    """Local HTTP server on port 0 answering with rigged JSON bodies."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(payloads.get(self.path,
                                           {"error": "not found"})).encode()
            self.send_response(status if self.path in payloads else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt, *args):
            pass

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}"
    finally:
        srv.shutdown()
        srv.server_close()


def levels(results):
    return [level for level, _ in results]


class ServerCheckTests(unittest.TestCase):
    def test_healthy_server_with_matching_rev_is_all_ok(self):
        with canned_server({"/": HEALTHY_ROOT}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        self.assertEqual(levels(results), [smoke.OK, smoke.OK, smoke.OK])

    def test_unreachable_server_is_fail(self):
        results = smoke.check_server("http://127.0.0.1:9")  # discard-porten
        self.assertEqual(levels(results), [smoke.FAIL])

    def test_rev_mismatch_warns_about_stale_worktree(self):
        with canned_server({"/": HEALTHY_ROOT}) as base:
            results = smoke.check_server(base, checkout_rev="fff9999")
        self.assertIn(smoke.WARN, levels(results))
        self.assertIn("WorkingDirectory", results[0][1])

    def test_probe_status_other_than_ok_warns_with_runbook_pointer(self):
        root = dict(HEALTHY_ROOT, claudeProbe="usage_http_401")
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        warn = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warn), 1)
        self.assertIn("usage_http_401", warn[0])
        self.assertIn("agent-setup", warn[0])

    def test_probe_warning_names_the_backoff_state_when_served(self):
        root = dict(HEALTHY_ROOT, claudeProbe="usage_http_401",
                    claudeProbeStreak=3, claudeProbeIntervalS=480,
                    claudeProbeCooldownLeftS=None)
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        warn = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warn), 1)
        self.assertIn("3 misses in a row", warn[0])
        self.assertIn("480 s", warn[0])
        self.assertNotIn("429-vila", warn[0])

    def test_expiring_credential_warns_before_probe_fails(self):
        root = dict(HEALTHY_ROOT, claudeCredential={
            "status": "expiring", "expiresInMin": 19})
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        warnings = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warnings), 1)
        self.assertIn("19 min", warnings[0])
        self.assertIn("new Claude Code CLI turn", warnings[0])

    def test_expired_saved_credential_does_not_hide_live_process_source(self):
        root = dict(HEALTHY_ROOT, claudeCredential={
            "status": "expired", "expiresInMin": 0})
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        warnings = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warnings), 1)
        self.assertIn("current quota source is live", warnings[0])
        self.assertIn("next client gap", warnings[0])
        self.assertIn("no server restart needed", warnings[0])

    def test_expired_saved_credential_with_dead_probe_is_not_called_live(self):
        root = dict(HEALTHY_ROOT, claudeProbe="token_expired_15:34",
                    claudeCredential={"status": "expired",
                                      "expiresInMin": 0})
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        warnings = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warnings), 2)
        self.assertFalse(any("quota source is live" in text
                             for text in warnings))
        self.assertTrue(any("rereads it automatically" in text
                            for text in warnings))

    def test_unknown_buckets_warn(self):
        root = dict(HEALTHY_ROOT, unknownRateLimitBuckets=["7d_haiku"])
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        self.assertIn(smoke.WARN, levels(results))

    def test_wrong_service_on_port_is_fail(self):
        with canned_server({"/": {"service": "annan"}}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        self.assertEqual(levels(results), [smoke.FAIL])

    def test_non_object_json_on_port_is_fail_not_a_traceback(self):
        # A foreign service can answer with a list, a number or null --
        # exactly the wrong-service scenario must not crash the diagnosis.
        for foreign in ([1, 2, 3], 42, None, "text"):
            with self.subTest(foreign=foreign):
                with canned_server({"/": foreign}) as base:
                    results = smoke.check_server(base,
                                                 checkout_rev="abc1234")
                self.assertEqual(levels(results), [smoke.FAIL])
                self.assertIn("wrong service", results[0][1])

    def test_source_fingerprint_mismatch_warns_about_edited_code(self):
        # The rev cannot see a dirty worktree or an edit after start --
        # the fingerprint can. Compared only when both sides exist.
        root = dict(HEALTHY_ROOT, srcFingerprint="aaaa11112222")
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234",
                                         checkout_src="bbbb33334444")
        warn = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warn), 1)
        self.assertIn("different source", warn[0])

        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234",
                                         checkout_src="aaaa11112222")
        self.assertEqual(levels(results), [smoke.OK, smoke.OK, smoke.OK])

    def test_missing_fingerprint_on_older_server_is_not_judged(self):
        with canned_server({"/": HEALTHY_ROOT}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234",
                                         checkout_src="bbbb33334444")
        self.assertEqual(levels(results), [smoke.OK, smoke.OK, smoke.OK])

    def test_frozen_usage_compute_is_fail(self):
        # OBS-08: figures that froze but look fresh are the worst kind of
        # error -- the smoke test must shout, not whisper.
        root = dict(HEALTHY_ROOT, usageComputeOk=False,
                    usageComputeFailingForS=612)
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        fails = [text for level, text in results if level == smoke.FAIL]
        self.assertEqual(len(fails), 1)
        self.assertIn("frozen figures", fails[0])
        self.assertIn("612", fails[0])

    def test_first_scan_in_progress_is_a_warning_not_a_failure(self):
        # Issue #62: placeholder counters during the first scan are named,
        # bounded and expected -- the smoke test says so, and says the
        # quota is live, without escalating to FAIL.
        root = dict(HEALTHY_ROOT,
                    usageTotals={"state": "refreshing", "sinceS": 41})
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        warnings = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warnings), 1)
        self.assertIn("first scan in progress", warnings[0])
        self.assertIn("41", warnings[0])
        self.assertNotIn(smoke.FAIL, levels(results))

    def test_a_failing_state_save_is_a_warning_with_its_duration(self):
        root = dict(HEALTHY_ROOT, maxTrackerSaveOk=False,
                    maxTrackerSaveFailingForS=77)
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        warnings = [text for level, text in results if level == smoke.WARN]
        self.assertEqual(len(warnings), 1)
        self.assertIn("cannot save", warnings[0])
        self.assertIn("77", warnings[0])

    def test_older_server_without_compute_field_is_not_judged(self):
        root = {k: v for k, v in HEALTHY_ROOT.items()
                if not k.startswith("usageCompute")}
        with canned_server({"/": root}) as base:
            results = smoke.check_server(base, checkout_rev="abc1234")
        self.assertEqual(levels(results), [smoke.OK, smoke.OK, smoke.OK])


class EndpointCheckTests(unittest.TestCase):
    def test_three_healthy_endpoints_are_ok(self):
        payloads = {"/api/tokens": HEALTHY_TOKENS,
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        with canned_server(payloads) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results), [smoke.OK] * 3)

    def test_error_form_is_fail_even_on_http_500(self):
        payloads = {"/api/tokens": {"error": "internal server error"},
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        with canned_server(payloads, status=500) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results)[0], smoke.FAIL)
        self.assertIn("internal server error", results[0][1])

    def test_stale_flags_warn(self):
        payloads = {"/api/tokens": dict(HEALTHY_TOKENS,
                                        claudeWeekStale=True),
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": dict(HEALTHY_TRACKER, stale=True)}
        with canned_server(payloads) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results),
                         [smoke.WARN, smoke.OK, smoke.WARN])

    def test_contract_version_skew_is_fail_not_a_false_green(self):
        # An old server process serving v1 looks HTTP-healthy while the
        # screen's parser rejects everything (tokens_parse.c requires
        # v == 2) -- exactly the "smoke green, display frozen" case that
        # must become a FAIL.
        payloads = {"/api/tokens": dict(HEALTHY_TOKENS, v=1),
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        with canned_server(payloads) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results),
                         [smoke.FAIL, smoke.OK, smoke.OK])
        self.assertIn("contract version", results[0][1])

    def test_missing_mandatory_field_is_fail(self):
        broken = {k: v for k, v in HEALTHY_TOKENS.items()
                  if k != "monthTokens"}
        payloads = {"/api/tokens": broken,
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        with canned_server(payloads) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results),
                         [smoke.FAIL, smoke.OK, smoke.OK])
        self.assertIn("monthTokens", results[0][1])

    def test_non_200_with_healthy_body_is_fail(self):
        # torget_http.c rejects everything but HTTP 200 -- a proxy or
        # cache answering 502 with a healthy-looking body must not get a
        # green smoke test.
        payloads = {"/api/tokens": HEALTHY_TOKENS,
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        with canned_server(payloads, status=502) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results), [smoke.FAIL] * 3)
        for _, text in results:
            self.assertIn("502", text)

    def test_agents_as_list_is_fail(self):
        # agent_status_parse.c requires agents to be an object with claude
        # and codex -- a list looks HTTP-healthy but the screen rejects it.
        payloads = {"/api/tokens": HEALTHY_TOKENS,
                    "/api/agent-status": dict(HEALTHY_AGENTS, agents=[]),
                    "/api/max-tracker": HEALTHY_TRACKER}
        with canned_server(payloads) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results),
                         [smoke.OK, smoke.FAIL, smoke.OK])
        self.assertIn("agents", results[1][1])

    def test_empty_provider_object_is_fail(self):
        payloads = {"/api/tokens": HEALTHY_TOKENS,
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": dict(HEALTHY_TRACKER, claude={})}
        with canned_server(payloads) as base:
            results = smoke.check_endpoints(base)
        self.assertEqual(levels(results),
                         [smoke.OK, smoke.OK, smoke.FAIL])
        self.assertIn("provider objects", results[2][1])


class LogFileCheckTests(unittest.TestCase):
    def test_missing_file_is_a_warning_not_a_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = smoke.check_log_file(Path(tmp) / "finns-inte.log")
        self.assertEqual(levels(results), [smoke.WARN])

    def test_small_clean_file_is_ok(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.log"
            f.write_text("2026-08-13 INFO serving http://0.0.0.0:8737\n")
            results = smoke.check_log_file(f)
        self.assertEqual(levels(results), [smoke.OK])

    def test_tracebacks_and_respawn_churn_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.log"
            f.write_text(
                "Traceback (most recent call last)\n boom\n" +
                "serving http://0.0.0.0:8737\n" * 12)
            results = smoke.check_log_file(f)
        self.assertEqual(levels(results),
                         [smoke.OK, smoke.WARN, smoke.WARN])
        self.assertIn("traceback", results[1][1])
        self.assertIn("respawn", results[2][1])

    def test_rotated_tail_evidence_is_still_seen(self):
        # Right after a rotation the fresh evidence lives in .old -- a
        # clean, newly truncated file must not hide it.
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.log"
            f.write_text("serving http://0.0.0.0:8737\n")
            (Path(tmp) / "t.log.old").write_text(
                "Traceback (most recent call last)\n boom\n" +
                "serving http://0.0.0.0:8737\n" * 11)
            results = smoke.check_log_file(f)
        self.assertEqual(levels(results),
                         [smoke.OK, smoke.WARN, smoke.WARN])
        self.assertIn(".old", results[1][1])
        self.assertIn("respawn", results[2][1])


VALID_STATE = {
    "usage-history.json": '{"v": 1, "samples": []}',
    "quota-cache.json": '{"v": 1, "records": []}',
    "max-tracker.json": '{"claude": {}, "codex": {}}',
}


class StateFileCheckTests(unittest.TestCase):
    def _write_state(self, tmp, **overrides):
        for name in smoke.STATE_FILES:
            (Path(tmp) / name).write_text(
                overrides.get(name, VALID_STATE[name]))

    def test_valid_files_are_ok_and_fresh_history_stays_quiet(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_state(tmp)
            results = smoke.check_state_files(tmp, now_ts=time.time())
        self.assertEqual(levels(results), [smoke.OK] * 3)

    def test_valid_json_with_wrong_shape_is_fail(self):
        # Valid JSON with the wrong shape is reset JUST AS silently as
        # corrupt bytes by the loaders -- the smoke test must not call it
        # healthy.
        with tempfile.TemporaryDirectory() as tmp:
            self._write_state(tmp, **{
                "usage-history.json": '{"v": 2, "samples": []}',
                "quota-cache.json": "{}",
            })
            results = smoke.check_state_files(tmp, now_ts=time.time())
        fails = [text for level, text in results if level == smoke.FAIL]
        self.assertEqual(len(fails), 2)
        for text in fails:
            self.assertIn("WRONG SHAPE", text)

    def test_corrupt_state_file_is_fail_with_forensics_pointer(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_state(tmp, **{"max-tracker.json": "{trasig"})
            results = smoke.check_state_files(tmp, now_ts=time.time())
        fails = [text for level, text in results if level == smoke.FAIL]
        self.assertEqual(len(fails), 1)
        self.assertIn("CORRUPT", fails[0])
        self.assertIn("forensics", fails[0])

    def test_missing_files_and_missing_dir_warn(self):
        with tempfile.TemporaryDirectory() as tmp:
            results = smoke.check_state_files(Path(tmp) / "finns-inte")
            self.assertEqual(levels(results), [smoke.WARN])
            results = smoke.check_state_files(tmp)
            self.assertEqual(levels(results), [smoke.WARN] * 3)

    def test_stale_usage_history_warns(self):
        with tempfile.TemporaryDirectory() as tmp:
            self._write_state(tmp)
            results = smoke.check_state_files(
                tmp, now_ts=time.time() + 25 * 3600)
        self.assertEqual(
            levels(results), [smoke.OK, smoke.WARN, smoke.OK, smoke.OK])


class RunExitCodeTests(unittest.TestCase):
    def _run(self, payloads, status=200):
        with tempfile.TemporaryDirectory() as tmp:
            log_file = Path(tmp) / "t.log"
            log_file.write_text("serving http://0.0.0.0:8737\n")
            state = Path(tmp) / "state"
            state.mkdir()
            for name in smoke.STATE_FILES:
                (state / name).write_text(VALID_STATE[name])
            out = io.StringIO()
            with canned_server(payloads, status=status) as base:
                code = smoke.run(base, log_path=log_file, state_dir=state,
                                 checkout_rev="abc1234", out=out)
            return code, out.getvalue()

    def test_healthy_chain_exits_0(self):
        payloads = {"/": HEALTHY_ROOT,
                    "/api/tokens": HEALTHY_TOKENS,
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        code, text = self._run(payloads)
        self.assertEqual(code, 0, text)
        self.assertIn("smoke test:", text)
        self.assertNotIn("[FAIL]", text)

    def test_warnings_exit_1(self):
        payloads = {"/": dict(HEALTHY_ROOT, claudeProbe="not_run"),
                    "/api/tokens": HEALTHY_TOKENS,
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        code, text = self._run(payloads)
        self.assertEqual(code, 1, text)

    def test_error_form_exits_2(self):
        payloads = {"/": HEALTHY_ROOT,
                    "/api/tokens": {"error": "internal server error"},
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        code, text = self._run(payloads)
        self.assertEqual(code, 2, text)

    def test_compute_failure_still_checks_every_endpoint(self):
        # A reached but sick server: the compute FAIL must not hide
        # independent failures in the other feeds -- all three contracts
        # must be examined.
        payloads = {"/": dict(HEALTHY_ROOT, usageComputeOk=False,
                              usageComputeFailingForS=90),
                    "/api/tokens": HEALTHY_TOKENS,
                    "/api/agent-status": HEALTHY_AGENTS,
                    "/api/max-tracker": HEALTHY_TRACKER}
        code, text = self._run(payloads)
        self.assertEqual(code, 2, text)
        self.assertIn("/api/agent-status: answers", text)
        self.assertIn("/api/max-tracker: answers", text)

    def test_unreachable_server_skips_endpoint_checks_and_exits_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = io.StringIO()
            code = smoke.run("http://127.0.0.1:9",
                             log_path=Path(tmp) / "t.log",
                             state_dir=Path(tmp) / "state",
                             checkout_rev="abc1234", out=out)
        self.assertEqual(code, 2)
        self.assertNotIn("/api/tokens", out.getvalue())


if __name__ == "__main__":
    unittest.main()
