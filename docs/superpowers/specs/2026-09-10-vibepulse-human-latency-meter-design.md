# VibePulse human-latency meter

**Date:** 2026-09-10

**Status:** Draft, for maintainer review. Nothing here is implemented; this
authorizes no code, no simulator frame and no flash.

**Scope:** One new optional page, "Blocked on you", and the server-side
ledger that feeds it. Also records where the *panic stop* the same
brainstorm document names as "the one feature to build before all of them"
stands, so this spec does not re-design it: the first half shipped — KEY3
sends a signed deny-all to `/api/panic` (`platform/button_arbitration.c`,
`components/app_tokens/needs_you_net.c`, `InteractionStore.panic`) and
`deny_all()` denies **the interactions pending at that moment**, which
`test_authenticated_panic_denies_only_the_current_snapshot` pins on
purpose. The second half the brainstorm asks for, *holding a deny-all
flag until cleared* so nothing can park right after the press, is **not
implemented** and stays remaining work outside this spec; a hook can park
a new interaction one second after a panic today. The ledger records a
panic as `panic` for exactly the holds it denied and claims nothing
about later ones.

## Problem

VibePulse already knows the one number no other tool surfaces in the
moment it matters: how long an agent sat waiting for a human. Every NEEDS
YOU alert starts when the tokenserver parks a permission or question
(`InteractionStore.park`, `created_at`) and ends when it is answered on the
glass, answered on the computer, expired or panicked. Today those
timestamps exist for the lifetime of the hold and are then dropped. The
panel shows *that* an agent is waiting, never *how much* of the day went to
waiting.

`docs/companion-features-brainstorm.md` argues this is the thesis of the
whole product ("your agents waited 34 minutes on you today") and, after
adversarial review, that the moat is delivery rather than measurement:
Claude Code's own OpenTelemetry span `claude_code.tool.blocked_on_user`
measures the same thing, beta and off by default, with no dashboard
panelling it. The document's own verdict stands here: ship the fallback
that needs no telemetry, adopt the span when it leaves beta.

## Chosen behavior

**A ledger of waits, on the server, durations only.** When a parked
interaction ends, the store appends one row to a `WaitLedger`: provider
(`claude`/`codex`), kind (`approval`/`question`), `startedAt` and `endedAt`
as wall-clock epoch seconds, `durationS` measured on the monotonic clock,
and `outcome` in `{panel, computer, expired, panic, removed, restart}`. The mapping
is from the store's removal reason **and the verdict together**, because
the reason alone cannot tell a panel answer from a hand-back: a direct-LAN
`resolve` reports `resolved` for `approve`, `deny` and `leave_it` alike,
and the relay path reports `terminal` for its LEAVE IT. So: `resolved` +
`approve`/`deny` → `panel`; `resolved` + `leave_it` and `terminal` →
`computer`; the expiry sweep → `expired`; `panic` → `panic`; any other
removal (the computer answered first, the hook went away) → `removed`;
a hold the previous process left open, closed by the new process from
its marker (below) → `restart`.
Two clocks on purpose:
the store's existing `created_at` is monotonic (`InteractionStore._now`,
`time.monotonic` by default), which is right for expiry and elapsed time
and useless for "which day was that" — a row stamped with it would land
near 1970. So `_Pending` gains `started_wall`, captured from the store's
existing `_wall` clock at park time, and the row carries that for calendar
placement while the duration comes from the monotonic pair, so a
wall-clock jump during a hold neither stretches nor shrinks what was
measured. No request id, project,
session key, tool name, command, question text or verdict content is ever
written. The ledger persists to `interaction-waits.json` in the state
directory through `state_files` (atomic write, parent fsync, quarantine on
corruption), retains 8 days like `usage-history.json`, and loads on start.

**One additive block on `/api/agent-status`.** The 1 s poll already carries
`pending`; it gains `waits`:

```json
"waits": {"todayS": 2040, "longestTodayS": 660, "blockedNowS": 252,
          "claudeTodayS": 1500, "codexTodayS": 540, "countToday": 7}
```

