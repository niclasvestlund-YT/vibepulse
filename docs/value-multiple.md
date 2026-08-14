# The value multiple

**"You've pulled $312 of API value out of a $100 plan. 3.1×."**

Every usage page in this ecosystem answers *how much have I spent?*. None of
them answers the question a subscriber actually has standing every month:
**am I getting my money's worth?** This does, by pricing the tokens your
agents already logged at published list API rates and dividing by what you
pay.

It is the same data the Usage page shows, aimed at a different question.

---

## Turning it on

Nothing to install. Run the tokenserver with what you actually pay:

```sh
python3 tools/tokenserver/tokenserver.py \
  --claude-plan max5x \
  --plan-cost-usd 100
```

`--plan-cost-usd` is the important one. Subscription prices are not part of
any API price list, so the server cannot look yours up — see
[Where the numbers come from](#where-the-numbers-come-from). Without it you
get the US list price for the plan, and `cost_source` says `default` so the
display can avoid implying precision.

Running Codex as well? Add `--codex-plan pro`. The two monthly costs are
added, so the multiple answers *what do I get back for everything I pay for?*

The figure lands on `GET /api/tokens` under an additive `value` key:

```json
"value": {
  "value_usd": 108.36,       "plan_usd": 100.0,
  "multiple": 1.08,          "state": "ok",
  "cost_source": "configured",
  "basis": "list API prices", "prices_as_of": "2026-08-14",
  "unpriced_token_share": 0.0
}
```

Already-flashed screens ignore the key — `tokens_parse.c` skips unknown
top-level fields — so adding this cannot break a device you have not
reflashed.

---

## The four states

`state` is what a display should branch on. It exists because a wrong number
here is worse than no number.

| `state` | Meaning | Show |
|---|---|---|
| `ok` | The multiple is meaningful. | The multiple. |
| `no_plan_cost` | Usage priced, but nothing says what the plan costs — there is no denominator. | The dollars; dash the multiple. |
| `partial` | Too much of the month came from models the price table does not know. | Dash everything. |

Two more fields modify how confidently you present it:

- **`cost_source`** — `configured` means you stated what you pay.
  `default` means the table used a list price; say so rather than implying
  precision.
- **`prices_as_of`** — the date the rate snapshot was generated. An unknown
  model already degrades the state, but a *known* model whose rate changed
  after this date prices silently at the old rate. Age this field if you want
  to warn about a stale snapshot.

---

## Why this is not just input + output

A real Claude assistant record from this repo's own logs:

```json
{"input_tokens": 2, "output_tokens": 4,
 "cache_creation_input_tokens": 8246, "cache_read_input_tokens": 23655}
```

Two input tokens. Twenty-three thousand cache-read tokens. Pricing only
input and output bills **$0.00011** for a record actually worth **$0.063** —
a 577× understatement. Cache is not a rounding error here; it is the bill.

Cache writes are split further, because a 1-hour write costs substantially
more than a 5-minute one — for Opus 5, $10.00/M against $6.25/M. Both are the
vendor's own published per-model rates, and the transcript carries which
bucket applies in `usage.cache_creation`, so neither is guessed. Where only a
flat total exists it is attributed to the cheaper bucket, so the fallback can
only ever *understate*.

### Providers do not count input the same way

This is the trap worth knowing if you extend this to another provider.

| Convention | `input_tokens` means | Used by |
|---|---|---|
| `cache_excluded_input` | Fresh input only; cache read/write reported separately. | Anthropic |
| `cache_included_input` | **Already includes** `cached_input_tokens`. | OpenAI / Codex |

Reading Codex's `input_tokens` as fresh input overcharges the input side by
3.6× on a typical turn. Each provider therefore declares its `accounting`
mode as data, not as an assumption in code.

Two more Codex specifics, both read out of `codex-rs` rather than assumed:
`reasoning_output_tokens` is a **subset** of `output_tokens` (adding it
double-counts), and `total_tokens` is the context window size, not a billing
figure.

---

## Keeping the rates current

Rates are **not hand-written**. They are generated from
[LiteLLM's `model_prices_and_context_window.json`][catalogue] — the maintained
catalogue the wider agent-usage tooling already standardised on — into
[`tools/tokenserver/prices.json`](../tools/tokenserver/prices.json). Refreshing
is one command:

```sh
python3 tools/tokenserver/update_prices.py
```

That is the only part of this feature that touches the network, and only when
you run it. The generated file is committed, so the server itself reads rates
off disk exactly as before.

`--check` regenerates in memory and exits non-zero if the committed file no
longer matches the catalogue, without writing anything:

```sh
python3 tools/tokenserver/update_prices.py --check
```

It is not wired into CI, deliberately: upstream bumps a rate whenever any
vendor does, and that should not fail an unrelated pull request. Run it on a
schedule, or before a release.

[catalogue]: https://github.com/BerriAI/litellm/blob/main/model_prices_and_context_window.json

## Adding or correcting a rate

Point `--prices` at your own file and it is merged **per model** over the
generated one — state only what differs:

```jsonc
// my-prices.json — corrects one rate, adds one model
{
  "providers": {
    "anthropic": {
      "models": {
        "claude-opus-5":  { "input": 4.50, "output": 22.00 },
        "claude-opus-6":  { "input": 7.00, "output": 35.00 }
      }
    }
  }
}
```

A model stated with only `input` and `output` still prices its cache traffic,
via the documented multiples of input (5-minute write 1.25×, 1-hour write 2×,
read 0.1×). Models from the catalogue never need that fallback — they carry
the vendor's real per-model cache rates.

```sh
python3 tools/tokenserver/tokenserver.py --prices my-prices.json
```

A model that ships after this release needs one JSON entry and no code
change. An unknown model is never silently free — its tokens count toward
`unpriced_token_share`, and past 2% the multiple degrades to a dash.

A malformed `--prices` file is a **hard startup failure**, never a silent
fallback. Quietly pricing against rates you believe you replaced is exactly
the failure this feature exists to avoid.

### Adding a whole provider

```jsonc
{
  "providers": {
    "yourprovider": {
      "accounting": "cache_included_input",
      "cache_write_5m_multiplier": 1.25,
      "cache_read_multiplier": 0.1,
      "tier_multipliers": { "standard": 1.0, "batch": 0.5 },
      "models": { "their-model-1": { "input": 3.0, "output": 12.0,
                                     "cache_read": 0.3 } }
    }
  }
}
```

`accounting` is required and validated at startup — an unrecognised mode
raises rather than defaulting to a convention that might overcharge.

To generate a new provider from the catalogue instead of hand-writing it, add
it to `ACCOUNTING` in `update_prices.py` and re-run. The only thing that map
supplies is the accounting convention; the rates come from the catalogue.

---

## Where the numbers come from

| Numbers | Provenance |
|---|---|
| **Model rates** | Generated from [LiteLLM's price catalogue][catalogue], pinned to an exact revision. `prices.json` records the upstream `blob_sha`, verifiable with `git hash-object` on the downloaded file. |
| **Cache rates** | The same catalogue: real per-model cache-read and cache-creation prices, including the separate 1-hour write rate. Not inferred from input. |
| **Accounting convention** | Curated in `update_prices.py`, because no catalogue carries it — and read out of each vendor's source rather than assumed. This is the one number-affecting fact that is ours. |
| **Subscription costs** | US public list prices as a starting point. Deliberately *not* from an API price list: those do not cover consumer subscriptions, and what you pay depends on plan, tax and currency. Pass `--plan-cost-usd`; the payload reports which was used. |

Why a catalogue rather than the vendor pricing pages: vendor prices live in
HTML marketing pages with no stable machine-readable form, while this is one
JSON document, updated as models ship, that already carries the per-model
cache rates vendors usually publish only as prose multipliers.

The previous release hand-maintained this table. It shipped `claude-sonnet-5`
at $3/$15 when the real rate was $2/$10 — a 50% error, sitting behind a
`"verified": true` flag. That is the argument for generating it, and there is
a regression test named after it.

---

## What it never does

- **Never reads message content.** Only numeric usage fields and model ids.
  Nothing leaves the Mac.
- **Never counts a duplicate twice.** Claude Code writes the same record into
  more than one transcript; the price rides on the record, so the existing
  `(message id, requestId)` dedup covers dollars exactly as it covers tokens.
- **Never prices a record halfway.** A record prices completely or not at
  all — a partially priced record is indistinguishable from a cheap one.
- **Never invents a denominator.** No plan cost means a dash, not a guess.

---

## Known limits

- **Codex is untested against a real install.** Its shapes were read out of
  the Codex source (`TokenCountEvent`, `TurnContextItem.model`,
  `TokenUsage`), and its tests are synthesised from those shapes. Confirm
  against a real `~/.codex` before trusting that half of the figure.
- **List prices are not your prices.** Enterprise discounts, credits and
  promotional rates are not modelled. The multiple answers "what would this
  have cost at list?", which is the honest version of the question.
- **Long-context tiers are not modelled.** Some models bill input above a
  200k-token context at a higher rate. Those tiers are priced at the standard
  rate here, so a long-context-heavy month *understates* slightly. It does not
  affect Opus 5, which has no such tier.
- **A rate that changed after `prices_as_of` prices at the old value.** An
  unknown *model* degrades the state; a known model at a stale *rate* cannot
  be detected locally. Re-run `update_prices.py`.
- **The multiple is month-to-date**, so it climbs through the month and
  resets on the 1st. Early-month figures are not a monthly rate.
