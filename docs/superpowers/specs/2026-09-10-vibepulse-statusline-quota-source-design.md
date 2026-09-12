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
consent, configures Claude Code's `statusLine.command` to run a small
launcher (`statusline_bridge.sh`, `.cmd` on Windows) that starts
`tools/tokenserver/statusline_bridge.py` with the interpreter setup
verified. The bridge reads stdin, keeps exactly `rate_limits.five_hour`
and `rate_limits.seven_day` (each as `used_percentage` and `resets_at`)
plus the Claude Code `version` string and an **account fingerprint**
(below), and writes them atomically to one
file in the tokenserver's state directory, `claude-statusline-quota.json`.
**Every retained field is validated strictly before it can touch the
file**, in the spirit of `docs/lessons.md`'s hostile-input entry:
`used_percentage` must be a finite number (not a bool) in `[0, 100]`,
`resets_at` a finite integer epoch that is in the future and no more
than eight days ahead, `version` a short printable string; a window that
fails any of it is treated as **absent from stdin** (the stored window
survives untouched), and a payload whose every window fails is treated
as a session-start run with no `rate_limits`. A lying value therefore
never replaces a last-known-good one, and one bad window never
invalidates the other.
**Per account, per window, not per invocation:** the file holds one
entry per account fingerprint (an invocation without a fingerprint goes
under `unknown`), and the bridge merges only into the entry of the
fingerprint it carries — the other accounts' windows are neither read
into the merge nor relabelled, so a session-start run from account B with
no `rate_limits`, or B's lower same-reset sample, can never retain A's
windows under B's name and hand A's quota to B's probe. An entry whose
windows have all expired is dropped. Each window carries two timestamps
with two jobs: `at`, the bridge's wall-clock time when the **winning
value** was observed, and `seen`, the wall-clock time the window was
**last reported** by any valid run, replay or not. The bridge reads the
existing file first; a window absent from stdin keeps
its previous entry; a window present replaces the stored one when its
`resets_at` is newer (a new window), or when the reset matches and the
percentage is higher; a replay of the same window with the same or a
lower percentage leaves the stored value **and its `at`** untouched and
advances only `seen`, so a cached value re-emitted by a non-API trigger
neither regresses the figure nor claims to be a newer observation of it
— while the bridge still counts as alive, because a weekly window that
sits at the same percentage for an hour of real turns is the normal
case, not a dead bridge; and a window whose
`resets_at` has passed is dropped. That matters because the statusLine runs at
session start *before* the session's first API response, with no
`rate_limits` at all: an invocation like that must not erase the fresh
sample another open session wrote seconds earlier. The whole
read-merge-replace runs under an interprocess lock on a sibling lock file
(`flock` on macOS/Linux, `msvcrt.locking` on Windows — the same gate
`_hold_probe_lock` uses), non-blocking with a short bounded wait, because
`os.replace` makes only the rename atomic: two bridges that read the same
old file and rename in turn would let the loser put back a stale copy of
a window the winner had just updated. A bridge that cannot take the lock
within the bound skips the write, runs the chained command, and exits 0;
the next trigger is seconds away. A stored file that is not UTF-8, not
JSON or not the `{v: 1, …}` shape is moved aside through
`state_files.quarantine_corrupt` (same rule as the three state stores,
bytes kept for forensics) and the merge starts from empty; a file that
exists but cannot be read (permissions, I/O) makes the bridge skip the
write rather than replace what it could not read. It prints nothing of
its own to stdout.

