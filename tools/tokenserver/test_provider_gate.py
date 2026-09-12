"""The startup gate: either provider is enough to serve.

The background is a real bug, not a hypothesis. Startup waited -- before
bind -- in a 30-second loop until ``~/.claude/projects`` existed. Codex
figures are read from ``CODEX_SESSIONS`` and do not need that directory at
all, so on a Codex-only machine the port never opened, nothing was
advertised over DNS-SD, and the panel found a computer it could not ask.
The README's prerequisite says "Claude Code and/or Codex"; that was true
on the setup page and false in reality.

The tests below hold both halves: that the gate lets either directory
through, and that the server can actually serve in that state -- a gate
that opens onto a service that crashes anyway would be no improvement.
"""

import inspect
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver import codex_usage, tokenserver


def setUpModule():
    # A statusLine bridge installed on the developer's own machine must
    # not feed these snapshots: point the reader at a file that is absent.
    tokenserver._claude_statusline_path_override = (
        Path(tempfile.gettempdir()) / "vibepulse-no-statusline-sample.json")


def tearDownModule():
    tokenserver._claude_statusline_path_override = None


class ProviderGateTest(unittest.TestCase):
    """``_any_provider_dir``: either provider is enough."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.claude = root / "claude" / "projects"
        self.codex = root / "codex" / "sessions"
        self.missing = root / "nowhere"

    def _gate(self, projects_dir, codex_dir):
        with mock.patch.object(tokenserver, "CODEX_SESSIONS", codex_dir):
            return tokenserver._any_provider_dir(projects_dir)

    def test_claude_only_passes(self):
        self.claude.mkdir(parents=True)
        self.assertTrue(self._gate(self.claude, self.missing))

    def test_codex_only_passes(self):
        """The core case: Codex installed, Claude Code not at all."""
        self.codex.mkdir(parents=True)
        self.assertTrue(self._gate(self.missing, self.codex))

    def test_both_present_passes(self):
        self.claude.mkdir(parents=True)
        self.codex.mkdir(parents=True)
        self.assertTrue(self._gate(self.claude, self.codex))

    def test_neither_present_waits(self):
        """The wait stays -- it was removed once and produced a silent
        launchd respawn every ten seconds that filled the log. The gate
        must be false here, not raise."""
        self.assertFalse(self._gate(self.missing, self.missing))

    def test_a_file_is_not_a_directory(self):
        """A file with the right name is not enough; ``is_dir`` is the rule."""
        self.missing.parent.mkdir(parents=True, exist_ok=True)
        self.missing.write_text("not a directory")
        self.assertFalse(self._gate(self.missing, self.missing))


class CodexHomeTest(unittest.TestCase):
    """``CODEX_SESSIONS`` must follow ``CODEX_HOME``, not a hard-coded ``~/.codex``.

    This is not a hypothetical case: ``run-windows-task.ps1`` exports
    ``CODEX_HOME`` before the service starts, and the desktop app sets it.
    With a hard-coded path the gate saw a directory that did not exist,
    waited forever, and the port never opened -- exactly the bug this change
    exists to fix, still present for the one configuration that is
    documented as supported.
    """

    def test_sessions_dir_follows_codex_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "managed-codex-home"
            (home / "sessions").mkdir(parents=True)

            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home)}):
                resolved = codex_usage.default_sessions_dir()

            self.assertEqual(resolved, home / "sessions")
            # The gate must open on that directory, with no Claude directory.
            with mock.patch.object(tokenserver, "CODEX_SESSIONS", resolved):
                self.assertTrue(
                    tokenserver._any_provider_dir(Path(temp_dir) / "no-claude"))

    def test_tokenserver_resolves_through_the_shared_source(self):
        """The constant must come from the same function as the month scan.

        Guards against someone reintroducing a hard-coded path: the gate,
        rate limits, agent status and Max Tracker would then read a
        different profile than the month value, which is exactly the silent
        split ``default_sessions_dir`` was written to close.
        """
        with mock.patch.object(codex_usage, "DEFAULT_SESSIONS_DIR",
                               Path("/sentinel/sessions")):
            self.assertEqual(codex_usage.default_sessions_dir(),
                             Path("/sentinel/sessions"))
        # The import-time value must have gone through the resolver, not past it.
        self.assertEqual(tokenserver.CODEX_SESSIONS.name, "sessions")


class CodexOnlySnapshotTest(unittest.TestCase):
    """The server must serve with an absent Claude directory.

    ``Path.glob`` on a directory that does not exist returns nothing without
    raising, so the Claude figures come out as zero instead of an error.
    That is the difference between "Codex-only is a fully supported state"
    and "we opened the port onto a service that falls over on the first
    request".
    """

    def test_snapshot_without_claude_dir_is_zero_not_an_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            absent = Path(temp_dir) / "claude-never-ran"
            self.assertFalse(absent.exists())
            # ``_compute`` reaches ``codex_usage.month_value()`` WITHOUT a
            # directory, which then falls back to the real ``~/.codex/sessions``.
            # Without this seam the test scans the developer's own rollout
            # history recursively: slow, dependent on private machine state,
            # and it leaves the codex_usage cache warm for the next test file.
            # Point it at an empty temporary directory instead.
            codex_root = Path(temp_dir) / "no-codex-here"
            codex_root.mkdir()

            # ``_compute`` is deliberately NOT mocked -- it is the thing that
            # must meet the absent directory. Only network and CLI parts are
            # stubbed.
            with mock.patch.object(codex_usage, "DEFAULT_SESSIONS_DIR",
                                   codex_root), \
                    mock.patch.object(tokenserver, "get_limits",
                                      return_value={}), \
                    mock.patch.object(tokenserver, "_read_codex_limits",
                                      return_value={}), \
                    mock.patch.object(
                        tokenserver, "_persist_quota_records_async"):
                tokenserver._last_result = None
                tokenserver._last_computed = 0.0
                snapshot = tokenserver.get_snapshot(absent,
                                                    now_ts=1_800_000_000)

            self.assertIsInstance(snapshot, dict)
            # Zero rows read, not an exception and not old figures.
            self.assertEqual(snapshot.get("monthTokens"), 0)

    def tearDown(self):
        # Both caches are module globals: do not leave them warm for the next test file.
        tokenserver._last_result = None
        tokenserver._last_computed = 0.0
        codex_usage.reset_cache()


class CodexOnlyEndToEndTest(unittest.TestCase):
    """End to end: a Codex-only machine must produce a servable response.

    The gate tests above prove the logic; this proves that a real
    ``/api/tokens`` request goes through ``Handler.do_GET`` with a Claude
    directory that does not exist and a Codex directory that does.

    It does not replace a real Codex-only computer -- nobody has seen the
    port open, DNS-SD advertise and the panel ask on a fresh install. It
    closes the part CI can actually prove: that the response is 200, that
    Claude's percentages are null (dashes on the screen, not zeros), and
    that ``claudeSourcePresent`` says why the volume figures are zero.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.claude_absent = root / "claude" / "projects"   # never created
        self.codex_sessions = root / "codex" / "sessions"
        self.codex_sessions.mkdir(parents=True)
        self.addCleanup(codex_usage.reset_cache)

    def _tokens_payload(self):
        handler = tokenserver.Handler.__new__(tokenserver.Handler)
        handler.path = "/api/tokens"
        handler.projects_dir = self.claude_absent
        handler.max_tracker_store = None
        handler.agent_status = mock.Mock()
        handler._send = mock.Mock()

        with mock.patch.object(codex_usage, "DEFAULT_SESSIONS_DIR",
                               self.codex_sessions), \
                mock.patch.object(tokenserver, "CODEX_SESSIONS",
                                  self.codex_sessions), \
                mock.patch.object(tokenserver, "get_limits",
                                  return_value={}), \
                mock.patch.object(tokenserver, "_read_codex_limits",
                                  return_value={}), \
                mock.patch.object(tokenserver,
                                  "_persist_quota_records_async"):
            tokenserver._last_result = None
            tokenserver._last_computed = 0.0
            # Since issue #62 the first request answers with a placeholder
            # (or, without the Accepts header, the error form) while the
            # scan runs in the background. This test is about what the
            # completed Codex-only scan serves, so run that scan first.
            tokenserver._refresh_usage_totals(self.claude_absent)
            handler.do_GET()

        handler._send.assert_called_once()
        code, payload = handler._send.call_args.args
        return code, payload

    def test_serves_200_with_no_claude_directory(self):
        code, payload = self._tokens_payload()
        self.assertEqual(code, 200)
        # v=2 is the contract tokens_parse.c requires (`v != 2.0` rejects).
        self.assertEqual(payload["v"], 2)

    def test_claude_percentages_are_null_so_the_panel_shows_dashes(self):
        """The honesty invariant where it shows: the percentages become null,
        and ``pct_or_null`` in tokens_parse.c turns null into has_pct=0, which
        the presenter renders as "–" and "USAGE UNAVAILABLE"."""
        _, payload = self._tokens_payload()
        for key in ("claudeSessionPct", "claudeWeekPct",
                    "claudeModelWeekPct"):
            self.assertIsNone(payload[key], key)

    def test_volume_zeroes_are_marked_as_an_absent_source(self):
        """The four counters cannot become null without older panels
        failing to parse, so the flag carries the truth instead."""
        _, payload = self._tokens_payload()
        self.assertFalse(payload["claudeSourcePresent"])
        self.assertEqual(payload["dayTokens"], 0)
        self.assertEqual(payload["monthTokens"], 0)

    def test_a_present_claude_directory_reports_the_source_as_present(self):
        """The check the other way round: the flag follows the directory,
        it is not hard-coded to false by the test setup."""
        self.claude_absent.mkdir(parents=True)
        _, payload = self._tokens_payload()
        self.assertTrue(payload["claudeSourcePresent"])

    def tearDown(self):
        tokenserver._last_result = None
        tokenserver._last_computed = 0.0


class WarmupLogHonestyTest(unittest.TestCase):
    """The startup event must not print zeros as measurements either.

    ``_first_scan_warmup`` lives inside ``main()`` and cannot be called in
    isolation, so the guard reads the source -- the same pattern as the
    ``BoundedThreadingHTTPServer`` test in test_tokenserver.py. That is
    enough for what it must catch: someone putting back an unconditional
    "0 tokens today" line.
    """

    def test_warmup_log_consults_the_source_flag(self):
        source = inspect.getsource(tokenserver.main)
        self.assertIn("claudeSourcePresent", source)
        # The counter formatting must come after the flag check, not
        # before it.
        flag_at = source.index("claudeSourcePresent")
        counters_at = source.index("snap['dayTokens']")
        self.assertLess(flag_at, counters_at)


if __name__ == "__main__":
    unittest.main()
