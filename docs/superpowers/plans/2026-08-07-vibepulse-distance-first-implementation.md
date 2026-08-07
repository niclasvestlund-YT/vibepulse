# VibePulse Distance-First AMOLED Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace VibePulse's card-heavy rotating usage UI with stable 480 × 480 Claude/Codex hero views, a manual Claude detail view, a dual-provider overview, compact agent status, and AMOLED-safe motion.

**Architecture:** Keep `/api/tokens` backward compatible and make the host own trusted English quota labels. Extend the pure C presenter with explicit hero/detail/overview view models, then let `usage_screen.c` render fixed LVGL widgets from those models. Keep agent/completion state in the existing policy modules; add one small pure motion policy so pulse opacity and pixel drift are deterministic and host-tested.

**Tech Stack:** ESP-IDF, ESP32-S3, LVGL 9.5, C11 host tests, Python `unittest`, SDL2 simulator, Waveshare 480 × 480 AMOLED.

---

## File responsibility map

- `tools/tokenserver/tokenserver.py`: normalize Claude's trusted model-window label to English; preserve Codex `used_percent` semantics.
- `tools/tokenserver/test_tokenserver.py`: prove labels and Codex usage direction at the host boundary.
- `tools/tokenserver/README.md` and `sim-fixtures/tokens.json`: document and exercise the same English wire label.
- `components/app_tokens/usage_presenter.h`: define provider-independent hero, detail, and overview view models.
- `components/app_tokens/usage_presenter.c`: choose the correct quota and format English percent/reset/status copy without UI dependencies.
- `test/test_usage_presenter.c`: exercise every presenter state, including missing limits and no automatic time rotation.
- `components/app_tokens/usage_screen.c`: own all static 480 × 480 LVGL layout and bind presenter data to fixed widgets.
- `components/app_tokens/usage_screen.h`: define six VibePulse pages and the simulator's direct-view hook.
- `components/app_tokens/agent_monitor_policy.c`: return short English safe activity labels.
- `components/app_tokens/agent_monitor.h`: expose rail visibility without exposing LVGL widgets.
- `components/app_tokens/agent_monitor.c`: render the compact provider status rail and the completion overlay with real assets.
- `components/app_tokens/usage_motion_policy.h/.c`: calculate 6–8 fps pulse opacity and 1–2 px AMOLED drift without LVGL.
- `test/test_usage_motion_policy.c`: boundary-test motion timing and reduced-motion behavior.
- `components/app_tokens/agent_completion_policy.h/.c`: shorten the wash phase to 500 ms while preserving queue/dismiss behavior.
- `test/test_agent_monitor_policy.c` and `test/test_agent_completion_policy.c`: lock English copy, stale behavior, and completion timing.
- `components/app_tokens/CMakeLists.txt`, `sim/CMakeLists.txt`, `test/run.sh`: wire the new pure policy into target, simulator, and host tests.
- `sim/main.c`: dump every priority state as a deterministic 480 × 480 BMP.
- `docs/superpowers/reviews/2026-08-07-vibepulse-distance-first-amoled.md`: record the two required physical review gates.

The existing multi-job completion queue, audio adapter plan, token history, forecast math, network transport, and Solelkollen/Vibbe apps remain separate and are not reimplemented here.

### Task 1: Lock English provider labels and Codex usage direction

**Files:**
- Modify: `tools/tokenserver/test_tokenserver.py`
- Modify: `tools/tokenserver/tokenserver.py:315-355`
- Modify: `tools/tokenserver/README.md`
- Modify: `sim-fixtures/tokens.json`
- Modify: `test/test_tokens.c`

- [ ] **Step 1: Change the host tests to require the approved English labels**

Replace the named-model expectations with:

```python
def test_named_model_week_buckets_keep_their_real_english_label(self):
    cases = {
        "7d_fable": "FABLE · WEEK",
        "7d_opus": "OPUS · WEEK",
        "7d_sonnet": "SONNET · WEEK",
    }
    for bucket, expected in cases.items():
        with self.subTest(bucket=bucket):
            parsed = tokenserver._parse_limit_headers(
                self._headers(bucket), now_ts=1_800_000_000)
            self.assertEqual(parsed["modelLabel"], expected)
            self.assertEqual(parsed["modelPct"], 73.0)
```

Change every other expected `modelLabel`/`claudeModelWeekLabel` value in the
same Python test file from `FABLE · VECKA` to `FABLE · WEEK`. Change the wire
example in `tools/tokenserver/README.md`, the simulator fixture, and the parser
fixture/assertion in `test/test_tokens.c` to the same English value. The C
parser remains byte-preserving; only its expected fixture text changes.

Add this Codex assertion beside the rollout-log tests:

```python
def test_codex_window_value_preserves_used_percent(self):
    window = {
        "used_percent": 57.0,
        "window_minutes": 10080,
        "resets_at": 1_900_000_000,
    }
    pct, reset_min, window_min = tokenserver._codex_window(
        window, now_ts=1_899_996_400)
    self.assertEqual(pct, 57.0)
    self.assertEqual(reset_min, 60)
    self.assertEqual(window_min, 10080)
```

- [ ] **Step 2: Run the focused tests and verify the label test fails**

Run:

```bash
python3 -m unittest tools.tokenserver.test_tokenserver.ClaudeLimitHeaderTests tools.tokenserver.test_tokenserver.CodexLimitLogTests -v
```

Expected: the three English label cases fail with the current `· VECKA` values; the Codex `used_percent` test passes and proves no inversion is needed on ESP32.

- [ ] **Step 3: Change only the trusted Claude label mapping**

Use this mapping inside `_parse_limit_headers`:

```python
model_labels = {
    "fable": "FABLE · WEEK",
    "opus": "OPUS · WEEK",
    "sonnet": "SONNET · WEEK",
}
```

Do not alter `_codex_window`; it already reads Codex `used_percent`.

- [ ] **Step 4: Run all tokenserver tests**

Run:

```bash
python3 -m unittest discover -s tools/tokenserver -p 'test_*.py' -v
./test/run.sh
```

Expected: all tokenserver tests pass, including history and agent-status suites.

- [ ] **Step 5: Commit the host contract change**

```bash
git add tools/tokenserver/tokenserver.py tools/tokenserver/test_tokenserver.py tools/tokenserver/README.md sim-fixtures/tokens.json test/test_tokens.c
git commit -m "Använd engelska Claude-quotaetiketter"
```

### Task 2: Replace rotating cards with explicit presenter view models

**Files:**
- Modify: `components/app_tokens/usage_presenter.h`
- Modify: `components/app_tokens/usage_presenter.c`
- Modify: `test/test_usage_presenter.c`

- [ ] **Step 1: Write failing presenter tests for the four priority surfaces**

Replace the rotation assertions with checks equivalent to:

```c
usage_hero_view hero = {0};
usage_presenter_build_hero(&tokens, USAGE_PROVIDER_CLAUDE, &hero);
check("Claude hero uses real model week",
      strcmp(hero.quota.label, "FABLE · WEEK") == 0 &&
      strcmp(hero.quota.pct_text, "73%") == 0);
check("hero reset is English",
      strcmp(hero.quota.reset_text, "RESET IN 2D 4H") == 0);

usage_detail_page_view details = {0};
usage_presenter_build_claude_details(&tokens, &details);
check("details are stable and ordered",
      details.row_count == 2 &&
      strcmp(details.rows[0].label, "WEEKLY · ALL MODELS") == 0 &&
      strcmp(details.rows[1].label, "5-HOUR LIMIT") == 0);

usage_overview_page_view overview = {0};
usage_presenter_build_overview(&tokens, &overview);
check("overview contains both providers",
      overview.row_count == 2 &&
      overview.rows[0].provider == USAGE_PROVIDER_CLAUDE &&
      overview.rows[1].provider == USAGE_PROVIDER_CODEX);

tk_tokens codex = {0};
codex.codex_week = limit(57, 2210, 5);
usage_presenter_build_hero(&codex, USAGE_PROVIDER_CODEX, &hero);
check("Codex stays used, never double inverted",
      strcmp(hero.quota.label, "WEEKLY") == 0 &&
      strcmp(hero.quota.pct_text, "57%") == 0);
```

