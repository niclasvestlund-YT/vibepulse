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
than eight days ahead, `version` a short printable string. Both windows
are validated **before** anything is merged, and if any window that is
present fails, the **whole payload is rejected**: nothing is written,
the stored entry stays exactly as it was, and the chained command still
runs. That is `docs/lessons.md`'s hostile-input rule as written — a
snapshot with one lying window is not a snapshot to take half of, since
merging its good-looking half would combine observations from different
emissions. A window that is merely *absent* is still "no observation"
(the session-start case above); only a present-and-malformed one
condemns the payload. A lying value therefore never replaces a
last-known-good one.
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
file or field cannot be read. **The fingerprint is bound to the
observation, not to the invocation:** a running session keeps Claude
Code's cached `rate_limits` and re-emits them on non-API triggers, so if
another process logs in as account B in between, reading `.claude.json`
at invocation time would label A's replayed values with B's
fingerprint. The bridge therefore keeps, in its own state beside the
sample file, a small per-session record — `sha256(session_id)[:16]`
(never the id), the last `rate_limits` values seen for that session, and
the fingerprint bound to them — and attaches the current `.claude.json`
fingerprint to a payload **only when a window in it shows what a replay
cannot produce**: a `resets_at` later than the session's last-seen one
for that window, or a higher percentage for the same reset. Only usage
accumulating or a window rolling over proves a fresh API response made
with the session's current credential. A payload that merely *differs*
is not proof — the documented reset trigger drops an expired window
from the cached `rate_limits` without any request, so a window
disappearing, or a lower value, is treated as a replay and carries the
fingerprint bound when the surviving values were first seen (or goes to
`unknown` for a first observation). The record lives **as long as the session can
still replay**: it is pruned only when every window in its last-seen
values has passed its `resets_at` — at most the eight days the
validation above accepts — **and** the session has not run the bridge
for `STATUSLINE_SESSION_TTL_S` (proposed 9 days, chosen to exceed that
horizon rather than a `seven_day` name that the horizon does not
honour), never merely on age since first seen — a session idle for a
day and a half still owns its binding. And a session's **first**
observation is always `unknown`: whether its values are unique or not,
nothing about a first payload says which account produced it (the
process may predate the bridge install, a login, or a lost record), so
the bridge writes it to the `unknown` entry and binds the session to
the current fingerprint only at its first *proving* payload — a later
`resets_at` or a higher same-reset percentage — after which replays
keep that binding. **And the binding is to the credential the session
started with, not to whatever the account file says after the
response:** a proof of a request is not a proof of *which* credential
made it, because another process can complete a login to B between A's
response and the bridge run, and the file the bridge reads is shared
and mutable. A session's credential is fixed when the session starts,
so the bridge binds a session only when the account value it reads has
demonstrably been in place since before that session began. The proof
is `accountSeenSince`, kept **per config directory** (keyed by
`sha256(CLAUDE_CONFIG_DIR path)[:16]`, because two concurrent sessions
in two config directories are two accounts that each stay stable, not
one account flapping; a single mark would reset on every alternating
trigger and leave both unbindable), and it does not depend on the
bridge having been invoked to witness the change — the session that is
itself the first to run the bridge after a `/login` is the common case,
and a mark set at that first invocation would postdate its transcript
and leave the user's next and only session `unknown` for its lifetime.
For the home config directory the mark is the **tokenserver's**, which
runs continuously: every `ACCOUNT_WATCH_S` (proposed 30 s) it `stat`s
`.claude.json` and the credential store (a read only when either
changed, never an HTTP call) and, whenever the store's token — resolved
through its profile as above — names the same account `.claude.json`
names, it records `accountSeenSince` as the **later of the two files'
modification times** at that observation: at that moment both files
already held their current, mutually consistent values, so the pair has
been in place at least since then. A `/login` whose two writes are
separated by a pause is therefore never a proof — until the second
write lands the token resolves to one account and the file names
another, no mark is recorded, and once it lands the mark is that
second write's time, so a session started inside the pause stays
`unknown` while a session started right after it, before the
tokenserver's next look, binds. The mark survives an observation only
when the interval before it **could not have hidden a login**. Reading
the same pair twice proves nothing about the time between: an
A→B→A round trip completed inside one interval leaves both files
naming A again, and a session started while B was active would then
satisfy the start-time test and be bound to A while it holds B's
credential. A login is a new grant, so it always writes a new token
into the store *and* rewrites `.claude.json`; therefore, if since the
previous observation the store's credential fingerprint changed **and**
`.claude.json`'s modification time changed — whatever both read now —
the tokenserver treats the interval as an **uncertain transition**,
and an uncertain transition is handled exactly like an observed one:
the old mark is discarded, and once the pair reads consistent a new one
is recorded at the later of the two modification times, so a session
started anywhere inside that interval stays `unknown` (its transcript
predates the new mark); **and every binding made under the old mark is
invalidated**, because a session bound to A before the interval may be
the one that ran the `/login` to B and still holds B's credential after
another process returned the files to A. The mark therefore carries a
**generation** number, incremented on every observed and every
uncertain transition; a per-session binding records the generation it
was made under, and a binding whose generation is not the mark's
current one sends its session to `unknown` for the rest of its
lifetime, as the transition rule below says. The false positive — a
token refresh and a `.claude.json` rewrite that merely coincide inside
one interval, as they can at a session start — costs the sessions
bound under the old generation their bindings and hands their quota
back to the probe until sessions started after the new mark bind; that
is the same accepted price as never crossing accounts, and a shorter
`ACCOUNT_WATCH_S` narrows it without changing the rule. A token refresh alone (store rewritten, file
untouched) and a `.claude.json` rewrite alone (file touched, token
unchanged — Claude Code rewrites it for many reasons) retain the mark,
because neither can be a completed login; an unresolved token (during
a cooldown) neither confirms nor moves it; and an observed change of
the account on either side clears it until the next consistent
observation. The same comparison runs at tokenserver start against the
persisted last observation (credential fingerprint, account fingerprint
and both modification times, hashes and times only), so an outage
hides no more than a watch interval does. **Detection at the next
observation is not enough on its own:** a session that ran the `/login`
to B and kept B's credential can emit a proving payload *between* two
observations, after another process returned the files to A, while the
persisted mark still says A and generation N — and a sample merged then
cannot be un-merged when the watcher catches up. So the bridge gates
every write under a fingerprint on the mark being **current**: the mark
records the `.claude.json` modification time the tokenserver last
confirmed, and the bridge, which `stat`s that file anyway, compares.
A payload arriving while the file's modification time differs from the
confirmed one is handled as if the account were unknown — it lands in
the `unknown` entry, no binding is made and none is used — until the
watcher's next observation either re-confirms the mark with the new
time (a rewrite alone) or replaces it (a transition). A login always
rewrites `.claude.json`, so the window between a hidden round trip and
its detection admits no write under a fingerprint; the price is up to
one watch interval of `unknown` after every `.claude.json` rewrite,
which delays a session's next proving payload rather than losing the
session. The bridge reads the mark from the tokenserver's state (the
same directory its own state lives in, read only) and **never derives
one of its own**: reading `.claude.json` once at setup or doctor time
would seed a mark from one file, which proves nothing about the
credential beside it — a `/login` that has written the file for B and
not yet replaced A's token would leave a session started in that torn
moment able to bind A's quota to B. Only a **watched directory** can
bind. The tokenserver watches the home config directory by default and
every `CLAUDE_CONFIG_DIR` that setup was run with (setup records the
directory in the tokenserver's configuration; the doctor lists them and
warns about a directory it is run in that is not registered), each
with its own credential store (`<dir>/.credentials.json`), `.claude.json`,
profile resolution, mark and generation. A registered directory whose
credential store the tokenserver cannot read — on macOS a non-default
directory may keep its token in a keychain item the tokenserver does
not read — and an unregistered directory are `unproven_dir`: their
sessions stay `unknown` for their lifetime, and `GET /` and the doctor
say so rather than letting the gap pass as `account_unknown`. And it takes the
session's start time from the creation time of the `transcript_path`
the payload names (a `stat`, never a read; `st_birthtime` on macOS,
creation time on Windows), a file Claude Code creates when the session
starts. The binding is made only if the session started **after**
`accountSeenSince`; otherwise the session stays `unknown` for its
lifetime, because the account file changed during it or before the
bridge could witness it — the racing-login case, an in-session
`/login`, and a session that predates the bridge install all land
there, by design, rather than under a fingerprint nobody proved. A
bound session never re-reads the file to *re-bind*: later proving
payloads keep the binding made at the first one. But a binding is not
permanent either: an in-session `/login` changes the account behind an
already-bound session, and the bridge cannot tell which session ran it,
so **an account transition in a config directory — observed, or
uncertain as defined above — invalidates every binding made under the
previous generation of that directory's mark** — those
sessions go to `unknown` for the rest of their lifetime, their later
increases and rollovers land in the `unknown` entry rather than in A's,
and a switch back never revives the old binding (the mark's generation
only ever grows, so a binding made under generation 3 is dead under
generation 5 even when both read A). The tokenserver derives the probe's fingerprint
**from the token itself**, not from a file beside it. `/login` writes
the credential store and `.claude.json` as two separate files, and no
read of the two — however stable across the request — proves that the
token sent belongs to the account the file names: a `/login` that has
written one file and not yet the other leaves both reads consistent
and wrong. The proof is the API's own. The OAuth profile endpoint
(`api.anthropic.com/api/oauth/profile`, the read the `user:profile`
scope listed in the credential record exists for) answers with the
account and organization the bearer token authenticates as, so the
probe's account fingerprint is `sha256(<profile account uuid>)[:16]` —
the same value the bridge derives from `oauthAccount.accountUuid`, so
bridge and probe still match when the files are consistent — and its
organization hash comes from the same response. The call is made
**once per credential fingerprint** (a new token string), never per
cycle: the resolved pairs `credentialFp → {accountFp, orgFp}` are kept
in the probe state file (hashes only, never a token), so a cycle whose
token is already resolved makes exactly the usage call it makes today,
and a token refresh costs one profile call. The profile call follows
the probe's rules: it is never made during a cooldown, a 429 on it
starts the same cooldown a usage 429 does and skips the usage call, and
it is made *before* the usage call so a token that cannot be resolved
is known before its figures exist. A token whose profile call fails —
any non-2xx, a credential record without the `user:profile` scope, a
response without the account field — is **account-unknown**: its
figures are keyed under its credential fingerprint (the cache rule
below), never merged into any account's cache, rings or Max Tracker,
the bridge is not accepted on its strength, and the next cycle retries
the profile call before the usage call. This covers Claude Desktop's
injected token too, whose account no file beside it can name, and it
makes `.claude.json` irrelevant to the probe: a `/login` racing the
request cannot misattribute anything, because nothing the probe
attributes comes from the file. The implementation's first task records
a fixture of the real profile response and pins the field path; until
that fixture exists the resolver returns unknown rather than guessing,
and the fixture, like every fixture here, holds placeholder uuids and
hashes, never a real account. **The fingerprint survives a cooldown
restart:** `_probe_limits()` today loads a persisted 429 cooldown and
returns before `_read_oauth_candidates()` runs, and the probe state file
holds only `cooldown_until`, so a tokenserver restarted while resting
would have no identity to accept an otherwise fresh bridge under for the
rest of the cooldown — the opposite of what the bridge is for. So
`_save_probe_state` persists, beside `cooldown_until`, the resolved
pairs and the **cache identity** the probe last used — the account
fingerprint when it had one, otherwise the credential fingerprint the
cache rule below derives from the token — with its source, never the
token itself; `_load_probe_state` restores them, and a restart during a
cooldown reads the candidates locally (only the requests are what the
cooldown rests), looks the current token's credential fingerprint up in
the restored pairs, and carries the account fingerprint when it is
there, so the persisted session and weekly floors are found under the
identity they were written to. A token the pairs do not know, and every
token under a legacy state file that has no pairs, stays account-unknown
until the cooldown ends and its profile call succeeds — rather than
attributing the cooldown, and the bridge's acceptance, to whichever
account happens to be readable. No fingerprint on either side, or two
that differ, means **no merge**: the probe stays the panel's source
exactly as today, the bridge sample stays in its file but is skipped by
arbitration and by the interval rule, and the doctor says `VARN
statusLine bridge: sample is from another Claude account` or `… account
unknown (token not yet resolved)`, so the bridge never silently does
nothing. The fingerprint is a hash: neither the uuid nor the e-mail is
written to the sample file, to `GET /` or to a log line.

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
   cache record for a window is a **full third participant** in step 1
   — in the later-`resets_at` selection as much as in the same-reset
   higher-percentage comparison — rather than only the step-3 fallback.
   So a tokenserver restarted after the probe persisted a newer reset
   than the bridge's still-unexpired older one selects the newer window
   from the record, not the obsolete bridge window, and the ring cannot
   jump backward when the bridge catches up. A tokenserver restarted during a 429 after
   the probe saw 60 % therefore still serves 60 % against a bridge
   replaying 40 % for the same reset, on both rings, because the record
   it wrote before the restart is in the comparison. Otherwise a probe that saw 60 % and then hit a 429 would drop
   out while a status-line trigger keeps replaying a cached 40 %, or a
   bridge that saw 60 % and then went quiet for 15 minutes would drop
   out while an unexpired probe observation of 40 % remains, and either
   way a ring would walk backward inside one reset window on nothing but
   a timer. What freshness still governs is liveness and scheduling,
   and liveness is a property of the **window**, not of the winning
   value: a window is live when the probe succeeded within its own
   interval and its observation is of the selected reset, *or* a
   matching-account bridge window has `seen` younger than
   `STATUSLINE_FRESH_S` (proposed 15 minutes), is unexpired **and is the
   reset the arbitration selected**, *or* — for the weekly window only —
   an accepted plan-usage sample of the selected reset is fresh under
   the 2026-08-23 rules, which is what keeps the card live today with
   the probe stale and no bridge
   (`test_snapshot_uses_fresh_local_claude_week_when_oauth_is_stale`)
   and must go on doing so for a user who declines the bridge — a bridge still reporting an older
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
   bridge. So the reader keeps `org` as a hash, the probe side
   records `sha256(<profile organization uuid>)[:16]` from the same
   profile response that named its account, and the plan-usage
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
   when it is known and, when it is not (a token whose profile call has
   not succeeded), a **credential fingerprint** — the first 16 hex
   characters of `sha256` over the winning token, one-way, never the
   token, shown nowhere — so that two unidentified accounts in a row
   land in two partitions rather than one shared `default-v1` bucket
   that would let A's unexpired quota serve B after Desktop switches
   accounts and B's probe momentarily fails. A token refresh changes the
   credential fingerprint: with the account known nothing moves, and
   with it unknown the refresh costs one cache miss; that is the accepted
   price of never crossing accounts. Every Claude record — probe- or
   bridge-fed — is persisted under that identity, `latest` gains an
   identity argument the tokenserver always passes, records under
   another identity are never candidates, and `default-v1` is no longer
   written for Claude. **Legacy records get one migration, not silent
   loss:** an existing installation carries still-valid Claude records
   under the hashed `default-v1` identity, and an identity-filtered
   lookup would leave them unreadable until the next successful probe —
   during a failing probe or a persisted cooldown that is exactly when
   the cache matters, bridge or no bridge. They are **not** re-keyed:
   the old identity carries no provenance, and a token resolved today
   says nothing about who produced a record last week (the
   user may have switched from A to B before upgrading), so relabelling
   would hand A's unexpired quota to B exactly when B's probe is
   failing. Legacy Claude records stay under `default-v1`, unreadable,
   until they expire; the panel shows the honest stale card for that
   one window, `GET /` reports `quotaCache: legacy_records_unreadable`
   with their count so the cause is stated rather than looking like a
   regression, and the first successful probe writes records under the
   fingerprint. This happens whether or not the bridge is installed.

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
   `other_account`, `account_unknown`, `unproven_dir`, where `ageS` is the age by `seen`
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
   the probe's observations pass today — and the session window gets
   the gate it lacks: `get_snapshot()` today forwards every non-null
   `sessionPct` to `MaxTrackerStore.observe_quota` and
   `usage_history.record_many` hard-codes the session sample as live,
   which was harmless while a session value could only come from a
   completed probe but is not once a persisted session floor can be
   served after a restart with both sources stale. So a session value
   is recorded as an observation only when the session window is
   **live** by the liveness rule above. The wire has no session-stale
   key the firmware would honour — `test/test_tokens.c` asserts that
   `claudeSessionStale` never sets provenance, and the card's only stale
   mark is `claudeWeekStale` — so a session value is sent only when the
   session window is live, or when `claudeWeekStale` is true and the
   whole card, session ring included, is therefore marked stale (today's
   stale card, unchanged); a stale session floor beside a live weekly
   window is **withheld** — `claudeSessionPct` null, the ring's absent
   state — rather than presented as live, and it reaches the tracker and
   the history in no case.