**Bound to one account, or not merged.** The statusLine payload names
no account, and the tokenserver's probe picks its own credential
(Claude Desktop's injected process token, the keychain entry, or the
credentials file), so nothing guarantees the two describe the same quota:
a Desktop signed in as one account and a CLI session signed in as another
would otherwise be arbitrated as one pool and put one account's figure on
the other's rings, cache and Max Tracker. So each source carries a
fingerprint and they are merged **only when the fingerprints match**. The
bridge's is the first 16 hex characters of `sha256(oauthAccount.accountUuid)`
read from the `.claude.json` of the session's config directory
(`CLAUDE_CONFIG_DIR`, default the home directory) — the file the same
`/login` writes beside the credential that session uses; absent when the
file or field cannot be read. The tokenserver derives the probe's the same
way, from the `.claude.json` beside the credential store the winning token
came from: the keychain entry and the credentials file are written by
that `/login`, so a probe served by either carries the home directory's
fingerprint; Claude Desktop's injected token carries it only when it
equals the keychain token (the candidates are already compared), and
otherwise no fingerprint, because Desktop's account is not readable from
outside its process. **The fingerprint survives a cooldown restart:**
`_probe_limits()` today loads a persisted 429 cooldown and returns
before `_read_oauth_candidates()` runs, and the probe state file holds
only `cooldown_until`, so a tokenserver restarted while resting would
have no winning token to derive a fingerprint from and would reject an
otherwise fresh bridge for the rest of the cooldown — the opposite of
what the bridge is for. So `_save_probe_state` persists the fingerprint
and its source beside `cooldown_until`, `_load_probe_state` restores
them, and when an older state file has none the tokenserver derives the
fingerprint from the local credential store without any HTTP call
(reading the candidates is local; only the request is what the cooldown
rests) **only when that store is unambiguous**: one candidate, or a
Desktop token equal to the keychain's. A legacy file cannot say which
of two differing tokens took the 429, so with a Desktop token that
differs from the keychain's the account stays unknown until a probe
succeeds, rather than attributing the cooldown — and the bridge's
acceptance — to whichever account happens to be readable. No fingerprint on either side, or two that differ,
means **no merge**: the probe stays the panel's source exactly as today,
the bridge sample stays in its file but is skipped by arbitration and by
the interval rule, and the doctor says `VARN statusLine bridge: sample is
from another Claude account` or `… account unknown (Claude Desktop
token)`, so the bridge never silently does nothing. The fingerprint is a
hash: neither the uuid nor the e-mail is written to the sample file, to
`GET /` or to a log line.

**The user's status line keeps working.** `settings.json` allows one
`statusLine` object, `{"type": "command", "command": "…"}`; setup writes
the whole object (`type` included, since a `command` without it is not a
status line Claude Code runs) and preserves any other supported sibling
keys such as `padding`. If a command already exists, setup records it
inside the bridge's own configuration (the bridge never rewrites
`settings.json` itself) and the bridge executes it with the same stdin,
passing its stdout and exit status through unchanged. **Install is
idempotent:** if the existing command is this checkout's launcher, or any
earlier VibePulse launcher — recognised **either** by the launcher path
setup recorded in the bridge configuration at the last install (that
file lives in the tokenserver's state directory, so it survives a
checkout being moved or deleted) **or** by a fixed marker in the
launcher file the command points at, when that file can still be read —
setup keeps the chained command it already recorded and rewrites only
the launcher path; recording the launcher as the chained command would
make every status-line run start a second bridge, recursively, and a
moved checkout would otherwise leave the user's line invoking a path
that no longer exists. A command that is neither the recorded path nor
a readable marked launcher is foreign and is recorded as the chained
command as before; the doctor reports `FIX statusLine bridge: launcher
missing` while the recorded path does not resolve. If none exists, the bridge exits 0 with empty
output, which Claude Code renders as no status line, the same as
before. The launcher exists for the one failure the bridge cannot survive
on its own: if the recorded interpreter no longer resolves (a moved or
deleted venv, Python gone from `PATH`), the launcher runs the chained
command directly, so the user's own line survives a broken bridge, and the
doctor reports `FIX statusLine bridge: interpreter not found`. Setup
refuses to install the bridge when the existing command cannot be
represented (a non-string, a command containing a newline) and says so,
rather than guessing.

**Source order in the tokenserver**, for the general weekly and the session
window, newest honest observation wins — and "newest" is decided by
timestamp, not by which source it is:

1. Among the bridge window (when its `resets_at` has not passed and its
   entry's account fingerprint is the probe's — its `seen` plays no part
   here) and the probe's last successful observation of the same window
   (likewise until its `resets_at` passes): if they
   describe different reset windows, the one with the later `resets_at`
   is the current window and wins; if they describe the **same** reset
   window, the **higher percentage** wins, whatever the timestamps say.
   Usage inside one window only accumulates, so the higher figure is the
   truer one and the ring never moves backward within a window — not
   through source priority, and not through a stale replay either: the
   statusLine also runs on permission-mode, vim-mode and timer triggers
   that make no API request and re-emit Claude Code's cached
   `rate_limits`, so a bridge write can carry a *newer* `at` with an
   *older* percentage than what the probe just saw on another device. A
   probe that observes more usage than the bridge therefore corrects
   cross-device drift at once, and a bridge sample that observes more
   than the probe overrides it the same way. **The higher observation
   stays eligible until its reset, not until its source goes quiet, on
   both sides:** the probe's last successful observation takes part in
   this comparison as long as its `resets_at` has not passed, whether or
   not the probe has succeeded within its interval since, and the
   bridge's stored value takes part as long as *its* `resets_at` has not
   passed, whether or not a status-line trigger has fired within
   `STATUSLINE_FRESH_S` — the tokenserver keeps both as a per-window
   monotonic floor, **and the floor survives a restart**: the quota cache
   already persists the weekly record under the identity, the session
   window is persisted the same way under its own scope
   (`general_session` exists in `_SCOPES` today), and the identity-matched
   cache record for a window takes part in the same-reset
   higher-percentage comparison as a third participant rather than only
   as the step-3 fallback. A tokenserver restarted during a 429 after
   the probe saw 60 % therefore still serves 60 % against a bridge
   replaying 40 % for the same reset, on both rings, because the record
   it wrote before the restart is in the comparison. Otherwise a probe that saw 60 % and then hit a 429 would drop
   out while a status-line trigger keeps replaying a cached 40 %, or a
   bridge that saw 60 % and then went quiet for 15 minutes would drop
   out while an unexpired probe observation of 40 % remains, and either
   way a ring would walk backward inside one reset window on nothing but
   a timer. What freshness still governs is liveness and scheduling,
   and liveness is a property of the **window**, not of the winning
   value: a window is live when *either* the probe succeeded within its
   own interval and its observation is of the selected reset *or* a
   matching-account bridge window has `seen` younger than
   `STATUSLINE_FRESH_S` (proposed 15 minutes), is unexpired **and is the
   reset the arbitration selected** — a bridge still reporting an older
   reset while the probe has moved to a newer one is not watching the
   window the ring shows, so it cannot keep that window live, exactly as
   it cannot stretch the probe interval.
   `claudeWeekStale` is the weekly window's liveness inverted, so in the
   probe-60 / bridge-40 case the card stays *not stale* through the
   probe's 429 cooldown as long as the bridge keeps reporting, even
   though the probe's 60 % is the figure served — the value is the
   highest honest observation, the flag says whether anyone is still
   watching the window. The bridge's `seen` also drives the bridged
   probe interval and the doctor's `fresh`/`stale` word; the probe's
   age drives its own interval. None of it decides which value is
   served: `seen` decides liveness, `resets_at` the window, `at` is the
   record of when the winning value was observed, and none of them
   decides the direction.