Also assert that calling any presenter function twice with different elapsed wall time is unnecessary: the new API has no `elapsed_ms` argument and therefore cannot auto-rotate.

- [ ] **Step 2: Run the presenter test and verify it fails to compile**

Run:

```bash
./test/run.sh
```

Expected: `test_usage_presenter.c` fails because the three new view types/functions do not exist.

- [ ] **Step 3: Define the public view models**

Add these definitions to `usage_presenter.h` and remove the old time-rotating provider API:

```c
typedef struct {
  usage_provider provider;
  char provider_label[12];
  usage_card_view quota;
} usage_hero_view;

typedef struct {
  int row_count;
  usage_card_view rows[2];
} usage_detail_page_view;

typedef struct {
  usage_provider provider;
  usage_card_view quota;
} usage_overview_row_view;

typedef struct {
  int row_count;
  usage_overview_row_view rows[2];
} usage_overview_page_view;

void usage_presenter_build_hero(const tk_tokens *tokens,
                                usage_provider provider,
                                usage_hero_view *out);
void usage_presenter_build_claude_details(
    const tk_tokens *tokens, usage_detail_page_view *out);
void usage_presenter_build_overview(
    const tk_tokens *tokens, usage_overview_page_view *out);
```

- [ ] **Step 4: Implement stable quota selection and English formatting**

Use these exact formatting rules in `usage_presenter.c`:

```c
static void format_reset(const tk_limit *limit, char *out, size_t cap) {
  if (!limit->has_reset) return;
  if (limit->reset_min >= 24 * 60) {
    snprintf(out, cap, "RESET IN %dD %dH", limit->reset_min / (24 * 60),
             (limit->reset_min / 60) % 24);
  } else if (limit->reset_min >= 60) {
    snprintf(out, cap, "RESET IN %dH %02dM", limit->reset_min / 60,
             limit->reset_min % 60);
  } else {
    snprintf(out, cap, "RESET IN %dM", limit->reset_min);
  }
}

static void build_hero_quota(const tk_tokens *tokens,
                             usage_provider provider,
                             usage_card_view *out) {
  if (provider == USAGE_PROVIDER_CODEX) {
    build_card(out, USAGE_CARD_ALL_WEEK, "WEEKLY", &tokens->codex_week);
    return;
  }
  if (tokens->has_claude_model_week_label &&
      tokens->claude_model_week_label[0] &&
      tokens->claude_model_week.has_pct) {
    build_card(out, USAGE_CARD_MODEL_WEEK,
               tokens->claude_model_week_label,
               &tokens->claude_model_week);
    return;
  }
  build_card(out, USAGE_CARD_ALL_WEEK, "WEEKLY · ALL MODELS",
             &tokens->claude_week);
}
```

`usage_presenter_build_claude_details` always emits all-model week first and 5-hour second. `usage_presenter_build_overview` calls the same hero selection for Claude and Codex so summary and hero semantics cannot diverge. Missing limits retain `–` plus `USAGE UNAVAILABLE`. Format valid deltas as `+N TODAY` for week rows and `+N LAST HOUR` for 5-hour rows.

- [ ] **Step 5: Run the host suite**

Run:

```bash
./test/run.sh
```

Expected: every C and Python wiring test passes; presenter output is English and stable.

- [ ] **Step 6: Commit the presenter refactor**

```bash
git add components/app_tokens/usage_presenter.h components/app_tokens/usage_presenter.c test/test_usage_presenter.c
git commit -m "Gör VibePulse-presentatören stabil och usage-first"
```

### Task 3: Build the two static distance-first hero pages

