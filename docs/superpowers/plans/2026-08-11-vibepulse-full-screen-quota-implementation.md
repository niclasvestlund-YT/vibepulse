# VibePulse Full-Screen Quota Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the six-screen compact VibePulse dashboard with the approved five-screen, distance-readable weekly quota interface while preserving truthful missing/stale data and existing completion overlays.

**Architecture:** Keep quota formatting in the pure C presenter and rebuild only the shared LVGL screen tree. VibePulse Studio remains the geometry/palette authority, the SDL simulator remains the raster authority, and the ESP target continues using the same renderer. Motion is intentionally gated: the static interface must pass physical AMOLED review before swipe behavior is instrumented or changed.

**Tech Stack:** C11, LVGL 9.5, ESP-IDF 5.5.2, Python 3 tests, VibePulse Studio, SDL simulator, IBM Plex Sans generated LVGL fonts.

---

## File map

- `components/app_tokens/usage_presenter.h/.c`: pure formatting contract for the three quota pages and two Burn Rate outcomes.
- `components/app_tokens/usage_screen.h/.c`: five-page LVGL widget tree and data application.
- `design/vibepulse/studio-design.json`: reviewed 480 × 480 geometry and locked palette.
- `components/app_tokens/vibepulse_layout.generated.h`: deterministic Studio export used by target and simulator.
- `platform/fonts/fetch-and-convert.sh`, `platform/fonts/plex_num_164.c`, `platform/fonts/plex_headline_48.c`, `platform/fonts/plex_ui_16.c`: exact-size hero, outcome, and forecast-detail fonts.
- `sim/main.c`: deterministic normal, stale, missing, and Burn Rate snapshot matrix.
- `test/test_usage_presenter.c`: presenter behavior tests.
- `test/test_vibepulse_layout_wiring.py`: source-level architecture and five-page invariants.
- `test/test_vibepulse_visual_landmarks.py`: exact-size raster landmarks and provider colors.
- `docs/superpowers/reviews/2026-08-11-vibepulse-full-screen-static.md`: simulator/build evidence; physical fields remain pending until an authorized flash and real inspection.

### Task 1: Lock the five-page presenter contract

**Files:**
- Modify: `test/test_usage_presenter.c`
- Modify: `components/app_tokens/usage_presenter.h`
- Modify: `components/app_tokens/usage_presenter.c`

- [x] **Step 1: Write failing quota-page tests**

Add assertions for an explicit `usage_quota_scope` API:

```c
usage_quota_page_view page = {0};
usage_presenter_build_quota_page(&tokens, USAGE_QUOTA_CLAUDE_MODEL, &page);
check("Fable page uses model-week data",
      page.provider == USAGE_PROVIDER_CLAUDE &&
      strcmp(page.quota.label, "FABLE · WEEK") == 0 &&
      strcmp(page.quota.pct_text, "73%") == 0 &&
      strcmp(page.quota.reset_short_text, "2D 4H") == 0);
usage_presenter_build_quota_page(&tokens, USAGE_QUOTA_CLAUDE_ALL, &page);
check("Claude all-model page is independent",
      strcmp(page.quota.label, "WEEKLY · ALL MODELS") == 0 &&
      strcmp(page.quota.pct_text, "47%") == 0);
usage_presenter_build_quota_page(&tokens, USAGE_QUOTA_CODEX_WEEK, &page);
check("Codex page stays consumed usage",
      page.provider == USAGE_PROVIDER_CODEX &&
      strcmp(page.quota.pct_text, "57%") == 0);
```

- [x] **Step 2: Write failing Burn Rate outcome tests**

Assert the approved action language and every defensive state:

```c
check("slow pace asks for action",
      strcmp(forecast_page.rows[0].headline, "SPEED UP") == 0 &&
      strcmp(forecast_page.rows[0].detail,
             "1.4x CURRENT PACE TO MAX OUT") == 0);
check("early exhaustion leads with timing",
      strcmp(forecast_page.rows[1].headline, "9H EARLY") == 0 &&
      strcmp(forecast_page.rows[1].detail,
             "RUNS OUT SAT 05:00") == 0);
check("collecting is truthful",
      strcmp(forecast_page.rows[0].headline, "LEARNING PACE") == 0 &&
      strcmp(forecast_page.rows[0].detail,
             "FORECAST NOT READY") == 0);
check("contradictory late exhaustion is unavailable",
      strcmp(forecast_page.rows[1].headline, "UNAVAILABLE") == 0 &&
      strcmp(forecast_page.rows[1].detail,
             "NO RELIABLE FORECAST") == 0);
```