5. The doctor prints `PASS statusLine bridge: fresh (N s)` / `WAIT statusLine
   bridge: installed, no sample yet` / `FIX statusLine bridge: the
   configured command is not this checkout's bridge` / `OFF`. The smoke test
   mirrors it as OK / VARN / FAIL. The SessionStart hook adds no new class:
   a fresh bridge simply makes `PROVIDER DATA STALE (Claude)` rarer.

## Failure and privacy boundaries

- The account proof is the tokenserver's watch and nothing else: no
  file is ever read once to seed a mark. A `CLAUDE_CONFIG_DIR` that
  setup did not register, or whose credential store the tokenserver
  cannot read, is `unproven_dir` — its sessions stay `unknown` for their
  lifetime — and `GET /` and the doctor name it rather than letting the
  gap pass as `account_unknown`.
- The bridge reads stdin **once**, parses at most 64 KiB of it (the
  documented payload is a few kilobytes) and never logs it. A payload
  beyond the cap is not a sample — nothing is written — but it is still
  the chained command's input: the bridge drains the rest of stdin and
  streams **all** of it, the parsed head included, to the chained
  command, so the user's own status line receives byte-for-byte what
  Claude Code sent whatever the bridge made of it. The statusLine input
  also carries `cwd`, `transcript_path`, `session_id`, `model`, `cost`,
  `workspace.repo` and, on some builds, PR and worktree names. **None of it
  is written anywhere.** A test feeds a payload with every documented key
  and asserts the sample file contains only the allowed keys: `v` and
  `accounts` at the top, per entry `five_hour`, `seven_day` and
  `claude_code_version`, per window `pct`, `resets_at`, `at` and `seen`.
