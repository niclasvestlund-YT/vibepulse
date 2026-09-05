#!/usr/bin/env python3
"""The KEY3 hold reaches SETTINGS, never a window. Say so everywhere.

Three separate documentation misses in one review round, all the same
shape: a passage telling the reader that holding KEY3 opens the update
window. That was true until the hold started opening a menu, and every
sentence describing the old gesture silently became false — sentences do
not move when the code does.

Fixing the ones a reviewer happens to name is not a fix, it is a queue.
This guard fails on the whole class:

    a live doc may not describe a KEY3 hold as reaching the update
    window unless the same passage also names the SETTINGS step.

Deliberately NOT covered here, because both are honest history rather
than instructions: CHANGELOG.md, whose released entries describe what
shipped at the time, and docs/superpowers/plans/, which are implementation
plans for work already done. Rewriting either to match today's behaviour
would be falsifying the record, which is the opposite of the point.

THE SAME MISTAKE ABOUT THE WI-FI SETUP WINDOW is now guarded too, and the
story of why is the useful part. tools/wifi-here.sh said the panel raises its
access point "direkt om du håller KEY3 ~3 s", and its retry advice said a
hold opens a new one. Both are wrong the same way — the hold opens SETTINGS,
where WIFI opens the window.

I first judged this class unguardable and wrote that down: a rule pairing a
hold with setup-window words appeared to flag three CORRECT passages. That
measurement was wrong, and wrong for a reason worth keeping. Two of the
three were artifacts of matching raw text — patterns here use literal
spaces, and wifi.md's exempt sentence has a line break inside it, so INSIDE
could not fire. The third was self-exemption: a step pattern accepting the
bare word WIFI matches the window's own name, "WIFI SETUP".

With passages whitespace-normalised (see below) and the step required to
name SETTINGS or to *pick* WIFI rather than merely mention it, the rule
flags zero correct passages in every live doc and catches both real misses.
So the class needed a better rule, not a reader. The lesson is that "I tried
it and it produced false positives" is only as good as the harness that
produced them.

Also deliberately NOT guarded, after trying: the neighbouring failure where
a doc offers the hold *while the UPDATE READY takeover is up*, which the
firmware ignores. The ota.md diagram shipped that way ("tap UPDATE pill, or
hold KEY3 ~3 s, then pick UPDATE in SETTINGS", directly under "mismatch:
UPDATE READY takeover"). A rule flagging passages that mention both a hold
and the takeover fires on three CORRECT ones, which list the two routes for
two different states — and the wrong version is textually identical to them,
"or" and all. A guard that flags correct prose trains people to sprinkle
appeasing words instead of thinking, which is worse than no guard. That
class needs a reader.
"""

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

# The docs a user or an agent actually follows. If a new runbook joins
# them it belongs here too.
LIVE_DOCS = (
    "README.md",
    "CLAUDE.md",
    "docs/ota.md",
    "docs/wifi.md",
    "docs/agent-setup.md",
    # The two operator runbooks. Adding them is the whole reason this file
    # changed: both still described the old gesture, and ota-flash.sh printed
    # it at RUNTIME — "håll KEY3 ~3 s..." on the terminal at the exact moment
    # someone is standing at the panel waiting for a ring that will not come.
    # A guard scoped to .md files was never going to see it. A doc is
    # whatever a person follows, not whatever has a Markdown extension.
    #
    # ota-flash.sh is genuinely covered: reverting its header fails this file.
    # wifi-here.sh is here for the OTA-window class only — its own error was
    # about the SETUP window, which WINDOW does not match. See the docstring.
    "tools/ota-flash.sh",
    "tools/wifi-here.sh",
)

