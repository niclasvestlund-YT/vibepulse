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

NOT GUARDED, AND MEASURED RATHER THAN ASSUMED: the same mistake about the
WI-FI SETUP window. tools/wifi-here.sh said the panel raises its access point
"direkt om du håller KEY3 ~3 s" — the hold opens SETTINGS, where WIFI opens
it. That text is corrected in this commit, but this guard did NOT catch it
and cannot: WINDOW deliberately excludes the setup window so the two windows
keep separate vocabularies, so the passage matched HOLD and nothing else.

Extending WINDOW to cover it was tried and rejected on evidence. A rule
pairing a hold with setup-window words flags three passages that are
CORRECT: wifi.md's hold-hold shortcut, ota.md's greyed-out-UPDATE
explanation, and agent-setup.md's "(or a 3 s KEY3 hold -> WIFI)" table row.
Same verdict as the takeover rule below, for the same reason. The runbook is
still in LIVE_DOCS — it would catch an OTA-window claim appearing there — but
its setup-window sentence is guarded by a reader, not by this file.

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

# The one legitimate way a hold and the update window belong in the same
# breath without the menu: the hold–hold shortcut, which happens INSIDE an
# already-open window and switches to Wi-Fi setup. That passage is true and
# unchanged by this work, so it is exempt — narrowly, by the phrasing that
# says the window is already open, not by a blanket "mentions open".
INSIDE = re.compile(
    r"(while (the )?(update|maintenance) window is open"
    r"|inside (the|an) (update|maintenance|already[- ]open) window"
    r"|while the OTA window is (already )?open)", re.I)


def passages(text):
    """Blank-line paragraphs, plus each table row on its own.

    Tables carry one instruction per row, so a row must stand up alone —
    the 403 troubleshooting entry was exactly such a row.
    """
    for block in text.split("\n\n"):
        rows = [ln for ln in block.splitlines() if ln.lstrip().startswith("|")]
        if rows:
            yield from rows
        else:
            yield block


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
