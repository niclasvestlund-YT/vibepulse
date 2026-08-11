# VibePulse live quota polish

**Status:** Approved visual direction; written specification awaiting review

**Date:** 2026-08-11

**Panel:** Waveshare ESP32-S3-Touch-AMOLED-2.16, 480 × 480 AMOLED

**Supersedes:** the header, quota-bar, bottom-stat, steady agent-status, and
completion-presentation sections of
`2026-08-11-vibepulse-full-screen-quota-design.md`. The five-page information
architecture, Burn Rate page, Volume page, missing-data rules, and manual
navigation remain unchanged.

## Outcome

VibePulse remains a quota instrument while Claude Code or Codex is working.
The weekly percentage stays dominant, today's usage remains a large secondary
statistic, and a compact provider-icon halo supplies ambient working motion.
When an agent needs the user, a provider-specific full-screen attention view
temporarily replaces the quota page so the state is legible across a room.

Claude and Codex use the same geometry and behavior. Their real icons and
locked provider identities distinguish them:

- Claude: `#D97757`
- Codex: `#6F78FF`

The interface never fabricates quota, daily change, reset, model, effort,
project, job count, or agent state.

## Steady quota page

The steady page contains no VibePulse logo, cards, five-hour quota, persistent
working rail, or automatic page rotation.

### Header

- A 32 × 32 provider icon starts at x 22. The provider name starts at x 64.
- Claude uses `tk_img_claude_32`, recolored only with Claude's locked accent.
- Codex uses the real layered 32 px cloud, chevron, and underscore assets. The
  cloud keeps its source appearance; the white foreground layers remain white.
- A one-pixel hairline remains at y 63.
- The right edge of contextual text is x 458. It is 14 px IBM Plex Sans,
  semibold, and subordinate to the quota metric.

Context is derived from fresh provider jobs:

| Provider state | Header context |
| --- | --- |
| Exactly one working job with model and effort | `NOW · <MODEL> · <EFFORT>` |
| Exactly one working job with partial metadata | `NOW · <available metadata>` |
| Two or more active jobs | `<N> CHATS ACTIVE` |
| No active job | `NO ACTIVE CHAT` |
| Agent data stale or unavailable | no invented context; use the existing stale/missing contract |

The UI must not select one model and present it as representative when several
Claude or Codex chats are active. The detailed agent monitor remains
responsible for individual projects and jobs.

### Quota hierarchy and geometry

- Quota label: existing y 72 token and 25 px visual treatment from the
  approved mockup.
- Hero percentage: LVGL object y 150 with `plex_num_164`. This corrects the
  previous excessive visual gap, whose validator incorrectly treated the
  font's nominal 164 px size as its rendered 119 px line height.
- Progress track: x 22, y 304, width 436, height 24, radius 12.
- Bottom values retain the shared 35 px stat font and two-column alignment.
  `+N% / USED TODAY` stays at bottom-left; reset distance and `TO RESET` stay
  at bottom-right.
- The five-page pager remains centered at y 456.

The shared LVGL raster, not the browser's CSS box model, is the visual
authority. If these coordinates fail the raster review, update this spec and
obtain visual approval before changing the implementation.

## Today within the weekly bar

The bar communicates total weekly usage and today's contribution without
adding another text row.

1. The track covers 0–100 percent.
2. A darker provider-derived fill covers 0 percent through the start-of-day
   baseline.
3. A three-pixel white marker identifies the start-of-day baseline and extends
   four pixels above and below the 24 px track.
4. The exact provider accent covers the baseline through current usage.
5. The right edge of the total fill still represents the hero percentage.

The baseline is `clamp(current_percent - today_delta, 0, current_percent)`.
Claude's muted pre-day fill starts at `#8A4F42`; Codex uses `#454B8A`.
Current-day segments use only the locked provider accents.

If a real daily delta is zero, show `+0%`, the marker at the current total,
and no current-day segment. If the delta is missing, show a dash for
`USED TODAY`, render a single provider-colored total fill, and omit the white
marker. Invalid or contradictory values are unavailable rather than coerced
into plausible-looking movement.

## Ambient working state

While at least one fresh job from the visible provider is working, a small
ring around the provider icon breathes in the provider accent. The 32 px icon
itself never changes brand color or position.

- Cycle: 1.35 seconds, ease-out.
- Dirty region: icon and halo only; no full-screen opacity layer or transform.
- Multiple jobs still use one provider halo; the job count is in the header.
- Idle, unknown, stale, and unavailable status has no halo.
- Waiting, completion, approval, and error use the attention state instead of
  trying to communicate through a tiny color dot.

## Full-screen attention state

An event that genuinely requires the user temporarily replaces the quota page.
The view is black with a six-pixel provider-accent outline inset eight pixels.
It contains:

- provider label at y 31, 18 px;
- real provider icon at 112 × 112, centered at y 145;
- `NEEDS YOU` at y 246, 52 px, bold and white;
- real project name at y 321, 25 px, in the provider accent;
- controlled detail such as `CLAUDE IS WAITING` or `CODEX IS WAITING` at
  y 365, 14 px;
- `TAP TO DISMISS` at y 430, 14 px.

Claude uses the large `tk_img_claude` asset. Codex uses the real large layered
cloud, chevron, and underscore assets; an approximate ghost or generic AI icon
is forbidden.

The icon ring performs three slow 1.6-second pulses on entry, then stops. The
outline and large copy remain visible until the event is dismissed, superseded
by a new state, or invalidated by fresh agent data. Touch dismisses only the
currently shown event locally and restores the quota page. Long press keeps
opening the Torget launcher. Dismissal never approves a Claude Code or Codex
action on the computer.

For several waiting events, the existing priority and event queue select the
visible project. The detail becomes `<N> CHATS WAITING`; dismissing one event
reveals the next rather than discarding all of them. Error uses the same
distance-readable anatomy with `ERROR`, never `NEEDS YOU`.

Any completion sound remains a separate hardware-gated feature. This visual
batch does not claim that a speaker is physically present.

## Truthful usage tick

Motion occurs only after an accepted quota sample changes a displayed value.
It is never a perpetual activity simulation.

For a positive update while the quota page is visible:

1. The hero changes to the new rounded integer during a 480 ms, five-pixel
   upward nudge and settle.
2. The total fill and current-day segment grow to the authoritative endpoint
   over 520 ms with ease-out timing.
3. If the displayed daily delta changes, `USED TODAY` changes and performs one
   620 ms provider-accent pulse.
4. All three elements stop completely at their authoritative values.

First load appears directly without counting from zero. A weekly reset or
other accepted decrease snaps to the authoritative value with no backwards
counting. Missing or stale data never animates. A hidden page updates silently;
opening it later does not replay old motion. If a newer accepted sample arrives
mid-animation, the renderer coalesces to the newest endpoint rather than
queueing stale intermediate values.

The implementation animates LVGL label position, fill width, and a bounded
accent style. It must not introduce a canvas, full-screen transform, large
opacity layer, extra display buffer, or automatic carousel movement.

## Codex parity

Codex receives the complete behavior, not a reduced variant:

- real Codex header and attention icons;
- Codex-blue working halo, bar segment, daily stat, and attention outline;
- single-job context such as `NOW · GPT-5.6 SOL · XHIGH`;
- truthful multi-chat count instead of a selected model;
- the same usage-tick timing and attention hierarchy;
- `CODEX IS WAITING` and the real project name in attention mode.

## Missing, stale, and reset behavior

- Preserve the last accepted quota during a stale period and stop all motion.
- Show existing `STALE` or `NO DATA` metadata without another row.
- A stale working lease cannot keep the halo alive and cannot synthesize an
  attention event.
- A daily boundary recomputes the marker from the next accepted history sample.
- A weekly reset clears both total and daily contribution according to the
  source data; it does not infer zero before a valid sample arrives.

## Verification contract

Before target delivery:

1. Presenter/policy tests cover zero, one, two, and four jobs; partial metadata;
   waiting priority; dismissal; stale leases; and provider parity.
2. Today-bar tests cover 0, 1, 12, contradictory, and missing deltas plus total
   percentages 0, 9, 73, 99, and 100.
3. Exact 480 × 480 LVGL captures cover Claude and Codex quota pages, the widest
   single-job context, multi-chat context, idle, stale, missing, `NEEDS YOU`,
   and error.
4. Rendered-glyph bounds—not nominal font sizes—enforce the quota-to-hero,
   hero-to-bar, and bar-to-stat gaps.
5. Motion tests verify final values, durations, coalescing, hidden-page
   behavior, reset behavior, and bounded dirty regions.
6. `tools/vibepulse_studio/design.py --check`, `tools/preview-ui.sh
   vibepulse`, the full host suite, and the ESP-IDF target build pass.
7. The static LVGL raster is reviewed before target build. Physical AMOLED and
   animation approval require an installed build and real-panel inspection.

## Delivery and OTA boundary

The repository currently uses a factory-only partition table and contains no
enabled OTA service. This design does not pretend otherwise. The first
OTA-capable release requires the separately reviewed safe A/B OTA foundation
and one final USB bootstrap of its bootloader, partition table, and application.

After that bootstrap, VibePulse builds may use authenticated local OTA only
through an explicitly opened maintenance window, with image digest checking,
inactive-slot writes, post-boot health validation, and rollback. If that
foundation is not implemented and physically verified, delivery remains USB;
the workflow must not silently replace the partition table or erase the device.