**Files:**
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/usage_screen.h`
- Create: `test/test_vibepulse_layout_wiring.py`
- Modify: `test/run.sh`

- [ ] **Step 1: Add a failing source-level layout contract**

Create `test/test_vibepulse_layout_wiring.py`:

```python
from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "components/app_tokens/usage_screen.c").read_text()

assert "extern const lv_font_t plex_num_146" in source
assert "#define HERO_BAR_H 18" in source
assert "create_hero_page" in source
assert '"WEEKLY · ALL MODELS"' not in source, "copy belongs in presenter"
assert "COL_CODEX       lv_color_hex(0x6F78FF)" in source
hero = source[source.index("static void create_hero_page"):]
hero = hero[:hero.index("static void create_forecast_page")]
assert "COL_CARD" not in hero, "priority usage pages must not use cards"

print("OK: VibePulse distance-first layout wiring")
```

Append `python3 test_vibepulse_layout_wiring.py` to `test/run.sh`.

- [ ] **Step 2: Run the wiring test and verify it fails**

Run:

```bash
python3 test/test_vibepulse_layout_wiring.py
```

Expected: failure because `usage_screen.c` still uses cards and `plex_num_118` for the hero.

- [ ] **Step 3: Replace the card layout with fixed hero widgets**

Use these dimensions at the top of `usage_screen.c`:

```c
#define SCREEN_W 480
#define SAFE_X 22
#define CONTENT_W 436
#define HEADER_Y 16
#define HEADER_H 48
#define HERO_LABEL_Y 86
#define HERO_PCT_Y 118
#define HERO_PCT_H 132
#define HERO_BAR_Y 276
#define HERO_BAR_H 18
#define HERO_RESET_Y 312
#define HERO_STATUS_Y 388
#define HERO_STATUS_H 66
```

Replace `usage_card_widgets` on Claude/Codex priority pages with:

```c
typedef struct {
  lv_obj_t *tile;
  lv_obj_t *content;
  lv_obj_t *provider;
  lv_obj_t *model;
  lv_obj_t *quota;
  lv_obj_t *pct;
  lv_obj_t *track;
  lv_obj_t *fill;
  lv_obj_t *reset;
} hero_widgets;
```

`create_hero_page` must create one content container, a compact VibePulse mark, provider text, optional right-aligned model metadata, a 24 px quota label, a `plex_num_146` percent label, an 18 px solid track/fill, and a 21–24 px reset label. Set background to `#000000`; do not create a panel object behind the percentage.

Build Claude at tile 0 and Codex at tile 1. Bind them with `usage_presenter_build_hero`. Clamp fill width to `0..CONTENT_W` and retain the last accepted quota during stale network state.

- [ ] **Step 4: Run host and simulator builds**

Run:

```bash
./test/run.sh
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
```

Expected: tests and simulator compile pass; no missing font or LVGL symbols.

- [ ] **Step 5: Commit the static hero pages**

```bash
git add components/app_tokens/usage_screen.c components/app_tokens/usage_screen.h test/test_vibepulse_layout_wiring.py test/run.sh
git commit -m "Bygg stora statiska VibePulse-huvudvyer"
```

### Task 4: Add manual detail and dual-provider overview pages

**Files:**
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/usage_screen.h`
- Modify: `components/app_tokens/app_tokens.h`
- Modify: `sim/main.c`
- Modify: `test/test_vibepulse_layout_wiring.py`

- [ ] **Step 1: Extend the failing layout contract to six deterministic pages**

Add assertions:

```python
header = (root / "components/app_tokens/usage_screen.h").read_text()
sim = (root / "sim/main.c").read_text()

assert "#define TK_USAGE_SCREEN_VIEWS 6" in header
assert "create_claude_details_page" in source
assert "create_overview_page" in source
for tag in (
    "vibepulse-claude-hero",
    "vibepulse-codex-hero",
    "vibepulse-claude-details",
    "vibepulse-overview",
):
    assert tag in sim
