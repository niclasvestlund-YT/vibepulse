#!/usr/bin/env python3
"""Show the arithmetic behind the value figure, so it can be checked.

A dollar amount on a shelf display is unfalsifiable by looking at it. This
prints what produced it: tokens per provider, dollars per provider, and the
implied price per million tokens -- the one number that tells you whether a
surprising total is real volume or a pricing bug.

    python3 tools/tokenserver/verify_value.py

The implied rate is the check that bites. Published list rates for the models
these agents run land between roughly $0.30/Mtok (cache-read heavy) and
$30/Mtok (output heavy). A blended month should sit near the low end, because
cache reads dominate. Land far outside that band and the fault is pricing, not
usage -- no amount of typing produces a rate the price table cannot.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

if __package__:
    from . import codex_usage, value_meter
    from .tokenserver import _compute
else:  # direct execution
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from tools.tokenserver import codex_usage, value_meter
    from tools.tokenserver.tokenserver import _compute

#: Bounds on a blended $/Mtok. Not a hard rule -- a deliberately output-only
#: month really can sit high -- but outside this band the pricing path is a
#: likelier explanation than the usage.
PLAUSIBLE_LOW = 0.20
PLAUSIBLE_HIGH = 35.0


def band(rate: float | None) -> str:
    if rate is None:
        return "n/a"
    if rate < PLAUSIBLE_LOW:
        return "SUSPICIOUS (below any published rate -- double-counted tokens?)"
    if rate > PLAUSIBLE_HIGH:
        return "SUSPICIOUS (above every published rate -- double-charged?)"
    return "plausible"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dir", default=str(Path.home() / ".claude/projects"))
    parser.add_argument("--codex-dir", default=None)
    args = parser.parse_args(argv)

    table = value_meter.default_table()
    now = datetime.now().astimezone()
    print(f"month to date: {now:%Y-%m} (as of {now:%Y-%m-%d %H:%M})")
    print(f"prices generated: {table.as_of()}\n")

    rows = []

    snapshot = _compute(Path(args.dir))
    claude_usd = snapshot.get("_month_value_usd")
    claude_tokens = snapshot.get("monthTokens")
    if claude_usd is None:
        # _compute's internals differ across branches; fall back to the
        # payload it publishes rather than guessing at private fields.
        value = snapshot.get("value") or {}
        claude_usd = value.get("claude_usd") or value.get("value_usd")
    rows.append(("claude", claude_tokens, claude_usd))

    codex_root = Path(args.codex_dir) if args.codex_dir else None
    codex_usd, codex_priced, codex_unpriced = codex_usage.month_value(
        codex_root, table=table)
    rows.append(("codex", codex_priced + codex_unpriced, codex_usd))

    print(f"{'provider':10} {'tokens':>16} {'usd':>12} {'$/Mtok':>9}  verdict")
    for name, tokens, usd in rows:
        if not tokens or not usd:
            print(f"{name:10} {tokens or 0:>16,} {usd or 0:>12,.2f} "
                  f"{'-':>9}  nothing to check")
            continue
        rate = usd / (tokens / 1_000_000)
        print(f"{name:10} {tokens:>16,} {usd:>12,.2f} {rate:>9.3f}  "
              f"{band(rate)}")

    if codex_unpriced:
        share = codex_unpriced / max(1, codex_priced + codex_unpriced)
        print(f"\ncodex unpriced tokens: {codex_unpriced:,} "
              f"({share:.1%}) -- models the table does not know")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
