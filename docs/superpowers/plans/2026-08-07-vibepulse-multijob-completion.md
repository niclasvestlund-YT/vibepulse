# VibePulse Multi-job Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make VibePulse keep quota dominant while Claude Code and Codex work, represent several simultaneous jobs, and signal each new completion with a provider-correct AMOLED pulse, touch-dismissable `KLAR` screen, and built-in speaker pip.

**Architecture:** The tokenserver publishes a bounded v2 list of jobs per provider. The ESP parser stores at most four public jobs per provider, a pure policy layer owns priority, completion FIFO, deduplication, and timing, and LVGL renders a shared two-provider status rail plus a sibling completion overlay. A target-only audio adapter uses Waveshare's ES8311 BSP while the tone generator and notification policy remain host-testable.

**Tech Stack:** Python 3 stdlib, C11, cJSON, LVGL 9.5, ESP-IDF 5.5.2, FreeRTOS, Waveshare BSP 2.0.1, `esp_codec_dev`, NVS, SDL2 simulator.

---

## File map

- `tools/tokenserver/agent_status.py`: group log events into provider jobs, bound memory, rank jobs, emit contract v2.
- `tools/tokenserver/test_agent_status.py`: server contract, multi-session grouping, ranking, lease, and privacy tests.
- `tools/tokenserver/README.md`: exact v2 JSON contract and semantics.
- `components/app_tokens/agent_status.h`: bounded C representation for two providers and four jobs each.
- `components/app_tokens/agent_status_parse.c`: strict v2 parser.
- `components/app_tokens/agent_net_policy.h`: response capacity sized for the bounded v2 payload.
- `components/app_tokens/agent_completion_policy.c/.h`: pure completion FIFO, timing, local dismissal, and provider/job selection.
- `components/app_tokens/agent_monitor.c/.h`: shared two-provider rail, real icon layers, activity animation, and completion overlay coordination.
- `components/app_tokens/agent_audio.c/.h`: provider tone generation, target codec task, NVS deduplication, and host stub.
- `components/app_tokens/usage_screen.c`: create monitor as a sibling of the tileview and read metadata from the highest-priority provider job.
- `components/app_tokens/app.c`: demo snapshots in the v2 shape and audio startup.
- `sim-fixtures/agent-status-*.json`: v2 single-, multi-job, and queued-completion fixtures.
- `sim/main.c`: deterministic completion QA stages and BMP dumps.
- `test/test_agent_status.c`: strict v2 parser tests.
- `test/test_agent_completion_policy.c`: FIFO, priority, timing, and touch tests.
- `test/test_agent_audio.c`: deterministic PCM tests without hardware.
- `test/run.sh`, `sim/CMakeLists.txt`, `components/app_tokens/CMakeLists.txt`: compile the new focused units in host, simulator, and target builds.

### Task 0: Checkpoint the already-written tokenserver robustness work

**Files:**
- Modify: `tools/tokenserver/tokenserver.py`
- Test: `tools/tokenserver/test_tokenserver.py`

- [ ] **Step 1: Inspect the existing dirty diff without changing it**

Run:

```bash
git diff -- tools/tokenserver/tokenserver.py tools/tokenserver/test_tokenserver.py
```

Expected: only the previously written bounded Codex scan, background refresh, partial-line, and failed-refresh preservation changes appear.

- [ ] **Step 2: Run the focused regression tests**

Run:

```bash
python3 -m unittest tools.tokenserver.test_tokenserver -v
```

Expected: `OK` and zero failures.

- [ ] **Step 3: Commit the verified checkpoint separately**

```bash
git add tools/tokenserver/tokenserver.py tools/tokenserver/test_tokenserver.py
git commit -m "Gör tokenserverns refresh och loggscan robust"
```

### Task 1: Publish several jobs per provider from tokenserver

**Files:**
- Modify: `tools/tokenserver/agent_status.py`
- Modify: `tools/tokenserver/test_agent_status.py`
- Modify: `tools/tokenserver/README.md`

- [ ] **Step 1: Write failing grouping and contract tests**

Add tests with two Claude sessions and two Codex turns. Claude's public task identity must be stable for all events in one `sessionId`, while `source_id` continues to make each `event_id` unique:

```python
def claude_task_id(session_id):
    return hashlib.sha256(
        session_id.encode("utf-8", errors="surrogatepass")
    ).hexdigest()

def test_claude_events_in_one_session_share_task_identity(self):
    first = claude_event("assistant", tool_name="Read")
    second = claude_event("assistant", tool_name="Edit")
    second["uuid"] = "event-2"
    self.assertEqual(classify_claude(first).task_id,
                     classify_claude(second).task_id)
    self.assertNotEqual(classify_claude(first).source_id,
                        classify_claude(second).source_id)

def test_snapshot_bounds_jobs_and_reports_all_active(self):
    store = AgentStatusStore(now=lambda: 10.0)
    for index in range(6):
        store.apply("claude", Event(
            "working", "editing", f"task-{index}", f"source-{index}",
            f"Project-{index}"), order_at=float(index))
    provider = store.snapshot()["agents"]["claude"]
    self.assertEqual(provider["active_count"], 6)
    self.assertEqual(len(provider["jobs"]), 4)

def test_two_done_jobs_survive_as_distinct_events(self):
    store = AgentStatusStore(now=lambda: 10.0)
    store.apply("codex", Event("done", None, "turn-a", "end-a", "Buddy"))
    store.apply("codex", Event("done", None, "turn-b", "end-b", "Torget"))
    jobs = store.snapshot()["agents"]["codex"]["jobs"]
    self.assertEqual({job["task_id"] for job in jobs}, {"turn-a", "turn-b"})
```

Update the exact-shape test to require:

```python
{
    "v": 2,
    "seq": 0,
    "agents": {
        "claude": {"active_count": 0, "jobs": []},
        "codex": {"active_count": 0, "jobs": []},
    },
}
```

- [ ] **Step 2: Run the new tests and verify RED**

Run:

```bash
python3 -m unittest tools.tokenserver.test_agent_status.StoreTests tools.tokenserver.test_agent_status.ClassifierTests -v
```

Expected: failures showing v1 shape, overwritten provider state, and source-sensitive Claude task identity.

- [ ] **Step 3: Implement bounded provider stores**

Use these constants and ranking, keeping no raw log content:

```python
PUBLIC_JOB_LIMIT = 4
TRACKED_JOB_LIMIT = 16
STATE_PRIORITY = {
    "waiting": 5, "error": 4, "working": 3,
    "done": 2, "idle": 1, "unknown": 0,
}

def _claude_identity(event):
    session_id = event.get("sessionId")
    source_id = event.get("uuid")
    if not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(source_id, str) or not source_id:
        return None
    task_id = hashlib.sha256(
        session_id.encode("utf-8", errors="surrogatepass")
    ).hexdigest()
    return task_id, source_id, sanitize_project(event.get("cwd"))
```

Change `AgentStatusStore._agents[provider]` to a dictionary keyed by bounded
`task_id`. `apply()` updates only that task, carries model/effort only within
the same task, increments `seq` only for public changes, and evicts the
lowest-priority oldest record when `TRACKED_JOB_LIMIT` is exceeded.

Build each provider snapshot with:

```python
public = [effective(record, now) for record in records.values()]
public = [job for job in public if job["state"] not in ("idle", "unknown")]
active_count = sum(job["state"] in ("working", "waiting", "error")
                   for job in public)
public.sort(key=lambda job: (
    -STATE_PRIORITY[job["state"]], job["updated_ms"], job["task_id"]))
provider = {"active_count": active_count,
            "jobs": public[:PUBLIC_JOB_LIMIT]}
```

Return `{"v": 2, "seq": seq, "agents": agents}`.

- [ ] **Step 4: Keep stateless Codex tool events on their current turn**

Add `self._stream_tasks = {}` to `AgentStatusService`. Key it by
`(provider, path, identity)`. Lifecycle/turn-context events with a real
`task_id` refresh the key. A Codex response item without turn metadata uses
the remembered turn; before any lifecycle event it uses a SHA-256 hash of
the stream identity. Remove keys when discovery drops the corresponding
file identity.

Add a service test that appends `task_started`, a tool call without
`turn_id`, and `task_complete` to one rollout and asserts that exactly one
Codex job remains.

- [ ] **Step 5: Run the full Python status suite**