```

- [ ] **Step 2: Run the wiring test and verify it fails**

Run `python3 test/test_vibepulse_layout_wiring.py`.

Expected: failure on page count and missing page constructors/tags.

- [ ] **Step 3: Create two-row widgets without card surfaces**

Use one reusable row shape for details and overview:

```c
typedef struct {
  lv_obj_t *provider_icon;
  lv_obj_t *provider;
  lv_obj_t *label;
  lv_obj_t *pct;
  lv_obj_t *used;
  lv_obj_t *track;
  lv_obj_t *fill;
  lv_obj_t *reset;
  lv_obj_t *status_dot;
} summary_row_widgets;
```

Use `plex_num_118` for each row percentage, 21–24 px labels, 16 px tracks, and one hairline between rows. Claude details page consumes `usage_detail_page_view`; the overview consumes `usage_overview_page_view` and uses real `tk_img_claude_32` plus the layered Codex 32 px assets from `agent_assets.h`.

Set the page order and count exactly:

```c
enum {
  VIEW_CLAUDE_HERO = 0,
  VIEW_CODEX_HERO = 1,
  VIEW_CLAUDE_DETAILS = 2,
  VIEW_OVERVIEW = 3,
  VIEW_FORECAST = 4,
  VIEW_VOLUME = 5,
};
```

Forecast and volume retain their current data and may retain their existing internal cards during this delivery; they must not leak `COL_CARD` back into priority-page helpers. `usage_screen_show_view` clamps to `0..5`.

- [ ] **Step 4: Make simulator QA dump all priority pages**

Update `run_vibepulse_static_qa` to feed normal tokens once and dump:

```c
tokens_show_view(VIEW_CLAUDE_HERO);
dump_frame("vibepulse-claude-hero");
tokens_show_view(VIEW_CODEX_HERO);
dump_frame("vibepulse-codex-hero");
tokens_show_view(VIEW_CLAUDE_DETAILS);
dump_frame("vibepulse-claude-details");
tokens_show_view(VIEW_OVERVIEW);
dump_frame("vibepulse-overview");
```

Also dump missing/stale variants for both hero pages. Update the interactive window title/help so `[` and `]` move through VibePulse pages while `N` remains the KEY3/app switch equivalent.

- [ ] **Step 5: Run tests and generate exact 480 × 480 BMPs**

Run:

```bash
./test/run.sh
ninja -C sim/build
./sim/build/torget-sim --vibepulse-static-qa
```

Expected: zero failures and new `/tmp/torget-vibepulse-*.bmp` files at exactly 480 × 480.

- [ ] **Step 6: Commit details, overview, and simulator QA**

```bash
git add components/app_tokens/usage_screen.c components/app_tokens/usage_screen.h components/app_tokens/app_tokens.h sim/main.c test/test_vibepulse_layout_wiring.py
git commit -m "Lägg till VibePulse-detaljer och provideröversikt"
```

### Task 5: Pass the mandatory static physical AMOLED gate

**Files:**
- Create: `docs/superpowers/reviews/2026-08-07-vibepulse-distance-first-amoled.md`
- Modify only if evidence requires it: `components/app_tokens/usage_screen.c`

- [ ] **Step 1: Inspect simulator captures before flashing**

Open the four normal priority BMPs and their missing/stale variants. Record pass/fail for: provider identity, percent dominance, 21 px minimum critical copy, 16–18 px bar, no card behind percent, no clipping, real provider assets, and no automatic content swap.

- [ ] **Step 2: Build the actual ESP32-S3 target**

Run:

```bash
. ~/esp/esp-idf/export.sh
idf.py build
```

Expected: `Project build complete`; no internal-memory or missing-font link errors.

- [ ] **Step 3: Flash the known panel**

Run:

```bash
./tools/flasha.sh
```

Expected: esptool verifies every written hash and resets the device.

- [ ] **Step 4: Photograph and review all four static priority pages**

View Claude/Codex hero pages from 1–2 meters and detail/overview at arm's length. The review document must contain these headings and a concrete pass/fail sentence under each:

```markdown
# VibePulse distance-first physical AMOLED review