2. The Claude Desktop plan-usage file, under the rules the 2026-08-23 spec
   already sets (general week only, reset borrowed from a still-valid cache
   record) **plus the same-window monotonic rule and the same account
   gate**. `_merge_claude_plan_usage` today lets a newer timestamp win;
   under this spec a plan-usage sample whose borrowed reset matches the
   window bridge and probe already hold takes part in the same
   higher-percentage-wins comparison, so a fresh Desktop sample of 40 %
   cannot replace a retained 60 % for the same weekly reset, and it wins
   outright only for a later reset. The account gate: the file names its organization
   (`org`, which `_read_claude_plan_usage` validates and today discards),
   and Desktop can be signed into a different account than the probe and
   bridge. So the reader keeps `org` as a hash, the fingerprint side
   records `sha256(oauthAccount.organizationUuid)[:16]` from the same
   `.claude.json` beside its account fingerprint, and the plan-usage
   step runs only when the two organization hashes are equal; when either
   is unknown or they differ the step is skipped and `GET /` says
   `claudePlanUsage: other_account` / `account_unknown`, rather than
   borrowing B's cache record to relabel A's number. The raw
   organization id still never leaves the reader.
3. The quota cache, marked stale, as today — **filtered by the same
   account**. `QuotaCache.latest(provider, scope)` today returns the
   newest unexpired record across every identity, and `_quota_identity`
   hashes `default-v1` for Claude, so once the bridge has fed account A's
   value into the cache, a probe switched to account B that momentarily
   has no live result would fall through to A's number. So the Claude
   identity fed to `_quota_identity` becomes the account fingerprint
   when it is known and, when it is not (a Desktop token that differs
   from the keychain's), a **credential fingerprint** — the first 16 hex
   characters of `sha256` over the winning token, one-way, never the
   token, shown nowhere — so that two unidentified accounts in a row
   land in two partitions rather than one shared `default-v1` bucket
   that would let A's unexpired quota serve B after Desktop switches
   accounts and B's probe momentarily fails. A token refresh changes the
   credential fingerprint and costs one cache miss; that is the accepted
   price of never crossing accounts. Every Claude record — probe- or
   bridge-fed — is persisted under that identity, `latest` gains an
   identity argument the tokenserver always passes, records under
   another identity are never candidates, and `default-v1` is no longer
   written for Claude.

The heaviest-model weekly window keeps today's order: probe, then cache,
under the same identity filter.

**The probe becomes a background verifier.** While **both** bridge windows
are independently fresh (`five_hour` and `seven_day` each with `seen`
younger than `STATUSLINE_FRESH_S`, unexpired, **and the window the
arbitration selected** — a bridge window whose `resets_at` is older than
the probe's observation of the same window is ineligible for arbitration
and must not count as live for scheduling either — and the entry's fingerprint matches
the probe's — a sample the arbitration will not use must not slow the
probe either — a fresh session window beside a
missing or stale weekly one does not count, because the probe is then the
only source that can recover the week) *and the probe's last status was a
completed probe*, the probe interval stretches to `PROBE_WHEN_BRIDGED_S`
(proposed 30 minutes, up from 240 s),
because its only unique contribution is the model pool and cross-device
drift, neither of which moves fast. The existing auth-recovery exception
in `_probe_interval_s()` keeps precedence: after `no_claude_oauth_token`,
`token_expired_…` or `token_dead_awaiting_refresh` the probe keeps
re-checking the local credential every `AUTH_RECOVERY_EVERY_S` (15 s),
bridge or no bridge, because the Claude Code turn that writes a fresh
bridge sample is the same turn that renews the credential, and the model
pool the bridge cannot supply would otherwise stay stale for up to half
an hour after a sign-in. On a 429 the probe rests exactly as it does
today; the glass no longer notices. When the bridge file is missing or
stale (no Claude Code turn on this computer for a while), the probe
returns to its current cadence automatically. No probe code is deleted in
this change; the interval rule is the whole difference.

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
2. Claude Code runs the launcher on its normal triggers. The bridge merges
   stdin into the existing file per window and writes
   `{"v": 1, "accounts": {"3f9c0a7e1b2d4c65": {"five_hour": {"pct":
   23.5, "resets_at": 1738425600, "at": <epoch s>, "seen": <epoch s>},
   "seven_day": {...}, "claude_code_version": "2.1.267"}}}`
   through the existing atomic-write discipline (`state_files`), 0600, in
   well under 100 ms, then runs the chained command if any.
3. The tokenserver reads the sample file **on the request path**, in the
   same place `_merge_claude_plan_usage` already folds the plan-usage file
   into the Claude view, so a statusLine write reaches the panel on its
   next poll. The read is size-bounded and fail-closed like
   `_read_claude_plan_usage`, and cached by `(mtime, size)` so an unchanged
   file costs one `stat` per request. It is deliberately *not* tied to
   `_refresh_limits`: `get_limits()` starts that only when the probe
   interval expires, and with the bridged interval at 30 minutes a sample
   read there would sit invisible for up to that long. `_refresh_limits`
   consults the file's freshness only to pick the probe interval, and
   that predicate lives in `_probe_interval_s()` itself — the function
   `get_limits()` calls on every request to decide whether a probe is
   due — not in `_refresh_limits`, so the moment the bridge crosses
   `STATUSLINE_FRESH_S` the interval drops back to the ladder and an
   overdue verifier starts on the next request rather than at the end of
   a 30-minute schedule set while the bridge was fresh. `GET /`
   reports `claudeStatusline: {status, ageS, claudeCodeVersion, account}`
   with statuses `fresh`, `stale`, `missing`, `invalid`, `not_installed`,
   `other_account`, `account_unknown`, where `ageS` is the age by `seen`
   of the youngest window in the probe's own entry and `account` is
   `match`, `mismatch` or `unknown`; the tokenserver reads only that
   entry and never the other accounts'.
4. `/api/tokens` is byte-identical in shape. `claudeWeekPct` and
   `claudeSessionPct` come from whichever source won; `claudeWeekStale`
   does **not** — it is the weekly window's liveness inverted, computed
   from both sources as the source-order section says, so an older probe
   observation that wins on percentage never marks the card stale while
   a matching bridge window is fresh; `claudeModelWeekPct`
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
  and asserts the sample file contains only the allowed keys: `v` and
  `accounts` at the top, per entry `five_hour`, `seven_day` and
  `claude_code_version`, per window `pct`, `resets_at`, `at` and `seen`.
- The bridge never contacts the network and never reads a credential. The
  one file it reads besides its own is `.claude.json`, for the single
  `oauthAccount.accountUuid` field, and only its hash leaves the process.
- A bridge crash or a full disk must not break the user's own status
  line: the chained command runs even when the sample write fails, and the
  bridge's own exceptions exit 0 silently (Claude Code treats status-line
  noise as output). A missing interpreter is the launcher's job, above;
  without the launcher that failure would not be transparent, and the spec
  does not claim it is. The doctor is where failures show.