Run:

```bash
python3 -m unittest tools.tokenserver.test_agent_status -v
```

Expected: all tests pass.

- [ ] **Step 6: Document and commit contract v2**

Replace the README's single-agent object with `active_count` plus bounded
`jobs`, state that at most four public jobs are sent, and retain every
privacy and lease invariant.

```bash
git add tools/tokenserver/agent_status.py tools/tokenserver/test_agent_status.py tools/tokenserver/README.md
git commit -m "Exponera flera samtidiga agentjobb"
```

### Task 2: Parse the bounded v2 contract on ESP and simulator

**Files:**
- Modify: `components/app_tokens/agent_status.h`
- Modify: `components/app_tokens/agent_status_parse.c`
- Modify: `components/app_tokens/agent_net_policy.h`
- Modify: `components/app_tokens/agent_net.c`
- Modify: `test/test_agent_status.c`
- Modify: `test/test_agent_net_policy.c`
- Modify: `sim-fixtures/agent-status-*.json`
- Create: `sim-fixtures/agent-status-multi-working.json`
- Create: `sim-fixtures/agent-status-multi-done.json`

- [ ] **Step 1: Write failing C parser tests for v2**

Define the desired representation in the test:

```c
#define TK_AGENT_PROVIDER_COUNT 2
#define TK_AGENT_JOBS_MAX 4

check("två Claude-jobb parsas", snapshot.claude.job_count == 2);
check("alla aktiva räknas även över listtak",
      snapshot.claude.active_count == 5);
check("Codex-projekt bevaras",
      strcmp(snapshot.codex.jobs[0].project, "Buddy") == 0);
```

Add rejection cases for v1, five `jobs`, negative/over-255 `active_count`,
duplicate provider keys, unknown job fields, and a body with valid JSON after
the terminating root object.

- [ ] **Step 2: Run the parser test and verify RED**

Run:

```bash
./test/run.sh
```

Expected: compile failures for missing provider-list fields, followed by parser test failures once the type declarations exist.

- [ ] **Step 3: Add bounded provider types and strict parsing**

Use this public shape:

```c
#define TK_AGENT_PROVIDER_COUNT 2
#define TK_AGENT_JOBS_MAX 4

typedef enum {
  TK_AGENT_PROVIDER_CLAUDE = 0,
  TK_AGENT_PROVIDER_CODEX = 1,
} tk_agent_provider;

typedef struct {
  uint8_t active_count;
  uint8_t job_count;
  tk_agent_status jobs[TK_AGENT_JOBS_MAX];
} tk_agent_provider_status;

typedef struct {
  uint32_t seq;
  tk_agent_provider_status claude;
  tk_agent_provider_status codex;
} tk_agent_snapshot;
```

Change the root version check to `version == 2`. Parse each provider object
with exactly `active_count` and `jobs`, require `jobs` to be an array of at
most four strict job objects, and parse each job through the existing bounded
string/state/activity helpers.

- [ ] **Step 4: Increase the bounded HTTP body once**

Set:

```c
#define TK_AGENT_HTTP_BODY_CAP 4096
```

Update the overflow log to print `TK_AGENT_HTTP_BODY_CAP - 1` instead of the
literal `1535`, and update the capacity tests to use the macro.

- [ ] **Step 5: Convert fixtures and verify host tests GREEN**

Every fixture must use:

```json
{"v":2,"seq":201,"agents":{"claude":{"active_count":1,"jobs":[{"task_id":"claude-task-201","event_id":"claude-working-201","state":"working","project":"Torget","activity":"editing","model":"FABLE 5","effort":"XHIGH","updated_ms":0}]},"codex":{"active_count":0,"jobs":[]}}}
```

Run:

```bash
./test/run.sh
```

Expected: all host binaries and wiring tests pass.

- [ ] **Step 6: Commit the device contract**

```bash
git add components/app_tokens/agent_status.h components/app_tokens/agent_status_parse.c components/app_tokens/agent_net_policy.h components/app_tokens/agent_net.c test/test_agent_status.c test/test_agent_net_policy.c sim-fixtures/agent-status-*.json
git commit -m "Parsa VibePulse agentstatus v2"
```

### Task 3: Add a pure completion queue and timing policy

