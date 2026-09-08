#!/usr/bin/env python3
"""A Claude-only install must not require a Codex CLI (#65).

`docs/agent-setup.md` offers `claude` as a first-class provider choice, but the
install path demanded a Codex executable unconditionally, so the documented flow
was unreachable on any machine without the Codex CLI — which is every friend's
machine that runs Claude and nothing else. The combined error also blamed Python
for a missing Codex.

These tests drive `main()` with `codex=None`, the honest simulation of such a
host: the executable is absent everywhere, not merely off `PATH`. On a developer
Mac that distinction matters, because `resolve_codex_executable` also finds
`/Applications/ChatGPT.app/Contents/Resources/codex`, so hiding `codex` from
`PATH` proves nothing.
"""

from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_setup():
    spec = importlib.util.spec_from_file_location(
        "vibepulse_setup", REPO_ROOT / "tools" / "vibepulse_setup.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["vibepulse_setup"] = module
    spec.loader.exec_module(module)
    return module


setup = _load_setup()


class _Completed:
    def __init__(self, stdout=""):
        self.returncode = 0
        self.stdout = stdout
        self.stderr = ""


def _no_codex_run(argv, **kwargs):
    """Answer the Python probe; treat any Codex command as a failure.

    A Claude-only install owns no Codex resources, so it must issue no Codex
    commands. Raising here is what proves the marketplace/plugin/MCP steps were
    skipped rather than merely tolerated.
    """
    joined = " ".join(str(a) for a in argv)
    if "codex" in joined.lower():
        raise AssertionError(
            f"Claude-only install issued a Codex command: {argv!r}")
    if len(argv) >= 2 and argv[1] == "-c":
        return _Completed("vibepulse-python-3.11+\n")
    return _Completed()


class ClaudeOnlyInstallWithoutCodex(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.config = Path(self._tmp.name) / "config.json"
        self.addCleanup(self._tmp.cleanup)

    def _run(self, argv, run=_no_codex_run):
        out = io.StringIO()
        code = setup.main(
            argv, config_path=self.config, codex=None, run=run,
            stdout=out, stdin_isatty=False)
        return code, out.getvalue()

    def test_claude_only_install_succeeds_without_codex(self):
        code, text = self._run(
            ["install", "--providers", "claude", "--no-detail"])
        self.assertEqual(code, 0, text)
        self.assertIn("PASS", text)
        saved = json.loads(self.config.read_text())
        self.assertTrue(saved.get("claude_interactions"), saved)
        self.assertFalse(saved.get("codex_interactions"), saved)

    def test_claude_only_install_mutates_no_codex_resources(self):
        # _no_codex_run raises on any subprocess, so reaching PASS proves the
        # marketplace/plugin/MCP steps were skipped rather than merely tolerated.
        code, text = self._run(
            ["install", "--providers", "claude", "--no-detail"])
        self.assertEqual(code, 0, text)

    def test_claude_only_success_does_not_mention_codex_hooks(self):
        # Telling someone to trust hooks in a program they do not have reads as
        # a failed step.
        _, text = self._run(
            ["install", "--providers", "claude", "--no-detail"])
        self.assertNotIn("/hooks", text)

    def test_codex_install_still_fails_clearly_without_codex(self):
        code, text = self._run(
            ["install", "--providers", "codex", "--no-detail"], run=None)
        self.assertEqual(code, 1, text)
        self.assertIn("Codex", text)
        self.assertNotIn("Claude: ON", text)

    def test_missing_codex_message_does_not_blame_python(self):
        _, text = self._run(
            ["install", "--providers", "codex", "--no-detail"], run=None)
        # The original message named Python first for a missing Codex, sending
        # people to reinstall a Python that was present and valid.
        self.assertNotIn("Python or Codex", text)

    def test_both_providers_require_codex(self):
        code, _ = self._run(
            ["install", "--providers", "both", "--no-detail"], run=None)
        self.assertEqual(code, 1)

    def test_providers_need_codex_predicate(self):
        self.assertFalse(setup._providers_need_codex("claude"))
        self.assertFalse(setup._providers_need_codex("off"))
        self.assertTrue(setup._providers_need_codex("codex"))
        self.assertTrue(setup._providers_need_codex("both"))


if __name__ == "__main__":
    unittest.main()
