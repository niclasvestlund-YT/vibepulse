"""The startup gate: either provider is enough to serve.

Bakgrunden är ett riktigt fel, inte en hypotes. Starten väntade — före
bind — i en 30-sekundersloop tills ``~/.claude/projects`` fanns. Codex
siffror läses ur ``CODEX_SESSIONS`` och behöver den katalogen inte alls,
så på en Codex-only-maskin öppnades porten aldrig, inget annonserades
över DNS-SD, och panelen hittade en dator den inte kunde fråga. README:s
förutsättning säger "Claude Code och/eller Codex"; det var sant på
setup-sidan och falskt i verkligheten.

Testen nedan håller båda halvorna: att grinden släpper igenom när endera
katalogen finns, och att servern faktiskt kan servera i det läget — en
grind som öppnar mot en tjänst som ändå kraschar vore ingen förbättring.
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.tokenserver import tokenserver


class ProviderGateTest(unittest.TestCase):
    """``_any_provider_dir``: endera leverantören räcker."""

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
        """Kärnfallet: Codex installerat, Claude Code inte alls."""
        self.codex.mkdir(parents=True)
        self.assertTrue(self._gate(self.missing, self.codex))

    def test_both_present_passes(self):
        self.claude.mkdir(parents=True)
        self.codex.mkdir(parents=True)
        self.assertTrue(self._gate(self.claude, self.codex))

    def test_neither_present_waits(self):
        """Väntan finns kvar — den togs bort en gång och gav en tyst
        launchd-respawn var tionde sekund som fyllde loggen. Grinden ska
        vara falsk här, inte kasta."""
        self.assertFalse(self._gate(self.missing, self.missing))

    def test_a_file_is_not_a_directory(self):
        """En fil med rätt namn räcker inte; ``is_dir`` är hela regeln."""
        self.missing.parent.mkdir(parents=True, exist_ok=True)
        self.missing.write_text("inte en katalog")
        self.assertFalse(self._gate(self.missing, self.missing))


class CodexOnlySnapshotTest(unittest.TestCase):
    """Servern ska servera med en frånvarande Claude-katalog.

    ``Path.glob`` på en katalog som inte finns ger tomt resultat utan att
    kasta, så Claude-siffrorna blir noll i stället för ett fel. Det är
    skillnaden mellan "Codex-only är ett fullgott läge" och "vi öppnade
    porten mot en tjänst som ändå faller på första requesten".
    """

    def test_snapshot_without_claude_dir_is_zero_not_an_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            absent = Path(temp_dir) / "claude-har-aldrig-körts"
            self.assertFalse(absent.exists())

            # ``_compute`` mockas medvetet INTE — det är just den som ska
            # möta den frånvarande katalogen. Bara det som rör nät och CLI
            # stubbas.
            with mock.patch.object(tokenserver, "get_limits",
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
            # Noll rader lästa, inte ett undantag och inte gamla siffror.
            self.assertEqual(snapshot.get("monthTokens"), 0)

    def tearDown(self):
        # Cachen är modulglobal: lämna den inte varm åt nästa testfil.
        tokenserver._last_result = None
        tokenserver._last_computed = 0.0


if __name__ == "__main__":
    unittest.main()
