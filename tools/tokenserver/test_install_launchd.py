"""The macOS autostart installer, and the template it replaced.

Two failures this file exists to prevent, both silent by nature:

  * An agent whose WorkingDirectory points at a directory that does not
    exist. launchd answers "Could not find service" to every later
    kickstart and says nothing about why -- which is what the hardcoded
    ~/Torget path in the checked-in template produces on any other clone.
  * An agent installed WITHOUT the relay URL. Nothing errors: the panel
    keeps working on the LAN and only the mailbox quietly goes stale.
  * An agent that kept the relay URL but dropped the other flags.
    ``--github-repo``, ``--claude-plan``, ``--codex-plan`` and ``--plan``
    are CLI-only too -- none of them is saved in the service's config -- so
    an installer that only understood ``--publish`` would darken the GitHub
    page, both plan badges and the value multiple's denominator, and say
    nothing about it.

The script is plain sh + awk + sed, so ``--print`` renders the plist it
would install without touching the machine, and these tests read that.
"""

from __future__ import annotations

import os
import plistlib
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "tools" / "tokenserver" / "install-launchd.sh"
TEMPLATE = REPO / "tools" / "tokenserver" / "se.torget.tokenserver.plist"
LABEL = "se.torget.tokenserver"

RELAY = "https://vibepulse-relay.example.workers.dev/u/" + "a" * 64


def render(*args, home=None):
    """Run the installer in --print mode and parse what it would install."""
    env = dict(os.environ)
    if home is not None:
        env["HOME"] = str(home)
    out = subprocess.run(["/bin/sh", str(SCRIPT), "--print", *args],
                         capture_output=True, check=True, env=env).stdout
    return plistlib.loads(out)


@unittest.skipIf(sys.platform == "win32", "sh installer is macOS/Linux only")
class RenderTests(unittest.TestCase):
    def test_it_renders_a_valid_plist(self):
        plist = render()
        self.assertEqual(plist["Label"], LABEL)
        self.assertTrue(plist["RunAtLoad"])
        self.assertTrue(plist["KeepAlive"])

    def test_the_working_directory_is_this_clone(self):
        # The whole point: never a path from somebody else's machine.
        plist = render()
        self.assertEqual(Path(plist["WorkingDirectory"]),
                         REPO / "tools" / "tokenserver")
        self.assertTrue((Path(plist["WorkingDirectory"]) /
                         "tokenserver.py").is_file())

    def test_without_a_url_it_does_not_pretend_to_publish(self):
        self.assertNotIn("--publish", render()["ProgramArguments"])

    def test_with_a_url_the_service_publishes(self):
        args = render("--publish", RELAY, "--publish-name", "macbook")[
            "ProgramArguments"]
        self.assertEqual(args[args.index("--publish") + 1], RELAY)
        self.assertEqual(args[args.index("--publish-name") + 1], "macbook")


