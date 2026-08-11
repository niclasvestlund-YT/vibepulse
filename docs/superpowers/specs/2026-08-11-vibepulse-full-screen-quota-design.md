# VibePulse full-screen quota design

**Status:** Approved visual design

**Approved:** 2026-08-11

**Panel:** Waveshare ESP32-S3-Touch-AMOLED-2.16, 480 × 480 AMOLED

**Design source:** `design/vibepulse/explorations/v2-full-screen.html`

## Problem

The flashed VibePulse interface treats the 480 × 480 panel like a compact
dashboard. Its two-row summary clips `WEEKLY · ALL MODELS` against the hero
percentage, while its single-quota hero leaves a large unused area below the
reset line. The five-hour quota and persistent agent-status rail add density
without serving the primary job: seeing weekly quota usage at a distance.

The previous physical-review document remains a draft. Real-panel feedback on
2026-08-11 supersedes its simulator-only statement that no layout correction
was needed. This specification is design approval, not physical AMOLED
approval of an implementation.

## Goals

- Make the weekly percentage readable at 1–2 metres.
- Give each quota the entire panel instead of placing two quotas in one row
  layout.
- Preserve the useful `used today` delta and reset timing without repeating
  them.
- Make Claude and Codex visually distinct through their locked provider
  accents, icons, and labels.
- Make Burn Rate answer an action question instead of repeating weekly
  percentages.
- Keep every reachable peer page represented by the pager.

## Non-goals

- No five-hour quota.
- No VibePulse logo on an app screen.
- No persistent Claude/Codex working-status rail.
- No automatic page rotation.
- No new animation before the static physical AMOLED gate passes.
- No changes to quota acquisition, agent completion overlays, audio, OTA,
  Bluetooth, or other hardware features in this visual batch.

## Page architecture

The horizontal carousel contains exactly five manually selected peer pages:

1. Claude — Fable Weekly
2. Claude — Weekly All Models
3. Codex — Weekly
4. Burn Rate — weekly forecast for Claude All Models and Codex
5. Volume — Claude token volume

The old Claude Details and Overview pages are removed. They duplicate the
three quota pages and force critical copy into smaller, collision-prone rows.
Existing swipe/button navigation remains manual; the firmware must not change
pages on a timer.

The pager always represents these five reachable pages. It is centred, 18 px
from the bottom, with an 18 × 6 px active pill, four 6 × 6 px inactive dots,
and 5 px gaps.

## Shared visual system

All screens use true black `#000000`, IBM Plex Sans, tabular numerals, and no
cards behind hero content. Provider accents are immutable:

- Claude: `#D97757`
- Codex: `#6F78FF`

Shared geometry:

| Element | Geometry |
| --- | --- |
| Screen | 480 × 480 px |
| Safe side inset | 22 px |
| Content width | 436 px |
| Header | y 22–63; 1 px hairline at y 63 |
| Provider icon | 29 × 29 px; Claude receives a 2 px upward optical shift |
| Provider name | 21 px, weight 600, 2.5 px tracking |
| Model | 14 px, weight 600, right aligned to x 458 |
| Effort | 11 px, weight 600, 2 px tracking, same right edge |
| Quota label | x 22, y 88, 21 px, weight 600, 2 px tracking |
| Quota hero | x 16, y 104, 164 px, weight 700, −9 px tracking |
| Progress track | x 22, y 304, 436 × 20 px, circular ends |
| Bottom stats | x 22–458, y 352; two equal columns |
| Bottom values | 35 px, weight 700 |
| Bottom captions | 14 px, weight 600, 2 px tracking |

The percent hero is white. The progress fill and `used today` value use the
provider accent. The track is `#303238`; muted copy is `#9298A2`; hairlines
are `#202328`.

## Quota pages

Each quota page has the same anatomy:

1. Provider identity at top-left and live model/effort metadata at top-right.
2. One full-width quota label.
3. One 164 px consumed-percentage hero.
4. One 20 px progress bar representing the same consumed percentage.
5. `+N% / USED TODAY` at bottom-left.
6. reset duration or time above `TO RESET` at bottom-right.
7. the five-position pager.

The percentage and bar always mean **quota consumed**, including Codex data
whose source may report remaining usage. Conversion to consumed percentage
must happen before presentation.

Claude Fable uses the model-week quota and its supplied model label. Claude
All Models uses `claude_week`. Codex Weekly uses `codex_week`. The installed
profile is designed with a Fable page; product-general conditional pagination
for accounts without a model-specific quota is outside this batch.

