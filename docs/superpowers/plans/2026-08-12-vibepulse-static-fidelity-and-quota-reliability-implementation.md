# VibePulse Static Fidelity and Quota Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the Codex logo and make Claude/Codex weekly quota data identity-safe, restart-safe, and explicitly live/stale/no-data on the 480 × 480 ESP32 display.

**Architecture:** Keep quota identity, persistence, and provenance on the Mac in a new content-free cache module; extend the flat v2 JSON with optional stale booleans; let the C parser and presenter carry provenance without LVGL inference. Replace the three Codex image layers with one deterministic source-derived I4 asset used by both renderers. Motion remains disabled until the corrected static build passes the physical AMOLED gate.

**Tech Stack:** Python 3.9+ stdlib, C11, cJSON, LVGL 9.5, Pillow, SDL simulator, ESP-IDF 5.5.2.

---

## Working boundary

- Work on `/Users/niclasvestlund/Torget` `main` only.
- Never modify, stage, regenerate, or commit the user-owned dirty
  `design/vibepulse/exports/claude-hero.png`.
- Do not start, stop, or mutate the user's live tokenserver during automated
  tests. All source-selection tests use temporary synthetic rollouts.
- Do not flash without a new explicit authorization after exact captures and a
  target build are presented.
- Do not add motion, a Spark page, another status row, or a new network source.

## File map

- Create `tools/tokenserver/quota_cache.py`: validated, atomic, content-free
  last-known-good records and reset expiry.
- Create `tools/tokenserver/test_quota_cache.py`: persistence, identity,
  privacy, reset and failure tests.
- Modify `tools/tokenserver/tokenserver.py`: classify Codex limit identities,
  bounded general-week scan, resolve live versus cached Claude/Codex quotas,
  publish stale provenance.
- Modify `tools/tokenserver/test_tokenserver.py`: general-versus-Spark,
  asynchronous refresh, restart cache and JSON contract tests.
- Modify `tools/tokenserver/README.md`: exact optional provenance fields and
  cache semantics.
- Modify `components/app_tokens/tokens.h`: store quota-source staleness in each
  `tk_limit`.
- Modify `components/app_tokens/tokens_parse.c`: validate optional booleans and
  reject stale-without-percent.
- Modify `test/test_tokens.c`: parser compatibility and hostile provenance.
- Modify `components/app_tokens/usage_presenter.h/.c`: carry quota staleness and
  use stable `FABLE · WEEK` page identity for missing model data.
- Modify `test/test_usage_presenter.c`: live/stale/no-data and fixed label.
- Modify `components/app_tokens/usage_screen.c`: render source stale in the
  reserved header slot and suppress the working halo for stale quota.
- Modify `tools/agent_assets/build-agent-images.py`: one composited I4 Codex
  image at native 112 and 32 px.
- Regenerate `components/app_tokens/agent_assets.h/.c`: remove separate Codex
  glyph descriptors.
- Modify `tools/agent_assets/test_build_agent_images.py`: palette, silhouette,
  dimensions and determinism.
- Modify `components/app_tokens/agent_monitor.c`: one large Codex image object.
- Modify `sim/main.c`, `test/test_preview_ui.py`,
  `test/test_vibepulse_layout_wiring.py`, and
  `test/test_vibepulse_visual_landmarks.py`: exact live/stale/no-data and logo
  captures.
- Update `docs/superpowers/reviews/2026-08-11-vibepulse-live-quota-polish.md`:
  record the failed first physical gate and corrected static evidence.

### Task 1: Add an identity-safe persistent quota cache

**Files:**
- Create: `tools/tokenserver/quota_cache.py`
- Create: `tools/tokenserver/test_quota_cache.py`
- Modify: `test/run.sh`

- [ ] **Step 1: Write the failing cache tests**

Cover one valid record, multiple identities in one scope, restart/load, exact
reset expiry, fresh decrease, malformed sibling isolation, write failure and
privacy. The public contract is:

```python
@dataclass(frozen=True)
class CachedQuota:
    provider: str
    scope: str
    identity: str
    pct: float
    reset_at: int
    observed_at: int
    label: str | None = None

class QuotaCache:
    def put(self, record: CachedQuota) -> bool: ...
    def latest(self, provider: str, scope: str,
               now: float | None = None) -> CachedQuota | None: ...
```