- The bridge never contacts the network and never reads a credential. The
  one file it reads besides its own is `.claude.json`, for the single
  `oauthAccount.accountUuid` field, and only its hash leaves the
  process; it `stat`s the session transcript for its creation time and
  never opens it.
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
  were present, their previous values, **and the values it installed**.
  Uninstall — only if `statusLine.command` still points at this
  installation's launcher — restores those fields field by field, never
  the object as a unit, and **each field only if its current value still
  equals what setup installed**: a `type` the installer added is removed
  only if it still reads `command`, a `command` it replaced is restored
  and one it added is removed, a field the user edited after the install
  is left as the user left it and reported as drift, sibling keys that were there
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
  the probe's, from its profile response: Desktop signed into account A
  beside a probe and bridge on B leaves B's figures untouched and `GET /`
  reports `other_account`; an unknown organization on either side skips
  the step; a matching-account plan-usage sample of 40 % with a borrowed
  reset equal to the retained window's leaves a retained 60 % in place;
- a `/login` to another account that lands anywhere in the probe's
  cycle cannot misattribute: a test swaps `.claude.json` from A to B
  between the token read and the response and asserts the result carries
  A's fingerprint (the token's own profile) with B's cache, rings and Max
  Tracker untouched, and the next cycle, whose new token resolves to B,
  carries B's; a token whose profile call fails, whose record lacks the
  `user:profile` scope, or whose response has no account field is
  account-unknown, keyed under its credential fingerprint, and retried
  next cycle before the usage call; a 429 on the profile call starts the
  same cooldown a usage 429 does with no usage call made; the profile
  call is made once per credential fingerprint — a second cycle with the
  same token makes only the usage call — and a Claude Desktop token that
  differs from the keychain's resolves to its own account;
