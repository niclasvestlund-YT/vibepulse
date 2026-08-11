# VibePulse Live Quota Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the approved distance-readable Claude and Codex quota pages with truthful today segmentation, adaptive live-agent context, bounded AMOLED-safe motion, and full-screen provider-specific attention states.

**Architecture:** Keep all data interpretation in host-testable C policy/presenter functions and keep LVGL limited to rendering authoritative view state. VibePulse Studio owns reviewed geometry and locked brand colors; the shared SDL/LVGL renderer owns pixel truth; the ESP target consumes the same sources. The implementation does not add networking, automatic carousels, canvases, or a persistent bottom working rail.

**Tech Stack:** C11, LVGL 9.5, ESP-IDF 5.5.2, Python 3.11+, Pillow, VibePulse Studio JSON/header export, SDL simulator, IBM Plex Sans LVGL fonts.

---

## Scope boundary

This plan implements the UI contract in
`docs/superpowers/specs/2026-08-11-vibepulse-live-quota-polish-design.md`.
It deliberately does not implement OTA. The current target has a factory-only
partition table and no OTA component, so installing this build on the current
device still requires USB. Safe A/B OTA is a separate subsystem and must follow
`docs/superpowers/plans/2026-08-10-safe-ota-foundation-implementation.md`; its
bootloader, partition table, and first application image require one final USB
bootstrap before later VibePulse releases can be delivered wirelessly.

## Physical execution gate

The shared AMOLED workflow is stricter than the logical task grouping below.
Tasks 3 and 5 therefore land their complete **static** object trees first:
header context, segmented bar, a visible-but-static working halo, and static
attention states. Exact LVGL captures and one target build are reviewed before
an authorized USB install. Halo breathing, attention pulses, and the usage tick
in Task 6 remain disabled until that static build has been inspected on the
physical AMOLED. This changes sequencing only; it removes no approved behavior.

## File map

- `design/vibepulse/studio-design.json`: reviewed 480 × 480 quota geometry and locked palette.
- `tools/vibepulse_studio/design.py`: validates geometry using rendered LVGL line height rather than nominal font size.
- `tools/vibepulse_studio/test_design.py`: Studio geometry and invalid-overlap regression tests.
- `components/app_tokens/vibepulse_layout.generated.h`: deterministic Studio export shared by target and simulator.
- `components/app_tokens/usage_live_policy.h/.c`: pure adaptive-header, today-bar, and usage-motion decisions.
- `test/test_usage_live_policy.c`: zero/one/many jobs, bar geometry, stale/missing data, and animation decision tests.
- `components/app_tokens/usage_screen.c`: quota-page LVGL tree, provider halo, segmented bar, and bounded animations.
- `components/app_tokens/agent_completion_policy.h/.c`: bounded, deduplicated attention-event queue for waiting, error, and completion states.
- `test/test_agent_completion_policy.c`: attention priority, freshness, queueing, and one-event dismissal tests.
- `components/app_tokens/agent_monitor.c`: full-screen Claude/Codex attention renderer and entry pulse.
- `components/app_tokens/agent_assets.h/.c`: existing real provider images; no new approximate icon.
- `platform/fonts/fetch-and-convert.sh`: deterministic 52 px attention-title font recipe.
- `platform/fonts/plex_attention_52.c`: generated AMOLED title font.
- `components/app_tokens/CMakeLists.txt`: includes the new pure policy source.
- `sim/CMakeLists.txt`: compiles the exact same policy and renderer.
- `sim/main.c`: deterministic steady, live, usage-tick, needs-you, error, stale, and missing capture states.
- `test/test_vibepulse_layout_wiring.py`: source-level layout and AMOLED animation constraints.
- `test/test_vibepulse_visual_landmarks.py`: exact 480 × 480 color, bounds, icon, bar, and overlay landmarks.
- `test/test_preview_ui.py`: exact capture matrix for the preview tool.
- `docs/superpowers/reviews/2026-08-11-vibepulse-live-quota-polish.md`: simulator, build, and physical-panel evidence.

### Task 1: Correct Studio's rendered-glyph geometry contract

