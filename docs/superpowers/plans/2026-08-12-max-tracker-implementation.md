# Max Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Two Max Tracker heatmap pages (Claude/Codex daily quota peaks, GitHub-style) replace the VOLUME view; tokenserver aggregates and backfills the data and serves `/api/max-tracker`.

**Architecture:** Server-derived (glance pattern): `tools/tokenserver/max_tracker.py` owns backfill, daily aggregation, streak math and persistence; the screen adds a byte-identical-shared parser + presenter and renders 140 rounded rects per page via one `LV_EVENT_DRAW_MAIN` callback (no canvas, no per-cell objects, no lv_anim).

**Tech Stack:** C11 (clang host tests + ESP-IDF 5.5/LVGL 9.5 target + SDL sim), Python 3.11+ stdlib (tokenserver + unittest).

## Global Constraints (from spec, verbatim where exact)

- Spec: `docs/superpowers/specs/2026-08-12-max-tracker-design.md`.
- True black background; IBM Plex; existing font subsets only (plex_text_21/16/17, plex_num_38); provider accents locked Claude `#D97757`, Codex `#6F78FF`.
- Heat stops Claude: (0)#0c0e11 (30)#2c1a12 (60)#6c3a22 (85)#D97757 (99)#F09470 (100)#FF2D1F. Codex: (0)#0c0e13 (30)#1a1c34 (60)#3a3f7a (85)#6F78FF (99)#969EFF (100)#FF2D1F. Red `#FF2D1F` appears at exactly 100 only.
- Gray activity fills lvl 0–2: `#14171c`, `#1d222a`, `#293039`, outline `#3d434d` width 1.
- Grid: 20 weeks × 7 rows, cell 18 px, gap 3, radius 3, grid x=31, y=112. Legend swatches 12 px at pcts {10,40,70,92,100}, right-aligned to grid edge, label "MAX".
- Never fabricate data: `pct: -1` renders gray-by-lvl or empty, never a color. Rejecting parse leaves `*out` untouched (tokens_parse contract).
- Static only: repository tests reject `lv_anim` in these renderers; no flash without explicit authorization.
- Do not touch `main/` (concurrent swipe-profiler work) except nothing here needs it. Do not touch `design/vibepulse/exports/claude-hero.png`.
- All commits end with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task 1: Contract fixtures + spec contract correction

**Files:**
- Modify: `docs/superpowers/specs/2026-08-12-max-tracker-design.md` (contract section)
- Create: `sim-fixtures/max-tracker-full.json`, `sim-fixtures/max-tracker-coldstart.json`, `sim-fixtures/max-tracker-empty.json`
- Modify: `sim-fixtures/README.md` (one line per fixture)

**Interfaces:**
- Produces: contract v1 dense form consumed by Tasks 2, 6, 8:
  `{"v":1,"weeks":20,"stale":false,"codingStreakDays":int|-1 via null, "claude":{...},"codex":{...}}` where each provider is
  `{"planLabel":"MAX 20X"(optional),"avgPeakPct":62.0|null,"maxWeeksStreak":0,"maxWeeks":0,"maxDays":0,"weekMaxed":[20 × 0/1],"days":[140 × [pct,lvl]]}`;
  `pct` −1..100 (−1 = no quota data), `lvl` −1..2 (−1 = inactive). Index 0 = oldest ISO-Monday; index 139 = today.

- [ ] **Step 1:** Replace the spec's dated-days contract JSON with the dense form above and note: "dates resolved server-side; device receives dense window" (edit the `## Contract` section only).
- [ ] **Step 2:** Write `max-tracker-full.json`: codex 140 pairs mostly 20–80 with six `[100,2]` spread in the last 6 weeks, `weekMaxed` last 6 = 1, `maxWeeksStreak: 6, maxWeeks: 6, maxDays: 12, avgPeakPct: 84.0`, `planLabel: "PRO"`; claude fully colored analog without plan label; `codingStreakDays: 47`. Keep file ≤ 6 KB (BODY budget check in Task 8).
- [ ] **Step 3:** Write `max-tracker-coldstart.json`: claude days = 14×[-1,-1] then mostly `[-1,0..2]`, last 5 = `[55..91, 2]`, `avgPeakPct: 62.0`, zeros for max fields with `weekMaxed` all 0; codex as in full. `planLabel: "MAX 20X"` on claude.
- [ ] **Step 4:** Write `max-tracker-empty.json`: `codingStreakDays: null`, both providers `avgPeakPct: null`, all days `[-1,-1]`, `weekMaxed` all 0.
- [ ] **Step 5:** Commit: `git add sim-fixtures docs/superpowers/specs && git commit -m "Add Max Tracker contract fixtures"`

