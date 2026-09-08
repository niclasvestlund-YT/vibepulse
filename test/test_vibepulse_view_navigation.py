#!/usr/bin/env python3
"""The usage screen's tiles are dense and Value is reachable, with the
optional GitHub page OFF as well as ON (#94).

#92 fixed a Value tile that indexed one past `ui.tiles` whenever
TK_GITHUB_SCREEN_ENABLED was 0 -- the default in a fresh clone -- and the
simulator (`sim/CMakeLists.txt`) and the firmware CI job both enable the
page, so no automated build ever ran the configuration that exposed it.
This test builds the simulator twice, once per setting, and runs its
`--vibepulse-view-qa` mode, which asks the REAL usage screen (not the
header, and not this file's reading of the header) whether every index
below TK_USAGE_SCREEN_VIEWS has a tile, whether showing each index lands
on it, whether the [ ] key walk visits every page in order and ends on
Value, and whether wrapping backwards from the first page lands on Value.
The assertions below are on the lines that mode prints, so a mode that
silently checked nothing would fail here, not pass vacuously.

The compiled half of the guard lives in `usage_screen.c`: two
_Static_asserts pin VIEW_VALUE inside the array and in its last slot, and
both builds here compile that file under their own setting. Reverting the
#92 header (a fixed `VIEW_VALUE = 7`) fails the GitHub-off build at
compile time before this test can even run it; verified 2026-09-08.

Needs SDL2, CMake and Ninja like the other simulator tests, and a display
(CI wraps the host gate in xvfb-run).
"""

from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
QA_MODE = "--vibepulse-view-qa"
BASE_TILES = 6  # Claude Fable, Claude all, Codex weekly, burn rate, two trackers

BUILDS = {
    # The bench's default build, shared with the landmark and drift tests.
    1: ("sim/build", []),
    # The target's default: no GitHub page. Its own directory, so it never
    # poisons the landmark captures with a seven-tile layout.
    0: ("sim/build-github-off", ["-DTORGET_SIM_GITHUB_PAGE=OFF"]),
}


def _build_and_run(build_dir: str, extra: list[str]) -> str:
    for argv in (["cmake", "-S", "sim", "-B", build_dir, "-G", "Ninja",
                  *extra],
                 ["cmake", "--build", build_dir]):
        subprocess.run(argv, cwd=ROOT, check=True, text=True,
                       capture_output=True)
    completed = subprocess.run(
        [str(ROOT / build_dir / "torget-sim"), QA_MODE],
        cwd=ROOT, env=dict(os.environ), text=True, capture_output=True,
        timeout=120)
    return completed.returncode, completed.stdout


def _facts(stdout: str) -> dict:
    """The printed evidence, parsed: header numbers, tile presence,
    show/step landings, and the verdict line."""
    facts = {"tiles": {}, "show": {}, "step": {}}
    for line in stdout.splitlines():
        if not line.startswith("view-qa: "):
            continue
        body = line[len("view-qa: "):]
        header = re.fullmatch(r"github=(\d) views=(\d+) value=(\d+)", body)
        if header:
            facts["github"], facts["views"], facts["value"] = map(
                int, header.groups())
            continue
        tile = re.fullmatch(r"tile (\d+) (present|MISSING)", body)
        if tile:
            facts["tiles"][int(tile.group(1))] = tile.group(2) == "present"
            continue
        show = re.fullmatch(r"show (\d+) -> (\d+)", body)
        if show:
            facts["show"][int(show.group(1))] = int(show.group(2))
            continue
        step = re.fullmatch(r"step (\d+) -> (\d+)", body)
        if step:
            facts["step"][int(step.group(1))] = int(step.group(2))
            continue
        wrap = re.fullmatch(r"wrap back from 0 -> (\d+)", body)
        if wrap:
            facts["wrap"] = int(wrap.group(1))
            continue
        if body in ("OK", "FAILED"):
            facts["verdict"] = body
    return facts


class ViewNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runs = {}
        for github, (build_dir, extra) in BUILDS.items():
            code, stdout = _build_and_run(build_dir, extra)
            cls.runs[github] = (code, stdout, _facts(stdout))

    def _run(self, github):
        code, stdout, facts = self.runs[github]
        # A header line is the minimum proof the mode ran at all; without it
        # every other assertion would be about an empty dict.
        self.assertIn("views", facts,
                      f"the QA mode printed no header:\n{stdout}")
        self.assertEqual(facts["github"], github,
                         "the build did not compile the setting it was "
                         "configured with")
        return code, stdout, facts

    def test_both_settings_pass_the_simulators_own_walk(self):
        for github in BUILDS:
            with self.subTest(github=github):
                code, stdout, facts = self._run(github)
                self.assertEqual(facts.get("verdict"), "OK", stdout)
                self.assertEqual(code, 0, stdout)

    def test_the_tile_count_follows_the_setting_and_value_is_last(self):
        for github in BUILDS:
            with self.subTest(github=github):
                _, stdout, facts = self._run(github)
                self.assertEqual(facts["views"], BASE_TILES + github + 1,
                                 stdout)
                self.assertEqual(facts["value"], facts["views"] - 1, stdout)

    def test_every_index_below_the_count_has_a_tile_and_none_past_it(self):
        for github in BUILDS:
            with self.subTest(github=github):
                _, stdout, facts = self._run(github)
                self.assertEqual(sorted(facts["tiles"]),
                                 list(range(facts["views"])), stdout)
                self.assertTrue(all(facts["tiles"].values()),
                                f"a tile is missing:\n{stdout}")
                self.assertNotIn("PAST THE END", stdout)

    def test_showing_each_index_lands_on_it(self):
        for github in BUILDS:
            with self.subTest(github=github):
                _, stdout, facts = self._run(github)
                self.assertEqual(
                    facts["show"], {i: i for i in range(facts["views"])},
                    stdout)

    def test_the_key_walk_reaches_value_in_exactly_views_minus_one_steps(self):
        for github in BUILDS:
            with self.subTest(github=github):
                _, stdout, facts = self._run(github)
                expected = {n: n for n in range(1, facts["views"])}
                self.assertEqual(facts["step"], expected, stdout)
                self.assertEqual(facts["step"][facts["views"] - 1],
                                 facts["value"], stdout)
                self.assertEqual(facts.get("wrap"), facts["value"], stdout)


if __name__ == "__main__":
    unittest.main()
