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
(`claude`/`codex`), kind (`approval`/`question`), `startedAt` as
wall-clock epoch seconds read once at park, `durationS` measured on the
monotonic clock, `endedAt` = `startedAt + durationS` (derived, never a
second wall reading),
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
"waits": {"todayS": 2040, "longestS": 660, "nowS": 252,
          "claudeS": 1500, "codexS": 540, "count": 7, "dayEndS": 30540}
```

The keys are short on purpose: the direct-LAN capacity gate
(`test/test_agent_status_body_capacity.py`) encodes the worst-case
snapshot with Python's default JSON separators and requires it to stay
under 80 % of the firmware's 4 096-byte envelope (3 276 bytes); with the
longer names this block would have pushed that fixture to 3 287 bytes,
so the contract is sized to the gate rather than the gate loosened.

`nowS` is the age of the oldest still-parked interaction, or 0.
"Today" is the host's local calendar day, the same rule the Max Tracker
uses, with one addition: **the served day never moves backward**. The
ledger keeps the latest local date it has served as today — **and
persists it**, as `servedDay` in the same file as the rows and markers,
written by the same writer, so a process started while the clock is
still regressed initialises from `max(wall date, servedDay)` rather
than from the regressed calendar — and when the wall clock regresses
across midnight (00:05 back to 23:55) it keeps serving that later date
— `dayEndS` then counts to the end of the held day, up to the 90 000
clamp — until the clock passes it again, and `GET /` reports
`waits.dayHeld` while it does. The hold is **bounded to one midnight**:
it covers the regression a clock correction produces, not a clock that
was wrong by a year. If the wall date falls more than one day behind
`servedDay` — the clock jumped forward, served a snapshot dated in the
future, and was then corrected — the ledger releases the hold: it resets
`servedDay` to the wall date, logs the discontinuity once, and reports
`waits.dayReset` on `GET /`; rows anchored to the future date simply
never overlap a real day again and age out with retention, and the page
shows the real day's waits from the next poll rather than an empty
future day for as long as the clock error was large. The single wall anchor
keeps rows from relocating; this rule keeps "today" itself from
relocating, so `todayS` cannot change on a backward step even though
the host's calendar briefly says an earlier date. **Open holds count too, but only what is on disk:** `todayS`, the
provider totals and `count` include, for every still-parked
interaction, the in-day part of its **last persisted checkpoint**
(`elapsedS` in the open-hold marker, below), computed the same way as a
closed row with `endedAt` taken as the checkpoint's moment — so the
day's first wait is visible on the hero while it is happening and not
only once it closes (a page saying `0` over a `BLOCKED RIGHT NOW` of
several minutes would be the invented-zero the honesty rule forbids),
and so the cumulative total never contains a second that a crash could
take back: the hero advances in steps of at most
`WAIT_MARKER_CHECKPOINT_S` and can trail the live age by that much,
which the spec prefers to a number that moves backward. `nowS`
alone is the live age. `longestS` follows the same rule: the
largest in-day part over closed rows **and** open holds' checkpoints, so
`LONGEST WAIT` can never read less than the **checkpointed in-day part**
of the current hold. It can legitimately read less than `BLOCKED RIGHT
NOW`, which is the hold's whole live age, for two reasons the page
accepts: the checkpoint lag, and midnight — at 00:10 a hold parked at
23:50 shows `nowS` 1200 and contributes at most 600 to
`longestS`, because the other 600 belong to yesterday. The provider
header is derived from the provider totals alone — the block carries no
presence flags and the encrypted relay strips `pending`, so a relay-fed
panel has nothing else to read — and so a freshly parked hold, whose
first checkpoint is near zero, can leave `count` at 1 and
`nowS` running while both provider totals still read 0 until the
next checkpoint; in that state the header reads the neutral `AGENTS`
rather than guessing a name, and it becomes `CLAUDE`, `CODEX` or
`CLAUDE + CODEX` as soon as a total is non-zero. When an open hold
closes, the aggregate keeps publishing its **checkpointed** contribution
until the writer has saved the row; only then does the full measured
duration replace it. The rule is the same one throughout: the hero
contains persisted seconds only, and the total never steps back at the
close, at the save, or at a crash between the two. Closed rows
contribute only the part of their measured duration that falls in the
day. The split is made on the interval
`[startedAt, startedAt + durationS]`: the monotonic `durationS` is the
measurement and `startedAt` — the **one** wall-clock reading a hold ever
takes, at park — is the only calendar anchor, for the checkpointed open
hold, the closed row and the `restart` row alike. No second wall reading
is taken at close, because two anchors would let a clock correction
between them move seconds across midnight at the handoff: a checkpoint
just after 00:05 counted in today, then a ten-minute backward step before
the close, would otherwise land the finished row in yesterday and step
`todayS` back. With one anchor a wall-clock correction during the hold
(an NTP step, a manual change) changes neither *how many* seconds there
are nor *where* they land — a ten-second hold across a one-hour forward
step contributes ten seconds, not 3 610, in the day the park was in.
What a correction can do is date a hold by a clock that was later found
wrong; the spec accepts that as the honest reading of the clock at the
time. A wait parked at 23:50 and answered at 00:10 puts ten
minutes in yesterday and ten in today, in `todayS`, the provider totals
and `count` alike (a split row counts once, in the day it ended).
`longestS` is the longest in-day part, not the longest whole row,
over open holds and closed rows alike.
Day boundaries are local wall-clock midnights (`datetime.astimezone()`),
so a DST day is 23 or 25 hours and the split follows it. The block is
about 110 bytes;
it must stay inside the device's
4096-byte body budget with the pending block and a full agent list
(`test/test_agent_status_body_capacity.py` is the gate). The firmware
ignores unknown root keys, so already-flashed panels are unaffected.

**One optional page, "Blocked on you".** Chosen through SETTINGS → LABS
(the optional-display mechanism PR #98 landed on main on 2026-09-12; this
page is one more entry in that list)
and off by default. Layout after the concept image
`docs/img/mockups/latency-meter.png` — concept art from the brainstorm
document, not a Studio capture and not an approved design; the approved
artefacts are the exact 480 × 480 shared-LVGL frames reviewed under the
visual gate below — with one correction the concept needs before it
becomes a frame: its header row reads `CLAUDE`, but the
dominant number is the sum over both providers. The header must name what
the number measures — `CLAUDE + CODEX` when both contributed today,
`CLAUDE` or `CODEX` when only one did, the neutral `AGENTS` when
something is counted but no provider total is non-zero yet, never one
provider's name over a combined total — and the split bar carries the
per-provider share. Then
the label `BLOCKED ON YOU · TODAY`, one dominant number in whole minutes
(floored) — except that a total under 60 seconds while something is
counted (`count > 0`) reads `<1 MIN` and never `0`. `count`
counts a hold only once its marker is on disk, so for the writer window
after a park (at most `WAIT_LEDGER_FLUSH_S`) the hero shows dashes while
`BLOCKED RIGHT NOW` already runs: that is "no durable data yet", which
dashes mean, and a crash in that window makes the restart show the same
dashes rather than take back a `<1 MIN` the file never held. That covers 1 to 59 seconds, which a short
completed wait or the first checkpoint of a live one produces, **and**
the wire's own 0: a wait that ended in under a second has a positive
monotonic `durationS`, counts once in `count`, and floors to 0 on
the wire, so the presentation keys on `count`, not on the seconds.
`LONGEST WAIT` follows suit: `<1s` when `count > 0` and
`longestS` is 0. A zero the measurement did not contain is the
invented zero this spec forbids, and dashes are reserved for no durable
data (`count` 0, whether or not something is blocked) —
the bar split by provider, and the two secondary figures `BLOCKED RIGHT NOW`
(live, from `nowS`, mm:ss) and `LONGEST WAIT`. Dashes when the
block is absent
(older server) or `count` is 0 and nothing is blocked now.
**Stale is shown, never guessed.** The agent-status client keeps the last
accepted snapshot when a poll fails (`agent_net.c`, by design), so
without a rule a wait that closed while the service was unreachable
would stay on the glass as `BLOCKED RIGHT NOW` for as long as the outage
lasts, with frozen totals. The page therefore keeps the monotonic time
of the last accepted `waits` block and, once it is older than
`TK_WAITS_STALE_MS` (proposed 20 000 ms, the same boundary the relay
source policy uses in `TK_AGENT_RELAY_STALE_MS`), renders `BLOCKED RIGHT
NOW` as dashes — a live number the panel cannot verify is not shown —
and keeps the day totals and `LONGEST WAIT` visible with the page's
stale marker (`STALE`, the treatment the other pages use for a source
that stopped answering), so the reader sees a number *as of* the last
answer, not a claim about now. The relay-fed variant follows the same
rule with the same debit the day countdown makes below: exact relay age
is unavailable, so the stale budget for a relay-fed block starts at
`TK_WAITS_STALE_RELAY_MS - RELAY_AGE_BOUND_MS - request duration` from
accept — the same three terms the day countdown subtracts, the fetch
included — where `RELAY_AGE_BOUND_MS` is the clock-independent 42 000 ms
derived below and `TK_WAITS_STALE_RELAY_MS` is proposed at 60 000 — a
relay-fed live label therefore lasts at most 18 s past accept, less the
fetch, and never more than 60 s
past the snapshot's build, and the spec says so rather than promising
the LAN's 20 s over a path that cannot deliver it; on the LAN the budget
starts at `TK_WAITS_STALE_MS` (20 000 ms) minus the measured request
duration. **Yesterday is never shown as
today:** the block carries `dayEndS`, the whole seconds until the
host's next local midnight (DST-correct, at most 90 000), because the
panel cannot infer the host's calendar boundary from its own clock,
least of all over the relay. `dayEndS` is relative to the moment the
server built the block, so the page first subtracts the block's **age at
accept** and only then counts down on its monotonic clock. On the LAN
that age is the poll's own request duration, which the direct poller
allows up to its 2 500 ms HTTP timeout (`agent_net.c`), so the page
records the request on its monotonic clock from send to accept and
starts the countdown at `dayEndS` minus that whole duration, rounded
up to a whole second — the server's floor and the round-up both err
early, never late. Over the relay the age of an accepted frame is
bounded **by server clocks only**, never by the panel's:
`decode_status_snapshot` checks the envelope's expiry against the
panel's own `time(NULL)` without establishing that the two clocks
agree, so neither `expires_at` nor an age estimated from it is a bound
a lagging panel can trust. The bound comes from the two ends the panel
does not control. First, the tokenserver **rebuilds** a status envelope
before every upload attempt — `_prepare_status` today builds it once
and the retry path can re-upload the same aged bytes, which this spec
changes: an envelope older than `STATUS_EXPIRY_S` (15 s) on the
tokenserver's own monotonic clock is discarded and rebuilt with fresh
`expires_at` and fresh `dayEndS` before it is sent, so a frame is at
most 15 s old when the PUT *starts* — and an envelope is likewise
discarded and rebuilt, whatever its age, when the ledger's `servedDay`
has changed since it was built, because a block that still carries
yesterday's totals and a `dayEndS` counting to a midnight that has
already passed is not one the panel's transit debit can correct, and a
subsequent outage would keep yesterday under `BLOCKED ON YOU · TODAY`. Second, the PUT itself takes time
that the worker's clock does not see, and `index.ts` starts the mailbox
TTL only once it has received and hashed the body — so the PUT gets a
**true end-to-end deadline**, `RELAY_PUT_BOUND_MS` (proposed 7 000 ms),
enforced on the tokenserver's monotonic clock rather than assumed from
socket timeouts: `_default_transport` today applies separate connect
and read timeouts, which DNS, TLS, a trickling request body and the
response can each stretch past their sum, so this spec has the status
PUT set every socket operation's timeout to the *remaining* budget and
abandon the attempt (close the connection, count a failure) the moment
the deadline passes. A frame the worker stored was therefore received
within the deadline — a store that happens before the client's abandon
is still inside it — and one the worker did not store carries no age
at all. Third, the mailbox serves a frame for at most
`STATUS_TTL_MS` (20 000 ms in `mailbox.ts`) on the worker's clock,
counted **from the moment the worker received the request**, not from
the later `Date.now()` `putStatus` passes today after authorisation,
body parsing and hashing (`index.ts`): the client's deadline bounds
nothing that happens inside the worker after the body arrived, so this
spec has the handler take `receivedAt` before its first `await` and
hand that to `mailbox.putStatus` as the TTL start, and any worker-side
delay after receipt then eats into the 20 s rather than extending the
frame's life. A worker test pins it (a handler stalled for 5 s after
receipt stores a frame that expires 20 s after receipt, not 25). An accepted frame is therefore at most 15 + 7 + 20 = 42 s old at
the moment the panel's fetch completes, plus the fetch itself, which
the panel measures on its monotonic clock. The countdown starts at
`dayEndS - RELAY_AGE_BOUND_MS/1000 - request duration` with
`RELAY_AGE_BOUND_MS` = 42 000, needs no clock but the panel's monotonic
one, and can only end early, never late — at worst the page shows
`DAY ENDED` 42 s before the host's midnight, and the next accepted block
(the relay publishes every second or so) replaces it within seconds.
The three server-side constants are pinned by a test
(`STATUS_EXPIRY_S * 1000 + RELAY_PUT_BOUND_MS + STATUS_TTL_MS ==
RELAY_AGE_BOUND_MS`) so a later change to any of them cannot silently
loosen the bound. A result of zero or less means the host's
day may have ended in transit and the block is rendered as day-ended on
arrival, never as today. Once the countdown reaches zero a
retained block is no longer rendered as today's measurement: the totals
and `LONGEST WAIT` become dashes with the stale marker (`DAY ENDED`),
and a fresh accepted block — which the server has already rolled over —
replaces them. The number
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
open at a **clean** shutdown is closed as a `restart` row with its exact
monotonic elapsed by a **locked shutdown transition**, not by a flush
racing live handlers: `serve_forever()` returning does not quiesce the
daemon handler threads (`BoundedThreadingHTTPServer` sets
`daemon_threads = True` and `block_on_close = False`, and
`server_close()` runs after the final saves today), so a handler could
still resolve or expire the hold while the flush converts it and append
a second row, or land after the last save. So `main` first calls
`store.begin_shutdown()`, which under the store lock marks the store
closing — every later `park`, `resolve`, `resolve_relay`, `panic` and
sweep returns "shutting down" without touching `_pending` — converts
every pending entry to its `restart` row in that same critical section,
and drops the entries; only then does the ledger's final flush run, and
`server_close()` after it. After an **unclean** stop the new
process closes each surviving marker as a `restart` row with
`durationS` = the marker's `elapsedS` and `endedAt` = `startedAt +
elapsedS`, then clears the list: the row stops at the last durable
observation, so a process that dies with a hold parked and stays down
for hours charges the human the seconds it measured before it died and
nothing of the outage (the hook connection ended at the crash, and so
did the wait). And because the live aggregate above counts open holds
by the same checkpoint, a crash between checkpoints takes back nothing
the panel had already added to `todayS`: the total after the restart
equals the total before it, with only `nowS` gone. A stale marker left by a close that
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
seconds (floored) clamped to 999 999, `count` is clamped to 9 999,
`dayEndS` to 90 000, never a float, never scientific notation. The
worst-case block is then 121 bytes in the relay's compact encoding and
the worst-case relay payload 2 515 bytes, 45 under the frame; on the
direct path the worst-case snapshot plus pending item plus this block
encodes to 3 257 bytes with the capacity test's default separators, 19
under its 80 % headroom gate. Both tests prove it rather than the spec
assuming it (below), and any further field must first be paid for in
both.

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
   after `serve_forever` returns), preceded by the locked shutdown
   transition above so no handler can add a row after it, so a clean
   stop loses nothing. An
   unclean stop (crash, power) can lose a row closed inside that last
   window — but not the seconds the panel showed for it: rows and
   open-hold markers live in the **same file** and one atomic write
   moves a hold from marker to row, so a crash inside the window leaves
   the marker, with its checkpoint, on disk, and the next start closes
   it as a `restart` row worth exactly what the aggregate was still
   publishing. The row's full duration is exposed only once that write
   has happened: `close` records the row as *unsaved* and the aggregate
   counts it by its last checkpoint until a write **that included that
   row** completes. Acknowledgements are per generation, not "the
   writer ran": the writer takes its snapshot under the store lock
   together with a generation number, and on completion marks saved
   exactly the rows and marker checkpoints that were in that snapshot —
   a row closed after the snapshot was taken, while the atomic write was
   still in flight, stays unsaved until the trailing write that carries
   it completes, so a crash between the two cannot leave the glass ahead
   of the disk. The on-disk state is therefore never behind the glass by more than
   `nowS`. `GET /` reports `waits.rows` and `waits.unsaved` so
   the doctor can show the file's state. What is persisted for an open
   hold is only its marker (provider, kind, `startedAt`, checkpointed
   `elapsedS`), rewritten by the same writer at park, at ending and on
   the checkpoint cadence while anything is open, and closed by the
   final flush on a clean stop, so a restart closes it at the last
   observation rather than forgetting it or extending it to the next
   boot. The aggregate counts each open or unsaved hold by that persisted
   checkpoint (the store keeps the value the writer last wrote beside
   the entry) and only `nowS` by its live age.
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
   provider totals, `count` and `longestS` count all three,
   and `nowS` is the largest elapsed among them. No new public API
   and nothing leaves the process; the method exists so the two sources
   are read together.
5. The firmware's agent-status parser reads `waits` optionally (all seven
   fields numeric and non-negative, else the block is treated as absent),
   stamps the accepted block with the monotonic clock, and the page
   renders it, or its stale form once `TK_WAITS_STALE_MS` has passed
   without a newer accepted block.
6. `GET /` reports `waits: {rows, unsaved, open, oldestDay, saveOk,
   dayHeld, dayReset}` — rows on disk, rows closed but not yet written,
   open-hold markers, the oldest retained day, the last save's outcome,
   whether the served day is currently held past a regressed clock, and
   the wall-clock time of the last future-day release (`null` when none
   has happened since the file was created; kept until the next release
   overwrites it, so the doctor can tell a deliberate reset from an
   unexpectedly empty ledger for as long as the question can arise) —
   the one schema the durability and day-policy sections refer to, for
   the doctor and the smoke test; a failing save uses the same FIX/VARN
   language as `maxTrackerSaveOk`, and the smoke test asserts all seven
   keys are present and `dayReset` is `null` or an epoch.

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
- Clock regression on the host makes `nowS` clamp at 0 and a
  negative duration is dropped rather than written; a `restart` row's
  duration is the checkpoint, monotonic-derived like every other, so a
  wall step between two processes moves only where its seconds land.

## Visual gate

The page is AMOLED work: `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`
applies in full. Exact 480 × 480 simulator frames for: zero state (dashes),
a live blocked state with a running mm:ss and the hero at `<1 MIN` (the
first checkpoint of the day's first wait), a day with both providers, a
day with one provider (bar is one colour), the relay-fed variant, the
stale state and the day-ended state — **both rendered from the same
non-zero fixture as the both-providers frame**, as the skill requires,
so provenance is the only visual change between live, stale (totals
with the `STALE` marker, `BLOCKED RIGHT NOW` as dashes) and day-ended
(every total dashed under the wider `DAY ENDED` marker, a different
layout from stale that must be seen to fit before it ships), and a
capture cannot hide or mislay a measurement behind the treatment —
the broad-number state (every seconds field at the
wire maximum of 999 999 and `count` at 9 999, so the hero reads a
five-digit minute count and `LONGEST WAIT` and `BLOCKED RIGHT NOW` read
`16666:39` — the skill's broad-number check, without which a capture
set can pass while clipping a valid measurement), and the Labs off
state (page absent, tiles dense). Static physical review before any
motion. No frame here authorizes a flash.

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
  wall step between the two processes changes nothing about the row —
  its placement derives from the persisted `startedAt` and the
  checkpoint, never from the new process's clock — while the served
  logical day may differ;
- a park writes its marker within the writer window, an ending removes
  it, and an open hold's checkpoint advances at least every
  `WAIT_MARKER_CHECKPOINT_S`, so the persisted `open` list mirrors
  `_pending`; a simulated crash between two checkpoints leaves `todayS`,
  the provider totals, `count` and `longestS` exactly where the
  panel last saw them (only `nowS` drops to 0);
- an open hold is counted in `todayS`, its provider total, `count`
  and `longestS` by its checkpoint: after the first checkpoint the
  day's first hold makes `LONGEST WAIT` equal the checkpointed part of
  `BLOCKED RIGHT NOW` and never 0, the hero never exceeds what the
  marker file holds, and at 00:10 a hold parked at 23:50 makes
  `nowS` 1200 and `longestS` at most 600; three concurrent
  holds across both providers are all counted and `nowS` is the
  oldest; and closing one does not step the total back — including a
  close that races the 1 s snapshot, which a test drives by interleaving
  `close` between what would have been two separate reads and asserting
  the block is monotone — and the closed row's full duration appears in
  the totals only after the writer has saved it, its checkpoint
  contribution standing in until then;
- `wait_aggregates` holds the store lock only for the snapshot copy: a
  test with a large synthetic ledger asserts the lock is released before
  the day arithmetic runs (a park issued from another thread during the
  aggregation completes without waiting for it);
- a close followed by a clean shutdown before the writer ran is on disk
  after the final flush; a resolve that races the shutdown transition
  (issued from a handler thread after `begin_shutdown` took the lock)
  returns "shutting down" and adds no second row, and the file after the
  final flush has exactly one row per hold; a restart while the clock is
  still regressed across midnight initialises today from the persisted
  `servedDay` and leaves `todayS` unchanged; a clock that jumped a year
  forward, served a snapshot and was corrected releases the hold on the
  next snapshot (`waits.dayReset`) and today's waits reappear; a close
  that lands while the atomic write is blocked (a test holds the file
  lock) stays unsaved through that write's completion and is
  acknowledged only by the trailing write; a close followed by a simulated crash inside the
  writer window leaves the row absent but the marker present, the next
  start closes the marker as a `restart` row worth its checkpoint, and
  `todayS`, the provider totals and `longestS` after the restart
  equal what the aggregate published before the crash;
- a row's `durationS` comes from the monotonic pair and its days from
  `[startedAt, startedAt + durationS]`: a wall-clock jump of an hour
  during a hold changes neither the total nor the longest wait nor the
  day placement, a backward step across local midnight between a
  checkpoint and the close leaves `todayS` exactly where it was because
  the served day is held (the open-to-closed handoff test, which also
  asserts `waits.dayHeld` on `GET /` and a `dayEndS` counting to the
  held day's end), `endedAt` equals `startedAt + durationS`
  on every row, and the day parts of every row always sum to its
  `durationS`;
- aggregates roll over at local midnight, a wait spanning midnight is
  split by overlap into both days, a wait spanning a DST change is placed
  by local wall-clock boundaries, and the 8-day retention prunes;
- `nowS` follows the oldest open interaction and drops to 0 on
  close;
- the persisted file and the payload carry no content fields (the
  denylist test above);
- the `/api/agent-status` body stays inside the device budget with `waits`
  plus a full pending block and agent list, and
  `test_worst_case_snapshot_plus_pending_fits_the_device` and
  `test_encrypted_status_strips_pending_and_fits_fixed_frame` both gain
  the worst-case `waits` block (every seconds field 999 999, `count`
  9 999, `dayEndS` 90 000) beside the field-wise full agent snapshot and
  still pass their own bounds — the 80 % headroom gate on the direct
  path and `MAX_STATUS_BYTES` on the relay; a float or an over-clamp
  value never reaches the wire;
- a corrupt ledger is quarantined and a failing save shows on `GET /`;
- the firmware parser accepts the block, rejects a malformed one as absent
  without dropping the rest of the payload, and older payloads without it
  still parse (C host test);
- the page's stale rule (C host test on the page model): a retained
  LAN snapshot older than `TK_WAITS_STALE_MS` minus its request duration
  renders `BLOCKED RIGHT NOW` as dashes and the totals with the stale
  marker, a relay-fed block does so `TK_WAITS_STALE_RELAY_MS -
  RELAY_AGE_BOUND_MS - request duration` after accept (a 4 s fetch goes
  stale 4 s sooner), so no live label outlives 20 s (LAN) or 60 s
  (relay) from the snapshot's build — a newer accepted block
  clears it, a snapshot that was never accepted shows the absent state,
  not stale, and a retained block whose `dayEndS` has counted down to
  zero renders the totals as dashes with `DAY ENDED` until a newer block
  arrives; the header reads `AGENTS` for a block with `count` 1 and
  both provider totals 0; the hero reads `<1 MIN` for `todayS` 1 to 59,
  dashes for 0 with nothing blocked, and whole floored minutes above;
- the day countdown over the relay starts at `dayEndS -
  RELAY_AGE_BOUND_MS/1000 - request duration` with no wall clock
  involved: a relay frame built one second before the host's midnight
  and accepted five seconds later renders day-ended on arrival, and so
  does one accepted with the panel clock set 30 s behind the host's, and
  so does a frame that sat in the mailbox for its full `STATUS_TTL_MS`;
  the tokenserver never starts a PUT with an envelope older than
  `STATUS_EXPIRY_S` on its monotonic clock (a retry after the window
  rebuilds it with a fresh `dayEndS`), nor one built before the served
  day changed (a retry that straddles midnight carries the new day's
  block, asserted by advancing the clock between build and retry), and a PUT that ran to its full
  connect-plus-read timeout still lands inside the bound;
  `STATUS_EXPIRY_S * 1000 + RELAY_PUT_BOUND_MS + STATUS_TTL_MS ==
  RELAY_AGE_BOUND_MS` is asserted; a LAN block subtracts the measured
  request duration rounded up (a 2 400 ms poll of a block with `dayEndS`
  2 renders day-ended on arrival); a block with `dayEndS` 40 over the
  relay is day-ended on arrival and at most 42 s early;
- the hero reads `<1 MIN` and `LONGEST WAIT` reads `<1s` for a block
  with `count` 1 and every seconds field 0 (a sub-second wait
  floored on the wire), and dashes for `count` 0 even while
  `nowS` runs (the writer window after a park), never `0`;
- `dayEndS` is the whole seconds to the host's next local midnight,
  23 or 25 hours across a DST change, never more than 90 000;
- the page's landmark captures match the nine frames above, the
  broad-number frame shows every glyph inside its box with no overlap
  (a pixel test on the capture, not only a landmark), and the header
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
   end is `startedAt + elapsedS` like every other row's, the last moment
   the old process observed the hold, not the human's answer and not the
   new process's start. The spec counts it in the totals so the number never
   steps back, and leaves any separate rendering to a later design.