- a legacy probe state file during a cooldown yields no account
  fingerprint for any token — the identity is the credential fingerprint
  and `.claude.json` naming an account changes nothing — and the bridge
  is not accepted until the cooldown ends and a profile call succeeds;
- legacy `default-v1` Claude records are never re-keyed: with a legacy
  record present and a fingerprint known, the lookup returns nothing for
  the fingerprint, `GET /` names the unreadable records and their count,
  and the first successful probe writes under the fingerprint, bridge
  installed or not;
- a session whose `rate_limits` are unchanged since its last run keeps
  its binding while its config directory's account value is unchanged —
  including after 36 idle hours, and including a session whose
  `seven_day` window resets 7.5 days after it was seen, the record
  surviving until every bound window has expired and
  `STATUSLINE_SESSION_TTL_S` of bridge silence has passed — while the
  same unchanged payload after `.claude.json` has come to name another
  account lands in `unknown` (the account-transition row below); a payload
  with a later `resets_at` or a higher same-reset percentage takes the
  current fingerprint, a payload that only dropped an expired window or
  lowered a value keeps the old binding (the reset-trigger case), every
  first observation of a session goes to `unknown` whether or not its
  values are unique and the session is bound at its first proving
  payload only if its transcript's creation time is later than the
  `accountSeenSince` of its config directory — for the home directory
  the tokenserver's mark: a `/login` followed by a session that is the
  first bridge invocation afterwards binds that session, because the
  mark is the later of the two files' modification times at the
  tokenserver's first consistent observation, not the observation time;
  a login whose credential write lands a minute after its `.claude.json`
  write records no mark until it does and then one at the second write,
  so a session started in between stays `unknown` and one started after
  it binds; a token refresh alone and a `.claude.json` rewrite alone
  move neither the mark nor a binding, while a round trip to another
  account and back completed inside one watch interval (both files
  rewritten, the token string different, the account the same) discards
  the mark, bumps its generation and records a new one at the later
  modification time, so a session started during the excursion stays
  `unknown` **and a session bound to A before the excursion, whose next
  proving payload shows B's usage, lands in `unknown` rather than in
  A's entry** — and the same holds for a round trip completed while the
  tokenserver was down, judged against its persisted last observation;
  a binding records the mark generation it was made under and any
  binding from an older generation is dead even when the account value
  reads the same; a proving payload from a session bound under the
  current generation that arrives while `.claude.json`'s modification
  time differs from the mark's confirmed one lands in `unknown` and
  makes and uses no binding, and the same session's next payload after
  the watcher re-confirmed the mark (a rewrite alone) is bound again,
  while after a round trip detected at that observation it lands in
  `unknown` for good; a fresh install binds the first post-install
  session because the watcher's first observation confirms the pair at
  the files' modification times, with no seed read anywhere; two
  concurrent sessions in two registered `CLAUDE_CONFIG_DIR`s keep two
  independent marks and both bind, while a session in an unregistered
  directory, or in a registered one whose credential store the
  tokenserver cannot read, stays `unknown` and `GET /` names the
  directory `unproven_dir`;
  a login to another account completed between the
  session's response and the bridge run resets `accountSeenSince` and
  leaves that session `unknown` for its lifetime, as does a session that
  predates the bridge install, and a bound session loses its binding
  when the file later names another account (the observed transition
  sends it to `unknown` for its lifetime, as the account-transition row
  below asserts) — the bridge only ever
  `stat`s the transcript and never opens it, and the per-session record
  contains a hash, never the session id;