# A hold is not always spelled with KEY3 next to it. The README passage
# that shipped wrong said "hold twice: the first 3-second hold opens the
# update window" and never named the button in that sentence at all — the
# first version of this guard required the adjacency and sailed past it.
# Swedish puts the verb first — "Håll KEY3", "om du håller KEY3" — and the
# noun form "KEY3-håll" was the only Swedish shape the first version knew.
# Both missed passages were verb-first, so the guard read two files that were
# wrong and called them clean.
HOLD = re.compile(
    r"(hold\w*\s+(the\s+)?KEY3|KEY3[- ]h[åo]ll|KEY3\s+hold"
    r"|h[åa]ll\w*\s+(ned\s+)?(på\s+)?KEY3"
    r"|\d+[\s-](?:second|s)\s+h[åo]?ll?d?|full hold|hold twice"
    r"|hold[–-]hold)", re.I)

# Reaching the maintenance window / its takeover ring.
WINDOW = re.compile(
    r"(update window|maintenance window|updates on|the ring|"
    # A bare "the window" counts: that is exactly how the notice line read
    # ("(or a KEY3 hold) -> opens the window"), and requiring the adjective
    # let it through. "the setup window" cannot match this — words
    # intervene — so the Wi-Fi window keeps its own vocabulary.
    r"\bthe window\b|"
    r"underh[åa]llsf[öo]nstret|uppdateringsf[öo]nstret)", re.I)

# The step that actually opens it. Must be the MENU's name: "UPDATE"
# alone is far too loose — it matches the takeover's pill, the "UPDATES ON"
# footer and the words "update window", so every real miss slipped through
# a first version of this guard that accepted it.
STEP = re.compile(r"SETTINGS")

# ---- the Wi-Fi setup window, which has its own vocabulary and its own step.
SETUP_WINDOW = re.compile(
    r"(setup[- ]?f[öo]nstret|setup window|accesspunkt|VibePulse-setup"
    r"|WIFI SETUP|WiFi SETUP)", re.I)

# Must name the MENU, or picking WIFI *from* it. The bare word WIFI cannot be
# the step: "WIFI SETUP" contains it, so a passage naming only the window
# would exempt itself — which is exactly what a first attempt did.
SETUP_STEP = re.compile(
    r"SETTINGS|(v[äa]lj|pick|choose|→|->)\s+\*{0,2}WIFI", re.I)

# The one legitimate way a hold and the update window belong in the same
# breath without the menu: the hold–hold shortcut, which happens INSIDE an
# already-open window and switches to Wi-Fi setup. That passage is true and
# unchanged by this work, so it is exempt — narrowly, by the phrasing that
# says the window is already open, not by a blanket "mentions open".
INSIDE = re.compile(
    r"(while (the )?(update|maintenance) window is open"
    r"|inside (the|an) (update|maintenance|already[- ]open) window"
    r"|while the OTA window is (already )?open)", re.I)


# What a shell script actually puts on the operator's terminal.
#
# The flags matter and are not cosmetic. A first version accepted only -e, so
# `echo -n "... håll "` followed by `echo "KEY3 ~3 s."` — one continuous
# sentence to the reader, and precisely how you would build one — matched
# nothing on the first line. That did not merely lose half the message: a
# line yielding no parts ENDS the adjacent-run grouping, so the guard scanned
# the tail alone and saw nothing wrong with it.
#
# printf is here for the same reason rather than a hypothetical one: other
# scripts under tools/ already address the operator with it, so a runbook
# gaining one is a matter of time. Its format specifiers survive as literal
# "%s" in the extracted text, which none of these patterns care about.
EMITTER = re.compile(r"""^\s*(?:echo|printf)\b""")
QUOTED = re.compile(r"""["']([^"']*)["']""")


