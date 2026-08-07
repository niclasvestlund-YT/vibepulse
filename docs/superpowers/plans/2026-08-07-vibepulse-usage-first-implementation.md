# VibePulse Usage-First Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the current flashing agent-centric screen with a quiet, usage-first VibePulse experience for Claude and Codex, including truthful model metadata, daily quota movement, a weekly pace forecast, and restrained activity animation.

**Architecture:** The Mac token server remains the source of truth and gains bounded quota history plus forecast calculations. The ESP32 receives a backward-compatible flat JSON contract, turns it into small presentation models, and renders provider pages with shared LVGL components. Static layout is approved in the simulator and on the physical AMOLED before any motion is added.

**Tech Stack:** Python 3 standard library, C11, ESP-IDF 5.5.2, LVGL 9.5, Unity-style C tests, simulator BMP snapshots.

---

## Guardrails

- Keep the shared application implementation byte-identical between simulator and ESP32 target, except for the existing networking boundary.
- Use true black as the screen background and neutral charcoal cards. Color communicates provider, quota fill, warning, or activity; it is not decoration.
- Never infer a model-specific quota label from the currently active model. Show `FABLE · VECKA`, `OPUS · VECKA`, or `SONNET · VECKA` only when the limits source names that bucket.
- Preserve unknown/unavailable values. Do not turn missing data into zero.
- Store no prompt, response, filename, or source text in usage history.
- Stop after Task 10 for a physical AMOLED review before implementing animation.
- Sound and spoken completion announcements stay in the existing agent-monitor follow-up plan and are not a blocker for this visual redesign.

## Task 1: Record a Clean Baseline

**Files:**
- Verify: `docs/superpowers/specs/2026-08-07-vibepulse-usage-first-design.md`
- Verify: `test/run.sh`
- Verify: `tools/tokenserver/test_agent_status.py`

- [ ] Confirm only `.superpowers/` and `work/` are untracked before implementation.
- [ ] Run the current C test suite and Python agent-status tests.
- [ ] Build the simulator and capture the existing screenshots as comparison evidence.
- [ ] Record any pre-existing failure in the execution notes; do not silently absorb it into this feature.

Commands:

```sh
./test/run.sh
python3 -m unittest tools.tokenserver.test_agent_status
cmake -S sim -B sim/build && cmake --build sim/build
./sim/build/torget-sim
```

Expected: tests pass and the simulator writes its current `/tmp/torget-*.bmp` matrix.

## Task 2: Capture Truthful Model and Effort Metadata

**Files:**
- Modify: `tools/tokenserver/agent_status.py`
- Modify: `tools/tokenserver/test_agent_status.py`

- [ ] Add failing tests for top-level Claude `message.model` and `effort`.
- [ ] Add a failing test proving nested tool-input model names are ignored.
- [ ] Add failing tests for Codex `turn_context.payload.model` and `effort`.
- [ ] Add a failing test proving metadata carries forward only within the same task.
- [ ] Extend `Event` and snapshots with nullable `model` and `effort`.
- [ ] Normalize known display names and cap UTF-8-safe output to 24 and 12 bytes.
- [ ] Run the focused test module and commit.

Core expectations:

```python
self.assertEqual(snapshot["model"], "FABLE 5")
self.assertEqual(snapshot["effort"], "XHIGH")
self.assertIsNone(other_task_snapshot["model"])
```

Normalization table:

```python
MODEL_LABELS = {
    "claude-fable-5": "FABLE 5",
    "claude-opus-5": "OPUS 5",
    "claude-sonnet-5": "SONNET 5",
    "gpt-5.6-sol": "GPT-5.6 SOL",
}
```

Command:

```sh
python3 -m unittest tools.tokenserver.test_agent_status
```

Commit: `Lägg till modell och effort i agentstatus`

## Task 3: Parse Optional Model Metadata on the Device