- the quota cache serves only records under the probe's own identity:
  after the bridge has fed account A's value into the cache, a probe
  switched to account B with no live result gets no cached value (stale
  card), never A's; the reverse holds; two consecutive unidentified
  accounts (tokens whose profile calls failed) land in two
  credential-fingerprint partitions and the second never reads the
  first's record; and the persisted cache never contains a token or
  `default-v1` for Claude;
- a tokenserver restarted during a persisted 429 cooldown restores the
  cache identity and the resolved pairs from the probe state file,
  carries the account fingerprint for a current token whose credential
  fingerprint is among the pairs without any HTTP call, and accepts a
  matching fresh bridge sample for the rest of the cooldown, so the card
  is not stale while the bridge is fresh; a current token the pairs do
  not know restores only the credential-fingerprint identity, the
  persisted session and weekly floors are found under it, and no bridge
  is accepted until the cooldown ends and the profile call succeeds; the
  state file never contains a token;
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
  unchanged and still runs it when the sample write raises, and the
  chained command receives stdin byte-for-byte — asserted on the full
  bytes, including a payload over the 64 KiB cap that the bridge itself
  rejected; the launcher
  runs the chained command when the recorded interpreter path does not
  resolve;
- a bridge write is visible on the very next `/api/tokens` request without
  a probe cycle in between, and an unchanged file is not re-parsed;