def emitted(line):
    """Everything an emitting line puts on the terminal, in order.

    Matching the COMMAND and then taking every quoted run, rather than
    writing one regex for the whole invocation, is the third attempt and the
    only one that has held. The first knew `echo` and `echo -e`, so
    `echo -n "... håll "` yielded nothing. The second added the flags but
    still took only the FIRST quoted string, which is right for echo and
    wrong for printf: `printf '%s%s\n' "... håll " "KEY3 ~3 s."` puts the
    stale sentence on screen out of its ARGUMENTS, and the guard read the
    format and called the file clean.

    Both misses had the same cause — a pattern that had to anticipate every
    shape of the invocation. This does not: the command name says the line
    speaks, and the quotes say what it says. A format specifier survives as
    a literal "%s" between the pieces, which none of the rules care about.
    """
    if not EMITTER.match(line):
        return []
    return QUOTED.findall(line)


def passages(text):
    """Blank-line paragraphs, plus each table row on its own.

    Tables carry one instruction per row, so a row must stand up alone —
    the 403 troubleshooting entry was exactly such a row.

    WHITESPACE IS NORMALISED, and that is not cosmetic. Every pattern here
    is written with literal spaces, and prose wraps: wifi.md's hold-hold
    passage says "while the update window is open" with a line break inside
    it, so INSIDE — the exemption meant precisely for that sentence — did not
    fire, and the passage was one line-wrap away from being reported as a
    violation. A guard that depends on where an author pressed Enter is a
    guard that reports noise.

    A SHELL SCRIPT ALSO GETS WHAT THE OPERATOR ACTUALLY READS. A message
    split across two echo statements is one sentence on the terminal and two
    unrelated fragments on disk: wifi-here.sh's retry advice ended "— håll"
    on one line and began "KEY3 ~3 s" on the next, with `" >&2\n  echo "` in
    between, so no pattern could bridge it. Codex found that line by reading
    it; this yields the joined text so the guard can too.

    ADJACENT echoes only, and that word is load-bearing. A first version
    joined every echo in the file into one synthetic passage, which Codex
    caught as a hole I had just dug: ota-flash.sh's corrected text mentions
    SETTINGS, so ANY other echo in that file — a different branch, a
    mutually exclusive path, text no operator ever sees in the same
    breath — would inherit that word and be excused by it. Reproduced before
    fixing: a stale two-line message injected into ota-flash.sh was NOT
    flagged, while the same message in a file without a SETTINGS echo was.
    A guard that grows a blind spot as the file it watches gets more correct
    is worse than one that never looked. Consecutive echo lines are one
    emitted message; a blank line, a command, or a closing brace ends it.
    """
    for block in text.split("\n\n"):
        rows = [ln for ln in block.splitlines() if ln.lstrip().startswith("|")]
        if rows:
            for row in rows:
                yield " ".join(row.split())
        else:
            yield " ".join(block.split())
    run = []
    for line in text.splitlines():
        parts = emitted(line)
        if parts:
            run.extend(parts)
            continue
        if run:
            yield " ".join(" ".join(run).split())
            run = []
    if run:
        yield " ".join(" ".join(run).split())