**Files:**
- Create: `components/app_tokens/agent_completion_policy.h`
- Create: `components/app_tokens/agent_completion_policy.c`
- Create: `test/test_agent_completion_policy.c`
- Modify: `test/run.sh`

- [ ] **Step 1: Write failing FIFO and phase tests**

Use a capacity of eight completion events and test the public API:

```c
tk_completion_queue queue = {0};
tk_completion_queue_apply(&queue, &snapshot, 1000);
const tk_completion_event *current = tk_completion_queue_current(&queue);
check("äldsta done visas först",
      current && strcmp(current->project, "Buddy") == 0);
check("pulserar före pipgränsen",
      tk_completion_phase_at(&queue, 3399) == TK_COMPLETION_PULSE);
check("statisk efter 2,4 sekunder",
      tk_completion_phase_at(&queue, 3400) == TK_COMPLETION_STATIC);
check("autoåtergår efter tio sekunder",
      tk_completion_phase_at(&queue, 11000) == TK_COMPLETION_HIDDEN);

tk_completion_queue_dismiss(&queue);
check("tryck går till nästa köade event",
      strcmp(tk_completion_queue_current(&queue)->project, "Torget") == 0);
```

Also test: repeated `event_id` never requeues; the first snapshot only queues
done events newer than 15 seconds; waiting/error outrank working in the rail;
four public jobs can report a larger `active_count`; and queue overflow drops
the oldest already-hidden item, never the displayed item.

- [ ] **Step 2: Compile and verify RED**

Add the new test binary to `test/run.sh`, then run:

```bash
./test/run.sh
```

Expected: compilation fails because `agent_completion_policy.h` and its API do not exist.

- [ ] **Step 3: Implement the minimal pure policy**

Expose:

```c
#define TK_COMPLETION_QUEUE_CAP 8
#define TK_COMPLETION_PULSE_MS 2400ULL
#define TK_COMPLETION_VISIBLE_MS 10000ULL

typedef enum {
  TK_COMPLETION_HIDDEN,
  TK_COMPLETION_PULSE,
  TK_COMPLETION_STATIC,
} tk_completion_phase;

typedef struct {
  int provider;
  char event_id[TK_AGENT_ID_CAP];
  char project[TK_AGENT_PROJECT_CAP];
  uint8_t other_active_count;
} tk_completion_event;

void tk_completion_queue_apply(tk_completion_queue *queue,
                               const tk_agent_snapshot *snapshot,
                               uint64_t now_ms);
const tk_completion_event *tk_completion_queue_current(
    const tk_completion_queue *queue);
tk_completion_phase tk_completion_phase_at(tk_completion_queue *queue,
                                            uint64_t now_ms);
void tk_completion_queue_dismiss(tk_completion_queue *queue);
const tk_agent_status *tk_agent_provider_primary(
    const tk_agent_provider_status *provider);
```

Keep only bounded IDs/project names in the queue. Sort newly observed done
jobs oldest first by descending `updated_ms`, then provider index, so FIFO is
deterministic.

- [ ] **Step 4: Run host tests GREEN and commit**

Run:

```bash
./test/run.sh
```

Expected: all host tests pass.

```bash
git add components/app_tokens/agent_completion_policy.c components/app_tokens/agent_completion_policy.h test/test_agent_completion_policy.c test/run.sh
git commit -m "Köa och kvittera agentavslut"
```

### Task 4: Build the shared status rail and static Claude/Codex completion overlays

**Files:**
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/agent_monitor.h`
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/app.c`
- Modify: `components/app_tokens/CMakeLists.txt`
- Modify: `sim/CMakeLists.txt`
- Modify: `sim/main.c`

- [ ] **Step 1: Add a failing wiring test for overlay gestures**

Extend `test/test_agent_demo_wiring.py` or create a focused wiring test that
requires all three calls in `agent_monitor.c`:

```python
assert "LV_EVENT_CLICKED" in source
assert "tk_completion_queue_dismiss" in source
assert "LV_EVENT_LONG_PRESSED" in source
assert "torget_launcher_open" in source
```

Require `usage_screen.c` to call `tk_agent_monitor_create(root)` once, after
the tileview is built, and forbid the old per-provider footer calls.