- the bridge rejects a non-object, oversized or non-UTF-8 stdin with exit 0
  and no file written; a payload in which any present window has a
  boolean, non-finite, negative or over-100 `used_percentage`, or a
  `resets_at` that is a bool, in the past or more than eight days ahead,
  is rejected whole — the other, valid-looking window is not merged
  either, the stored entry is byte-identical afterwards, and the chained
  command still runs;
- the bridge writes the account fingerprint from the session's config
  directory (`CLAUDE_CONFIG_DIR` honoured) and omits it when `.claude.json`
  is missing or has no `oauthAccount`; the sample file, `GET /` and the
  log never contain the uuid or the e-mail; a sample whose fingerprint
  differs from the probe's, or is missing on either side, is skipped by
  the arbitration and the interval rule, the probe's figures reach the
  panel unchanged, and `GET /` and the doctor report `other_account` /
  `account_unknown`; the probe's fingerprint comes from the token's own
  profile, so a Desktop process token that differs from the keychain's
  carries its own account's fingerprint once resolved and none before;
- for the same reset window the tokenserver serves the higher of the
  bridge and probe percentages whichever was observed later, so a probe
  seeing cross-device usage wins over a fresher bridge replay and a bridge
  seeing more wins over an older probe, and the persisted quota cache
  never records a lower figure for a window it already holds; a probe
  observation of 60 % followed by a 429 and a fresh bridge replay of
  40 % for the same reset keeps both rings at 60 % until the window
  resets — also across a tokenserver restart during the cooldown, for
  the session ring as well as the weekly one, because the persisted
  identity-matched record is in the comparison — a restart with a
  persisted record for a newer reset and a bridge entry for an older
  unexpired one selects the newer reset from the record, and `claudeWeekStale`
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
  live gate, and a session value reaches the Max Tracker and the usage
  history only while the session window is live: a persisted session
  floor after a restart with the probe past its interval and no fresh
  bridge is served with `claudeWeekStale` true (the stale card) and
  recorded nowhere, and the same floor beside a live weekly window from
  a fresh bridge payload without a session window is withheld
  (`claudeSessionPct` null) because the firmware could not flag it;