class OtaGestureDocsTest(unittest.TestCase):
    def test_no_live_doc_sends_a_hold_straight_to_the_window(self):
        offenders = []
        for name in LIVE_DOCS:
            path = ROOT / name
            if not path.exists():
                continue
            for passage in passages(path.read_text(encoding="utf-8")):
                if HOLD.search(passage) and WINDOW.search(passage) \
                        and not STEP.search(passage) \
                        and not INSIDE.search(passage):
                    offenders.append(
                        f"{name}: {' '.join(passage.split())[:160]}")
        self.assertEqual(offenders, [], "\n".join(
            ["a KEY3 hold opens SETTINGS; name the UPDATE step too:"]
            + offenders))

    def test_no_live_doc_sends_a_hold_straight_to_the_setup_window(self):
        """The Wi-Fi half of the same class. wifi-here.sh got it wrong twice
        — in its header and, worse, in the retry advice it prints when the
        Mac fails to reach the panel's AP, which is exactly when the operator
        is stuck and reading carefully."""
        offenders = []
        for name in LIVE_DOCS:
            path = ROOT / name
            if not path.exists():
                continue
            for passage in passages(path.read_text(encoding="utf-8")):
                if HOLD.search(passage) and SETUP_WINDOW.search(passage) \
                        and not SETUP_STEP.search(passage) \
                        and not INSIDE.search(passage):
                    offenders.append(f"{name}: {passage[:160]}")
        self.assertEqual(offenders, [], "\n".join(
            ["a KEY3 hold opens SETTINGS; name the WIFI step too:"]
            + offenders))

    def test_the_setup_rule_catches_its_real_misses_and_spares_the_rest(self):
        """Both halves measured, because a rule is only as trustworthy as the
        evidence that it neither under- nor over-reaches."""
        misses = (
            "#   1. Panelen reser sin egen accesspunkt (VibePulse-setup). "
            "Den gör det\n#      själv efter 90 s utan nät, eller direkt om "
            "du håller KEY3 ~3 s.",
            '  echo "Står WIFI SETUP på glaset? Fönstret är öppet i tio '
            'minuter — håll" >&2\n  echo "KEY3 ~3 s för att öppna ett '
            'nytt." >&2',
        )
        correct = (
            "#      själv efter 90 s utan nät, eller om du håller KEY3 ~3 s "
            "och väljer\n#      WIFI i SETTINGS — hållet öppnar menyn, inte "
            "fönstret.",
            "**Hold again to switch windows.** A second full 3 s hold while "
            "the update\nwindow is open closes it and opens WIFI SETUP "
            "instead.",
        )

        def flagged(text):
            return [p for p in passages(text)
                    if HOLD.search(p) and SETUP_WINDOW.search(p)
                    and not SETUP_STEP.search(p) and not INSIDE.search(p)]

        for text in misses:
            with self.subTest(miss=text[:60]):
                self.assertTrue(flagged(text), "setup rule missed a real one")
        for text in correct:
            with self.subTest(ok=text[:60]):
                self.assertEqual(flagged(text), [],
                                 "setup rule rejects correct wording")

    def test_one_correct_echo_cannot_excuse_a_stale_one_elsewhere(self):
        """The hole a first version of the echo-joining dug.

        Joining every echo in a file into one passage means the file's own
        corrected text — ota-flash.sh now names SETTINGS — is inherited by
        every other echo in it, including ones in a different branch that no
        operator sees in the same breath. The guard would then get blinder
        the more of the file was fixed, which is the worst possible
        direction. Consecutive lines are one message; anything else is not.
        """
        script = (ROOT / "tools/ota-flash.sh").read_text(encoding="utf-8")
        planted = script + (
            '\nretry() {\n'
            '  echo "kom inte in. Underhållsfönstret stängdes — håll" >&2\n'
            '  echo "KEY3 ~3 s för att öppna ett nytt." >&2\n'
            '}\n')
        flagged = [p for p in passages(planted)
                   if HOLD.search(p) and WINDOW.search(p)
                   and not STEP.search(p) and not INSIDE.search(p)]
        self.assertTrue(
            flagged,
            "a stale message in another branch was excused by the SETTINGS "
            "in this script's own corrected text")

        # And the script as it stands must stay clean, so the grouping is not
        # merely strict enough to flag everything.
        self.assertEqual(
            [p for p in passages(script)
             if HOLD.search(p) and WINDOW.search(p)
             and not STEP.search(p) and not INSIDE.search(p)], [])

    def test_a_message_built_with_echo_n_or_printf_is_still_read(self):
        """Both are ways to emit one sentence from two commands, and both
        were invisible: a line the pattern cannot read yields no parts, which
        ends the adjacent run, so the guard scanned the tail on its own and
        found nothing wrong with it. Missing a flag did not weaken the
        reading — it silenced it."""
        built = (
            'status() {\n'
            '  echo -n "Underhållsfönstret öppnas med ett håll "\n'
            '  echo "KEY3 ~3 s."\n'
            '}',
            'status() {\n'
            "  printf 'Underhållsfönstret öppnas med ett håll '\n"
            '  echo "KEY3 ~3 s."\n'
            '}',
            # One line, one command: the sentence lives in printf's ARGUMENTS
            # and only "%s%s" is in the format. Reading the format alone —
            # which a previous version did — sees nothing at all here.
            'status() {\n'
            "  printf '%s%s\\n' \"Underhållsfönstret öppnas med ett håll \" "
            '"KEY3 ~3 s."\n'
            '}',
            # Several arguments to one echo are one line on the terminal too.
            'status() {\n'
            '  echo "Underhållsfönstret öppnas med ett håll " "KEY3 ~3 s."\n'
            '}',
        )
        for text in built:
            with self.subTest(form=text.split("\n")[1].strip()[:20]):
                flagged = [p for p in passages(text)
                           if HOLD.search(p) and WINDOW.search(p)
                           and not STEP.search(p) and not INSIDE.search(p)]
                self.assertTrue(
                    flagged,
                    "a message split across two emitting commands was not "
                    "reassembled, so the guard never saw the sentence")

    def test_the_guard_would_have_caught_the_real_misses(self):
        """A guard that passes everything proves nothing, so run it against
        the exact sentences that shipped wrong."""
        real_misses = (
            "- **UPDATE pill** (or a KEY3 hold) → opens the window; if\n"
            "  `tools/ota-flash.sh` is waiting on the Mac, delivery is "
            "automatic.",
            "| Upload gets 403 | Window not open | Hold KEY3; the glass must "
            "show the ring/UPDATES ON |",
            "On a panel that already *has* a network, hold twice: the first "
            "3-second hold opens the update window, a second full hold "
            "switches it to WIFI SETUP.",
            # Swedish, verb-first, from tools/ota-flash.sh — the header line
            # and the runtime echo. Both sailed past the first HOLD pattern.
            "#   3. Håll KEY3 ~3 s tills UPDATES ON-ringen syns — "
            "uppladdningen går av sig själv.",
            'echo "väntar på underhållsfönstret på $HOST — håll KEY3 ~3 s..."',
        )
        for text in real_misses:
            with self.subTest(text=text[:60]):
                flagged = [p for p in passages(text)
                           if HOLD.search(p) and WINDOW.search(p)
                           and not STEP.search(p) and not INSIDE.search(p)]
                self.assertTrue(flagged, "guard missed a real regression")

    def test_the_guard_accepts_the_corrected_wording(self):
        """And it must not simply reject every mention of the gesture."""
        corrected = (
            "Hold KEY3 and pick **UPDATE** in SETTINGS (or, if the takeover "
            "is already on the glass, answer it with its UPDATE pill) when "
            "you're ready.",
            "| Upload gets 403 | Window not open | Hold KEY3, then pick "
            "UPDATE in SETTINGS — the hold alone only opens the menu; the "
            "glass must show the ring/UPDATES ON |",
            # The corrected Swedish, so the widened pattern is checked for
            # over-reach too: it must accept these, not merely reject the old.
            "#   3. Håll KEY3 ~3 s och välj UPDATE i SETTINGS — hållet "
            "öppnar MENYN, inte fönstret. När UPDATES ON-ringen syns går "
            "uppladdningen av sig själv.",
            "#      själv efter 90 s utan nät, eller om du håller KEY3 ~3 s "
            "och väljer WIFI i SETTINGS — hållet öppnar menyn, inte "
            "fönstret.",
        )
        for text in corrected:
            with self.subTest(text=text[:60]):
                flagged = [p for p in passages(text)
                           if HOLD.search(p) and WINDOW.search(p)
                           and not STEP.search(p) and not INSIDE.search(p)]
                self.assertEqual(flagged, [], "guard rejects correct wording")


if __name__ == "__main__":
    unittest.main()