Model and effort metadata are shown only when present. The UI never invents a
model, effort, daily delta, reset time, percentage, or forecast.

## Burn Rate page

Burn Rate does not repeat current weekly percentages. Its job is to answer:

- Should the user speed up to consume the quota by reset?
- Is the user on pace?
- Will the quota run out before reset, and when?

The header reads `BURN RATE` with `WEEKLY / FORECAST` at top-right. Two equal,
unboxed rows are separated by the shared hairline at y 251. Both rows use the
same anatomy and positions:

| Element | Row 1 | Row 2 | Style |
| --- | --- | --- | --- |
| Scope | y 82 | y 270 | 16 px/600, 2 px tracking, `#B2B7C0` |
| Outcome hero | y 111 | y 299 | 56 px/700, −2 px tracking, white |
| Explanation | y 177 | y 365 | 16 px/600, 1.5 px tracking, provider accent |

Normal examples:

| Provider state | Scope | Outcome | Explanation |
| --- | --- | --- | --- |
| Claude forecast reaches less than 100% at reset | `CLAUDE · ALL MODELS` | `SPEED UP` | `1.4× CURRENT PACE TO MAX OUT` |
| Codex exhausts before reset | `CODEX · WEEKLY` | `9H EARLY` | `RUNS OUT SAT 06:10` |

Forecast state rules:

- `at_reset`, pace factor greater than 1.0: `SPEED UP`; show the factor as a
  multiplier of current pace. `1.4×` means multiply by 1.4, not add 140%.
- `at_reset`, rounded factor equal to 1.0: `ON PACE`; explanation
  `≈ CURRENT PACE TO MAX OUT`.
- `exhausts`, negative offset: hero is the humanised magnitude followed by
  `EARLY`; explanation is local weekday/time. Under 60 minutes uses minutes;
  1–23 hours uses hours; 24 hours or more uses days plus hours.
- `exhausts`, zero offset: `ON PACE`; explanation `RUNS OUT AT RESET`.
- A positive offset in `exhausts` contradicts the current producer model and
  is presented as unavailable rather than as a misleading late forecast.
- `collecting`: `LEARNING PACE`; explanation `FORECAST NOT READY`.
- Missing, malformed, or unreliable input: `UNAVAILABLE`; explanation
  `NO RELIABLE FORECAST`.
- If the weekly quota is missing, suppress its forecast.

Claude Burn Rate deliberately uses the All Models week. The server's forecast
does not currently forecast Fable/model-week usage. Forecasts use the existing
24-hour history window and minimum sample requirements; the screen must not
present them as instantaneous measurements.

## Volume page

Volume follows the same header and lower-stat geometry without implying a
quota. It contains:

- Claude identity at top-left and `VOLUME / TOKENS` at top-right.
- `USED TODAY` as the eyebrow.
- the daily token volume as a 154 px hero with a 27 px `MTOK` unit.
- a hairline at y 304 instead of a progress bar.
- session count at bottom-left.
- monthly MTOK at bottom-right.
- the five-position pager.

Volume values are shown only when supplied by the existing data model. A
missing value is a dash, never zero.

## Missing and stale states

There is no bottom status rail.

- Before first valid quota data, keep provider and quota identity, show a hero
  dash, an empty track, and dashes for unavailable supporting values.
- On stale data, retain the last accepted values. Keep the model on the first
  metadata line and replace the effort line with `STALE`.
- On missing data, the top-right metadata may show `NO DATA`; it must not add a
  new row or card.
- A missing daily delta is a dash, not `+0%`.
- Completion overlays remain separate transient states and are not embedded in
  these five steady pages.

## Motion and AMOLED constraints

This batch is static. It adds no timers, automatic view changes, opacity
layers, canvas buffers, or persistent decoration. The AMOLED-safe black
background remains, and this batch does not change any global pixel-drift
behavior. Any later progress interpolation or completion animation requires a
successful static panel review first.

## Acceptance

Before implementation is considered complete:

1. VibePulse Studio validates the saved design and exports deterministic
   target tokens.
2. Exact 480 × 480 captures exist for all five normal pages plus quota missing
   and stale states and all Burn Rate states.
3. Longest copy (`WEEKLY · ALL MODELS`, forecast explanations, model metadata)
   has no clipping or collision.
4. Pager count and active position match all five reachable pages.
5. Host tests and the full ESP-IDF target build pass.
6. A physical flash happens only after explicit authorization.
7. The real AMOLED is inspected at arm's length and 1–2 metres before motion
   work begins. New physical evidence updates the pending review document.