**Files:**
- Modify: `components/app_tokens/agent_status.h`
- Modify: `components/app_tokens/agent_status.c`
- Modify: `test/test_agent_status.c`

- [ ] Add failing tests for present, missing, null, overlong, and wrong-type `model` and `effort`.
- [ ] Add `AGENT_MODEL_CAP 25` and `AGENT_EFFORT_CAP 13` including NUL.
- [ ] Parse both fields as optional and nullable so old servers remain compatible.
- [ ] Reject present wrong-type and overlong values.
- [ ] Run the C suite and commit.

Contract shape:

```c
typedef struct {
    char task_id[AGENT_TASK_ID_CAP];
    char project[AGENT_PROJECT_CAP];
    char activity[AGENT_ACTIVITY_CAP];
    char model[AGENT_MODEL_CAP];
    char effort[AGENT_EFFORT_CAP];
    bool has_model;
    bool has_effort;
} agent_status_t;
```

Command:

```sh
./test/run.sh
```

Commit: `Läs modellmetadata i VibePulse`

## Task 4: Preserve the Real Claude Quota Bucket Label

**Files:**
- Modify: `tools/tokenserver/tokenserver.py`
- Create: `tools/tokenserver/test_tokenserver.py`

- [ ] Extract the header-to-limit parsing into a directly testable helper if required.
- [ ] Add failing tests for explicit Fable, Opus, and Sonnet weekly bucket names.
- [ ] Add a failing test proving a generic weekly limit has no model label.
- [ ] Add a failing test proving active agent model does not manufacture a quota label.
- [ ] Return nullable `claudeModelWeekLabel` only from the limits source.
- [ ] Run focused server tests and commit.

Expected JSON fragments:

```json
{"claudeModelWeekLabel":"FABLE · VECKA"}
```

or, when the source is generic:

```json
{"claudeModelWeekLabel":null}
```

Command:

```sh
python3 -m unittest tools.tokenserver.test_tokenserver
```

Commit: `Behåll Claudes riktiga quotanamn`

## Task 5: Build Privacy-Safe Usage History and Forecasts

**Files:**
- Create: `tools/tokenserver/usage_history.py`
- Create: `tools/tokenserver/test_usage_history.py`

- [ ] Add failing tests for 15-minute sampling, 8-day retention, reset-cycle separation, and atomic persistence.
- [ ] Add corruption recovery and verify it starts empty without deleting unrelated files.
- [ ] Add failing forecast tests for insufficient span, insufficient movement, low pace, exhaustion before reset, and falling percentages.
- [ ] Implement least-squares percentage slope over the latest 24 hours in the same reset cycle.
- [ ] Require at least 3 samples, 90 minutes of span, and 1 percentage point of movement.
- [ ] Store only timestamp, provider, window, percentage, and reset-cycle identifier.
- [ ] Run tests and commit.

Public model:

```python
@dataclass(frozen=True)
class Forecast:
    state: str  # unavailable, collecting, at_reset, exhausts
    pct_at_reset: int | None = None
    pace_factor: float | None = None
    exhausts_at: int | None = None
    offset_minutes: int | None = None
```

Constants:

```python
SAMPLE_INTERVAL_S = 15 * 60
RETENTION_S = 8 * 24 * 60 * 60
FORECAST_WINDOW_S = 24 * 60 * 60
MIN_FORECAST_SPAN_S = 90 * 60
MIN_FORECAST_DELTA = 1.0
```

Command:

```sh
python3 -m unittest tools.tokenserver.test_usage_history
```

Commit: `Räkna veckotakt från lokal usagehistorik`

## Task 6: Enrich the Token Server Snapshot

**Files:**
- Modify: `tools/tokenserver/tokenserver.py`
- Modify: `tools/tokenserver/test_tokenserver.py`
- Modify: `README.md`