- Setup never installs the bridge without the user's explicit yes and
  never overwrites a foreign `statusLine.command` without recording it.
  Setup records, beside the chained command, which fields it owned: whether
  the `statusLine` object existed at all, whether `type` and `command`
  were present, and their previous values. Uninstall — **only if
  `statusLine.command` still points at this installation's launcher** —
  restores exactly those fields, field by field, never the object as a
  unit: a `type` the installer added is removed, a `command` it replaced
  is restored and one it added is removed, sibling keys that were there
  before or were added since (`padding`, for instance) are left as they
  are, and the `statusLine` object itself is removed only if it is empty
  after that — so an object the installer created and the user later
  extended keeps the user's fields. If the user
  changed the command after installing, nothing is touched and the doctor
  reports the drift instead of replacing a newer edit with an older one. `settings.json` edits go
  through the same read-modify-write with backup that the hook
  installation already uses.
- Two Claude Code sessions writing the file concurrently: the lock
  serializes the read-merge-replace, and the per-window rule (newer
  `resets_at`, else higher percentage for the same reset) means the
  invocation that gets the lock second cannot overwrite a truer figure
  with a staler one. Both samples are true; the file ends up holding, per
  window, the winning observation **with its own `at`** whichever order
  the two ran in — never the newest `at` stapled to a different
  percentage, which would advance freshness for a value nobody observed
  at that moment.
