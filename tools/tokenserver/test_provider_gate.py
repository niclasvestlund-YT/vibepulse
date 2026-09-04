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

from tools.tokenserver import codex_usage, tokenserver


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


class CodexHomeTest(unittest.TestCase):
    """``CODEX_SESSIONS`` ska följa ``CODEX_HOME``, inte hårdkodat ``~/.codex``.

    Det här är inte ett hypotetiskt fall: ``run-windows-task.ps1`` exporterar
    ``CODEX_HOME`` innan tjänsten startar, och desktop-appen sätter den. Med
    en hårdkodad sökväg såg grinden en katalog som inte fanns, väntade för
    evigt, och porten öppnades aldrig — exakt felet den här ändringen finns
    för att laga, kvar för den ena konfiguration som är dokumenterat stödd.
    """

    def test_sessions_dir_follows_codex_home(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            home = Path(temp_dir) / "managed-codex-home"
            (home / "sessions").mkdir(parents=True)

            with mock.patch.dict("os.environ", {"CODEX_HOME": str(home)}):
                resolved = codex_usage.default_sessions_dir()

            self.assertEqual(resolved, home / "sessions")
            # Grinden ska öppna på den katalogen, utan någon Claude-katalog.
            with mock.patch.object(tokenserver, "CODEX_SESSIONS", resolved):
                self.assertTrue(
                    tokenserver._any_provider_dir(Path(temp_dir) / "no-claude"))

    def test_tokenserver_resolves_through_the_shared_source(self):
        """Konstanten ska komma från samma funktion som månadsskanningen.

        Vaktar mot att någon återinför en hårdkodad sökväg: då läser
        grinden, rate-limits, agentstatus och Max Tracker en annan profil
        än månadsvärdet, vilket är precis den tysta delningen
        ``default_sessions_dir`` skrevs för att stänga.
        """
        with mock.patch.object(codex_usage, "DEFAULT_SESSIONS_DIR",
                               Path("/sentinel/sessions")):
            self.assertEqual(codex_usage.default_sessions_dir(),
                             Path("/sentinel/sessions"))
        # Importtidsvärdet ska ha gått genom resolvern, inte förbi den.
        self.assertEqual(tokenserver.CODEX_SESSIONS.name, "sessions")


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
            # ``_compute`` når ``codex_usage.month_value()`` UTAN katalog, som
            # då faller tillbaka på den riktiga ``~/.codex/sessions``. Utan
            # den här seamen skannar testet utvecklarens egen rollout-historik
            # rekursivt: långsamt, beroende av privat maskintillstånd, och det
            # lämnar codex_usage-cachen varm åt nästa testfil. Peka den på en
            # tom temporär katalog i stället.
            codex_root = Path(temp_dir) / "no-codex-here"
            codex_root.mkdir()

            # ``_compute`` mockas medvetet INTE — det är just den som ska
            # möta den frånvarande katalogen. Bara det som rör nät och CLI
            # stubbas.
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
            # Noll rader lästa, inte ett undantag och inte gamla siffror.
            self.assertEqual(snapshot.get("monthTokens"), 0)

    def tearDown(self):
        # Båda cacharna är modulglobala: lämna dem inte varma åt nästa testfil.
        tokenserver._last_result = None
        tokenserver._last_computed = 0.0
        codex_usage.reset_cache()


class CodexOnlyEndToEndTest(unittest.TestCase):
    """Hela vägen: en Codex-only-maskin ska ge ett serverbart svar.

    Grindtesten ovan bevisar logiken; det här bevisar att en riktig
    ``/api/tokens``-förfrågan går igenom ``Handler.do_GET`` med en
    Claude-katalog som inte finns och en Codex-katalog som gör det.

    Det ersätter inte en riktig Codex-only-dator — ingen har sett porten
    öppnas, DNS-SD annonsera och panelen fråga på en färsk installation.
    Det stänger den del som CI faktiskt kan bevisa: att svaret blir 200,
    att Claudes procenttal är null (streck på skärmen, inte nollor), och
    att ``claudeSourcePresent`` säger varför volymsiffrorna är noll.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.claude_absent = root / "claude" / "projects"   # skapas aldrig
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
            handler.do_GET()

        handler._send.assert_called_once()
        code, payload = handler._send.call_args.args
        return code, payload

    def test_serves_200_with_no_claude_directory(self):
        code, payload = self._tokens_payload()
        self.assertEqual(code, 200)
        # v=2 är kontraktet tokens_parse.c kräver (`v != 2.0` avvisar).
        self.assertEqual(payload["v"], 2)

    def test_claude_percentages_are_null_so_the_panel_shows_dashes(self):
        """Ärlighetsinvarianten där den syns: procenttalen blir null, och
        ``pct_or_null`` i tokens_parse.c gör null till has_pct=0, vilket
        presentern renderar som "–" och "USAGE UNAVAILABLE"."""
        _, payload = self._tokens_payload()
        for key in ("claudeSessionPct", "claudeWeekPct",
                    "claudeModelWeekPct"):
            self.assertIsNone(payload[key], key)

    def test_volume_zeroes_are_marked_as_an_absent_source(self):
        """De fyra räknarna kan inte bli null utan att äldre paneler slutar
        parsa, så flaggan bär sanningen i stället."""
        _, payload = self._tokens_payload()
        self.assertFalse(payload["claudeSourcePresent"])
        self.assertEqual(payload["dayTokens"], 0)
        self.assertEqual(payload["monthTokens"], 0)

    def test_a_present_claude_directory_reports_the_source_as_present(self):
        """Kontrollen åt andra hållet: flaggan följer katalogen, den är
        inte hårdkodad till false av testuppsättningen."""
        self.claude_absent.mkdir(parents=True)
        _, payload = self._tokens_payload()
        self.assertTrue(payload["claudeSourcePresent"])

    def tearDown(self):
        tokenserver._last_result = None
        tokenserver._last_computed = 0.0


if __name__ == "__main__":
    unittest.main()