- [ ] Add failing tests for daily weekly deltas and the optional last-hour Claude session delta.
- [ ] Add failing tests for all forecast states for Claude and Codex.
- [ ] Integrate `UsageHistory` at the 30-second snapshot recompute without writing more than once per 15 minutes.
- [ ] Default persistence to `~/Library/Application Support/VibePulse/usage-history.json` with an injectable test path.
- [ ] Emit flat optional fields while retaining every existing v2 field.
- [ ] Document the new optional contract and privacy boundary.
- [ ] Run all Python tests and commit.

New optional fields:

```text
claudeModelWeekLabel
claudeModelWeekTodayDeltaPct
claudeWeekTodayDeltaPct
claudeSessionHourDeltaPct
codexWeekTodayDeltaPct
claudeForecastState / PctAtReset / PaceFactor / At / OffsetMin
codexForecastState / PctAtReset / PaceFactor / At / OffsetMin
```

Commands:

```sh
python3 -m unittest discover -s tools/tokenserver -p 'test_*.py'
```

Commit: `Utöka tokenservern med delta och veckotakt`

## Task 7: Extend the Device Contract and Add a Pure Presenter

**Files:**
- Modify: `components/app_tokens/tokens.h`
- Modify: `components/app_tokens/tokens_parse.c`
- Modify: `test/test_tokens.c`
- Create: `components/app_tokens/usage_presenter.h`
- Create: `components/app_tokens/usage_presenter.c`
- Create: `test/test_usage_presenter.c`
- Modify: `test/run.sh`
- Modify: `sim/CMakeLists.txt`

- [ ] Add parser tests for all optional fields, absent fields, null fields, bounds, and invalid types. Malformed optional presentation fields become unavailable and must not reject otherwise valid usage.
- [ ] Add `has_delta` and `delta_pct` to quota windows.
- [ ] Add a bounded quota-label field and a forecast enum/structure.
- [ ] Add presenter tests for Claude upper/lower cards, Codex single-card layout, unavailable values, and exact copy.
- [ ] Make Claude lower-card selection a pure function of elapsed time: weekly at 0–6999 ms, 5-hour at 7000–13999 ms.
- [ ] Keep formatting independent of LVGL.
- [ ] Run the C suite and commit.

Presenter interface:

```c
typedef enum {
    USAGE_CARD_MODEL_WEEK,
    USAGE_CARD_ALL_WEEK,
    USAGE_CARD_FIVE_HOURS,
} usage_card_kind_t;

void usage_presenter_build_provider(const tk_tokens_t *tokens,
                                    tk_provider_t provider,
                                    uint32_t elapsed_ms,
                                    usage_provider_view_t *out);
```

Command:

```sh
./test/run.sh
```

Commit: `Skapa usage-presenter för VibePulse`

## Task 8: Generate Small Provider Assets from the Real Artwork

**Files:**
- Modify: `tools/agent_assets/build-agent-images.py`
- Create: `tools/agent_assets/test_build_agent_images.py`
- Modify: `components/app_tokens/agent_assets.c`
- Modify: `components/app_tokens/agent_assets.h`

- [ ] Extend the existing generator to emit 32×32 RGB565A8 descriptors from the checked-in source images.
- [ ] Regenerate assets; do not hand-redraw Claude or Codex.
- [ ] Verify dimensions, transparency, and descriptor format with the generator's validation path.
- [ ] Build the simulator and target component.
- [ ] Commit generated assets and generator changes together.

Commands:

```sh
python3 tools/agent_assets/build-agent-images.py
cmake --build sim/build
```

Commit: `Lägg till små riktiga providerikoner`

## Task 9: Build the Static Usage-First Screens

**Files:**
- Create: `components/app_tokens/usage_screen.h`
- Create: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/app.c`
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/CMakeLists.txt`
- Modify: `sim/CMakeLists.txt`

