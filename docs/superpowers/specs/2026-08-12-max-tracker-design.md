# Max Tracker — design (approved 2026-08-12)

Two new VibePulse pages: a GitHub-style daily heatmap per provider showing
"how hard did I run my plan", with honest backfill and streak stat tiles.
The VOLUME view is removed in the same change (user decision: it carries no
story). View count stays at parity: 5 today → 6 after (4 kept + 2 new).

## Decisions locked during brainstorm (user-approved)

1. **Cell color = strict quota-%** of the user's own plan (Strava fairness).
   Never volume-as-percent, never fabricated values.
2. **One page per provider**: CLAUDE · MAX TRACKER and CODEX · MAX TRACKER.
3. **Cell value = day's peak of the 5h/session window**. MAX WEEKS counts
   ISO weeks where the general weekly window reached 100 %.
4. **No per-cell touch in v1** — the page is ambient; tiles carry numbers.
5. **Stat tiles**: STREAK (combined coding streak, days in a row with any
   agent activity), MAX WEEKS, AVG PEAK, MAX DAYS (days at 100 %).
6. **Architecture**: server-derived. tokenserver aggregates and serves a new
   flat endpoint; the screen is pure presentation (glance pattern).
7. **No fire glyphs** (user rejected). A GitHub-style 5-step mini legend
   (dim → provider accent → red) with the label MAX sits under the grid,
   right-aligned. Red is reserved for exactly 100 %.
8. **Provider-colored heat scales**; deep red at 100 % on both:
   - Claude stops: (0)#0c0e11 (30)#2c1a12 (60)#6c3a22 (85)#D97757
     (99)#F09470 (100)#FF2D1F
   - Codex stops: (0)#0c0e13 (30)#1a1c34 (60)#3a3f7a (85)#6F78FF
     (99)#969EFF (100)#FF2D1F
9. **Optional plan badge** (user addition 2026-08-12): tokenserver flags
   `--claude-plan {pro,max5x,max20x}` / `--codex-plan {plus,pro}` map to an
   allowlisted `planLabel` ("PRO", "MAX 5X", "MAX 20X", "PLUS") in the
   contract; the tracker eyebrow row renders it right-aligned, muted,
   plex_text_16. Absent flag → absent field → nothing rendered. Labels are
   display-identity only — all math stays plan-agnostic percentages.
10. **Graded activity backfill instead of dummy data**: days before quota
   sampling render as outlined gray cells with 3 intensity levels derived
   from real daily token volume (server-computed terciles per provider over
   the window). Gray = "active, quota unknown"; color = real quota-%.
   Historical days never gain quota data; gray phases out as real quota days
   accumulate to the right.

## Data (tokenserver)

New module `tools/tokenserver/max_tracker.py`:

- **Codex backfill**: incremental scan of all `~/.codex/sessions/**/
  rollout-*.jsonl` for `payload.rate_limits` snapshots (verified present in
  62/62 sampled files back to 2026-03-22). Same acceptance rules as the
  existing fallback (direct `event_msg`/`token_count` only, no quoted
  objects). Daily peak of the primary (≤600 min) window %, weekly-window
  100 % per ISO week. Bounded IO: 64 KiB blocks, ≤1 MiB or 256 records per
  file per pass, drained over multiple passes; backfill state persisted so
  it runs once per file identity.
- **Claude backfill**: daily activity flag + daily token volume from the
  existing volume scan of `~/.claude/projects/**/*.jsonl` (already
  incremental). Claude quota-% is NOT reconstructible historically — those
  days keep `pct: null`.
- **Ongoing**: every successful quota observation (the existing 120 s probe
  and Codex reads) updates today's peak. Days roll over at local midnight.
- **Persistence**: `~/Library/Application Support/VibePulse/
  max-tracker.json`, atomic write, mode 0600. One record per provider-day:
  `{d, pct|null, act, vol_level|null}` plus per-ISO-week `{w, maxed}`.
  Retention 400 days. No prompts, commands, projects, models, file names or
  raw log events can be written — same hard privacy boundary as
  usage-history.
- Stale values from the quota cache are never written into day peaks.

## Contract — GET /api/max-tracker (v1, flat, numbers not strings)

Dense form: dates resolved server-side; device receives dense window (no
date strings on the wire — index position alone carries the day).

```json
{"v": 1, "weeks": 20, "stale": false, "codingStreakDays": 47,
 "claude": {"avgPeakPct": 62.0, "maxWeeksStreak": 0, "maxWeeks": 0,
            "maxDays": 0,
            "weekMaxed": [0, 0, "...(20 total)", 0],
            "days": [[-1, -1], [-1, 0], "...(140 total)", [91, 2]]},
 "codex": {"planLabel": "PRO", "same shape": 0}}
```

- `days` is fixed-length 140 (20 weeks × 7 days); index 0 is the oldest
  ISO-Monday, index 139 is today. `pct` ranges −1..100 (−1 = no quota data
  for that day, honest absence); `lvl` ranges −1..2 (−1 = inactive day,
  0–2 = server-computed volume tercile for activity-only backfill days).
  100 is reserved for an exact quota max.
