# Labs display experiments

Concepts recorded on 2026-09-08. These are optional additions to explore,
not implemented pages, approved LVGL layouts or installed firmware.

## Reset clock and countdown

**Purpose:** see the next quota reset from across the room. One dominant
piece of information on the 480 × 480 display.

Two presentations of the same known reset timestamp:

- A weekday and large clock time, for example **FRIDAY 17:00**.
- Time remaining, formatted as days/hours or hours/minutes.

The selected window and provider must remain identifiable. Keep the black
AMOLED background and provider accents: Codex `#6F78FF`, Claude `#D97757`.
The examples are fictional; use source timestamps in an implemented view.

Reuse the existing quota data path. The host already carries reset timestamps
such as `weekResetAt` and `codexWeekResetAt`; the panel can count down locally
with a synchronized clock between fetches. Do not poll a provider each second.
The existing reset line on quota pages remains part of the base product.

A reset belongs to a specific quota window. It does not mean every limit
resets or that an agent is ready to run. At zero, fetch fresh data before
claiming recovery. Label cached data, show a dash without a known timestamp,
and never invent a next cycle from an expired cache.

## Coding quotes

**Purpose:** a playful idle page with one short quote, large type and a few
accent-coloured words. A local, bundled text collection is enough; it does
not need an AI request or a paid API.

Initial original copy, with no attribution to real people:

- “En sista prompt. Sen lägger jag mig.”
- “99 % klart. Sedan igår.”
- “Det fungerar. Ingen rör någonting.”
- “Jag skriver inte buggar. Jag beställer dem.”
- “Planen var en enkel app.”
- “Claude bygger. Codex granskar. Jag tar kaffe.”
- “It works on my prompt.”
- “Vibe now. Debug later.”

Explore tap-to-change and a configurable idle rotation. The copy is display
content, not an instruction to the coding agent. Typography and Swedish
glyph coverage need native-size review before implementation.

## Reset moments

**Purpose:** briefly show “Nu kör vi igen” after the source confirms a new
quota window. This is a separate opt-in from the passive reset clock, just
as the existing new-star popup is separate from the GitHub page.

Do not trigger on startup, reconnect, stale-cache expiry or a local countdown
alone. Return to the previous page, allow dismissal and avoid repeated
celebrations of the same reset. Quotes and celebrations must yield to pending
questions and device maintenance screens. Sound is not a dependency.

## Before any of these ships

Choose one static layout, verify it at 480 × 480 in Studio and shared LVGL,
exercise missing/stale/live states and widest copy, then follow the existing
physical-review process. Motion, sound and firmware installation retain
their own verification and authorization requirements.

Return to the [Labs catalogue](README.md).