**Files:**
- Modify: `tools/vibepulse_studio/test_design.py`
- Modify: `tools/vibepulse_studio/design.py`
- Modify: `design/vibepulse/studio-design.json`
- Regenerate: `components/app_tokens/vibepulse_layout.generated.h`

- [ ] **Step 1: Write failing geometry tests**

Add explicit assertions for the approved values:

```python
self.assertEqual(design["hero"]["percentY"], 150)
self.assertEqual(design["hero"]["barY"], 304)
self.assertEqual(design["hero"]["barHeight"], 24)
```

Add a validator regression proving the 164 px font's reviewed LVGL line height,
not its nominal conversion size, is used for vertical collision checks:

```python
def test_percent_uses_rendered_line_height(self):
    design = copy.deepcopy(self.design)
    design["hero"]["percentY"] = 150
    design["hero"]["barY"] = 304
    self.assertIs(validate_design(design, self.display), design)

def test_rendered_percent_still_needs_optical_gap(self):
    design = copy.deepcopy(self.design)
    design["hero"]["barY"] = 276
    with self.assertRaisesRegex(DesignError, "percentage.*progress bar"):
        validate_design(design, self.display)
```

- [ ] **Step 2: Run the focused Studio test and verify RED**

Run:

```bash
.venv/bin/python -m unittest tools.vibepulse_studio.test_design -v
```

Expected: the approved `percentY` and 24 px bar assertions fail, and the
current nominal-164 calculation rejects the approved layout.

- [ ] **Step 3: Implement the minimal rendered-height rule**

Keep `percentFontPx` as the asset identity but validate spacing with an explicit
reviewed raster metric:

```python
PERCENT_RENDERED_LINE_HEIGHT_PX = 119

if (hero["percentY"] + PERCENT_RENDERED_LINE_HEIGHT_PX
        + MIN_SECTION_GAP_PX > hero["barY"]):
    raise DesignError("percentage must not overlap the progress bar")
```

Set `percentY` to `150` and `barHeight` to `24` in the design JSON. Do not
change `safeX`, `contentWidth`, `quotaY`, provider colors, or screen dimensions.

- [ ] **Step 4: Regenerate and verify the shared header**

Run:

```bash
.venv/bin/python tools/vibepulse_studio/design.py --write
.venv/bin/python tools/vibepulse_studio/design.py --check
```

Expected: both exit 0 and the generated header contains:

```c
#define VP_PERCENT_Y 150
#define VP_BAR_Y 304
#define VP_BAR_H 24
```

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
.venv/bin/python -m unittest tools.vibepulse_studio.test_design \
  tools.vibepulse_studio.test_server -v
git add tools/vibepulse_studio/test_design.py \
  tools/vibepulse_studio/design.py \
  design/vibepulse/studio-design.json \
  components/app_tokens/vibepulse_layout.generated.h
git commit -m "Correct VibePulse rendered hero geometry"
```

Expected: tests pass. Do not stage `design/vibepulse/exports/claude-hero.png`.

### Task 2: Add a pure live quota policy

**Files:**
- Create: `components/app_tokens/usage_live_policy.h`
- Create: `components/app_tokens/usage_live_policy.c`
- Create: `test/test_usage_live_policy.c`
- Modify: `test/run.sh`
- Modify: `components/app_tokens/CMakeLists.txt`
- Modify: `sim/CMakeLists.txt`

- [ ] **Step 1: Write the failing public contract test**

Define test cases for 0, 1, 2, and 4 fresh working jobs, partial metadata,
stale jobs, missing quota, and both providers. The test must call this API:

```c
typedef struct {
  char context[64];
  bool halo_active;
} usage_live_header_view;

typedef struct {
  bool has_total;
  bool has_today;
  int total_px;
  int baseline_px;
  int today_px;
  int marker_x;
} usage_today_bar_view;

typedef enum {
  USAGE_UPDATE_DIRECT,
  USAGE_UPDATE_ANIMATE_FORWARD,
  USAGE_UPDATE_SNAP_BACKWARD,
  USAGE_UPDATE_SILENT,
} usage_update_mode;
```

Required assertions include:

```c
check("one job shows honest model and effort",
      strcmp(header.context, "NOW · OPUS 5 · ULTRA") == 0 &&
      header.halo_active);