- with no bridge installed and the probe stale, a fresh matching
  plan-usage sample of the selected reset keeps `claudeWeekStale` false
  exactly as `test_snapshot_uses_fresh_local_claude_week_when_oauth_is_stale`
  asserts today;
- an account transition observed in a config directory sends every
  session bound under the previous value to `unknown` for its lifetime,
  their later proving payloads land in the `unknown` entry, and a
  switch back does not revive the old binding;
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
  alone while reporting the drift, and a `type` the user edited after
  the install (with the command still the launcher) is likewise left
  alone and reported, the other owned fields still restored;
- the `/api/tokens` body-capacity test still passes (no new wire fields).

## Acceptance

On a Pro or Max account with the bridge installed, the panel's Claude
session and weekly rings follow the statusLine within one panel poll of a
Claude Code turn, the probe's 429 cooldowns no longer produce a stale
Claude card while the bridge is fresh, the Fable/Opus ring behaves exactly
as before, and a user who declines the bridge sees no change at all
beyond the one-time unreadable legacy cache window that step 3 names.

## Open questions for the maintainer

1. `STATUSLINE_FRESH_S` and `PROBE_WHEN_BRIDGED_S`: 15 and 30 minutes are
   proposals. The trade is cross-device drift versus 429 exposure.
2. Should setup offer `refreshInterval` (Claude Code re-runs the bridge on
   a timer while idle)? It keeps the sample fresh across long idle periods
   at the cost of a process spawn every N seconds in every open session.
   The default in this spec is not to set it.
3. The probe binds its account from the token's own profile response
   rather than from the `.claude.json` beside the credential (resolved
   above, after review showed that no file read proves the pairing).
   What remains open is only the field path, which the recorded fixture
   pins in the first implementation task; if the response turns out not
   to name the account, the probe stays account-unknown and the bridge
   is never merged — the spec degrades to today's behaviour, never to a
   guess.
4. Windows: `statusLine.command` runs through the user's shell; the
   launcher is a `.cmd` there, invoking the verified `python.exe` path,
   and the doctor must check the registered command matches this
   checkout's launcher, the same drift check the tokenserver source
   fingerprint does today.