### Task 2: Shared data model + parser (hosttested)

**Files:**
- Create: `components/app_tokens/max_tracker.h`, `components/app_tokens/max_tracker_parse.h`, `components/app_tokens/max_tracker_parse.c`
- Create: `test/test_max_tracker_parse.c`
- Modify: `test/run.sh` (add compile block after the tokens test, same shape)

**Interfaces:**
- Produces (consumed by Tasks 3, 6, 8):

```c
/* max_tracker.h */
#define TK_MT_WEEKS 20
#define TK_MT_DAYS (TK_MT_WEEKS * 7)
typedef struct { int8_t pct; int8_t lvl; } tk_mt_day;
typedef struct {
  bool has_avg; double avg_peak_pct;
  int max_weeks; int max_weeks_streak; int max_days;
  bool has_plan; char plan_label[12];
  bool week_maxed[TK_MT_WEEKS];
  tk_mt_day days[TK_MT_DAYS];
} tk_mt_provider;
typedef struct {
  int coding_streak_days; /* -1 = unknown */
  bool stale;
  tk_mt_provider claude, codex;
} tk_max_tracker;
/* max_tracker_parse.h */
bool tk_max_tracker_parse(const char *json, size_t len, tk_max_tracker *out);
```

- [ ] **Step 1:** Write `test/test_max_tracker_parse.c` mirroring `test_tokens.c` structure: load `max-tracker-full.json` via `FIXTURES_DIR`, assert v-guard, exact day values (`days[139].pct == 100` style), plan label copy, and hostile cases as inline strings — wrong `v`, `weeks: 19`, 139/141-length days, `[101,0]`, `[0,3]`, `["x",0]`, `{"error":1}`, truncated JSON, `planLabel` with lowercase/`<`/23 chars (reject → `has_plan` false but rest accepted ONLY if label merely absent; malformed label rejects nothing else? No: display-safety rule — invalid label is dropped, parse still succeeds), null `codingStreakDays` → −1, null `avgPeakPct` → `has_avg` false. Every reject case asserts `*out` is byte-untouched (memcmp against seeded pattern).
- [ ] **Step 2:** Add run.sh block (copy the tokens block, swap `max_tracker_parse.c` / `test_max_tracker_parse.c` / `/tmp/torget-max-tracker-test`); run `./test/run.sh` → expect FAIL (missing files).
- [ ] **Step 3:** Implement `max_tracker_parse.c` with cJSON, all bounds above, single `tk_max_tracker tmp` filled then `*out = tmp` on success. Allowlist for label: `A–Z`, `0–9`, space, length 1–11.
- [ ] **Step 4:** `./test/run.sh` → parser suite PASS.
- [ ] **Step 5:** Commit `"Parse the Max Tracker contract defensively"`.

### Task 3: Presenter (colors, tiles, legend — pure C)

**Files:**
- Create: `components/app_tokens/max_tracker_presenter.h/.c`
- Create: `test/test_max_tracker_presenter.c`; Modify: `test/run.sh`

**Interfaces:**
- Produces (consumed by Task 8):

```c
typedef struct { uint8_t r, g, b; } tk_mt_rgb;
tk_mt_rgb tk_mt_cell_rgb(bool codex, int pct);      /* pct 0..100 */
tk_mt_rgb tk_mt_gray_rgb(int lvl);                  /* lvl 0..2 */
extern const int TK_MT_LEGEND_PCTS[5];              /* {10,40,70,92,100} */
typedef struct { char value[8]; char unit[6]; } tk_mt_tile;
void tk_mt_tiles(const tk_max_tracker *t, bool codex, tk_mt_tile out[4]);
/* out[0] STREAK d, out[1] MAX WEEKS, out[2] AVG PEAK %, out[3] MAX DAYS;
   missing basis renders "–" with empty unit */
```

- [ ] **Step 1:** Tests: exact stop interpolation (`tk_mt_cell_rgb(false,100) == {255,45,31}`, `(true,85) == {111,120,255}`, midpoint math at 45), gray levels exact, tiles: streak −1 → "–", avg has_avg false → "–", avg 62.0 → "62" unit "%", values clamp ≥ 0.
- [ ] **Step 2:** run.sh block; run → FAIL. **Step 3:** implement (linear interpolation between the Global Constraints stop tables, integer rounding `lround`). **Step 4:** run → PASS. **Step 5:** Commit `"Present Max Tracker colors and tiles"`.