- [ ] **Step 2: Run wiring tests and verify RED**

Run:

```bash
./test/run.sh
```

Expected: the new monitor wiring assertions fail.

- [ ] **Step 3: Replace provider footers with one shared rail**

Change the API to:

```c
void tk_agent_monitor_create(lv_obj_t *app_root);
void tk_agent_monitor_apply(const tk_agent_snapshot *snapshot, int64_t now_us);
void tk_agent_monitor_tick(int64_t now_us);
```

Create a shared 444 x 78 rail at `(18, 366)` as a sibling of the tileview.
When only one provider has jobs, use the full width. When both have jobs,
split into two 218 px lanes with an 8 px gap. Each lane shows its real 32 px
asset, `CLAUDE · N JOBBAR` or `CODEX · N JOBBAR`, and the primary job's
project/activity. Hide the entire rail when neither provider has a public job.

- [ ] **Step 4: Create the static completion overlay**

Create a 480 x 480 black sibling above the tileview and rail, hidden by
default. Its static composition is:

```text
y=28    provider label: CLAUDE CODE or CODEX
y=78    real provider asset, centered in a 150 x 150 slot
y=244   KLAR, centered, status font 64 px
y=324   project, uppercase and bounded
y=398   optional "N JOBBAR" for remaining active jobs
```

Codex uses the original cloud layers without recoloring: sampled source
gradient `#ACA9FF` to `#3D48FF`, white chevron/underscore, and pulse color
`#3D48FF`. Claude uses the white mask on `#D97757`. Do not recreate either
asset with labels, emoji, SVG, or LVGL primitives.

Register `LV_EVENT_CLICKED` to dismiss only the current completion and
`LV_EVENT_LONG_PRESSED` to call `torget_launcher_open()`.

- [ ] **Step 5: Update metadata and demo snapshots**

`usage_screen_apply_agent()` reads model/effort from
`tk_agent_provider_primary()`. `TK_AGENT_DEMO` fills `snapshot.claude.jobs[0]`,
sets `job_count`/`active_count`, and continues cycling states without using
the removed scalar provider fields.

- [ ] **Step 6: Add deterministic simulator dumps**

Add `--vibepulse-completion-qa` that exits after writing:

```text
/tmp/torget-vibepulse-multi-working.bmp
/tmp/torget-vibepulse-claude-done-static.bmp
/tmp/torget-vibepulse-codex-done-static.bmp
/tmp/torget-vibepulse-two-done-queued.bmp
```

Use the v2 multi fixtures; call `lv_obj_update_layout()` and `lv_refr_now()`
before every dump through the existing `dump_frame()` helper.

- [ ] **Step 7: Build host and simulator GREEN**

Run:

```bash
./test/run.sh
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
./sim/build/torget-sim --vibepulse-completion-qa
```

Expected: tests and build exit zero and all four BMP paths are printed.

- [ ] **Step 8: Compare and commit the static UI**

Open the selected Codex mockup and both static simulator BMPs side by side.
Fix P0--P2 layout differences, then record the comparison in
`docs/superpowers/reviews/2026-08-07-vibepulse-completion-static.md`.

```bash
git add components/app_tokens/agent_monitor.c components/app_tokens/agent_monitor.h components/app_tokens/usage_screen.c components/app_tokens/app.c components/app_tokens/CMakeLists.txt sim/CMakeLists.txt sim/main.c test/test_agent_demo_wiring.py docs/superpowers/reviews/2026-08-07-vibepulse-completion-static.md
git commit -m "Rita VibePulse arbetsrad och klarsidor"
```

- [ ] **Step 9: Stop for the physical static AMOLED gate**

Build and flash the static overlay only. Photograph Claude and Codex straight
on at normal shelf brightness. Do not implement animation until provider,
`KLAR`, project, and remaining-job count are readable from 2--3 metres.

### Task 5: Animate working state and the two slow AMOLED pulses