- [x] **Step 3: Run the presenter test and verify RED**

Run:

```bash
cc -std=c11 -Wall -Wextra -Icomponents/app_tokens \
  components/app_tokens/usage_presenter.c test/test_usage_presenter.c \
  -o /tmp/torget-usage-presenter-test && /tmp/torget-usage-presenter-test
```

Expected: compilation or assertion failure because the explicit quota scopes and approved Burn Rate outputs do not exist yet.

- [x] **Step 4: Implement the minimal presenter API**

Define:

```c
typedef enum {
  USAGE_QUOTA_CLAUDE_MODEL,
  USAGE_QUOTA_CLAUDE_ALL,
  USAGE_QUOTA_CODEX_WEEK,
} usage_quota_scope;

typedef struct {
  usage_provider provider;
  usage_card_view quota;
} usage_quota_page_view;

void usage_presenter_build_quota_page(const tk_tokens *tokens,
                                      usage_quota_scope scope,
                                      usage_quota_page_view *out);
```

Populate `reset_short_text` with only the value (`2D 4H`, `4H 09M`, or `9M`). Make Burn Rate lead with `SPEED UP`, `ON PACE`, a humanised `EARLY` duration, `LEARNING PACE`, or `UNAVAILABLE`; never repeat current percentages.

- [x] **Step 5: Run the presenter test and verify GREEN**

Run the command from Step 3. Expected: `OK: all usage presenter tests pass`.

### Task 2: Export the approved geometry and exact fonts

**Files:**
- Modify: `tools/vibepulse_studio/test_design.py`
- Modify: `tools/vibepulse_studio/design.py`
- Modify: `design/vibepulse/studio-design.json`
- Modify: `components/app_tokens/vibepulse_layout.generated.h`
- Modify: `platform/fonts/fetch-and-convert.sh`
- Create: `platform/fonts/plex_num_164.c`
- Create: `platform/fonts/plex_headline_56.c`
- Create: `platform/fonts/plex_ui_16.c`

- [x] **Step 1: Write failing Studio token assertions**

Assert the approved static geometry:

```python
self.assertEqual(design["hero"]["providerY"], 22)
self.assertEqual(design["hero"]["quotaY"], 72)
self.assertEqual(design["hero"]["percentY"], 104)
self.assertEqual(design["hero"]["percentFontPx"], 164)
self.assertEqual(design["hero"]["barY"], 304)
self.assertEqual(design["hero"]["barHeight"], 20)
self.assertEqual(design["hero"]["resetY"], 352)
```

- [x] **Step 2: Run Studio tests and verify RED**

Run: `python3 -m unittest tools.vibepulse_studio.test_design`

Expected: geometry assertions fail against the previous 146 px hero layout.

- [x] **Step 3: Save and export reviewed tokens**

Update the design JSON to the approved values, retain Claude `#D97757`, Codex `#6F78FF`, and black `#000000`, then run:

```bash
python3 tools/vibepulse_studio/design.py --write
python3 tools/vibepulse_studio/design.py --check
```

Expected: both commands exit 0 and the generated header contains `VP_PERCENT_FONT_PX 164`, `VP_BAR_Y 304`, and `VP_BAR_H 20`.

- [x] **Step 4: Add exact generated fonts**

Add these deterministic recipes to `fetch-and-convert.sh`:

```sh
conv Bold     164 "0x30-0x39,0x25,0x2E,0x2013" plex_num_164
conv Bold      48 "0x20,0x30-0x39,0x41-0x5A" plex_headline_48
```

Run: `platform/fonts/fetch-and-convert.sh`

Expected: both new `.c` files exist and expose `plex_num_164` and `plex_headline_48`.

- [x] **Step 5: Run Studio tests and verify GREEN**

Run: `python3 -m unittest tools.vibepulse_studio.test_design tools.vibepulse_studio.test_server`

Expected: all Studio tests pass.

### Task 3: Build the five static LVGL pages