check("several jobs do not claim one model",
      strcmp(header.context, "4 CHATS ACTIVE") == 0);
check("idle provider is explicit",
      strcmp(header.context, "NO ACTIVE CHAT") == 0 &&
      !header.halo_active);
check("73 total and 12 today split correctly",
      bar.total_px == 318 && bar.baseline_px == 266 &&
      bar.today_px == 52 && bar.marker_x == 266);
```

Also assert total percentages `0, 9, 73, 99, 100`, daily deltas `0, 1, 12`,
missing delta, delta greater than total, initial load, positive update, reset,
stale update, hidden page, and mid-animation coalescing.

- [ ] **Step 2: Wire the test and verify RED**

Add to `test/run.sh`:

```sh
cc -std=c11 -Wall -Wextra -Werror -O1 \
  ../components/app_tokens/usage_live_policy.c \
  ../components/app_tokens/agent_monitor_policy.c \
  test_usage_live_policy.c \
  -lm -o /tmp/torget-usage-live-policy-test
/tmp/torget-usage-live-policy-test
```

Run `./test/run.sh` and expect compilation failure because the new module does
not exist.

- [ ] **Step 3: Implement adaptive-header policy**

Use each job's effective state and packet age; do not trust `active_count` by
itself and do not select a model for multiple jobs:

```c
void usage_live_build_header(const tk_agent_provider_status *provider,
                             uint64_t packet_age_ms, bool data_stale,
                             usage_live_header_view *out);
```

Count jobs whose effective state is `WORKING`, `WAITING`, or `ERROR`. Enable
the halo only when at least one effective state is `WORKING` and neither
provider data nor quota data is stale. For exactly one working job, emit
`NOW · MODEL · EFFORT`, omitting only unavailable fields. For 2+ active jobs,
emit `N CHATS ACTIVE`. For zero emit `NO ACTIVE CHAT`. Leave context empty for
unavailable agent data rather than inventing activity.

- [ ] **Step 4: Implement today-bar and animation decisions**

Use width 436 and round once at the final pixel conversion:

```c
bool usage_live_build_today_bar(double total_pct, bool has_total,
                                double today_pct, bool has_today,
                                int track_width,
                                usage_today_bar_view *out);

usage_update_mode usage_live_update_mode(bool initialized, bool stale,
                                         bool visible, double old_pct,
                                         double new_pct);
```

Reject non-finite values, total outside `0..100`, daily delta outside
`0..total`, or non-positive track width. A missing daily delta produces one
provider-colored total fill and no marker. Initial load is direct, increases on
the visible fresh page animate, decreases snap, and hidden/stale updates are
silent.

- [ ] **Step 5: Run focused tests and commit**

Run:

```bash
./test/run.sh
git add components/app_tokens/usage_live_policy.h \
  components/app_tokens/usage_live_policy.c \
  components/app_tokens/CMakeLists.txt sim/CMakeLists.txt \
  test/test_usage_live_policy.c test/run.sh
git commit -m "Add truthful VibePulse live quota policy"
```

Expected: all host tests pass.

### Task 3: Render the polished steady Claude and Codex pages

**Files:**
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `test/test_vibepulse_layout_wiring.py`
- Modify: `test/test_vibepulse_visual_landmarks.py`
- Modify: `sim/main.c`

- [ ] **Step 1: Write failing source and raster assertions**

Require one right-aligned header-context label, two bar fills, one marker, and
one bounded halo per quota page. Reject the old stacked model/effort header:

```python
for required in ("context", "baseline_fill", "today_fill", "marker", "halo"):
    self.assertIn(required, self.source)