## Claude hero at 1–2 m
## Codex hero at 1–2 m
## Claude details at arm's length
## Dual-provider overview at arm's length
## Day and night brightness
## Required corrections before motion
```

- [ ] **Step 5: Apply only measured static corrections**

Change coordinates, font choice, or bar height only when the photograph identifies a concrete defect. Rebuild simulator and target after every correction. Do not add animation during this task.

- [ ] **Step 6: Commit the approved physical gate**

```bash
git add components/app_tokens/usage_screen.c docs/superpowers/reviews/2026-08-07-vibepulse-distance-first-amoled.md
git commit -m "Godkänn VibePulse statiskt på AMOLED"
```

### Task 6: Compact and translate the agent-status rail

**Files:**
- Modify: `components/app_tokens/agent_monitor_policy.c`
- Modify: `test/test_agent_monitor_policy.c`
- Modify: `components/app_tokens/agent_monitor.h`
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `test/test_vibepulse_layout_wiring.py`

- [ ] **Step 1: Change policy tests to the short approved English copy**

Require this mapping:

```c
check("editing is short English copy",
      strcmp(tk_agent_monitor_activity_text(&copy), "EDITING FILES") == 0);
copy.activity = TK_ACTIVITY_TESTING;
check("testing is short English copy",
      strcmp(tk_agent_monitor_activity_text(&copy), "RUNNING TESTS") == 0);
copy.activity = TK_ACTIVITY_WAITING_APPROVAL;
check("approval asks for the user",
      strcmp(tk_agent_monitor_activity_text(&copy), "NEEDS APPROVAL") == 0);
copy.state = TK_AGENT_DONE;
check("done is explicit", strcmp(tk_agent_monitor_activity_text(&copy),
                                  "DONE") == 0);
copy.state = TK_AGENT_ERROR;
check("error is explicit", strcmp(tk_agent_monitor_activity_text(&copy),
                                   "ERROR") == 0);