**Files:**
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/agent_completion_policy.c`
- Modify: `test/test_agent_completion_policy.c`
- Modify: `sim/main.c`

- [ ] **Step 1: Add failing frame/phase boundary tests**

Add pure helpers and tests:

```c
check("första pulsen börjar mörkt", tk_completion_pulse_opa(0) == 72);
check("första pulsen når full färg", tk_completion_pulse_opa(600) == 255);
check("mellanpulsen är mörk", tk_completion_pulse_opa(1200) == 72);
check("andra pulsen når full färg", tk_completion_pulse_opa(1800) == 255);
check("pulsen slutar mörk", tk_completion_pulse_opa(2400) == 72);
```

- [ ] **Step 2: Run host tests and verify RED**

Run `./test/run.sh`.

Expected: missing `tk_completion_pulse_opa()` causes compile failure.

- [ ] **Step 3: Implement bounded animation work**

Implement `tk_completion_pulse_opa(elapsed_ms)` as a piecewise linear
triangle wave with two 1200 ms cycles and opacity range 72--255. Update the
full-screen overlay no faster than 8 fps. Do not change panel DBV brightness.

For the rail, update only the dirty 32 x 32 pet and 28 x 24 activity meter:

- Codex cloud y-offset: `0, -1, -2, -1, 0`; underscore toggles every fourth frame.
- Claude y-offset: `0, -1, -2, -1, 0`; eye frame changes only if a supplied real asset frame exists.
- Three bars rotate heights `8, 18, 12` across lanes at 6--8 fps.

- [ ] **Step 4: Verify simulator frames and commit**

Run:

```bash
./test/run.sh
ninja -C sim/build
./sim/build/torget-sim --vibepulse-completion-qa
```

Expected: host tests pass and the completion QA command exits zero.

```bash
git add components/app_tokens/agent_monitor.c components/app_tokens/agent_completion_policy.c test/test_agent_completion_policy.c sim/main.c
git commit -m "Animera VibePulse arbete och klarsignal"
```

### Task 6: Play provider-specific pips through the built-in ES8311

**Files:**
- Create: `components/app_tokens/agent_audio.h`
- Create: `components/app_tokens/agent_audio.c`
- Create: `test/test_agent_audio.c`
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `components/app_tokens/app.c`
- Modify: `components/app_tokens/CMakeLists.txt`
- Modify: `sim/CMakeLists.txt`
- Modify: `test/run.sh`

- [ ] **Step 1: Write failing host tone tests**

Test the target-independent generator:

```c
int16_t pcm[TK_AGENT_TONE_MAX_SAMPLES];
size_t codex = tk_agent_audio_render_tone(TK_AGENT_PROVIDER_CODEX,
                                          pcm, TK_AGENT_TONE_MAX_SAMPLES);
size_t claude = tk_agent_audio_render_tone(TK_AGENT_PROVIDER_CLAUDE,
                                           pcm, TK_AGENT_TONE_MAX_SAMPLES);
check("båda pipen är under 350 ms",
      codex <= 22050 * 350 / 1000 && claude <= 22050 * 350 / 1000);
check("providerpipen skiljer sig", codex != claude);
int peak = 0;
for (size_t i = 0; i < claude; i++) {
  int sample = pcm[i] < 0 ? -pcm[i] : pcm[i];
  if (sample > peak) peak = sample;
}
check("PCM klipper aldrig", peak <= 12000);
```

Add a notification-policy test that the same `event_id` requests sound once,
and that a muted state requests none.

- [ ] **Step 2: Run host tests and verify RED**

Add the new binary to `test/run.sh`, then run `./test/run.sh`.

Expected: missing audio API causes compile failure.

- [ ] **Step 3: Implement the deterministic tones and host stub**

Expose:

```c
#define TK_AGENT_AUDIO_RATE 22050
#define TK_AGENT_TONE_MAX_SAMPLES (TK_AGENT_AUDIO_RATE * 350 / 1000)

void tk_agent_audio_start(void);
void tk_agent_audio_notify(int provider, const char *event_id);
void tk_agent_audio_set_muted(bool muted);
bool tk_agent_audio_is_muted(void);
size_t tk_agent_audio_render_tone(int provider, int16_t *pcm,
                                  size_t capacity);