- [ ] Create a shared 480×480 page skeleton with a 54 px header, usage area, page dots, and centered 66 px activity footer.
- [ ] Reproduce the real VibePulse `V.` app icon as a small dark plate with white V and violet dot.
- [ ] Show provider left and truthful model/effort right on two rows; hide unavailable metadata without placeholder noise.
- [ ] Render Claude with two neutral cards and Codex with one larger weekly card.
- [ ] Make percentage the dominant text, followed by a split progress bar, reset text, and optional `+N% IDAG`.
- [ ] Embed the existing agent monitor policy into the footer instead of showing a full-screen overlay.
- [ ] Center pet + two-line activity copy + pulse-bar area as one group.
- [ ] Add the `VECKOTAKT` page shell and retain the existing volume page.
- [ ] Disable all new animation and card rotation for this task.
- [ ] Build and commit.

Static hierarchy:

```text
[ V.  CLAUDE ]                         [ FABLE 5 ]
                                           XHIGH
[ 73%  FABLE · VECKA                 +6% IDAG ]
[ before-today bar | today increment          ]
[ ÅTERSTÄLLS SÖN 09:00                        ]

[ 47%  ALLA · VECKA                  +3% IDAG ]
[ before-today bar | today increment          ]
[ ÅTERSTÄLLS MÅN 12:00                        ]

                     ●  ○  ○  ○
[pet]  ÄNDRAR FILER                  [pulse bars]
       PROJEKT · TORGET
```

Commit: `Bygg statisk usage-first skärm`

## Task 10: Simulator QA and Physical AMOLED Gate

**Files:**
- Modify: `sim/main.c`
- Create: `docs/superpowers/reviews/2026-08-07-vibepulse-static-amoled.md`

- [ ] Add deterministic simulator states for Claude normal, Claude missing values, Codex normal, forecast shell, and long Swedish activity copy.
- [ ] Dump 480×480 BMPs for every state.
- [ ] Inspect every screenshot at native size for clipping, balance, percentage prominence, baseline alignment, and icon clarity.
- [ ] Run `git diff --check`, C tests, Python tests, and simulator build.
- [ ] Build the ESP32 target with ESP-IDF 5.5.2.
- [ ] Flash the known device using the existing safe target procedure.
- [ ] Photograph Claude and Codex static pages on the physical AMOLED.
- [ ] Record brightness, black level, legibility at distance, bezel balance, and any adjustments in the review document.
- [ ] Show the photographs to the user and obtain approval before Task 11.

Commands:

```sh
./test/run.sh
python3 -m unittest discover -s tools/tokenserver -p 'test_*.py'
cmake --build sim/build
git diff --check
```

Commit before flashing: `Förbered fysisk granskning av usage-skärmen`

**Hard gate:** Do not start motion, rotation, or forecast visualization until the physical static screen is approved.

## Task 11: Add Restrained Activity and Provider Behavior

**Files:**
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/agent_monitor_policy.c`
- Modify: `components/app_tokens/agent_monitor_policy.h`
- Modify: `test/test_agent_monitor_policy.c`
- Delete: `components/app_tokens/agent_usage.c`
- Delete: `components/app_tokens/agent_usage.h`
- Delete: `test/test_agent_usage.c`
- Modify: `test/run.sh`

- [ ] Add policy tests for auto-following the working provider, manual page pinning, lease expiry, waiting, done, and error.
- [ ] Preserve exact safe activity categories already produced by the status service.
- [ ] Animate the pet by 1–2 px and pulse three bars only during active work.
- [ ] Stop motion when waiting, done, or error; use state color and copy instead.
- [ ] Ensure provider changes do not cause full-screen flashes.
- [ ] Remove the obsolete highest-quota overlay selector.
- [ ] Run the suite and commit.

Commit: `Gör VibePulse-aktiviteten lugnt levande`

## Task 12: Add Claude Card Rotation and Daily Movement

**Files:**
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/usage_presenter.c`
- Modify: `test/test_usage_presenter.c`