`blockedNowS` is the age of the oldest still-parked interaction, or 0.
"Today" is the host's local calendar day, the same rule the Max Tracker
uses. **Open holds count too, but only what is on disk:** `todayS`, the
provider totals and `countToday` include, for every still-parked
interaction, the in-day part of its **last persisted checkpoint**
(`elapsedS` in the open-hold marker, below), computed the same way as a
closed row with `endedAt` taken as the checkpoint's moment — so the
day's first wait is visible on the hero while it is happening and not
only once it closes (a page saying `0` over a `BLOCKED RIGHT NOW` of
several minutes would be the invented-zero the honesty rule forbids),
and so the cumulative total never contains a second that a crash could
take back: the hero advances in steps of at most
`WAIT_MARKER_CHECKPOINT_S` and can trail the live age by that much,
which the spec prefers to a number that moves backward. `blockedNowS`
alone is the live age. `longestTodayS` follows the same rule: the
largest in-day part over closed rows **and** open holds' checkpoints, so
`LONGEST WAIT` can never read less than the **checkpointed in-day part**
of the current hold. It can legitimately read less than `BLOCKED RIGHT
NOW`, which is the hold's whole live age, for two reasons the page
accepts: the checkpoint lag, and midnight — at 00:10 a hold parked at
23:50 shows `blockedNowS` 1200 and contributes at most 600 to
`longestTodayS`, because the other 600 belong to yesterday. The provider
header names the providers of open holds as well. When an open hold
closes, its row replaces its checkpointed contribution with the full
measured duration; the total never steps back at that moment. Closed rows
contribute only the part of their measured duration that falls in the
day. The split is made on the interval
`[endedAt - durationS, endedAt]`, not on `[startedAt, endedAt]`: the
monotonic `durationS` is the measurement, `endedAt` is the one wall-clock
reading taken at the moment the row is written, and a wall-clock
correction during the hold (an NTP step, a manual change) can therefore
shift *where* the seconds land but never *how many* there are — a
ten-second hold across a one-hour forward step contributes ten seconds,
not 3 610. `startedAt` is kept for the record and is not used in
aggregation. A wait parked at 23:50 and answered at 00:10 puts ten
minutes in yesterday and ten in today, in `todayS`, the provider totals
and `countToday` alike (a split row counts once, in the day it ended).
`longestTodayS` is the longest in-day part, not the longest whole row,
over open holds and closed rows alike.
Day boundaries are local wall-clock midnights (`datetime.astimezone()`),
so a DST day is 23 or 25 hours and the split follows it. The block is
about 110 bytes;
it must stay inside the device's
4096-byte body budget with the pending block and a full agent list
(`test/test_agent_status_body_capacity.py` is the gate). The firmware
ignores unknown root keys, so already-flashed panels are unaffected.

