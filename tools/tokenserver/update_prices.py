#!/usr/bin/env python3
"""Regenerate ``prices.json`` from a maintained public price catalogue.

Nobody should hand-maintain API rates. They change, they are easy to mistype,
and a wrong rate is invisible -- the multiple just quietly lies. So the rates
in ``prices.json`` are *generated* from LiteLLM's
``model_prices_and_context_window.json``, the catalogue the wider agent-usage
tooling already standardised on, and refreshing them is one command:

    python3 tools/tokenserver/update_prices.py

The generated file is committed, so the tokenserver itself never touches the
network -- it reads rates off disk like it always did. This script is the only
part that fetches, and only when a human runs it.

Why a catalogue instead of the vendor pages: vendor pricing lives in HTML
marketing pages with no stable machine-readable form, while this catalogue is
a single JSON document, updated as models ship, carrying the fields that
actually matter here -- per-model cache-read and cache-creation rates, which
vendor tables usually express only as prose multipliers.

Pass ``--from FILE`` to generate from a local copy instead of fetching, and
``--check`` to verify the committed file is current without rewriting it
(useful in CI).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from datetime import date
from pathlib import Path

CATALOGUE_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)

OUTPUT_PATH = Path(__file__).with_name("prices.json")

#: Catalogue provider -> how that provider counts ``input_tokens``. This is the
#: one fact the catalogue does not carry and cannot be derived from rates, so
#: it stays here as curated knowledge. Getting it wrong silently mis-bills, so
#: it is asserted in tests rather than trusted.
ACCOUNTING = {
    "anthropic": "cache_excluded_input",
    "openai": "cache_included_input",
}

#: Modes worth pricing. An agent CLI talks to chat/responses models; audio,
#: image, embedding and moderation entries would only pad the file.
MODES = {"chat", "responses"}

#: catalogue key -> our key. Per-million conversion applies to all of them.
RATE_KEYS = {
    "input_cost_per_token": "input",
    "output_cost_per_token": "output",
    "cache_read_input_token_cost": "cache_read",
    "cache_creation_input_token_cost": "cache_write_5m",
    "cache_creation_input_token_cost_above_1hr": "cache_write_1h",
}

PER_MILLION = 1_000_000


def fetch(url: str = CATALOGUE_URL) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as response:
        return response.read()


def blob_sha(raw: bytes) -> str:
    """Git blob SHA of the upstream bytes -- pins exactly which revision this
    was generated from, and is checkable against GitHub without trusting us."""
    header = b"blob %d\0" % len(raw)
    return hashlib.sha1(header + raw).hexdigest()


def _rate(value) -> float | None:
    """Per-token cost -> per-million, with float noise rounded away.

    The catalogue stores 2e-07 style figures; multiplying by a million lands
    on 0.19999999999999998, which would then be committed and diffed forever.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value < 0:
        return None
    return round(value * PER_MILLION, 6)


def convert(catalogue: dict) -> dict[str, dict[str, dict]]:
    """Catalogue -> ``{provider: {model: rates}}`` for supported providers."""
    out: dict[str, dict[str, dict]] = {name: {} for name in ACCOUNTING}
    for model, spec in catalogue.items():
        if not isinstance(spec, dict):
            continue
        provider = spec.get("litellm_provider")
        if provider not in out or spec.get("mode") not in MODES:
            continue
        rates = {}
        for source_key, our_key in RATE_KEYS.items():
            rate = _rate(spec.get(source_key))
            if rate is not None:
                rates[our_key] = rate
        # Both sides of the bill are required. A model priced on input alone
        # would look 5x too cheap, which is worse than being unpriced.
        if "input" not in rates or "output" not in rates:
            continue
        out[provider][model] = rates
    return {name: dict(sorted(models.items()))
            for name, models in out.items()}


def build(catalogue: dict, source_sha: str, generated: str) -> dict:
    models = convert(catalogue)
    providers = {}
    for name, accounting in ACCOUNTING.items():
        providers[name] = {
            "accounting": accounting,
            # Fallbacks for a model the catalogue prices without cache figures,
            # and for override files that state only input/output. Documented
            # vendor multiples of the input rate.
            "cache_write_5m_multiplier": 1.25,
            "cache_write_1h_multiplier": 2.0,
            "cache_read_multiplier": 0.1,
            # Verified flat across every catalogue model carrying batch rates.
            "tier_multipliers": {"standard": 1.0, "batch": 0.5},
            "models": models[name],
        }
    return {
        "_readme": [
            "GENERATED FILE -- do not hand-edit.",
            "",
            "Regenerate with:  python3 tools/tokenserver/update_prices.py",
            "Override without editing:  --prices my-prices.json  (merged per",
            "model over these, so state only what differs).",
            "",
            "Rates are USD per million tokens, converted from the catalogue's",
            "per-token figures. cache_write_5m / cache_write_1h / cache_read",
            "are the vendor's real per-model cache rates where the catalogue",
            "publishes them; the *_multiplier fallbacks apply only to models",
            "that lack them.",
            "",
            "'accounting' is the one fact no catalogue carries: providers do",
            "not count input tokens the same way, and guessing mis-bills.",
            "  cache_excluded_input  input_tokens is FRESH input only; cache",
            "                        reads and writes are separate. Anthropic.",
            "  cache_included_input  input_tokens ALREADY INCLUDES cached",
            "                        tokens, so fresh input is the difference.",
            "                        OpenAI / Codex.",
        ],
        "source": {
            "catalogue": "BerriAI/litellm model_prices_and_context_window.json",
            "url": CATALOGUE_URL,
            "blob_sha": source_sha,
            "generated": generated,
        },
        "providers": providers,
        "plans": {
            "_note": (
                "Monthly subscription cost in USD. Deliberately not sourced "
                "from the catalogue: API price lists do not cover consumer "
                "subscriptions, and what you pay depends on your plan, tax "
                "and currency. These are the public US list prices as a "
                "starting point -- pass --plan-cost-usd to state what you "
                "actually pay, and the payload reports which was used."),
            "claude": {"pro": 20.0, "max5x": 100.0, "max20x": 200.0},
            "codex": {"plus": 20.0, "pro": 200.0},
        },
    }


def render(document: dict) -> str:
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--from", dest="source", metavar="FILE",
                        help="generate from a local catalogue copy")
    parser.add_argument("--check", action="store_true",
                        help="fail if the committed file is out of date")
    parser.add_argument("--out", default=OUTPUT_PATH, type=Path)
    args = parser.parse_args(argv)

    raw = (Path(args.source).read_bytes() if args.source
           else fetch())
    catalogue = json.loads(raw)

    if args.check and args.out.exists():
        # Keep the existing stamp so an unchanged catalogue is a no-op diff.
        previous = json.loads(args.out.read_text(encoding="utf-8"))
        stamp = (previous.get("source") or {}).get("generated")
    else:
        stamp = None
    document = build(catalogue, blob_sha(raw),
                     stamp or date.today().isoformat())

    rendered = render(document)
    if args.check:
        current = args.out.read_text(encoding="utf-8") if args.out.exists() else ""
        if current != rendered:
            print(f"{args.out} is out of date; run update_prices.py",
                  file=sys.stderr)
            return 1
        print(f"{args.out} is current")
        return 0

    args.out.write_text(rendered, encoding="utf-8")
    counts = {name: len(spec["models"])
              for name, spec in document["providers"].items()}
    print(f"wrote {args.out} from blob {blob_sha(raw)[:12]}: "
          + ", ".join(f"{n} {p}" for p, n in counts.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