self.assertNotIn("lv_obj_t *effort;", self.source)
self.assertIn("USAGE_HERO_NUDGE_MS 480", self.source)
self.assertIn("USAGE_BAR_GROW_MS 520", self.source)
self.assertIn("USAGE_TODAY_PULSE_MS 620", self.source)
```

Update the raster test to assert Claude rows contain both `(138, 79, 66)` and
`(217, 119, 87)`, Codex rows contain both `(69, 75, 138)` and
`(111, 120, 255)`, the white marker is three pixels wide, and the track ends at
x 457.

- [ ] **Step 2: Run focused checks and verify RED**

Run:

```bash
.venv/bin/python test/test_vibepulse_layout_wiring.py
.venv/bin/python test/test_vibepulse_visual_landmarks.py
```

Expected: the source assertions fail before the renderer is changed.

- [ ] **Step 3: Replace stacked metadata with adaptive context**

Change `quota_page` to own `context` and halo state. Keep the exact existing
32 px provider assets. The header geometry is:

```c
page->context = label(tile, &plex_ui_14, COL_META,
                      242, VP_PROVIDER_Y + 5, 216, 20);
lv_obj_set_style_text_align(page->context, LV_TEXT_ALIGN_RIGHT, 0);
```

Apply `usage_live_build_header` for each provider from
`usage_screen_apply_agent`. On stale or unavailable data, preserve the existing
`STALE`/`NO DATA` truth contract and never display a stale model as current.

- [ ] **Step 4: Build the segmented 24 px progress bar**

Inside the clipped track, create a muted baseline fill and exact-accent today
fill. Create the three-pixel white marker as a sibling of the track so it may
extend four pixels above and below without being clipped. Use:

```c
#define COL_CLAUDE_BASE lv_color_hex(0x8A4F42)
#define COL_CODEX_BASE  lv_color_hex(0x454B8A)
```

For missing daily delta, hide marker and today fill, and recolor the baseline
fill with the provider's exact accent. For invalid contradictory data, show the
daily dash and omit the marker rather than clamping it into a plausible split.

- [ ] **Step 5: Add the bounded static working halo object**

Create a transparent 40 × 40 ring centered on the 32 px provider icon. Show its
reviewed static midpoint for fresh visible-provider working state and hide it
for idle, unknown, stale, waiting, done, and error. Do not start an `lv_anim`
yet. The 1350 ms border-opacity/width breathing is enabled in Task 6 only after
the physical static gate. The halo remains its own bounded object; the tile,
tileview, and root are never motion targets.

- [ ] **Step 6: Preserve full-screen information hierarchy**

Place the hero label at `VP_PERCENT_Y` with its existing 164 px font; retain
the two bottom stats and y=456 pager. Do not reintroduce a logo, five-hour
quota, card background, status rail, or auto-rotation.

- [ ] **Step 7: Add deterministic steady-state fixtures and verify GREEN**

Extend the static QA matrix with longest single-job context, two-chat context,
idle, stale, and missing cases for both providers. Run:

```bash
cmake -S sim -B sim/build -G Ninja
cmake --build sim/build
.venv/bin/python test/test_vibepulse_layout_wiring.py
.venv/bin/python test/test_vibepulse_visual_landmarks.py
```

Expected: all captures are 480 × 480, all text is within x `22..458`, the hero
does not touch quota or bar, and both providers use their exact icons/colors.

- [ ] **Step 8: Commit the steady renderer**

Run:

```bash
git add components/app_tokens/usage_screen.c sim/main.c \
  test/test_vibepulse_layout_wiring.py \
  test/test_vibepulse_visual_landmarks.py
git commit -m "Polish VibePulse Claude and Codex quota pages"
```

Do not stage the user-owned exported PNG.

### Task 4: Generalize the bounded event queue for attention states

**Files:**
- Modify: `components/app_tokens/agent_completion_policy.h`
- Modify: `components/app_tokens/agent_completion_policy.c`
- Modify: `test/test_agent_completion_policy.c`

- [ ] **Step 1: Write failing attention-event tests**

Extend `tk_completion_event` with the authoritative state and waiting count:

```c
tk_agent_state state;
uint8_t same_state_count;
```

Add tests that require `WAITING` ahead of `ERROR`, `ERROR` ahead of `DONE`,
fresh working jobs never enter the queue, stale first-snapshot events never
enter, two waiting events are queued independently, and one dismissal reveals
the next without clearing it.

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
cc -std=c11 -Wall -Wextra -Werror -O1 \
  components/app_tokens/agent_completion_policy.c \
  test/test_agent_completion_policy.c \
  -o /tmp/torget-agent-completion-policy-test && \
  /tmp/torget-agent-completion-policy-test
```

