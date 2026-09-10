# VibePulse statusLine quota source

**Date:** 2026-09-10

**Status:** Draft, for maintainer review. Nothing in this document is
implemented, and it authorizes no code, setup change or flash.

**Scope:** Tokenserver Claude quota sourcing, the setup command's
`settings.json` edit, and the doctor/smoke/SessionStart classification of
the new source. Codex, the panel firmware, the relays and the wire
contract of `/api/tokens` are unchanged.

## Problem

Claude's session (5 h) and weekly percentages come from an undocumented
endpoint, `api.anthropic.com/api/oauth/usage`, called with an OAuth token the
tokenserver reads out of the keychain, the credentials file or a Claude
Desktop child process. That endpoint rate-limits polling hard. Everything
that makes the probe the most complex part of the server exists to survive
it: the 429 cooldown and its persisted state, the failure-streak backoff,
the dead-token set, the three credential sources and their expiry rules, the
header-probe fallback that costs a real request, the flock so two instances
do not double-probe, and the passive recovery loop from the 2026-08-23 spec.
`docs/lessons.md` has three entries about it (the 429 night, the expired
token that outranked a fresh login, the dead saved credential beside a live
source).

Claude Code already has the same numbers, locally, with no network call and
no credential. Its **statusLine** feature runs a command the user
configures and pipes it a JSON document on stdin that includes:

```json
"rate_limits": {
  "five_hour": { "used_percentage": 23.5, "resets_at": 1738425600 },
  "seven_day": { "used_percentage": 41.2, "resets_at": 1738857600 }
}
```

`resets_at` is Unix epoch seconds. The block is present for Claude.ai Pro
and Max subscribers (and behind a Claude apps gateway, where a third window
`spend_limit` appears), only after the session's first API response, and
each window is dropped once its `resets_at` passes. The command runs at
session start, on every new assistant message, after `/compact`, on
permission-mode and vim-mode changes, when a rate-limit window reaches its
`resets_at`, and optionally on a fixed `refreshInterval` timer; updates are
debounced at 300 ms and an in-flight run is cancelled by a newer trigger.
(Verified against `code.claude.com/docs/en/statusline` on 2026-09-10; the
details that matter for this design are restated here so the spec does not
depend on the page staying put.)

Two things the statusLine does **not** carry, and the design has to say so
because `docs/companion-features-brainstorm.md` claims the probe can be
deleted:

- **The heaviest-model weekly window.** The panel's third Claude ring is
  the Fable/Opus pool (`claudeModelWeekPct`, `claudeModelWeekLabel`). The
  statusLine has `five_hour` and `seven_day` only. The OAuth probe is today
  the only observed source that names that pool and its reset.
- **Usage from other devices while this computer is idle.** The statusLine
  fires on this machine's own activity. If the same account burns quota on
  a laptop, the shelf computer's last sample is stale until its next local
  turn. The probe sees the account, not the machine.

So the probe cannot go. What can go is its role as the *load-bearing*
source for the two windows everyone has, and with it the reason its
failure modes reach the glass.

## Chosen behavior

**A bridge, not a poller.** The setup command, with the user's explicit
consent, configures Claude Code's `statusLine.command` to run
`tools/tokenserver/statusline_bridge.py`. The bridge reads stdin, keeps
exactly `rate_limits.five_hour` and `rate_limits.seven_day` (each as
`used_percentage` and `resets_at`) plus the Claude Code `version` string,
and writes them atomically to one file in the tokenserver's state
directory, `claude-statusline-quota.json`, stamped with the bridge's own
wall-clock time. It prints nothing of its own to stdout.

**The user's status line keeps working.** `settings.json` allows one
`statusLine.command`. If one already exists, setup records it inside the
bridge's own configuration (the bridge never rewrites `settings.json`
itself) and the bridge executes it with the same stdin, passing its stdout
and exit status through unchanged. If none exists, the bridge exits 0 with
empty output, which Claude Code renders as no status line, the same as
before. Setup refuses to install the bridge when the existing command
cannot be represented (a non-string, a command containing a newline) and
says so, rather than guessing.

**Source order in the tokenserver**, for the general weekly and the session
window, newest honest observation wins:

1. The bridge file, when its sample is younger than
   `STATUSLINE_FRESH_S` (proposed 15 minutes) and its `resets_at` has not
   passed.
2. The OAuth probe, when it succeeded within its own interval.
3. The Claude Desktop plan-usage file, under the rules the 2026-08-23 spec
   already sets (general week only, reset borrowed from a still-valid cache
   record).
4. The quota cache, marked stale, as today.

The heaviest-model weekly window keeps today's order: probe, then cache.

**The probe becomes a background verifier.** While the bridge file is fresh,
the probe interval stretches to `PROBE_WHEN_BRIDGED_S` (proposed 30
minutes, up from 240 s), because its only unique contribution is the model
pool and cross-device drift, neither of which moves fast. On a 429 the
probe rests exactly as it does today; the glass no longer notices. When the
bridge file is missing or stale (no Claude Code turn on this computer for
a while), the probe returns to its current cadence automatically. No
probe code is deleted in this change; the interval rule is the whole
difference.

**Nothing is inferred.** A window absent from the bridge sample (the free
tier, an API-key session, a window past its reset) is absent from the
sample file too, and the merge treats it as "no observation", not zero.

## Data flow