```

Generate sine tones with 5 ms attack/release and peak amplitude 12000:

- Codex: 1047 Hz for 70 ms, 55 ms silence, 1319 Hz for 90 ms.
- Claude: 784 Hz for 90 ms, 45 ms silence, 988 Hz for 110 ms.

Under non-ESP builds, startup and notification are no-ops while generation
remains real and testable.

- [ ] **Step 4: Implement target codec task and NVS dedupe**

Under `ESP_PLATFORM`:

1. Create a FreeRTOS queue of four bounded `{provider,event_id}` messages.
2. Start one low-priority task once from `tk_agent_audio_start()`.
3. Initialize `bsp_audio_codec_speaker_init()` once.
4. Open `esp_codec_dev` as mono, 16-bit, 22050 Hz and set output volume 20.
5. Render into one static/internal PCM buffer and call `esp_codec_dev_write()`.
6. Store last played provider event IDs in NVS namespace `vibepulse_audio`.
7. On any init/write failure log once and leave visual completion untouched.

Add direct dependencies:

```cmake
PRIV_REQUIRES torget_net esp_http_client nvs_flash
              waveshare__esp32_s3_touch_amoled_2_16 esp_codec_dev
```

- [ ] **Step 5: Connect sound at the exact phase boundary**

When the current completion first crosses from `PULSE` to `STATIC`, call
`tk_agent_audio_notify(provider, event_id)` once. A 44 x 44 px speaker button
in the completion top-right toggles mute without dismissing the overlay; its
click callback stops event bubbling. Touch elsewhere still dismisses.

- [ ] **Step 6: Run host, simulator, and target builds**

Run:

```bash
./test/run.sh
ninja -C sim/build
. ~/esp/esp-idf/export.sh
idf.py build
```

Expected: every command exits zero; target link includes the audio adapter.

- [ ] **Step 7: Commit audio**

```bash
git add components/app_tokens/agent_audio.c components/app_tokens/agent_audio.h components/app_tokens/agent_monitor.c components/app_tokens/app.c components/app_tokens/CMakeLists.txt sim/CMakeLists.txt test/test_agent_audio.c test/run.sh
git commit -m "Spela olika klarsignaler för Claude och Codex"
```

### Task 7: End-to-end verification and physical flash

**Files:**
- Modify: `docs/superpowers/reviews/2026-08-07-vibepulse-completion-static.md`
- Modify: `docs/superpowers/specs/2026-08-07-vibepulse-completion-beacon-design.md` only if physical evidence changes a locked measurement.

- [ ] **Step 1: Run every software verification fresh**

```bash
python3 -m unittest tools.tokenserver.test_agent_status tools.tokenserver.test_tokenserver -v
./test/run.sh
cmake -S sim -B sim/build -G Ninja
ninja -C sim/build
./sim/build/torget-sim --vibepulse-completion-qa
. ~/esp/esp-idf/export.sh
idf.py build
```

Expected: zero failures and zero nonzero exits.

- [ ] **Step 2: Inspect the generated images**

Open all four 480 x 480 BMPs plus the selected Codex mockup in one visual
comparison. Confirm: giant quota remains dominant while working; real icons;
no clipped Swedish text; exact Codex gradient; Claude coral; project alignment;
two-provider rail; and `KLAR` readability.

- [ ] **Step 3: Flash the known device and monitor boot**

```bash
. ~/esp/esp-idf/export.sh
idf.py -p /dev/cu.usbmodem101 flash monitor
```

Expected boot evidence: VibePulse starts, agent-status polling starts, audio
codec initializes without reducing the logged largest internal/DMA blocks to
an unsafe level, and no watchdog or LVGL errors appear.

- [ ] **Step 4: Exercise the physical acceptance matrix**

Verify on glass:

1. Claude only working; Codex only working; both working; three jobs in one provider.
2. Claude done and Codex done use different exact visuals and audible pips.
3. Two completions queue; touch shows the next; ten seconds returns to usage.
4. Longpress opens launcher; KEY3 changes app; rotation keeps touch aligned.
5. Muting survives reboot; an old `event_id` never beeps again.
6. From 2--3 metres, quota, provider, `KLAR`, and remaining-job count are legible.

- [ ] **Step 5: Record evidence and final verification commit**

Add simulator paths, physical photo paths, boot log evidence, measured heap,
and every acceptance result to the review document.

```bash
git add docs/superpowers/reviews/2026-08-07-vibepulse-completion-static.md
git commit -m "Verifiera VibePulse klarsignal på AMOLED"
```