Required assertions include:

```python
self.assertEqual(reloaded.latest("codex", "week").pct, 46.0)
self.assertIsNone(cache.latest("claude", "model_week", now=RESET_AT))
self.assertEqual(cache.latest("codex", "week").identity, "general:abc")
self.assertNotIn("accessToken", cache_path.read_text())
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```bash
.venv/bin/python -m unittest tools.tokenserver.test_quota_cache -v
```

Expected: import failure because `quota_cache.py` does not exist.

- [ ] **Step 3: Implement the minimal cache**

Use schema v1 and one record per exact `(provider, scope, identity)`. Validate
allowed providers/scopes, ASCII identity length, finite `0..100` percent,
future reset, observation time, and trusted label length. `latest()` removes
expired records logically and returns the greatest `observed_at` only within
the requested semantic scope. Persist with temporary file, flush, `fsync`, and
`os.replace`; restore in-memory state if persistence fails.

- [ ] **Step 4: Verify focused and full host tests**

Run:

```bash
.venv/bin/python -m unittest tools.tokenserver.test_quota_cache -v
PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh
```

Expected: both exit 0.

- [ ] **Step 5: Commit the cache**

```bash
git add tools/tokenserver/quota_cache.py \
  tools/tokenserver/test_quota_cache.py test/run.sh
git commit -m "Persist VibePulse quota truth safely"
```

### Task 2: Classify Codex identities and resolve live versus cached quota

**Files:**
- Modify: `tools/tokenserver/tokenserver.py`
- Modify: `tools/tokenserver/test_tokenserver.py`
- Modify: `tools/tokenserver/README.md`

- [ ] **Step 1: Write failing general-versus-named tests**

Build temporary rollout files containing:

```python
general = {"rate_limits": {
    "limit_id": "general-id", "limit_name": None,
    "primary": {"used_percent": 46.0, "window_minutes": 10080,
                "resets_at": GENERAL_RESET}}}
spark = {"rate_limits": {
    "limit_id": "spark-id", "limit_name": "GPT-5.3-Codex-Spark",
    "primary": {"used_percent": 0.0, "window_minutes": 10080,
                "resets_at": SPARK_RESET}}}
```

Assert a newer Spark file cannot replace the 46% general weekly value; the
scanner examines at most 20 newest files and the existing 1 MiB tail per file;
freshness selects within the general identity; raw IDs are never returned.
Accept `rate_limits` only from the expected rollout event envelope so quoted
fixtures or tool output inside conversation content cannot become quota data.
Missing `window_minutes` is unclassifiable, not a session limit. Hash stable
source identity before constructing a `CachedQuota`.

- [ ] **Step 2: Write failing provenance/cache integration tests**

With a temporary `QuotaCache`, assert:

```python
self.assertEqual(snapshot["codexWeekPct"], 46.0)
self.assertFalse(snapshot["codexWeekStale"])
self.assertEqual(restarted["codexWeekPct"], 46.0)
self.assertTrue(restarted["codexWeekStale"])
self.assertIsNone(after_reset["codexWeekPct"])
self.assertFalse(after_reset["codexWeekStale"])
```

Repeat for Claude general week and model week. A failed/expired Claude probe
must retain an unexpired matching cache but must not manufacture `FABLE · WEEK`
inside the data service when no trusted label was ever observed. Also prove
that a successful in-memory sample stops being classified as live immediately
after the next scheduled source probe fails; it may only reappear through the
cache with `stale: true`.
Unknown named Claude buckets such as `7d_haiku` must not overwrite the general
week bucket. Cached reset minutes are recomputed at serve time from the stored
absolute reset timestamp. Stale samples are never written into usage history.

- [ ] **Step 3: Run focused tests and verify RED**

```bash
.venv/bin/python -m unittest \
  tools.tokenserver.test_tokenserver.ClaudeLimitHeaderTests \
  tools.tokenserver.test_tokenserver.CodexLimitLogTests -v