**One optional page, "Blocked on you".** Chosen through SETTINGS → LABS
(the mechanism PR #98 introduces; this page is not built until that lands)
and off by default. Layout after the concept image
`docs/img/mockups/latency-meter.png` — concept art from the brainstorm
document, not a Studio capture and not an approved design; the approved
artefacts are the exact 480 × 480 shared-LVGL frames reviewed under the
visual gate below — with one correction the concept needs before it
becomes a frame: its header row reads `CLAUDE`, but the
dominant number is the sum over both providers. The header must name what
the number measures — `CLAUDE + CODEX` when both contributed today,
`CLAUDE` or `CODEX` when only one did, never one provider's name over a
combined total — and the split bar carries the per-provider share. Then
the label `BLOCKED ON YOU · TODAY`, one dominant number in minutes, the
bar split by provider, and the two secondary figures `BLOCKED RIGHT NOW`
(live, from `blockedNowS`, mm:ss) and `LONGEST WAIT`. Dashes when the
block is absent
(older server) or `countToday` is 0 and nothing is blocked now. The number
is **never framed as waste**: the label is what it measures, not a verdict
on the reader.

**What is counted, exactly.** Time between a hook parking an interaction
and that interaction ending, whatever ended it. Not counted: time an agent
sits idle at a prompt with nothing parked (unknown to the server), time
spent by the agent itself. Holds that were open when the tokenserver
restarted **are** counted, because they already counted live: the
number the panel showed must not step back when the process comes up
again — but only for the time actually observed, never for the outage.
`InteractionStore._pending` is memory-only, so the ledger keeps a
durable, privacy-safe **open-hold marker**: an `open` list in the same
persisted file with one `{provider, kind, startedAt, elapsedS}` per
parked interaction, where `elapsedS` is a **monotonic-derived
checkpoint** of the hold's elapsed time as of the last write. The writer
rewrites the list at every park and every ending, and, while any hold is
open, at least every `WAIT_MARKER_CHECKPOINT_S` (proposed 30 s), so the
checkpoint is never more than that far behind the live figure. A hold
open at a **clean** shutdown is closed by the final flush as a `restart`
row with its exact monotonic elapsed. After an **unclean** stop the new
process closes each surviving marker as a `restart` row with
`durationS` = the marker's `elapsedS` and `endedAt` = `startedAt +
elapsedS`, then clears the list: the row stops at the last durable
observation, so a process that dies with a hold parked and stays down
for hours charges the human the seconds it measured before it died and
nothing of the outage (the hook connection ended at the crash, and so
did the wait). And because the live aggregate above counts open holds
by the same checkpoint, a crash between checkpoints takes back nothing
the panel had already added to `todayS`: the total after the restart
equals the total before it, with only `blockedNowS` gone. A stale marker left by a close that
happened inside the writer window before a crash resolves the same way:
its checkpoint is at most the hold's true length, so a short completed
wait can be recorded short or absent, never long. No wall-clock
arithmetic produces a duration anywhere. An `expired` wait counts in
full: the human was needed for the whole hold.

**Same data across the relay.** The numbers relay carries `/api/tokens`
and `/api/max-tracker`; `/api/agent-status` rides the encrypted status
relay when enabled. The `waits` block travels with it unchanged; a
relay-fed panel sees the same page. No new relay endpoint. The relay's
frame is the tighter budget: `_prepare_status` rejects a canonical
payload over `MAX_STATUS_BYTES` (2560) outright, and the field-wise full
agent snapshot already measures 2 394 bytes, so the block is serialized
as **bounded integers**: every seconds field is a whole number of
seconds (floored) clamped to 999 999, `countToday` is clamped to 9 999,
never a float, never scientific notation. The worst-case block is then
132 bytes and the worst-case relay payload 2 526 bytes; the fixed-frame
test proves it rather than the spec assuming it (below).

## Data flow

1. A hook parks an interaction; the store records `created_at` from its
   monotonic clock as today, and additionally `started_wall` from its
   wall clock (`self._wall()`, already read there for the relay expiry).
2. The interaction ends. In the one place each ending already passes
   through (`resolve`, `resolve_relay`, `panic`, expiry sweep, computer
   fallback removal), the store calls `ledger.close(entry, outcome, now)`.
3. `WaitLedger` appends the row in memory and marks itself dirty; a
   background writer persists it with the same coalescing pattern as the
   Max Tracker (`_mark_max_tracker_dirty`), never on the hook's thread.
   **Durability boundary:** the writer runs within `WAIT_LEDGER_FLUSH_S`
   (proposed 2 s) of a close, and `main` gives the ledger the same final
   flush on shutdown the Max Tracker already gets (`max_tracker_store.save()`
   after `serve_forever` returns), so a clean stop loses nothing. An
   unclean stop (crash, power) can lose rows closed inside that last
   window, and a total the panel already showed can then be lower after
   the restart; the spec accepts that bound rather than writing on the
   hook's thread, and `GET /` reports `waits.rows` so the doctor can show
   the file's state. What is persisted for an open hold is only its
   marker (provider, kind, `startedAt`, checkpointed `elapsedS`),
   rewritten by the same writer at park, at ending and on the checkpoint
   cadence while anything is open, and closed by the final flush on a
   clean stop, so a restart closes it at the last observation rather
   than forgetting it or extending it to the next boot. The aggregate
   counts each open hold by that persisted checkpoint (the store keeps
   the value the writer last wrote beside the pending entry) and only
   `blockedNowS` by its live age.
4. `AgentStatusService.snapshot()` calls one method,
   `InteractionStore.wait_aggregates(now)`, in two steps. Under the
   store's own lock it takes one coherent **snapshot** — the ledger's
   rows that can touch today and a privacy-limited view of *all* pending
   entries (provider, kind, `started_wall`, the last persisted checkpoint
   and the live monotonic elapsed, nothing else) in the same critical
   section — and releases the lock. Rows are
   kept in `endedAt` order and a row touches today only if its `endedAt`
   is at or after today's local midnight, so that copy is a bisect plus a
   slice of today's tail, not a walk of the eight-day file. The overlap
   and local-calendar arithmetic then runs on the copy outside the lock,
   so a park, a resolve, the expiry sweep or a concurrent relay snapshot
   never queues behind the day arithmetic — the request-stalling shape
   `docs/lessons.md` records. `close` appends to the ledger under that
   same lock before the pending entry is dropped, so a snapshot can never
   observe the gap between "row not yet appended" and "hold no longer
   pending" and report a lower total for one second. Not only the oldest
   open hold is seen: with three agents parked at once, `todayS`, the
   provider totals, `countToday` and `longestTodayS` count all three,
   and `blockedNowS` is the largest elapsed among them. No new public API
   and nothing leaves the process; the method exists so the two sources
   are read together.
5. The firmware's agent-status parser reads `waits` optionally (all six
   fields numeric and non-negative, else the block is treated as absent),
   and the page renders it.
6. `GET /` reports `waits: {rows, oldestDay, saveOk}` for the doctor and
   the smoke test; a failing save uses the same FIX/VARN language as
   `maxTrackerSaveOk`.

## Failure and privacy boundaries

- The ledger holds durations and two enums. A test asserts the persisted
  file, the `/api/agent-status` block and every log line the `WaitLedger`
  itself emits contain none of: request ids, project names, session keys,
  tool names, commands, question text, verdict payloads, addresses. The
  store's existing audit line (`InteractionStore._log`, wired to the
  tokenserver logger in `main`) already names the request id, tool,
  project and session by design and is out of this spec's scope; the
  ledger adds no new line that repeats any of it.
- A ledger failure (disk full, corrupt file) never blocks a decision: the
  hook path calls `close` under the store lock but the write happens on the
  writer thread, and any exception in `close` is caught and reported on
  `GET /`, never raised into `resolve`.
- Silence, timeout, panel absence and computer fallback are still never
  approval. The ledger observes endings; it does not cause them.
- The block is additive. The `/api/agent-status` `v` stays 2; the pending
  block, the digest binding and the relay encryption are untouched.
- Clock regression on the host makes `blockedNowS` clamp at 0 and a
  negative duration is dropped rather than written; a `restart` row's
  duration is the checkpoint, monotonic-derived like every other, so a
  wall step between two processes moves only where its seconds land.

## Visual gate

The page is AMOLED work: `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`
applies in full. Exact 480 × 480 simulator frames for: zero state (dashes),
a live blocked state with a running mm:ss, a day with both providers, a
day with one provider (bar is one colour), the relay-fed variant, and the
Labs off state (page absent, tiles dense). Static physical review before
any motion. No frame here authorizes a flash.

## Tests

Regression tests must prove:

- every ending path produces exactly one ledger row with the right
  outcome: direct `approve` and `deny` → `panel`, direct `leave_it` and
  relay `terminal` → `computer`, relay `approve`/`deny` → `panel`, expiry
  → `expired`, panic → `panic`, other removal → `removed`; a store
  constructed fresh over a file whose `open` list has two markers (the
  unclean-restart case) produces exactly two `restart` rows whose
  `durationS` equals each marker's checkpoint — not the time to the new
  process's start, so a simulated three-hour outage adds nothing — and
  clears the list; a clean shutdown with a hold open writes its
  `restart` row with the exact monotonic elapsed and no marker; and a
  wall step between the two processes changes only the row's day
  placement;
- a park writes its marker within the writer window, an ending removes
  it, and an open hold's checkpoint advances at least every
  `WAIT_MARKER_CHECKPOINT_S`, so the persisted `open` list mirrors
  `_pending`; a simulated crash between two checkpoints leaves `todayS`,
  the provider totals, `countToday` and `longestTodayS` exactly where the
  panel last saw them (only `blockedNowS` drops to 0);
- an open hold is counted in `todayS`, its provider total, `countToday`
  and `longestTodayS` by its checkpoint: after the first checkpoint the
  day's first hold makes `LONGEST WAIT` equal the checkpointed part of
  `BLOCKED RIGHT NOW` and never 0, the hero never exceeds what the
  marker file holds, and at 00:10 a hold parked at 23:50 makes
  `blockedNowS` 1200 and `longestTodayS` at most 600; three concurrent
  holds across both providers are all counted and `blockedNowS` is the
  oldest; and closing one does not step the total back — including a
  close that races the 1 s snapshot, which a test drives by interleaving
  `close` between what would have been two separate reads and asserting
  the block is monotone;
- `wait_aggregates` holds the store lock only for the snapshot copy: a
  test with a large synthetic ledger asserts the lock is released before
  the day arithmetic runs (a park issued from another thread during the
  aggregation completes without waiting for it);
- a close followed by a clean shutdown before the writer ran is on disk
  after the final flush; a close followed by a simulated crash inside the
  writer window is absent after restart and the test names that as the
  accepted bound;
- a row's `durationS` comes from the monotonic pair and its days from
  `[endedAt - durationS, endedAt]`: a wall-clock jump of an hour during a
  hold changes neither the total nor the longest wait, and the day parts
  of every row always sum to its `durationS`;
- aggregates roll over at local midnight, a wait spanning midnight is
  split by overlap into both days, a wait spanning a DST change is placed
  by local wall-clock boundaries, and the 8-day retention prunes;
- `blockedNowS` follows the oldest open interaction and drops to 0 on
  close;
- the persisted file and the payload carry no content fields (the
  denylist test above);
- the `/api/agent-status` body stays inside the device budget with `waits`
  plus a full pending block and agent list, and
  `test_encrypted_status_strips_pending_and_fits_fixed_frame` gains the
  worst-case `waits` block (every seconds field 999 999, `countToday`
  9 999) beside the field-wise full agent snapshot and still fits
  `MAX_STATUS_BYTES`; a float or an over-clamp value never reaches the
  wire;
- a corrupt ledger is quarantined and a failing save shows on `GET /`;
- the firmware parser accepts the block, rejects a malformed one as absent
  without dropping the rest of the payload, and older payloads without it
  still parse (C host test);
- the page's landmark captures match the six frames above, and the header
  reads `CLAUDE + CODEX` in the both-providers frame and the single name
  in the one-provider frame.

## Acceptance

After a day with the page enabled, the panel shows the minutes agents
spent parked on a human that day, the longest single wait, a live count
while something is parked, and dashes when nothing has happened, with no
new content leaving the computer and no change for anyone who leaves the
page off.

## Open questions for the maintainer

1. Should a relay-answered wait be its own outcome (`relay`) so the page
   could one day split "answered on the glass" from "answered elsewhere"?
   The spec folds it into `panel`.
2. Is 8 days the right retention, or should the page eventually offer a
   week view like the Max Tracker? The ledger format allows it either way.
3. Adopt `claude_code.tool.blocked_on_user` when it leaves beta, or keep the
   hook-derived number as the single source to avoid two definitions of
   the same minute? The spec leans single source.
4. Should a `restart` row be shown apart from the others on the page one
   day? Its duration is the marker's monotonic-derived checkpoint and its
   synthetic end is `startedAt + elapsedS`, the last moment the old
   process observed the hold, not the human's answer and not the new
   process's start. The spec counts it in the totals so the number never
   steps back, and leaves any separate rendering to a later design.