- `weekMaxed` is fixed-length 20, one entry per ISO week aligned to Monday,
  same left-to-right order as `days`; `1` = the general weekly window
  reached 100 % that week, `0` otherwise.
- `planLabel` is optional per provider: an allowlisted display string
  ("PRO", "MAX 5X", "MAX 20X", "PLUS"). Absent flag → absent field →
  nothing rendered.
- `avgPeakPct` is `null` when there are zero real-`pct` days to average
  (never fabricated); `codingStreakDays` is `null` before any activity is
  recorded (device maps this to −1 = unknown).
- Aggregates (`codingStreakDays`, `avgPeakPct`, `maxWeeksStreak`, `maxWeeks`,
  `maxDays`) are server-computed; the screen never derives them.
- `stale: true` when the server could not refresh today (mirrors the quota
  stale contract); header shows STALE, history cells render normally.

## Screen (components/app_tokens)

- **Views**: remove `VIEW_VOLUME`; add `VIEW_TRACKER_CLAUDE`,
  `VIEW_TRACKER_CODEX`. `TK_USAGE_SCREEN_VIEWS` 5 → 6. Order: CLAUDE_FABLE,
  CLAUDE_ALL, CODEX_WEEKLY, BURN_RATE, TRACKER_CLAUDE, TRACKER_CODEX.
- **New files**: `max_tracker_parse.c/h` (contract rules + hostile-input
  tests, cJSON, byte-identical sim/target), `max_tracker_presenter.c/h`
  (pure layout math: cell colors from stops tables, legend swatches, tile
  strings, sv-SE numbers), rendering integrated in `usage_screen.c` tiles.
- **Rendering**: one plain `lv_obj_t` per page with a `LV_EVENT_DRAW_MAIN`
  custom draw of 140 rounded rects (18 px cell, 3 px gap, radius 3, grid x
  31, y 112) plus 5 legend swatches. No lv_canvas, no persistent buffers,
  no per-cell objects (AMOLED-skill memory invariant). Static only —
  no lv_anim (motion stays gated behind the physical review).
- **Header/labels**: existing shared live header; eyebrow "MAX TRACKER"
  (plex_text_21); tile labels plex_text_16; values plex_num_38; units
  plex_text_17; legend label "MAX" plex_text_16. **No new fonts needed** —
  all glyphs exist in the current subsets.
- **Fetch**: `net.c` gains a max-tracker fetch on the same cadence class as
  tokens (30 s is unnecessary — history moves daily; poll every 5 min,
  refetch immediately after reconnect), gated on the same base URL secret.
- **VOLUME removal**: delete the tile, `usage_screen_set_volume`, its
  presenter rows, sim tour stages 11–12, `vibepulse-volume` captures and
  their landmark assertions; day/month volume fields stay in the tokens
  contract (server unchanged, other consumers unaffected).

## Sim, fixtures, QA

- Fixtures: `max-tracker-full.json` (Codex 20 weeks, reds in maxed weeks),
  `max-tracker-coldstart.json` (Claude gray gradient + 5 colored days),
  `max-tracker-empty.json` (all absent). Hostile cases (wrong types, >100
  pct, bad dates, giant arrays, duplicate keys, truncated JSON) live as
  inline JSON string literals in `test/test_max_tracker_parse.c`, not a
  fixture file — same pattern as the tokens and agent-status parser tests.
- Sim key `M` feeds the next tracker fixture; `[`/`]` paging already covers
  the new views. Tour dumps `vibepulse-tracker-claude` and
  `vibepulse-tracker-codex`; static QA adds stale + empty states.
- Landmark tests: exact 480×480; grid geometry; at least one cell asserting
  each of: empty #0c0e11-family, gray level fill+outline, provider accent,
  exact red #FF2D1F only at 100; legend right edge = grid right edge;
  pager has 6 dots.

## Honesty and failure

- Before first fetch: NO DATA header state, dash tiles, empty grid.
- Server unreachable > 2 min: STALE header treatment (existing pattern);
  tiles keep last values, never tick.
- Streak definitions: coding streak counts calendar days (local) with
  `act`; today counts as soon as activity exists, never presumed.
  MAX WEEKS streak counts consecutive completed ISO weeks.
- AVG PEAK averages only days with real `pct`; fewer than 3 such days →
  value renders but no smoothing claims; zero such days → dash.
- Never fabricate: no dummy history, no invented zeros, gray levels only
  from real volume.

## Testing gates

1. tokenserver unittests: backfill bounds, aggregation, streak math across
   DST/month edges, atomic persistence, privacy-field allowlist, terciles.
2. Host parser/presenter tests via `./test/run.sh` incl. hostile fixtures.
3. Studio design check + `tools/preview-ui.sh vibepulse` exact captures.
4. Target build (`idf.py build`) with Buddy present.
5. Physical AMOLED review remains a separate explicit gate before any
   flash; static-before-motion unchanged.