```

Expected: Spark selection and stale provenance assertions fail.

- [ ] **Step 4: Implement bounded classification and resolution**

Add a pure classifier returning semantic scope, hashed local identity, label,
percent, reset and observation time. `limit_name` non-empty means named and is
excluded from general `WEEKLY`. Scan up to 20 files without changing the 1 MiB
per-file tail cap. Resolve each display scope through:

```python
def resolve_quota(live, cache, provider, scope, now):
    if live is not None:
        cache.put(live)
        return live, False
    return cache.latest(provider, scope, now=now), True
```

Return `(None, False)` when no unexpired cache exists. Publish optional boolean
fields `claudeWeekStale`, `claudeModelWeekStale`, and `codexWeekStale`. Keep
refresh work asynchronous. Track the result of the latest attempted source
probe separately from the last good sample: between scheduled successful
probes the sample remains live, but after an attempted failure it can only be
served from the persistent cache as stale. A Codex scan that finds named limits
but no unnamed/general limit is a failed general-week observation, not a live
zero.

- [ ] **Step 5: Document and verify the server contract**

Update the README JSON example and rules. Run:

```bash
.venv/bin/python -m unittest discover -s tools/tokenserver -p 'test_*.py' -v
PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh
```

Expected: all tokenserver and host tests pass without touching the live service.

- [ ] **Step 6: Commit the source fix**

```bash
git add tools/tokenserver/tokenserver.py \
  tools/tokenserver/test_tokenserver.py tools/tokenserver/README.md
git commit -m "Separate general and named VibePulse quotas"
```

### Task 3: Carry quota provenance truthfully to the ESP renderer

**Files:**
- Modify: `components/app_tokens/tokens.h`
- Modify: `components/app_tokens/tokens_parse.c`
- Modify: `components/app_tokens/usage_presenter.h`
- Modify: `components/app_tokens/usage_presenter.c`
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `test/test_tokens.c`
- Modify: `test/test_usage_presenter.c`
- Modify: `test/test_vibepulse_layout_wiring.py`

- [ ] **Step 1: Write failing parser and presenter tests**

Add `int stale` to `tk_limit`. Prove absent optional stale fields default false;
boolean true is accepted only with percent; string/number stale and
stale-with-null percent reject the payload. Presenter tests must prove:

```c
check("cached Fable is stale", page.quota.stale == 1);
check("missing Fable keeps stable page name",
      strcmp(page.quota.label, "FABLE · WEEK") == 0);
```

Add hostile numeric cases for non-finite or greater-than-100 percentages,
unbounded reset values before integer conversion, and fractional protocol
versions. Increase the token response body cap to 2048 and keep a host payload
headroom test in the full gate.

- [ ] **Step 2: Run focused tests and verify RED**

Run the parser and presenter commands already registered in `test/run.sh`.
Expected: compilation/assertion failure because `stale` is absent and missing
model data still says `MODEL · WEEK`.

- [ ] **Step 3: Implement strict optional booleans and presenter pass-through**

Parse only cJSON true/false; missing defaults false. Reject true if the paired
percent is absent. Copy `tk_limit.stale` into `usage_card_view.stale`. Use
`FABLE · WEEK` as the fixed identity of `USAGE_QUOTA_CLAUDE_MODEL`; a trusted
live/cached label still overrides it. Keep the page visible without trusted
data and render the presenter's en dash as the large value instead of replacing
it with an empty string in LVGL.

- [ ] **Step 4: Render one truthful reserved status**

Store `quota_stale` on each `quota_page`. Header priority becomes:

```c
if (!page->has_data) context = "NO DATA";
else if (ui.stale || page->quota_stale) context = "STALE";
else context = view.context;
```

Pass `ui.stale || page->quota_stale` to the live-header policy so cached quota
never shows a working halo. Preserve the existing context and halo render
caches. Route the final `LIVE`/`STALE`/`NO DATA` choice through the presenter,
not three separate renderer policies, and add last-rendered-string guards to
the 10 Hz volume labels so unchanged text causes no allocation/invalidation.

- [ ] **Step 5: Verify and commit ESP provenance**

```bash
PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh
git add components/app_tokens/tokens.h \
  components/app_tokens/tokens_parse.c \
  components/app_tokens/usage_presenter.h \
  components/app_tokens/usage_presenter.c \
  components/app_tokens/usage_screen.c test/test_tokens.c \
  test/test_usage_presenter.c test/test_vibepulse_layout_wiring.py