1. The user runs `python3 tools/vibepulse_setup.py install` (or a new
   `--statusline` step). Setup shows the exact `settings.json` change and
   asks. On yes, it writes `statusLine.command` pointing at the bridge and
   stores any previous command in the bridge configuration file beside the
   sample file. On no, nothing changes and the doctor reports `OFF
   statusLine bridge: not installed`.
2. Claude Code runs the bridge on its normal triggers. The bridge writes
   `{"v": 1, "at": <epoch s>, "five_hour": {"pct": 23.5, "resets_at":
   1738425600}, "seven_day": {...}, "claude_code_version": "2.1.267"}`
   through the existing atomic-write discipline (`state_files`), 0600, in
   well under 100 ms, then runs the chained command if any.
3. The tokenserver's `_refresh_limits` reads the sample file (size-bounded,
   fail-closed validation like `_read_claude_plan_usage`) and builds the
   Claude quota view from the source order above. `GET /` reports
   `claudeStatusline: {status, ageS, claudeCodeVersion}` with statuses
   `fresh`, `stale`, `missing`, `invalid`, `not_installed`.
4. `/api/tokens` is byte-identical in shape. `claudeWeekStale` and
   `claudeSessionPct` come from whichever source won; `claudeModelWeekPct`
   keeps its probe-or-cache path. The Max Tracker records a bridge
   observation as live only under the same "fresh, live, with reset" gate
   the probe's observations pass today.
5. The doctor prints `PASS statusLine bridge: fresh (N s)` / `WAIT statusLine
   bridge: installed, no sample yet` / `FIX statusLine bridge: the
   configured command is not this checkout's bridge` / `OFF`. The smoke test
   mirrors it as OK / VARN / FAIL. The SessionStart hook adds no new class:
   a fresh bridge simply makes `PROVIDER DATA STALE (Claude)` rarer.

## Failure and privacy boundaries

- The bridge reads stdin **once**, size-bounded (the documented payload is
  a few kilobytes; cap at 64 KiB), and never logs it. The statusLine input
  also carries `cwd`, `transcript_path`, `session_id`, `model`, `cost`,
  `workspace.repo` and, on some builds, PR and worktree names. **None of it
  is written anywhere.** A test feeds a payload with every documented key
  and asserts the sample file contains only the five allowed keys.
- The bridge never contacts the network and never reads a credential.
- A bridge crash, a missing Python, or a full disk must not break the
  user's own status line: the chained command runs even when the sample
  write fails, and the bridge's own exceptions exit 0 silently (Claude Code
  treats status-line noise as output). The doctor is where failures show.
- Setup never installs the bridge without the user's explicit yes, never
  overwrites a foreign `statusLine.command` without recording it, and
  uninstall restores the recorded command or removes the key if none was
  recorded. `settings.json` edits go through the same read-modify-write
  with backup that the hook installation already uses.
- Two Claude Code sessions writing the file concurrently is last-writer-wins
  through `os.replace`; both samples are true, and the merge uses `at`.
- The sample file's `at` is the bridge's wall clock, compared against the
  tokenserver's wall clock on the same machine. Clock regression makes the
  sample stale by age, never fresh by mistake (`age_s < -60` rejects, as in
  the plan-usage reader).
- The 2026-08-23 recovery spec's rules stand: the probe still never sends a
  dead or expired token, and a 429 cooldown is never shortened. This spec
  only lengthens the probe interval while a better source is fresh.

## Tests

Regression tests must prove:

- the bridge keeps exactly `five_hour` and `seven_day` `used_percentage`
  and `resets_at` and the version, and drops every other documented stdin
  key, including nested ones;
- the bridge passes a chained command's stdout and exit status through
  unchanged and still runs it when the sample write raises;
- the bridge rejects a non-object, oversized or non-UTF-8 stdin with exit 0
  and no file written;
- the tokenserver prefers a fresh bridge sample over an older probe result
  and an older plan-usage sample, falls back to the probe when the sample
  is stale or its reset has passed, and never invents a model-pool
  percentage from the bridge;
- the probe interval is `PROBE_WHEN_BRIDGED_S` only while the sample is
  fresh and returns to the current ladder otherwise;
- a bridge observation reaches the Max Tracker only through the existing
  live gate;
- `GET /` reports the five bridge statuses; the doctor and smoke test map
  them as specified; the SessionStart context stays within its byte bound;
- setup shows the diff, refuses an unrepresentable existing command,
  records a chained command, and uninstall restores it;
- the `/api/tokens` body-capacity test still passes (no new wire fields).

## Acceptance

On a Pro or Max account with the bridge installed, the panel's Claude
session and weekly rings follow the statusLine within one panel poll of a
Claude Code turn, the probe's 429 cooldowns no longer produce a stale
Claude card while the bridge is fresh, the Fable/Opus ring behaves exactly
as before, and a user who declines the bridge sees no change at all.

## Open questions for the maintainer

1. `STATUSLINE_FRESH_S` and `PROBE_WHEN_BRIDGED_S`: 15 and 30 minutes are
   proposals. The trade is cross-device drift versus 429 exposure.
2. Should setup offer `refreshInterval` (Claude Code re-runs the bridge on
   a timer while idle)? It keeps the sample fresh across long idle periods
   at the cost of a process spawn every N seconds in every open session.
   The default in this spec is not to set it.
3. Windows: `statusLine.command` runs through the user's shell; the bridge
   must be invoked as `python <path>` there and the doctor must check the
   registered command matches this checkout, the same drift check the
   tokenserver source fingerprint does today.