@unittest.skipIf(sys.platform == "win32", "sh installer is macOS/Linux only")
class InheritTests(unittest.TestCase):
    """Re-running the installer must not silently stop the publishing."""

    def _install_into(self, home, *args):
        agents = Path(home) / "Library" / "LaunchAgents"
        agents.mkdir(parents=True, exist_ok=True)
        out = subprocess.run(
            ["/bin/sh", str(SCRIPT), "--print", *args],
            capture_output=True, check=True,
            env={**os.environ, "HOME": str(home)}).stdout
        (agents / f"{LABEL}.plist").write_bytes(out)

    def test_a_bare_rerun_keeps_the_relay_url(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            self._install_into(home, "--publish", RELAY,
                               "--publish-name", "macbook")
            args = render(home=home)["ProgramArguments"]
            self.assertEqual(args[args.index("--publish") + 1], RELAY)
            self.assertEqual(args[args.index("--publish-name") + 1],
                             "macbook")

    def test_a_url_with_xml_metacharacters_survives_a_rerun(self):
        # Escaped on the way in, unescaped on the way out -- otherwise a
        # reinstall walks the URL one "&amp;" further from the truth each time.
        import tempfile
        awkward = "https://relay.example/u/abc?a=1&b=2"
        with tempfile.TemporaryDirectory() as home:
            self._install_into(home, "--publish", awkward)
            args = render(home=home)["ProgramArguments"]
            self.assertEqual(args[args.index("--publish") + 1], awkward)


REAL_WORLD = [
    "--github-repo", "niclasvestlund-YT/vibepulse",
    "--claude-plan", "max20x", "--codex-plan", "pro",
    "--plan", "claude=200", "--plan", "codex=100",
    "--publish", RELAY,
]


@unittest.skipIf(sys.platform == "win32", "sh installer is macOS/Linux only")
class PassThroughTests(unittest.TestCase):
    def test_everything_after_a_double_dash_is_kept_verbatim(self):
        args = render("--", *REAL_WORLD)["ProgramArguments"]
        self.assertEqual(args[3:], REAL_WORLD)

    def test_a_bare_rerun_keeps_flags_the_installer_never_heard_of(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            agents = Path(home) / "Library" / "LaunchAgents"
            agents.mkdir(parents=True)
            (agents / f"{LABEL}.plist").write_bytes(subprocess.run(
                ["/bin/sh", str(SCRIPT), "--print", "--",
                 *REAL_WORLD, "--some-future-flag", "42"],
                capture_output=True, check=True,
                env={**os.environ, "HOME": home}).stdout)
            self.assertEqual(render(home=home)["ProgramArguments"][3:],
                             REAL_WORLD + ["--some-future-flag", "42"])

    def test_publish_replaces_rather_than_duplicates(self):
        args = render("--publish", "https://new.example/u/z", "--",
                      *REAL_WORLD)["ProgramArguments"]
        self.assertEqual(args.count("--publish"), 1)
        self.assertEqual(args[args.index("--publish") + 1],
                         "https://new.example/u/z")
        self.assertIn("--github-repo", args)
        self.assertEqual(args.count("--plan"), 2)


@unittest.skipIf(sys.platform == "win32", "sh installer is macOS/Linux only")
class FromRunningTests(unittest.TestCase):
    """--from-running must capture a service, not anything naming the file.

    The first version matched on `pgrep -f tokenserver.py` alone and
    happily swallowed the test's own shell -- whose command line merely
    mentioned the filename -- writing the entire script into the plist as
    the service's arguments.
    """

    def test_a_process_that_only_mentions_the_name_is_not_a_service(self):
        import tempfile
        script = "true tokenserver.py"   # names it; is not python
        with tempfile.TemporaryDirectory() as home:
            proc = subprocess.Popen(["/bin/sh", "-c", f"{script}; sleep 30"])
            try:
                done = subprocess.run(
                    ["/bin/sh", str(SCRIPT), "--print", "--from-running"],
                    capture_output=True,
                    env={**os.environ, "HOME": home})
                self.assertNotEqual(done.returncode, 0, done.stdout[:400])
                self.assertIn(b"hittar ingen", done.stderr)
            finally:
                proc.kill()
                proc.wait()

    def test_a_real_service_is_captured_verbatim(self):
        import tempfile
        with tempfile.TemporaryDirectory() as home:
            fake = Path(home) / "tokenserver.py"
            fake.write_text("import time; time.sleep(30)\n")
            proc = subprocess.Popen([sys.executable, "-u", str(fake),
                                     *REAL_WORLD])
            try:
                plist = render("--from-running", home=home)
                self.assertEqual(plist["ProgramArguments"][3:], REAL_WORLD)
            finally:
                proc.kill()
                proc.wait()


class TemplateTests(unittest.TestCase):
    """The checked-in plist stays a readable template, not a recipe."""

    def test_the_template_is_still_valid_xml(self):
        # An XML comment may not contain two hyphens in a row, which makes
        # writing "--publish" into the comment a way to break the file.
        with TEMPLATE.open("rb") as handle:
            self.assertEqual(plistlib.load(handle)["Label"], LABEL)

    def test_the_readme_does_not_tell_anyone_to_copy_it(self):
        readme = (REPO / "tools" / "tokenserver" / "README.md").read_text(
            encoding="utf-8")
        self.assertNotIn("cp se.torget.tokenserver.plist", readme,
                         "copying the template reinstates the ~/Torget path "
                         "and drops the relay URL")
        self.assertIn("install-launchd.sh", readme)


if __name__ == "__main__":
    unittest.main()