Expected: compilation/assertion failure because events do not retain state and
the existing policy only queues `DONE`.

- [ ] **Step 3: Implement truthful attention admission and priority**

Accept only jobs with a non-empty event ID and state `WAITING`, `ERROR`, or
`DONE`. Keep the existing bounded queue and seen-ID ring. Sort new candidates by
state priority, then freshness, then provider. Compute `same_state_count` from
the accepted snapshot so the view may say `2 CHATS WAITING` without exposing
prompts, commands, or filenames.

Do not auto-dismiss waiting/error after ten seconds. Keep the entry phase for
three 1.6-second pulses and then return static until local dismissal or a fresh
snapshot invalidates the event. A `DONE` completion may retain its current
bounded visible lifetime if product behavior still requires auto-return.

- [ ] **Step 4: Run tests and commit**

Run:

```bash
./test/run.sh
git add components/app_tokens/agent_completion_policy.h \
  components/app_tokens/agent_completion_policy.c \
  test/test_agent_completion_policy.c
git commit -m "Queue VibePulse attention events truthfully"
```

Expected: the full host suite passes.

### Task 5: Render full-screen Claude and Codex attention states

**Files:**
- Modify: `platform/fonts/fetch-and-convert.sh`
- Create: `platform/fonts/plex_attention_52.c`
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `sim/main.c`
- Modify: `test/test_vibepulse_layout_wiring.py`
- Modify: `test/test_vibepulse_visual_landmarks.py`
- Modify: `test/test_preview_ui.py`

- [ ] **Step 1: Add failing overlay capture assertions**

Require exact captures for:

```python
"torget-vibepulse-claude-needs-you.bmp"
"torget-vibepulse-codex-needs-you.bmp"
"torget-vibepulse-claude-error.bmp"
"torget-vibepulse-two-waiting-queued.bmp"
```

Assert a six-pixel provider border at the reviewed inset, real-icon-colored
pixels in the centered 112 px icon region, and white title glyph bounds around
y 246. Explicitly reject the previous `DONE`-only anatomy as the waiting view.

- [ ] **Step 2: Run visual tests and verify RED**

Run:

```bash
.venv/bin/python test/test_preview_ui.py
.venv/bin/python test/test_vibepulse_visual_landmarks.py
```

Expected: missing capture names and missing border/title landmarks fail.

- [ ] **Step 3: Generate the 52 px attention font**

Add this deterministic recipe:

```sh
conv Bold 52 "0x20,0x41-0x5A" plex_attention_52
```

Run `platform/fonts/fetch-and-convert.sh` and verify the generated symbol is
`plex_attention_52`.

- [ ] **Step 4: Rebuild the attention object tree**

Use a black 480 × 480 root with a six-pixel radius-36 outline inset eight.
Place provider label y=31, 112 px real icon centered y=145, title y=246,
project y=321, controlled detail y=365, and `TAP TO DISMISS` y=430. Reuse
`tk_img_claude` and all three real large Codex layers; scale them to 112 px with
one shared group and retain white Codex foreground layers.

Map authoritative state to copy:

```c
WAITING -> "NEEDS YOU" / "<PROVIDER> IS WAITING"
ERROR   -> "ERROR"     / "<PROVIDER> NEEDS ATTENTION"
DONE    -> "DONE"      / "<PROVIDER> FINISHED"
```

When `same_state_count > 1`, the detail is `N CHATS WAITING` or
`N CHATS NEED ATTENTION`. Project is always the current queue event's real,
bounded project field.

- [ ] **Step 5: Preserve interaction and defer entry pulses to the motion gate**

Render the provider icon ring at its reviewed static midpoint. A normal tap
dismisses only the current local event; long press opens Torget and suppresses
the following click. Neither gesture writes to the computer or approves an
agent action. The three 1600 ms entry pulses are enabled in Task 6 only after
the physical static gate; after 4800 ms they must settle to this same static
state.

- [ ] **Step 6: Capture both providers and verify GREEN**

Run:

```bash
cmake --build sim/build
TORGET_CAPTURE_DIR="$(mktemp -d)" \
  sim/build/torget-sim --vibepulse-completion-qa
.venv/bin/python test/test_preview_ui.py
.venv/bin/python test/test_vibepulse_visual_landmarks.py
```

Expected: all overlay captures are true 480 × 480 and the exact Claude/Codex
assets, colors, copy, and safe bounds pass.

- [ ] **Step 7: Commit the attention renderer**

Run:

```bash
git add platform/fonts/fetch-and-convert.sh \
  platform/fonts/plex_attention_52.c \
  components/app_tokens/agent_monitor.c sim/main.c \
  test/test_vibepulse_layout_wiring.py \
  test/test_vibepulse_visual_landmarks.py test/test_preview_ui.py
git commit -m "Add VibePulse needs-you attention states"
```

### Task 6: Add bounded, truthful usage-tick motion

**Files:**
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/usage_live_policy.h`
- Modify: `components/app_tokens/usage_live_policy.c`
- Modify: `test/test_usage_live_policy.c`
- Modify: `sim/main.c`
- Modify: `test/test_vibepulse_layout_wiring.py`

**Prerequisite:** the static Claude/Codex quota pages and attention states have
passed an authorized install and physical AMOLED review. If that evidence is
not recorded, stop here rather than enabling any motion.

- [ ] **Step 1: Add failing motion-policy and source tests**

Require these exact timing constants and ensure no large-object animation:

```c
#define USAGE_HERO_NUDGE_MS 480
#define USAGE_BAR_GROW_MS 520
#define USAGE_TODAY_PULSE_MS 620
```

Add policy cases for first load/direct, fresh visible increase/animate, fresh
visible decrease/snap, stale/silent, hidden/silent, and a second increase while
animating/coalesce to newest endpoint.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```bash
./test/run.sh
```

Expected: the new motion assertions fail before LVGL animation state exists.

- [ ] **Step 3: Track authoritative per-page endpoints**

Store initialized flag, displayed integer percentage, authoritative total,
authoritative daily value, and target fill widths in each `quota_page`. Treat
every call to `usage_screen_apply_tokens` as an accepted data sample. Before
starting a new animation, delete the page's current hero/bar/today animations
and start directly from the objects' current values toward only the newest
authoritative endpoint.

At the same gate, enable the deferred provider halo breathing (1350 ms
ease-out playback cycle) and attention entry ring (three 1600 ms pulses, then
static). Both animate only their bounded ring object.

- [ ] **Step 4: Implement the three bounded effects**

For a fresh positive update on the active tile:

1. Set the hero to the new rounded integer and animate y from `VP_PERCENT_Y+5`
   to `VP_PERCENT_Y` over 480 ms.
2. Animate baseline and today widths to their new pixel endpoints over 520 ms
   using ease-out.
3. If the daily label changes, set the authoritative text and pulse only its
   text color/opacity over 620 ms.

Initial load sets all endpoints directly. A decrease deletes running
animations and snaps. Hidden pages accept final object values silently so they
never replay on entry. Stale/missing data deletes motion and preserves the
truthful last accepted display contract.

- [ ] **Step 5: Add deterministic animation checkpoints**

In the simulator add a QA mode that applies 73/+12, then 74/+13, advances LVGL
by fixed intervals, and captures start/mid/final frames. Add a second update
before the first finishes and assert the final capture contains only the newest
percentage and bar endpoint. Add a hidden-page update and assert no replay after
navigation.

- [ ] **Step 6: Verify and commit**

Run:

```bash
./test/run.sh
cmake --build sim/build
.venv/bin/python test/test_vibepulse_visual_landmarks.py
git add components/app_tokens/usage_screen.c \
  components/app_tokens/usage_live_policy.h \
  components/app_tokens/usage_live_policy.c \
  test/test_usage_live_policy.c sim/main.c \
  test/test_vibepulse_layout_wiring.py