```

- [ ] **Step 2: Run the policy test and verify the Swedish-copy failures**

Run `./test/run.sh`.

Expected: agent-monitor policy assertions fail until the mapping changes.

- [ ] **Step 3: Implement the complete safe activity mapping**

Use:

```c
case TK_ACTIVITY_THINKING: return "THINKING";
case TK_ACTIVITY_READING: return "READING CODE";
case TK_ACTIVITY_EDITING: return "EDITING FILES";
case TK_ACTIVITY_SEARCHING: return "SEARCHING";
case TK_ACTIVITY_RUNNING: return "RUNNING COMMAND";
case TK_ACTIVITY_TESTING: return "RUNNING TESTS";
case TK_ACTIVITY_BUILDING: return "BUILDING";
case TK_ACTIVITY_WAITING_INPUT: return "NEEDS INPUT";
case TK_ACTIVITY_WAITING_APPROVAL: return "NEEDS APPROVAL";
```

Return `WORKING`, `DONE`, or `ERROR` for state fallbacks. Preserve the lease rule that stale `WORKING` becomes hidden/unknown, never `DONE`.

- [ ] **Step 4: Rebuild the rail as one compact line per visible provider**

In `agent_monitor.c`, set `COL_CODEX` to `0x6F78FF`. Keep the real 32 px assets. Remove project text from the normal rail, raise activity text to `plex_text_21`, and render provider/count plus safe activity in a 66 px area beginning at `HERO_STATUS_Y`. When two providers are visible, each half shows its icon, pulse dot, provider/count, and a clipped safe activity label; no full project list is drawn.

Expose `tk_agent_monitor_set_rail_enabled(bool enabled)` from
`agent_monitor.h`. A tileview value-change callback and
`usage_screen_show_view` call it with `true` only for the two hero pages. Hide
the normal rail on detail, overview, forecast, and volume pages; overview has
inline provider dots. Keep the completion overlay global and foregrounded.

`usage_screen_apply_agent` stores the last sanitized snapshot and its apply
time. `usage_screen_tick` derives each overview dot from
`tk_agent_monitor_effective_state`; stale `WORKING` hides the dot. Model and
effort metadata on hero pages are set only for an effective fresh
`TK_AGENT_WORKING` state and are cleared for idle, stale, waiting, error, and
done.

- [ ] **Step 5: Run host and simulator QA**

Run:

```bash
./test/run.sh
ninja -C sim/build
./sim/build/torget-sim --vibepulse-static-qa
./sim/build/torget-sim --vibepulse-completion-qa
```

Expected: all tests pass; hero screenshots show readable English activity without covering quota/reset.

- [ ] **Step 6: Commit the compact rail**

```bash
git add components/app_tokens/agent_monitor_policy.c test/test_agent_monitor_policy.c components/app_tokens/agent_monitor.h components/app_tokens/agent_monitor.c components/app_tokens/usage_screen.c test/test_vibepulse_layout_wiring.py
git commit -m "Gör VibePulse-agentstatus kompakt och engelsk"
```

### Task 7: Add deterministic AMOLED-safe motion and the short completion wash

**Files:**
- Create: `components/app_tokens/usage_motion_policy.h`
- Create: `components/app_tokens/usage_motion_policy.c`
- Create: `test/test_usage_motion_policy.c`
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/agent_completion_policy.h`
- Modify: `components/app_tokens/agent_completion_policy.c`
- Modify: `test/test_agent_completion_policy.c`
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/CMakeLists.txt`
- Modify: `sim/CMakeLists.txt`
- Modify: `test/run.sh`

- [ ] **Step 1: Write failing pure motion tests**

Create tests for the exact public contract:

```c
usage_motion_frame f0 = usage_motion_frame_at(0, false);
usage_motion_frame f1 = usage_motion_frame_at(125, false);
usage_motion_frame drift = usage_motion_frame_at(60000, false);
usage_motion_frame reduced = usage_motion_frame_at(60000, true);

check("pulse changes at 8 fps", f0.pulse_opa != f1.pulse_opa);
check("drift remains within two pixels",
      drift.dx >= 0 && drift.dx <= 2 && drift.dy >= 0 && drift.dy <= 2);
check("reduced motion is static",
      reduced.dx == 1 && reduced.dy == 1 &&
      reduced.pulse_opa == 255);
```

In completion tests, require `TK_COMPLETION_PULSE_MS == 500`, `PULSE` at 499 ms, and `STATIC` at 500 ms while the 10-second auto-return and FIFO queue remain unchanged.

- [ ] **Step 2: Run tests and verify missing-policy/timing failures**

Run `./test/run.sh`.

Expected: motion test cannot link and completion timing assertions fail against 2400 ms.

- [ ] **Step 3: Implement the pure motion policy**

Create this interface:

```c
typedef struct {
  int8_t dx;
  int8_t dy;
  uint8_t pulse_opa;
} usage_motion_frame;

usage_motion_frame usage_motion_frame_at(uint64_t now_ms,
                                          bool reduced_motion);
```

Use an eight-frame triangle opacity table sampled every 125 ms:

```c
static const uint8_t pulse[8] = {128, 154, 180, 206, 232, 206, 180, 154};
static const int8_t drift[4][2] = {{1, 1}, {2, 1}, {2, 2}, {1, 2}};
```

Select pulse with `(now_ms / 125) % 8` and drift with `(now_ms / 60000) % 4`. Reduced motion returns `{1, 1, 255}`.

- [ ] **Step 4: Apply motion only to reserved objects**

In `usage_screen_tick`, update only the hero content-container offset and active status-dot opacity when the corresponding frame changes. Keep a two-pixel inset so all drift positions remain inside 480 × 480. Do not resize, recreate, or relayout the percent label on a tick.

Use a compile-time reduced-motion switch without adding a runtime allocation:

```c
#ifndef TK_REDUCED_MOTION
#define TK_REDUCED_MOTION 0
#endif