### Task 4: tokenserver aggregation core (pure functions)

**Files:**
- Create: `tools/tokenserver/max_tracker.py`, `tools/tokenserver/test_max_tracker.py`
- Modify: `test/run.sh` (append module to the tokenserver unittest list)

**Interfaces:**
- Produces (consumed by Tasks 5, 6):

```python
def volume_levels(day_volumes: dict[str, int]) -> dict[str, int]  # terciles → 0..2 over nonzero
def coding_streak(active_dates: set[str], today: str) -> int      # consecutive incl. today-if-active
def week_key(date_str: str) -> str                                 # ISO "2026-W32"
def max_weeks_streak(week_maxed: dict[str, bool], this_week: str) -> int  # completed weeks only
def dense_window(today: str, weeks: int, per_day: dict[str, dict]) -> list[list[int]]
    # aligns to ISO Monday, returns weeks*7 [pct,lvl] pairs, absent day → [-1,-1]
def build_payload(state: dict, today: str, plans: dict[str, str | None]) -> dict  # the v1 contract
```

- [ ] **Step 1:** Tests first (`test_max_tracker.py`, unittest): terciles with ties and single-day input; streak across month boundary and DST switch (2026-03-29 Europe/Stockholm dates as plain strings — functions operate on date strings, no tz math inside); dense window alignment (today mid-week → trailing partial week padded with `[-1,-1]` after today? No: today is the LAST cell; the window starts `weeks*7-1` days before today aligned so column 19 row = today's ISO weekday; leading cells before window start are `[-1,-1]`); payload nulls (`codingStreakDays: None` when no activity data yet); plan allowlist mapping `{"pro":"PRO","max5x":"MAX 5X","max20x":"MAX 20X","plus":"PLUS"}`, invalid → omitted.
- [ ] **Step 2:** Run `python -m unittest tools.tokenserver.test_max_tracker -v` → FAIL. **Step 3:** implement pure functions (no IO in this task). **Step 4:** run → PASS. **Step 5:** Commit `"Aggregate Max Tracker days, streaks and windows"`.

### Task 5: Backfill + ongoing rollup + persistence

**Files:**
- Modify: `tools/tokenserver/max_tracker.py` (add `MaxTrackerStore` class), `tools/tokenserver/test_max_tracker.py`

**Interfaces:**
- Produces (consumed by Task 6):

```python
class MaxTrackerStore:
    def __init__(self, path: Path, codex_root: Path, claude_root: Path): ...
    def observe_quota(self, provider: str, window_minutes: float | None,
                      pct: float, ts: float) -> None   # live probe hook
    def observe_volume(self, provider: str, date_str: str, tokens: int) -> None
    def backfill_step(self, budget_bytes: int = 1_048_576) -> bool  # True = more work
    def snapshot(self, today: str, plans: dict) -> dict             # build_payload wrapper
    def save(self) -> None                                          # atomic 0600
```

- [ ] **Step 1:** Tests: Codex rollout backfill against tmpdir fixture files containing real-shape `event_msg`/`token_count` lines with `payload.rate_limits` (primary ≤600 min → day peak; secondary >600 min at 100 → week maxed); quoted/nested rate_limits rejected (reuse the acceptance rule from the existing fallback — import and call it, do not duplicate); per-step byte budget honored (file longer than budget drains over two `backfill_step` calls); backfill state keyed on inode+size so a second run is a no-op; persistence round-trip is atomic (write to `.tmp`, `os.replace`), mode 0600, retention 400 days pruned on save; **privacy allowlist test**: serialized JSON keys are exactly `{v, days:{d,pct,act,lvl}, weeks:{w,maxed}, backfill}` per provider — assert no other keys can appear (schema walker).
- [ ] **Step 2:** run → FAIL. **Step 3:** implement; stale cache values never call `observe_quota` (caller-side rule wired in Task 6). Claude historical `pct` stays absent — only `act`/`lvl` from volumes. **Step 4:** run → PASS. **Step 5:** Commit `"Backfill and persist Max Tracker history"`.

### Task 6: Endpoint, flags, wiring into tokenserver

**Files:**
- Modify: `tools/tokenserver/tokenserver.py` — argparse (`--claude-plan`, `--codex-plan`, `choices` per Task 4 map), Handler.do_GET route `"/api/max-tracker"` (`tokenserver.py:1276-1287`), root listing `endpoints` array, startup print; hook `MaxTrackerStore.observe_quota` where the live Claude probe and Codex reads publish fresh (non-stale, non-cached) percentages; hook `observe_volume` where daily volume rollup already exists; drive `backfill_step()` from the existing background thread loop (one step per 0.5 s tick until done).
- Modify: `tools/tokenserver/test_tokenserver.py` (route test), `tools/tokenserver/README.md` (English quickstart line + short Swedish section)

- [ ] **Step 1:** Route test first: GET `/api/max-tracker` returns v1 payload with `stale` mirroring the quota-cache stale rule; unknown plan flag rejected by argparse (`SystemExit`). Run → FAIL.
- [ ] **Step 2:** Implement wiring. **Step 3:** tests PASS + `python3 tools/tokenserver/tokenserver.py --port 8739` smoke + `curl http://127.0.0.1:8739/api/max-tracker` shows real backfilled Codex data (manual observation, record in commit body). Kill the smoke server.
- [ ] **Step 4:** Commit `"Serve /api/max-tracker with plan badges"`.

### Task 7: Remove the VOLUME view

**Files:**
- Modify: `components/app_tokens/app_tokens.h` (enum: delete `VIEW_VOLUME = 4`; add `VIEW_TRACKER_CLAUDE = 4, VIEW_TRACKER_CODEX = 5`), `components/app_tokens/usage_screen.h` (`TK_USAGE_SCREEN_VIEWS 5 → 6`), `components/app_tokens/usage_screen.c` (volume tile creation near line 361-391, `volume_*` ui fields lines 82-91, `usage_screen_set_volume`), `components/app_tokens/app.c:45-47,94-95` (delete set_volume calls + now-unused ticker fields if orphaned), `sim/main.c` (tour stages 11-12 → tracker dumps placeholder until Task 8; delete `dump_frame("vibepulse-volume")` at line 542 in static QA), `tools/preview-ui.sh:100` (drop `torget-vibepulse-volume.bmp`), `test/test_vibepulse_visual_landmarks.py:43` (drop from EXPECTED)

- [ ] **Step 1:** Delete in the order above; keep `dayTokens*` in the tokens contract/parser untouched (server + other consumers unaffected).
- [ ] **Step 2:** `cmake -S sim -B sim/build -G Ninja && ninja -C sim/build` → compiles; `./test/run.sh` → landmark suite still passes with 35-image matrix.
- [ ] **Step 3:** Commit `"Remove the VIBEPULSE volume view"`.

### Task 8: Tracker pages on screen

**Files:**
- Create: nothing (renderer lives in `usage_screen.c` like other tiles)
- Modify: `components/app_tokens/usage_screen.c` (two `create_tracker_page(int view, bool codex)` tiles: shared live header reuse, eyebrow "MAX TRACKER" plex_text_21, right-aligned muted plan badge plex_text_16 when `has_plan`, one plain `lv_obj_t` grid area with `LV_EVENT_DRAW_MAIN` callback drawing 140 rects + 5 legend swatches + "MAX" label, stat row via existing stat-label/value styles), `components/app_tokens/app_tokens.h` (`void tokens_apply_max_tracker(const tk_max_tracker *t);`), `components/app_tokens/app.c` (store + forward under `torget_ui_lock` pattern), `components/app_tokens/usage_screen.h` (`void usage_screen_apply_max_tracker(const tk_max_tracker *t);`), `sim/CMakeLists.txt` (add `max_tracker_parse.c`, `max_tracker_presenter.c`), `components/app_tokens/CMakeLists.txt` (same two SRCS), `sim/main.c` (M key: `SDL_SCANCODE_M` cycles the three fixtures through `tk_max_tracker_parse` + `tokens_apply_max_tracker`; tour: stage 11 `torget_app_show(1); usage_screen_show_view(VIEW_TRACKER_CLAUDE)` after feeding coldstart fixture → `dump_frame("vibepulse-tracker-claude")`, stage 12 codex full → `dump_frame("vibepulse-tracker-codex")`)

Draw callback core (LVGL 9.5, static, no anim):

```c
static void tracker_grid_draw(lv_event_t *e) {
  const tracker_page *pg = lv_event_get_user_data(e);
  lv_layer_t *layer = lv_event_get_layer(e);
  lv_area_t o; lv_obj_get_coords(lv_event_get_target(e), &o);
  for (int i = 0; i < TK_MT_DAYS; i++) {
    const tk_mt_day *d = &pg->data.days[i];
    lv_draw_rect_dsc_t dsc; lv_draw_rect_dsc_init(&dsc);
    dsc.radius = 3; dsc.bg_opa = LV_OPA_COVER;
    if (d->pct >= 0) { tk_mt_rgb c = tk_mt_cell_rgb(pg->codex, d->pct);
      dsc.bg_color = lv_color_make(c.r, c.g, c.b);
    } else if (d->lvl >= 0) { tk_mt_rgb c = tk_mt_gray_rgb(d->lvl);
      dsc.bg_color = lv_color_make(c.r, c.g, c.b);
      dsc.border_width = 1; dsc.border_opa = LV_OPA_COVER;
      dsc.border_color = lv_color_hex(0x3d434d);
    } else dsc.bg_color = lv_color_hex(pg->codex ? 0x0c0e13 : 0x0c0e11);
    int wx = i / 7, wy = i % 7;
    lv_area_t a = { o.x1 + wx * 21, o.y1 + wy * 21,
                    o.x1 + wx * 21 + 17, o.y1 + wy * 21 + 17 };
    lv_draw_rect(layer, &dsc, &a);
  }
  /* legend: 5 swatches 12px + "MAX" drawn as lv_label in create, not here */
}
```

- [ ] **Step 1:** Implement pages + apply chain; `usage_screen_apply_max_tracker` copies the struct and calls `lv_obj_invalidate` on both grid objects.
- [ ] **Step 2:** Build sim; run `./sim/build/torget-sim`, press `N` then `]` to the tracker pages, `M` through all three fixtures; verify NO DATA/coldstart/full by eye at 1:1.
- [ ] **Step 3:** `./test/run.sh` green; commit `"Render Max Tracker pages"`.

### Task 9: QA matrix + landmark tests

**Files:**
- Modify: `sim/main.c` (static QA: after existing states, feed coldstart fixture → `dump_frame("vibepulse-tracker-claude-coldstart")`, full → `"vibepulse-tracker-codex-full"`, empty → `"vibepulse-tracker-empty"`, `usage_screen_set_stale(true)` → `"vibepulse-tracker-stale"`), `tools/preview-ui.sh` (add the four names), `test/test_vibepulse_visual_landmarks.py` (EXPECTED + assertions: exact 480×480; cell (19,6) top-left pixel sampling `#FF2D1F` in full fixture at a known 100-day; a gray lvl-1 cell fill `#1d222a` with `#3d434d` border pixel; legend right edge x == 448; empty fixture has zero pixels equal to `#FF2D1F`; pager renders 6 dots)

- [ ] **Step 1:** Extend static QA + allowlist; run `PYTHON_BIN=… ./tools/preview-ui.sh vibepulse` → generates matrix; landmark test FAILS on missing assertions first, then implement assertions against real captures.
- [ ] **Step 2:** `./test/run.sh` fully green. **Step 3:** Inspect all four tracker captures at 1:1 (Read tool) before claiming pass. **Step 4:** Commit `"Prove Max Tracker rasters by landmark"`.

### Task 10: Target fetch + docs + full gates

**Files:**
- Modify: `components/app_tokens/net.c` (second task `max_tracker_task` gated on `#ifdef TK_MAX_TRACKER_URL`, 5-min cadence `#define MT_FETCH_EVERY_MS 300000`, BODY_MAX 8192 for this task only — fixture ≤ 6 KB guard from Task 1, phase-shifted `vTaskDelay(pdMS_TO_TICKS(15000))` after `torget_net_wait()`), `secrets.h.example` (add `TK_MAX_TRACKER_URL` line in the VibePulse block, commented like the others), `README.sv.md` (view list + M key), `README.md` (one line under What's on screen), `tools/tokenserver/README.md` (plan flags)

- [ ] **Step 1:** Implement fetch task (same parse-or-keep semantics as `net_task`; stale handled by screen tick).
- [ ] **Step 2:** Gates in order, all must pass: `./test/run.sh`; `PYTHON_BIN=… ./tools/preview-ui.sh vibepulse`; sim build + interactive spot-check; `. ~/esp/esp-idf/export.sh && idf.py build` (Buddy present) — record `torget.bin` size and app-partition headroom in the commit body.
- [ ] **Step 3:** Copy the final four tracker captures to `design/vibepulse/explorations/max-tracker/` for the record.
- [ ] **Step 4:** Commit `"Fetch Max Tracker on the target"`; push.

**Physical gate (explicitly NOT in this plan):** flash + static AMOLED review require separate user authorization per the AMOLED skill.