git commit -m "Animate authoritative VibePulse usage updates"
```

Expected: host, source, and exact-raster tests pass; the final frame equals the
newest authoritative sample.

### Task 7: Produce the real-size review package and target build

**Files:**
- Modify: `docs/superpowers/reviews/2026-08-11-vibepulse-live-quota-polish.md`
- Update only if approved: `design/vibepulse/exports/*.png`

- [ ] **Step 1: Run the complete verification matrix**

Run:

```bash
.venv/bin/python tools/vibepulse_studio/design.py --check
./test/run.sh
tools/preview-ui.sh vibepulse
.venv/bin/python test/test_vibepulse_visual_landmarks.py
idf.py build
```

Expected: Studio is in sync, all host tests pass, preview generation succeeds,
all captures are 480 × 480, and ESP-IDF builds without flash overflow.

- [ ] **Step 2: Inspect exact-size Claude and Codex output**

Review at 1:1 panel pixels:

- longest single-chat header context;
- multi-chat and idle context;
- Claude and Codex segmented bars at 0, 73, and 100;
- stale and missing data;
- both provider needs-you screens and error;
- start, midpoint, and endpoint of a +1 percent tick.

Reject any clipped glyph, false metadata, provider-color drift, approximate
icon, status rail, or unbounded full-screen animation.

- [ ] **Step 3: Record evidence and unresolved physical checks**

Write the commands, pass/fail results, capture paths, and target artifact size
to the review document. Mark physical AMOLED legibility, real touch dismissal,
and real animation smoothness as pending until a target install is authorized.

- [ ] **Step 4: Commit verified implementation evidence**

Run:

```bash
git status --short
git add docs/superpowers/reviews/2026-08-11-vibepulse-live-quota-polish.md
git commit -m "Verify VibePulse live quota polish"
```

Expected: only intentionally scoped files are committed and
`design/vibepulse/exports/claude-hero.png` remains untouched unless the user
explicitly approved replacing that artifact.

### Task 8: Install safely and perform the physical AMOLED gate

**Files:**
- Modify after inspection: `docs/superpowers/reviews/2026-08-11-vibepulse-live-quota-polish.md`

- [ ] **Step 1: Confirm delivery capability before changing the device**

Run:

```bash
grep -n "factory\|ota_0\|ota_1\|otadata" partitions.csv
test -d components/torget_ota
```

Expected today: a factory partition exists, OTA slots do not, and the OTA
component check fails. Therefore do not advertise or attempt OTA for this build.

- [ ] **Step 2: Obtain explicit USB-flash authorization**

Present the verified simulator images first. Explain that the current device
requires USB for this build. Do not flash or repartition until the user confirms
the cable is connected and approves the target write.

- [ ] **Step 3: Flash the already-built artifact over USB**

After authorization, resolve the exact serial target and use the repository's
non-destructive flash helper:

```bash
tools/flasha.sh
```

Expected: boot completes, Solelkollen and Vibbe still launch, VibePulse receives
data, and no boot loop or startup hang occurs.

- [ ] **Step 4: Perform physical AMOLED review before any polish follow-up**

Photograph Claude steady, Codex steady, one usage tick, one working halo, and
both attention states at normal desk distance. Verify percent legibility,
marker visibility, touch dismissal, long-press launcher, swipe responsiveness,
and no impact on the other Torget apps.

- [ ] **Step 5: Record results without hiding failures**

Update the review document with physical evidence. If any physical gate fails,
leave it open and return to the exact failing task rather than declaring the
release done.

## Plan self-review

- The plan covers every approved design requirement: provider parity, adaptive
  context, real icons, segmented today bar, working halo, needs-you/error
  overlays, bounded queueing, touch dismissal, long press, truthful motion,
  missing/stale/reset behavior, exact-size raster review, and physical AMOLED
  review.
- Every new data rule is in a pure host-testable module or existing bounded
  queue policy; LVGL does not infer agent or quota truth.
- The plan contains no placeholder icon, fabricated source, hidden network
  write, automatic carousel, canvas, large opacity animation, or claimed audio.
- C types use `bool`, fixed-capacity strings, bounded queue counts, and integer
  final pixels consistently with the current codebase.
- OTA is not smuggled into the UI task. Its separate A/B foundation and one-time
  USB bootstrap remain explicit, so a visual change cannot accidentally erase
  the current factory image or secrets.
- The user-owned dirty export `design/vibepulse/exports/claude-hero.png` is
  excluded from every commit command.