git commit -m "Render cached VibePulse quotas as stale"
```

### Task 3A: Make chat attention counts truthful

**Files:**
- Modify: `tools/tokenserver/agent_status.py`
- Modify: `tools/tokenserver/test_agent_status.py`
- Modify: `components/app_tokens/agent_completion_policy.c`
- Modify: `components/app_tokens/usage_live_policy.c`
- Modify: `test/test_agent_completion_policy.c`
- Modify: `test/test_usage_live_policy.c`
- Modify deterministic multi-provider simulator fixtures/captures only where
  their approved semantics change.

- [ ] **Step 1: Write failing lease and provider-isolation tests**

Prove server-side `waiting`/`error` becomes `unknown` after exactly two hours
while a new event revives it. Prove `same_state_count` includes only the
triggering provider. Prove quota-header waiting/error context disappears when
the agent packet exceeds the existing 120-second lease.

- [ ] **Step 2: Implement the narrow static policy**

Add `WAITING_LEASE_S = 2 * 60 * 60` beside the existing working lease. Pass the
triggering provider into the count helper; do not add per-project filtering.
Apply agent packet age to waiting/error activity without changing the wire
format, motion, quota packet stale semantics, or completion queue identity.

- [ ] **Step 3: Verify and commit independently**

Run focused Python/C tests, the exact simulator capture gate, and the complete
host suite. Update changed deterministic capture semantics deliberately and
record that `N CHATS WAITING` is now per provider.

### Task 4: Generate and render one faithful Codex image

**Files:**
- Modify: `tools/agent_assets/build-agent-images.py`
- Modify: `tools/agent_assets/test_build_agent_images.py`
- Regenerate: `components/app_tokens/agent_assets.h`
- Regenerate: `components/app_tokens/agent_assets.c`
- Modify: `components/app_tokens/usage_screen.c`
- Modify: `components/app_tokens/agent_monitor.c`
- Modify: `test/test_vibepulse_layout_wiring.py`

- [ ] **Step 1: Write failing asset tests**

Replace the tuple contract with:

```python
large = build.build_codex(112)
small = build.build_codex(32)
self.assertEqual(len(large), 16 * 4 + 112 * 112 // 2)
self.assertEqual(len(small), 16 * 4 + 32 * 32 // 2)
self.assertEqual(large, build.build_codex(112))
```

Decode I4 in the test and assert index zero transparency, one exact white
palette entry, substantial blue and white pixels, non-rectangular alpha bounds,
no visible white plate or lavender fringe at the canvas corners, and no stretch
API in either Codex render path. Run generator tests from the full host gate and
verify regeneration produces no diff.

- [ ] **Step 2: Run the focused test and verify RED**

```bash
.venv/bin/python -m unittest tools.agent_assets.test_build_agent_images -v
```

Expected: tuple/descriptor assertions fail with the existing three-layer asset.

- [ ] **Step 3: Implement deterministic compositing**

Derive the colored cloud bounding box and the two enclosed white terminal
components from `codex-icon.png`. De-fringe the white plate, compose cloud and
glyphs before alpha-aware high-quality scaling, and generate directly at 112
and 32 px. Reserve I4 index zero for transparency and one exact palette entry
for white; quantize cloud pixels deterministically into the remaining 14
entries. Emit only `tk_img_codex` and `tk_img_codex_32`.

- [ ] **Step 4: Regenerate and simplify LVGL renderers**

Run the pinned generator. Replace both `create_codex_icon` layer loops with one
`lv_image_create`, one source descriptor, no recolor and no stretch transform.
Render the native 112 × 112 attention asset and native 32 × 32 header asset at
1:1 in their exact boxes.

- [ ] **Step 5: Verify generated scope and commit**

```bash
.venv/bin/python -m unittest tools.agent_assets.test_build_agent_images -v
PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh
git diff --check
git add tools/agent_assets/build-agent-images.py \
  tools/agent_assets/test_build_agent_images.py \
  components/app_tokens/agent_assets.h \
  components/app_tokens/agent_assets.c \
  components/app_tokens/usage_screen.c \
  components/app_tokens/agent_monitor.c \
  test/test_vibepulse_layout_wiring.py
git commit -m "Render the real Codex logo as one asset"
```

### Task 5: Produce exact live, stale, no-data, and logo evidence

**Files:**
- Modify: `sim/main.c`
- Modify: `test/test_preview_ui.py`
- Modify: `test/test_vibepulse_visual_landmarks.py`
- Modify: `docs/superpowers/reviews/2026-08-11-vibepulse-live-quota-polish.md`

- [ ] **Step 1: Add failing deterministic capture expectations**

Add exact captures for Codex 46% general weekly live, Claude cached Fable stale,
Codex cached weekly stale, Fable no-data with fixed label, and corrected Codex
attention. Update the exact allowlist before simulator implementation and verify
preview tests fail on the missing captures.

- [ ] **Step 2: Implement only deterministic simulator fixtures**

Construct `tk_tokens` directly; do not read the live service. Exercise stale
booleans, null model-week, the corrected general Codex value and the real
composited icon. Do not add animation checkpoints.

- [ ] **Step 3: Strengthen raster landmarks**

Assert the 112 × 112 Codex icon has black corners, source-blue gradient, exact
white terminal pixels, non-rectangular silhouette and no pixels outside the
box. Assert the 32 × 32 header asset independently has the same structural
landmarks. Assert stale/no-data text occupies the one reserved header slot and bars,
reset and delta never fabricate missing values.

- [ ] **Step 4: Run the complete static verification matrix**

```bash
.venv/bin/python tools/vibepulse_studio/design.py --check
PYTHON_BIN="$PWD/.venv/bin/python" ./test/run.sh
PYTHON_BIN="$PWD/.venv/bin/python" ./tools/preview-ui.sh vibepulse
.venv/bin/python test/test_vibepulse_visual_landmarks.py
source "$HOME/esp/esp-idf/export.sh"
idf.py build
```

Expected: all exit 0; all captures are 480 × 480; target remains within the
factory app partition.

- [ ] **Step 5: Inspect and show the actual images**

At 1:1 inspect and present Claude Fable live/stale/no-data, Codex general 46%
live/stale, small Codex header, large Codex needs-you and both provider attention
states. Reject any plate, broken glyph, clipped text, false live halo, extra row,
or approximate value.

- [ ] **Step 6: Record evidence and commit**

Document commands, artifact size, capture paths, the failed first physical gate,
and that the corrected physical gate remains pending. Commit only the review and
deterministic simulator/test changes.

```bash
git add sim/main.c test/test_preview_ui.py \
  test/test_vibepulse_visual_landmarks.py \
  docs/superpowers/reviews/2026-08-11-vibepulse-live-quota-polish.md
git commit -m "Verify VibePulse static fidelity fixes"
```

### Task 6: Repeat the physical static gate

- [ ] **Step 1: Present exact captures and request explicit USB authorization**

State that the current factory-only partition still has no OTA and that this is
a new device write. Do not infer authorization from the earlier flash.

- [ ] **Step 2: Flash only after authorization**

Resolve the exact `/dev/cu.usbmodem*` target and run `tools/flasha.sh`. Require
esptool exit 0 and verified image hashes.

- [ ] **Step 3: Inspect real AMOLED states**

Photograph the quota and attention states at normal desk distance. Verify real
Codex silhouette, Fable label, 46% general Codex weekly, stale/no-data wording,
provider colors, touch dismissal, long press, app switching, Solelkollen and
Vibbe startup.

- [ ] **Step 4: Record pass/fail before motion**

If any item fails, keep Task 6 motion blocked and return to the exact failing
task. Only a documented physical pass authorizes bounded animation work.

## Plan self-review

- Every approved icon, identity, persistence, provenance, privacy, reset,
  simulator, target and physical requirement maps to one task.
- The plan does not use usage history as the last-known-good source cache.
- The general Codex scanner remains bounded and asynchronous.
- Optional wire booleans preserve contract v2 compatibility.
- No task adds Spark UI, motion, audio, OTA, new rows or fabricated data.
- The user-owned dirty Claude export is absent from every commit command.