**Files:**
- Modify: `test/test_vibepulse_layout_wiring.py`
- Modify: `components/app_tokens/usage_screen.h`
- Modify: `components/app_tokens/usage_screen.c`

- [x] **Step 1: Write failing structure tests**

Require exactly five views and reject the removed dashboard structures:

```python
assert "#define TK_USAGE_SCREEN_VIEWS 5" in header
for removed in ("create_claude_details_page", "create_overview_page",
                "create_card", "status_halo", "status_dot"):
    assert removed not in source
for required in ("create_quota_page", "create_burn_rate_page",
                 "create_volume_page", "plex_num_164",
                 "plex_headline_48"):
    assert required in source
assert source.count("new_tile(") >= 5
```

- [x] **Step 2: Run the structure test and verify RED**

Run: `python3 test/test_vibepulse_layout_wiring.py`

Expected: failure because the current renderer has six views, cards, duplicate summary pages, and the bottom status rail.

- [x] **Step 3: Implement shared quota anatomy**

Create three pages from one helper:

```c
static void create_quota_page(quota_page *page, int index,
                              usage_quota_scope scope,
                              usage_provider provider);
```

Each page contains only: provider icon/name; right-aligned model and effort; quota label; 164 px hero; 20 px provider-coloured bar; `USED TODAY`; `TO RESET`; five-position pager. Keep completion overlays in `agent_monitor.c` and do not create a persistent status rail.

- [x] **Step 4: Implement Burn Rate and Volume pages**

Build two unboxed Burn Rate rows with a single separator at y=251 and use the same header/pager helpers. Build Volume with the Claude identity, `USED TODAY`, the supplied MTOK value, one divider at y=304, sessions, and monthly MTOK. Missing values render `–`, never zero.

- [x] **Step 5: Apply live data without invention**

Apply all three quota scopes independently. Cache model/effort per provider so stale state keeps the model and replaces only the effort line with `STALE`; missing metadata may show `NO DATA`. Clamp progress width to 0–436 px and keep page selection manual.

- [x] **Step 6: Run structure and host tests**

Run:

```bash
python3 test/test_vibepulse_layout_wiring.py
./test/run.sh
```

Expected: wiring test and all host tests pass.

### Task 4: Expand deterministic exact-size visual QA

**Files:**
- Modify: `sim/main.c`
- Modify: `test/test_vibepulse_visual_landmarks.py`
- Modify: `test/test_preview_ui.py`

- [x] **Step 1: Write failing capture-matrix assertions**

Require captures for:

```python
EXPECTED = {
    "vibepulse-claude-fable",
    "vibepulse-claude-all",
    "vibepulse-codex-weekly",
    "vibepulse-burn-speed-up",
    "vibepulse-burn-on-pace",
    "vibepulse-burn-early",
    "vibepulse-burn-learning",
    "vibepulse-burn-unavailable",
    "vibepulse-volume",
    "vibepulse-claude-stale",
    "vibepulse-claude-missing",
    "vibepulse-codex-missing",
}
```

Assert every BMP is exactly `(480, 480)` and provider fills contain the locked RGB colors `(217, 119, 87)` or `(111, 120, 255)`.

- [x] **Step 2: Run visual tests and verify RED**

Run: `python3 -m unittest test.test_vibepulse_visual_landmarks`

Expected: missing captures/landmarks because the simulator still emits the old matrix.

- [x] **Step 3: Add deterministic simulator states**

Update `run_vibepulse_static_qa()` to feed one normal token fixture, then explicitly select and dump each of the five pages. Add dedicated forecast fixtures in code for speed-up, on-pace, early, collecting, unavailable, and contradictory positive-offset cases. Restore data between stale and missing captures so each state is independent.

- [x] **Step 4: Build simulator and export exact captures**

Run:

```bash
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
TORGET_CAPTURE_DIR=/tmp/vibepulse-full-screen-qa \
  ./sim/build/torget-sim --vibepulse-static-qa
```

Expected: exit 0 and the full 480 × 480 BMP matrix appears under `/tmp/vibepulse-full-screen-qa`.

- [x] **Step 5: Inspect all captures at 1:1 and verify GREEN**

Run:

```bash
python3 -m unittest test.test_vibepulse_visual_landmarks
tools/preview-ui.sh vibepulse
```