- The sample file's `at` and `seen` are the bridge's wall clock, compared
  against the tokenserver's wall clock on the same machine. Clock
  regression makes the sample stale by age, never fresh by mistake
  (`age_s < -60` rejects, as in the plan-usage reader).
- The 2026-08-23 recovery spec's rules stand: the probe still never sends a
  dead or expired token, and a 429 cooldown is never shortened. This spec
  only lengthens the probe interval while a better source is fresh.

## Tests

Regression tests must prove:

- the bridge keeps exactly `five_hour` and `seven_day` `used_percentage`
  and `resets_at`, the version and the account fingerprint, and drops
  every other documented stdin key, including nested ones;
- a session-start invocation without `rate_limits` leaves both existing
  windows in the file untouched, an invocation with one window replaces
  that window only, a replay of the same window with the same or a lower
  percentage leaves the stored value and its `at` unchanged and advances
  `seen` only, a new `resets_at` replaces the window, and a window past
  its `resets_at` is dropped;
- alternating accounts: runs from fingerprints A and B, including B's
  session-start run without `rate_limits` and B's lower same-reset
  sample, leave A's windows under A and B's under B, never relabelled or
  merged; the tokenserver whose probe is B serves B's entry only; an
  entry whose windows have all expired disappears;
- an actively reported window whose percentage has not moved for longer
  than `STATUSLINE_FRESH_S` is still fresh (by `seen`) and still keeps
  the bridged probe interval, while its `at` stays at the observation
  that set the value; and a bridge value of 60 % whose `seen` then ages
  past `STATUSLINE_FRESH_S` still beats an unexpired probe observation
  of 40 % for the same reset, so the ring holds 60 % until the window
  resets while the bridge counts as stale for scheduling;
- the plan-usage step runs only when the file's organization hash equals
  the one beside the probe's fingerprint: Desktop signed into account A
  beside a probe and bridge on B leaves B's figures untouched and `GET /`
  reports `other_account`; an unknown organization on either side skips
  the step; a matching-account plan-usage sample of 40 % with a borrowed
  reset equal to the retained window's leaves a retained 60 % in place;
- a legacy probe state file during a cooldown yields a fingerprint only
  from an unambiguous local store: one candidate, or Desktop equal to
  keychain; with two differing tokens the account stays unknown and the
  bridge is not accepted until a probe succeeds;