usage_motion_frame frame = usage_motion_frame_at(
    (uint64_t)now_us / 1000ULL, TK_REDUCED_MOTION != 0);
```

- [ ] **Step 5: Shorten and render the completion wash**

Set:

```c
#define TK_COMPLETION_PULSE_MS 500ULL
```

Add a background wash object behind the completion content. During `TK_COMPLETION_PULSE`, set its provider color and triangular opacity peaking below full white-equivalent brightness; during `STATIC`, set wash opacity to zero and show real provider icon, `DONE`, project, and optional `N ACTIVE`. Preserve click dismissal, long-press launcher, queue order, and auto-return.

- [ ] **Step 6: Wire and run every local verification**

Add `usage_motion_policy.c` to target/simulator builds and compile `test_usage_motion_policy.c` from `test/run.sh`. Then run:

```bash
./test/run.sh
ninja -C sim/build
./sim/build/torget-sim --vibepulse-static-qa
./sim/build/torget-sim --vibepulse-completion-qa
```

Expected: all tests pass; static snapshots remain pixel-stable when captured at fixed timestamps; completion QA includes wash and `DONE` frames.

- [ ] **Step 7: Commit motion and completion polish**

```bash
git add components/app_tokens/usage_motion_policy.h components/app_tokens/usage_motion_policy.c test/test_usage_motion_policy.c components/app_tokens/usage_screen.c components/app_tokens/agent_completion_policy.h components/app_tokens/agent_completion_policy.c test/test_agent_completion_policy.c components/app_tokens/agent_monitor.c components/app_tokens/CMakeLists.txt sim/CMakeLists.txt test/run.sh
git commit -m "Lägg till lugn AMOLED-rörelse i VibePulse"
```

### Task 8: Final software and physical verification

**Files:**
- Modify: `docs/superpowers/reviews/2026-08-07-vibepulse-distance-first-amoled.md`
- Modify only when evidence proves a defect: files already named above.

- [ ] **Step 1: Run every software verification fresh**

```bash
python3 -m unittest discover -s tools/tokenserver -p 'test_*.py' -v
./test/run.sh
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
./sim/build/torget-sim --vibepulse-static-qa
./sim/build/torget-sim --vibepulse-completion-qa
. ~/esp/esp-idf/export.sh
idf.py build
```

Expected: zero failed tests, zero nonzero exits, and a completed ESP32-S3 firmware image.

- [ ] **Step 2: Inspect the final 480 × 480 evidence set**

Compare hero/detail/overview, stale/missing, one provider working, two providers working, `NEEDS APPROVAL`, `ERROR`, wash, Claude `DONE`, and Codex `DONE`. Reject any cropped text, wrong asset, card behind percent, Codex color mismatch, moving percent baseline, or Swedish usage/status copy.

- [ ] **Step 3: Flash and inspect device stability**

```bash
./tools/flasha.sh
```

After boot, observe serial memory telemetry through at least one token refresh and one completion event. Confirm no reboot, TLS regression, `NO_MEM`, frozen display flush, or stale working animation.

- [ ] **Step 4: Perform the final physical checklist**

Record results in the review document: Claude/Codex percent at 1–2 m; reset at arm's length; stable manual swipe; KEY3 app switch; long-press launcher; real icons; day/night brightness; 1–2 px drift without clipping; slow work pulse; 500 ms completion wash; touch dismissal; 10 s auto-return; simultaneous jobs; sound behavior from the existing completion implementation.

- [ ] **Step 5: Commit only evidence-backed final corrections**

```bash
git add components/app_tokens tools/tokenserver sim test docs/superpowers/reviews/2026-08-07-vibepulse-distance-first-amoled.md
git commit -m "Slutför VibePulse distance-first AMOLED-design"
```

- [ ] **Step 6: Confirm the branch is clean**

Run:

```bash
git status --short --branch
```

Expected: `## main` with no modified or untracked files.