- [ ] Test the 7-second lower-card switch boundary and wraparound.
- [ ] Fade and shift only the lower card contents a few pixels; keep the page, header, upper card, footer, and background stable.
- [ ] Split weekly bars into before-today and today segments.
- [ ] Show `+N% IDAG`; after a weekly reset, show `+N% SEDAN RESET` until the first local day boundary.
- [ ] Show the optional last-hour delta only on `5 TIMMAR` when available.
- [ ] Run C tests and simulator screenshots through several rotation cycles.
- [ ] Commit.

Commit: `Låt Claude-kortet växla utan skärmflash`

## Task 13: Complete the VECKOTAKT Page

**Files:**
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/usage_presenter.c`
- Modify: `test/test_usage_presenter.c`
- Modify: `sim/main.c`

- [ ] Add exact-copy tests for `SAMLAR TAKT`, `PROGNOS SAKNAS`, at-reset, and exhausts states.
- [ ] Render Claude and Codex together using compact provider rows.
- [ ] Show `85% VID RESET` and `ÖKA 1,4× FÖR ATT MAXA` for a low pace.
- [ ] Show `QUOTAN TAR SLUT LÖR 05:00` and `9 H TIDIGT` for early exhaustion.
- [ ] Avoid false precision beyond whole percentages, one decimal pace factor, and hour-level offset copy.
- [ ] Add deterministic simulator screenshots for every forecast state.
- [ ] Run tests, inspect screenshots, and commit.

Commit: `Lägg till veckotakt för Claude och Codex`

## Task 14: Full Verification, Service Rollout, and Final Physical Review

**Files:**
- Modify: `README.md`
- Modify: `spec/ui-spec.md`
- Modify: `docs/superpowers/reviews/2026-08-07-vibepulse-static-amoled.md`

- [ ] Run all C and Python tests from a clean process.
- [ ] Build the simulator and inspect the complete screenshot matrix.
- [ ] Build the physical target with the pinned ESP-IDF version.
- [ ] Restart the existing VibePulse token service from this worktree and verify real `/tokens` and `/agent-status` responses without logging private content.
- [ ] Flash the device and observe Claude working, Claude waiting, Codex working, lower-card rotation, VECKOTAKT, manual navigation, and volume.
- [ ] Confirm missing/stale data is explicit and no fake zero appears.
- [ ] Confirm the screen can be read at walking-away distance and motion is visible without becoming distracting.
- [ ] Update UI documentation and append final physical observations.
- [ ] Run `git diff --check` and review `git status` so `.superpowers/` and `work/` remain uncommitted.
- [ ] Commit final documentation and verification adjustments.

Commands:

```sh
./test/run.sh
python3 -m unittest discover -s tools/tokenserver -p 'test_*.py'
cmake --build sim/build
git diff --check
git status --short
```

Final commit: `Slutför VibePulse usage-first`

## Definition of Done

- Claude and Codex are visually unmistakable from icon, provider name, and truthful model metadata.
- Usage percentages dominate each provider screen and remain readable on the physical 480×480 AMOLED.
- Claude shows a named model week only when supplied, plus a calm rotating all-week/5-hour lower card.
- Codex shows one large all-week card without an invented main-screen 5-hour focus.
- Weekly cards make today's movement visible without inventing data.
- The footer tells what the active provider is doing using safe categories and restrained motion.
- VECKOTAKT shows a useful shared forecast or an honest collecting/unavailable state.
- Existing provider selection, volume, stale-data, and compatibility behavior still pass.
- Static physical approval occurred before animation work, and final behavior was reviewed again on the device.

## Plan Self-Review Checklist

- [x] Every design-spec requirement maps to at least one task above.
- [x] New Python and C fields agree in spelling, nullability, units, and bounds.
- [x] Every behavior change starts with a failing focused test where practical.
- [x] No task asks an implementer to invent copy, layout, persistence, or forecast behavior.
- [x] Search the plan for common placeholder markers; the result is empty.
- [x] Run `git diff --check` before committing this plan.