- the quota cache serves only records under the probe's own identity:
  after the bridge has fed account A's value into the cache, a probe
  switched to account B with no live result gets no cached value (stale
  card), never A's; the reverse holds; two consecutive unidentified
  accounts (Desktop tokens differing from the keychain's) land in two
  credential-fingerprint partitions and the second never reads the
  first's record; and the persisted cache never contains a token or
  `default-v1` for Claude;
- a tokenserver restarted during a persisted 429 cooldown restores the
  fingerprint from the probe state file (or derives it locally without
  an HTTP call when the file predates the field) and accepts a matching
  fresh bridge sample for the rest of the cooldown, so the card is not
  stale while the bridge is fresh;
- two bridges run concurrently against one file (a real second process,
  not a mock) end with, per window, the winning observation and the `at`
  that belongs to it — a lower percentage with a newer `at` loses whole,
  timestamp included — and no lost window, in either order; a
  bridge that cannot take the lock within the bound writes nothing and
  still runs the chained command;
- a stored file that is corrupt is quarantined beside the original and
  the next sample starts from empty; a stored file that cannot be read is
  left untouched and no write happens;
- the bridge passes a chained command's stdout and exit status through
  unchanged and still runs it when the sample write raises; the launcher
  runs the chained command when the recorded interpreter path does not
  resolve;
- a bridge write is visible on the very next `/api/tokens` request without
  a probe cycle in between, and an unchanged file is not re-parsed;
- the bridge rejects a non-object, oversized or non-UTF-8 stdin with exit 0
  and no file written; a window with a boolean, non-finite, negative or
  over-100 `used_percentage`, or a `resets_at` that is a bool, in the
  past or more than eight days ahead, is treated as absent and leaves
  the stored window untouched while the other window still merges;
- the bridge writes the account fingerprint from the session's config
  directory (`CLAUDE_CONFIG_DIR` honoured) and omits it when `.claude.json`
  is missing or has no `oauthAccount`; the sample file, `GET /` and the
  log never contain the uuid or the e-mail; a sample whose fingerprint
  differs from the probe's, or is missing on either side, is skipped by
  the arbitration and the interval rule, the probe's figures reach the
  panel unchanged, and `GET /` and the doctor report `other_account` /
  `account_unknown`; a Desktop process token equal to the keychain token
  carries the keychain's fingerprint and one that differs carries none;
- for the same reset window the tokenserver serves the higher of the
  bridge and probe percentages whichever was observed later, so a probe
  seeing cross-device usage wins over a fresher bridge replay and a bridge
  seeing more wins over an older probe, and the persisted quota cache
  never records a lower figure for a window it already holds; a probe
  observation of 60 % followed by a 429 and a fresh bridge replay of
  40 % for the same reset keeps both rings at 60 % until the window
  resets — also across a tokenserver restart during the cooldown, for
  the session ring as well as the weekly one, because the persisted
  identity-matched record is in the comparison — and `claudeWeekStale`
  stays false for as long as the bridge keeps reporting the selected
  reset, going true once both the probe is past its interval and the
  bridge's `seen` is past `STATUSLINE_FRESH_S`, and also when the
  bridge's only fresh window is an older reset than the probe's; for
  different windows the later `resets_at` wins; it prefers both over an
  older plan-usage sample, uses the probe alone only when the bridge
  window is absent, expired or from another account (never merely
  because `seen` aged out), and never invents a model-pool percentage
  from the bridge;
- the probe interval is `PROBE_WHEN_BRIDGED_S` only while both windows
  are fresh, are the selected reset windows, and the last status was a
  completed probe; a fresh bridge window from an older reset than the
  probe's keeps the ladder; one fresh window
  beside a missing or stale one keeps the current ladder, the
  auth-recovery statuses keep `AUTH_RECOVERY_EVERY_S` with a fresh bridge
  sample present, the ladder returns otherwise, and a bridge that goes
  stale after a probe was scheduled at the long interval makes
  `get_limits()` start a probe on the next request;
- a bridge observation reaches the Max Tracker only through the existing
  live gate;
- `GET /` reports the five bridge statuses; the doctor and smoke test map
  them as specified; the SessionStart context stays within its byte bound;
- setup writes a complete `{"type": "command", "command": …}` object
  when none existed and preserves sibling keys when one did, shows the
  diff, refuses an unrepresentable existing command, records a chained
  command, and a second install over an existing launcher keeps the
  originally recorded chained command instead of recording the launcher
  (the recursion test), and so does a second install from a **moved**
  checkout whose old launcher path no longer exists but matches the
  recorded one (the moved-checkout test: the chained command survives
  and the doctor's `launcher missing` clears); uninstall, when the command is still the
  launcher, removes a `type` the installer added, restores a replaced
  `command`, keeps unrelated siblings in every case — including a
  `padding` added after an install that created the object, which then
  leaves `{"padding": …}` behind — and removes the object only when
  nothing is left in it; it leaves a command the user changed afterwards
  alone while reporting the drift;
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
3. Could the probe bind its account from the API response itself (an
   organization id header) instead of the `.claude.json` beside the
   credential, which would also cover Claude Desktop's injected token?
   The spec does not rely on it because the header's value and its
   relation to `oauthAccount` are unverified; if they match in practice,
   it is a strict improvement on the `account_unknown` case.
4. Windows: `statusLine.command` runs through the user's shell; the
   launcher is a `.cmd` there, invoking the verified `python.exe` path,
   and the doctor must check the registered command matches this
   checkout's launcher, the same drift check the tokenserver source
   fingerprint does today.