Expected: no clipping in `WEEKLY · ALL MODELS`, model/effort, forecast explanations, or five-position pagers; automated visual tests pass.

### Task 5: Verify the static target batch and record the open physical gate

**Files:**
- Create: `docs/superpowers/reviews/2026-08-11-vibepulse-full-screen-static.md`

- [x] **Step 1: Run the full host suite**

Run: `./test/run.sh`

Expected: exit 0 with no failed C or Python tests.

- [x] **Step 2: Validate Studio and preview wiring**

Run:

```bash
python3 tools/vibepulse_studio/design.py --check
python3 test/test_preview_ui.py
git diff --check
```

Expected: all commands exit 0.

- [x] **Step 3: Build the ESP-IDF target once**

Run:

```bash
. "$HOME/esp/esp-idf/export.sh" >/dev/null 2>&1
idf.py build
```

Expected: exit 0; record application size plus remaining partition and internal-memory headroom.

- [x] **Step 4: Record evidence without fabricating physical approval**

Create the review with simulator filenames, exact dimensions, commands, test counts, build size, and a physical table explicitly marked `PENDING FLASH / PENDING PHOTO`. Do not claim panel readability or swipe improvement.

- [ ] **Step 5: Stop at the flash gate**

Show the exact 480 × 480 captures to the user and request explicit authorization for this build. Do not run `tools/flasha.sh` from prior or implicit approval.

### Task 6: Measure touchscreen navigation after static physical approval

**Files:**
- Modify after approval: `components/app_tokens/usage_screen.c`
- Modify after approval: `components/app_solelkollen/app.c`
- Modify after approval: `main/main.c`
- Test after approval: physical `torget-home-01`

- [ ] **Step 1: Confirm the static AMOLED gate**

Record arm's-length and 1–2 metre observations/photos for the flashed five-page batch. Stop if the static layout needs correction.

- [ ] **Step 2: Instrument before tuning**

Add timestamped `LV_EVENT_SCROLL_BEGIN`, `LV_EVENT_SCROLL_END`, display flush count, elapsed flush time, free internal heap, and largest internal block logging. Preserve `DISPLAY_FLUSH_ROWS 12` for the baseline.

- [ ] **Step 3: Capture a repeatable baseline**

Perform 20 horizontal swipes in each app while quota and Solelkollen HTTPS polling are active. Record gesture-to-first-redraw, completion time, flush count, missed swipes, and any SPI/TLS errors.

- [ ] **Step 4: Test one navigation variable at a time**

First compare the baseline with elastic and momentum disabled while keeping buffer height, bus speed, page tree, and refresh period unchanged. Only after evidence shows the remaining bottleneck may a short deterministic snap be tested. Do not add opacity layers, canvas buffers, or increase flush rows without an explicit measured memory budget and HTTPS stress test.

- [ ] **Step 5: Accept only a stable improvement**

Target first visual reaction within 50 ms and completed navigation within 180–220 ms, with no missed gestures or display stalls across the 20-swipe HTTPS stress run. Keep the baseline behavior if the experiment does not improve both responsiveness and stability.

### Task 7: Preserve the workflow lessons for Studio and both agents

**Files:**
- Modify: `test/test_shared_amoled_skill.py`
- Modify: `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`

- [x] **Step 1: Write a failing shared-skill contract**

Require the shared skill to distinguish static and motion approval and to name the measurable display-pipeline evidence: gesture timing, flush count/time, largest internal block, and network/TLS stress.

- [x] **Step 2: Run the contract and verify RED**

Run:

```bash
python3 -m unittest \
  test.test_shared_amoled_skill.SharedAmoledSkillTests.test_canonical_skill_has_bounded_trigger_and_interface
```

Observed: eight missing workflow phrases failed against the previous skill.

- [x] **Step 3: Add the minimal reusable interaction-performance workflow**

Document that cross-app lag should be investigated at the shared display pipeline, that static approval does not imply motion approval, and that buffer height must never be increased without measured memory and TLS evidence.

- [x] **Step 4: Run the contract and verify GREEN**

Run the command from Step 2. Observed: one test passed; the skill remains below its 500-word limit at 476 words. Because the skill lives in the repository and the existing installer symlinks it into Codex, this single source is available to both Claude and Codex in new sessions.
