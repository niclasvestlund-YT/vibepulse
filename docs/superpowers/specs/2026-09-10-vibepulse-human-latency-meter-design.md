# VibePulse human-latency meter

**Date:** 2026-09-10

**Status:** Draft, for maintainer review. Nothing here is implemented; this
authorizes no code, no simulator frame and no flash.

**Scope:** One new optional page, "Blocked on you", and the server-side
ledger that feeds it. Also records that the *panic stop* the same
brainstorm document names as "the one feature to build before all of them"
already shipped: KEY3 sends a signed deny-all to `/api/panic`
(`platform/button_arbitration.c`, `components/app_tokens/needs_you_net.c`,
`InteractionStore.panic`), so this spec does not re-design it.

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
and `outcome` in `{panel, computer, expired, panic, removed}`, mapped from
the removal reasons the store already produces. Two clocks on purpose:
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
uses, and a row contributes only the part of `[startedAt, endedAt]` that
overlaps that day: a wait parked at 23:50 and answered at 00:10 puts ten
minutes in yesterday and ten in today, in `todayS`, the provider totals
and `countToday` alike (a split row counts once, in the day it ended).
`longestTodayS` is the longest overlap, not the longest whole row. Day
boundaries are local wall-clock midnights (`datetime.astimezone()`), so
a DST day is 23 or 25 hours and the overlap follows it; nothing is
assigned by `startedAt` or `endedAt` alone. The block is about 110 bytes;
it must stay inside the device's
4096-byte body budget with the pending block and a full agent list
(`test/test_agent_status_body_capacity.py` is the gate). The firmware
ignores unknown root keys, so already-flashed panels are unaffected.

**One optional page, "Blocked on you".** Chosen through SETTINGS → LABS
(the mechanism PR #98 introduces; this page is not built until that lands)
and off by default. Layout per the approved mockup
`docs/img/mockups/latency-meter.png`: the provider header row, the label
`BLOCKED ON YOU · TODAY`, one dominant number in minutes, a bar split by
provider, and the two secondary figures `BLOCKED RIGHT NOW` (live, from
`blockedNowS`, mm:ss) and `LONGEST WAIT`. Dashes when the block is absent
(older server) or `countToday` is 0 and nothing is blocked now. The number
is **never framed as waste**: the label is what it measures, not a verdict
on the reader.

**What is counted, exactly.** Time between a hook parking an interaction
and that interaction ending, whatever ended it. Not counted: time an agent
sits idle at a prompt with nothing parked (unknown to the server), time
spent by the agent itself, and holds that were open when the tokenserver
restarted: `InteractionStore._pending` is memory-only, the ledger writes a
row only when an interaction ends, so the new process has no way to know
those holds existed and records nothing for them. No startup message
claims otherwise; a durable open-hold marker is open question 4. An
`expired` wait counts in full: the human was needed for the whole hold.

**Same data across the relay.** The numbers relay carries `/api/tokens`
and `/api/max-tracker`; `/api/agent-status` rides the encrypted status
relay when enabled. The `waits` block travels with it unchanged; a
relay-fed panel sees the same page. No new relay endpoint.

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
4. `AgentStatusService.snapshot()` asks the ledger for today's aggregates
   and the store for the oldest open `created_at`, and adds `waits`.
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
  negative duration is dropped rather than written.

## Visual gate

The page is AMOLED work: `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`
applies in full. Exact 480 × 480 simulator frames for: zero state (dashes),
a live blocked state with a running mm:ss, a day with both providers, a
day with one provider (bar is one colour), the relay-fed variant, and the
Labs off state (page absent, tiles dense). Static physical review before
any motion. No frame here authorizes a flash.

## Tests

Regression tests must prove:

- every ending path (panel answer, relay answer, computer fallback, expiry,
  panic) produces exactly one ledger row with the right outcome, and a
  store constructed fresh (the restart case) produces none for holds the
  previous process had open;
- a row's `durationS` comes from the monotonic pair and its day from the
  wall-clock pair: a wall-clock jump of an hour during a hold changes
  neither;
- aggregates roll over at local midnight, a wait spanning midnight is
  split by overlap into both days, a wait spanning a DST change is placed
  by local wall-clock boundaries, and the 8-day retention prunes;
- `blockedNowS` follows the oldest open interaction and drops to 0 on
  close;
- the persisted file and the payload carry no content fields (the
  denylist test above);
- the `/api/agent-status` body stays inside the device budget with `waits`
  plus a full pending block and agent list;
- a corrupt ledger is quarantined and a failing save shows on `GET /`;
- the firmware parser accepts the block, rejects a malformed one as absent
  without dropping the rest of the payload, and older payloads without it
  still parse (C host test);
- the page's landmark captures match the six frames above.

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
4. Should the store persist a privacy-safe open-hold marker (count and
   wall-clock start only, written at park and cleared at end) so a restart
   can say "N holds were open and are not measured"? Today it cannot know;
   the spec leaves that unmeasured rather than guessed.
