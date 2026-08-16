# Observability backlog

The queue of "things we know we can't see, and things that fail silently"
— found by a full audit of the firmware, the tokenserver, and the process
around them (2026-08-13). How these get found and worked is described in
[observability.md](observability.md); stories behind them live in
[lessons.md](lessons.md).

**Last combed: 2026-08-13 (initial audit — this document is its result).**

Rules of the file:

- IDs are stable; never renumber. New items append to the matching tier.
- `Status:` is `open`, `in progress`, or `done (commit)`. Done items keep
  their entry (they document why the code looks the way it does).
- An item is one coherent fix, small enough to land in one sitting where
  possible. Evidence refs are the audit's receipts — verify against
  current code before building on them.
- Firmware items that change what's on the glass go through
  `.claude/skills/iterating-esp32-amoled-ui/SKILL.md` like any visual
  work. Nothing here authorizes a flash.

Tiers:

- **P1 — stop flying blind.** Failures that today leave no evidence at
  all, or evidence that lies.
- **P2 — stop making it worse.** Behavior that amplifies a failure once
  it starts (hammering, blocking, aborting) or loses data.
- **P3 — process & hygiene.** Docs, CI, lint, and turning telemetry into
  alerts.

---

## P1 — stop flying blind

### OBS-01 · Boot banner: version, git rev, and reset reason
`firmware · S · done (2026-08-13)` — `app_main` now logs `boot: <namn>
<version> (byggd <datum> <tid>, IDF <ver>), omstartsorsak <namn> (<kod>)`
as its first line: the version is git describe via ESP-IDF's app
descriptor, and PANIK/TASKVAKTHUND/BROWNOUT are decoded loudly. Takes
effect on the next flash. Original problem, for the record:
The firmware never announces what it is or why it started:
`esp_reset_reason()` is called nowhere in the repo, and boot logs carry
no version or rev. A board that panicked and rebooted overnight is
indistinguishable from one that never did, and "is this board running
what I just flashed?" is unanswerable — the exact stale-artifact problem
the server already solved with `rev`/`startedAt` after it cost an hour
(`tools/tokenserver/tokenserver.py:1510-1512`, commit `8f6b8bd`).
**Fix:** one `ESP_LOGI` at `app_main` start (`main/main.c`) with
`esp_app_get_description()` (version, idf ver, build time) +
`esp_reset_reason()` decoded to text. Cheapest line in this backlog.

### OBS-02 · Enable coredump to flash
`firmware · M · open`
No `CONFIG_ESP_COREDUMP_*` anywhere, no coredump row in `partitions.csv`
— a panic prints a backtrace to a console that is almost never attached,
then reboots. The evidence never existed. `partitions.csv` documents 16 MB
flash with deliberate headroom ("marginalen är gratis"), so a 64 K
coredump partition is free.
**Fix:** add coredump partition + `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y`
(+ pin `CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT` explicitly, see OBS-28);
document `idf.py coredump-info` retrieval in the runbook.

### OBS-03 · Reboot ledger in NVS
`firmware · S · open`
NVS is initialized (`main/main.c:378-383`) but never used for a single
key. Persist per-reason reset counters + a boot counter, log them in the
OBS-01 banner. Turns "did it reboot while I was away?" from unanswerable
into one serial line — and later feeds a diagnostics view (OBS-27).

### OBS-04 · Give the tokenserver a real logger
`server · M · done (2026-08-13)` — stdlib `logging` to stderr with
timestamps; probe status changes log as `claude-probe: X -> Y`
transitions (one line per change, silence in steady state); store-save
failures log throttled; agent-status sanitization untouched. Keychain
cause granularity remains OBS-20. Original problem:
The service has no logging framework: four `print()` sites, no
timestamps, no levels (`tokenserver.py:656,1574,1605`;
`agent_status.py:929`). Meanwhile its most important events — probe
status transitions (401 appears/clears, 429 backoff entered, keychain
fallback engaged, network errors) — update internal state without
printing anything, so the log file cannot reconstruct when things
happened.
**Fix:** stdlib `logging` to stderr with timestamps + levels; log
**state transitions, not steady state** (probe status changes, first
success after failure, store save failures) so volume stays near zero.
Keep the agent-status privacy sanitization exactly as is
(`test_agent_status.py` asserts message shape — content-free stays
content-free).

### OBS-05 · Un-silence the HTTP layer
`server · S · done (2026-08-13)` — `log_error` logs again while the
access log stays muted; all four routes share one guarded `_reply` (the
agent-status route keeps the `{"error": ...}` contract now); every 500
logs its traceback; client disconnects are quiet by design. Original
problem:
`Handler.log_message` is overridden to `pass` to keep 30 s polls out of
the log (`tokenserver.py:1523-1524`) — but `BaseHTTPRequestHandler`
routes `log_error` through `log_message`, so *error* logging died with
it. The two guarded routes swallow their exceptions entirely
(`:1500-1501`, `:1507-1508`: 500 sent, cause discarded), and
`/api/agent-status` has no guard at all (`:1502-1503`) — the one route
that can dump a raw traceback and break the documented
`{"error": ...}` contract.
**Fix:** keep access logs muted but restore `log_error`; guard the
agent-status route like its siblings; log the exception (with traceback)
whenever a 500 is served.

### OBS-06 · Move the launchd log out of /tmp, cap it
`server · S · done (2026-08-13)` — plist now logs to
`~/Library/Logs/torget-tokenserver.log`; the server self-rotates it at
startup and hourly while running (a long-lived process must not outgrow
the cap between restarts), 5 MB threshold with the last 256 KB preserved
in `.old`, guarded by an fstat/stat identity check so terminal runs
never touch it. Original problem:
`se.torget.tokenserver.plist` sends both streams to
`/tmp/torget-tokenserver.log`: unrotated and uncapped within a boot, yet
erased by macOS reboot//tmp-cleaning — unbounded *and* unavailable for
post-mortems at the same time.
**Fix:** `~/Library/Logs/VibePulse/tokenserver.log` (surfaces in
Console.app, survives reboot), plus a startup size check that rotates the
file once past a few MB (stdlib, no logrotate dependency). Update plist,
`tools/tokenserver/README.md`, and the runbook.

### OBS-07 · Fatal boot error + KeepAlive = silent crash loop
`server · S · done (2026-08-13)` — a missing `~/.claude/projects` logs
one warning and waits (30 s poll) instead of exiting; the plist gained
`ThrottleInterval 30` as the backstop for any other early death.
Original problem:
Missing `~/.claude/projects` raises `SystemExit`
(`tokenserver.py:1570`); the plist has `KeepAlive` with no
`ThrottleInterval`, so launchd respawns every ~10 s forever, appending
the same line ≈8 600×/day to an unrotated file. Nothing distinguishes
this from healthy except reading the file.
**Fix:** wait-and-retry with backoff instead of exiting (the directory
appearing later is the normal case on a fresh Mac), plus
`ThrottleInterval` in the plist as a backstop for any other fatal error.

### OBS-08 · A crashed usage recompute freezes the numbers forever, silently
`server · S · done (2026-08-13)` — a crash now logs (throttled to one
per 5 min), recovery logs as a transition, `GET /` exposes
`usageComputeOk`/`usageComputeFailingForS`, and the smoke test FAILs on
it. Deliberately not done: pushing a stale flag into `/api/tokens`
itself — the firmware parser is contract-strict, so new fields there
are OBS-09-scale contract work; until then the *screen* still can't see
this, only `GET /` and the smoke test can. Original problem:
`_refresh_usage_totals` swallows any `_compute` exception and keeps
serving the previous snapshot — while still bumping `_last_computed`, so
the refresh never retries eagerly and nothing is ever printed
(`tokenserver.py:1283-1293`). Token totals silently stop advancing while
the API keeps answering 200 with confident numbers; the `*Stale` flags
cover quota percentages only, so the screen cannot detect it. Violates
the honesty invariant ("never makes numbers up").
**Fix:** log the exception (throttled), expose
`lastComputeOk`/`lastComputeAge` on `GET /`, and mark the snapshot stale
when recomputes keep failing.

### OBS-09 · Staleness is one clock fed by one endpoint
`firmware · M · open`
`last_success_us` is written only by `/api/tokens` successes
(`components/app_tokens/app.c:34`) but the derived `stale` flag is OR-ed
into every page (`usage_screen.c:450,458`). If `/api/max-tracker`
(5 min) or `/api/agent-status` (1 s) dies while `/api/tokens` keeps
succeeding, their pages show hours-old data under a `LIVE` header. Same
honesty-invariant violation as OBS-08, on the device side.
**Fix:** per-feed `last_success` timestamps; each page goes stale on its
*own* feed. Visual change → AMOLED skill gate applies.

### OBS-10 · Failed max-tracker save loses the dirty flag
`server · S · done (2026-08-13)` — a failed save re-marks dirty and logs
(throttled to one per 5 min) so the next observation retries; the final
shutdown flush logs its failure too. Original problem:
The background writer clears `_max_tracker_dirty` *before* calling
`store.save()` and swallows the exception (`tokenserver.py:1195-1199`);
the shutdown flush swallows too (`:1616`). One disk-full or permissions
error and observed day-peaks are silently discarded until an unrelated
event happens to re-mark dirty.
**Fix:** re-mark dirty on failure, log it (throttled), and let the next
cycle retry.

### OBS-11 · Corrupt state files are silently wiped — quarantine them instead
`server · S · open`
All three state stores respond to a corrupt file by starting empty with
no message: `max_tracker.py:1095-1102` (up to **400 days** of history
plus backfill watermarks), `quota_cache.py:112`, `usage_history.py:81`.
Recovery is impossible because the corrupt bytes get overwritten by the
next save.
**Fix:** on parse failure, rename the file to `<name>.corrupt-<ts>` and
log loudly before starting fresh. The data is usually 99 % intact —
quarantining preserves the forensics and the option to hand-repair.

### OBS-12 · Fetch failures discard their own diagnosis
`firmware · S · open`
Three related holes in the device's network error reporting:
(a) `agent_net.c:120` collapses the three-valued fetch result to
`ESP_OK/ESP_FAIL` before logging, so the log can only ever say
`transportfel ESP_FAIL` — IO-error vs overflow is computed and thrown
away. (b) When the HTTP client can't even be created the task logs once
and `vTaskDelete`s itself (`agent_net.c:110-114`): the agent feed is dead
until reboot with nothing on screen. (c) `torget_http.c:48-49` returns
false on client-init failure with no log at all — the only silent path in
an otherwise well-logged function.
**Fix:** log the real enum + URL; retry instead of task suicide; add the
missing log line.

---

## P2 — stop making it worse

### OBS-13 · No backoff anywhere in the firmware
`firmware · M · open`
Every device poller runs at a fixed cadence no matter what: tokens 30 s,
max-tracker 300 s (`net.c:62,112`), agent-status **1 000 ms**
(`agent_net.c:19,136`), WiFi reconnect 2 s (`main.c:165`). A dead
tokenserver gets 86 400 connect attempts/day from the agent poller
alone. The server side already learned this lesson the hard way — the
429 night (commits `c5510b5`, `8f6b8bd`, and [lessons.md](lessons.md))
ended in streak-based slowdown and cooldowns — but the firmware never
got the same medicine.
**Fix:** consecutive-failure backoff with a cap (e.g. agent 1 s → 30 s,
tokens 30 s → 300 s), reset on success; log transitions only.

### OBS-14 · WiFi retry blocks the system event loop
`firmware · S · open`
The 2 s reconnect delay runs *inside* the default event-loop handler
(`main/main.c:165`), stalling every other system event (IP events
included) for 2 s per disconnect — worst exactly when the network is
flapping.
**Fix:** schedule the retry (timer or the net task) instead of sleeping
in the handler; this is also where OBS-13's WiFi backoff lands.

### OBS-15 · NET_READY is granted even when time sync failed
`firmware · S · open`
The code comment says SNTP is the precondition for `NET_READY`
(`main/main.c:191-193`) and the timeout log says fetches "får vänta på
den" — but `net_task` sets `NET_READY` unconditionally
(`main.c:211-212`). Apps then fetch HTTPS with a 1970 clock and TLS
fails as *cert-not-yet-valid*, logged only as generic transport errors —
a misleading trail (the log blames the network, the clock is at fault).
**Fix:** either honor the stated contract (block until sync, with
retry + logging) or keep the optimistic start but log the first fetch
attempts as "clock unset — TLS failures expected" so the trail reads
true. Decide, then make comment and code agree.

### OBS-16 · Abort is the configured response to survivable failures
`firmware · S · open`
Two `ESP_ERROR_CHECK` sites turn tolerable failures into reboots of a
shelf appliance: `esp_netif_sntp_init` (`main.c:196` — SNTP *failure* is
tolerated two lines later, but its *init* panics) and `torget_ui_lock()`
(`main.c:84` — every UI mutation from every task sits behind an abort).
With OBS-01/02/03 unfixed, these reboots also leave zero evidence.
**Fix:** downgrade to log-and-degrade where a frozen-but-visible screen
beats a reboot; keep hard aborts only for true bring-up (display init,
NVS).

### OBS-17 · The real tasks have no watchdog coverage
`firmware · M · open`
No task ever calls `esp_task_wdt_add()`; only the idle tasks are
watched (IDF default). The one hang ever observed on hardware was caught
*incidentally* by IDLE0 (`spec/hardware.md:52`). A `tokens` task wedged
in a socket read renders exactly like a healthy screen with stale data.
**Fix:** subscribe the long-lived tasks (`tokens`, `max-tracker`,
`agent-status`, `rotation`) to the TWDT with feed points in their loops;
pin TWDT config in `sdkconfig.defaults` (OBS-28). With OBS-01, a WDT
reset then becomes a *diagnosed* event instead of a mystery.

### OBS-18 · Probe backoff state is invisible, and stale probe data lingers
`server · S · open`
Three small holes in the probe's observability:
(a) `_probe_failure_streak` and the slowed interval
(`tokenserver.py:305,673-679`) appear in no payload — dashes can mean
"failing every 120 s" or "resting at 480 s" and you cannot tell.
(b) `_probe_headers`/`_probe_unknown_buckets` are cleared only on
success (`:607-608`), so `GET /` can show hours-old header names beside
a current failure. (c) `_probe_status` is built with `+=` on the probe
thread and read unlocked by HTTP threads (`:1516`) — a torn half-status
can be served.
**Fix:** expose streak/interval/cooldown on `GET /`; stamp or clear
headers on failure; assemble status into a local and publish once.

### OBS-19 · Slow-client and backfill blind spots
`server · S · open`
(a) No handler `timeout`/`protocol_version` on the HTTP handler
(`tokenserver.py:1465`): a half-open LAN connection parks a worker
thread in `readline()` forever, uncounted and unlogged.
(b) The max-tracker backfill loop swallows all exceptions at 2 Hz
(`:1554-1559`): a persistent fault (permissions, pathological file)
spins silently forever.
**Fix:** set a socket timeout; log backfill exceptions throttled (same
one-per-type-per-30 s pattern agent_status already uses).

### OBS-20 · Keychain failure is one undifferentiated shrug
`server · S · open`
A blanket `except Exception` around the `security` call
(`tokenserver.py:357-368`) collapses four distinct situations — binary
missing, **user clicked Deny on the keychain prompt**, malformed JSON,
timeout — into `(None, None)`. The README explicitly coaches users
through that prompt; if they deny it, the only trace is
`no_claude_oauth_token` on an endpoint the runbook doesn't mention.
**Fix:** catch narrowly, give each cause its own probe-status suffix and
one logged transition (OBS-04).

### OBS-21 · Two of three state writers skip the directory fsync
`server · S · open`
`quota_cache` does the full atomic dance — file fsync, rename, *parent
directory fsync*, with rollback (`quota_cache.py:148-154`) — precisely
because a rename can otherwise evaporate on power loss.
`usage_history.py:92-113` and `max_tracker.py:1239-1261` stop at the
file fsync: the exact hole the third sibling was hardened against, and
max-tracker is the file with 400 days in it.
**Fix:** port the quota_cache pattern to both.

### OBS-22 · Parser rejections cannot be diagnosed from the device
`firmware · S · open`
The three contract-strict parsers reject a whole payload via 33
`goto done` / 113 `return false` sites, none of which record what
offended; the caller logs `hämtningen avvisad, värden står kvar`
(`net.c:57`) with no URL, length, or field. Contract-strictness is the
right policy (see lessons: NUL-truncation, ambiguous keys) — the
*silence* is the problem: a server schema drift produces dashes and a
log line that explains nothing.
**Fix:** without weakening the reject-everything stance, log payload
length + a coarse reason code (which parser, which section index) on
rejection. Enough to aim the next question, cheap enough for an ESP32.

---

## P3 — process & hygiene

### OBS-23 · The best diagnostics are undocumented; one documented one is wrong
`docs · S · open`
(a) `GET /` — rev, startedAt, claudeProbe, unknown buckets — appears in
no runbook; `docs/agent-setup.md` never mentions it even while its
symptom table depends on `claudeProbe`. (b)
`tools/tokenserver/README.md` promises the startup log prints the exact
`anthropic-ratelimit-*` headers on first probe — but that print sits in
the *fallback* path (`tokenserver.py:656`) and never fires on a healthy
server. (c) `idf.py monitor` — the only way to see the only firmware log
— is mentioned in `README.sv.md` only, not in the English runbook that
repeatedly says "check the serial log".
**Fix:** add a "reading the logs" section to `docs/agent-setup.md`
pointing at [observability.md](observability.md); correct the README
claim; mention the monitor command + power caveat.

### OBS-24 · CI runs a fraction of the local gate
`ci · M · open`
CI = five tokenserver unittest modules + an ESP-IDF build. The 11 C test
binaries, visual landmarks, hardware-registry checks, and skill-contract
tests in `./test/run.sh` never run in CI — every parser-regression class
from the lessons log is guarded only on the maintainer's Mac. The
`ci.yml` header says the full gate "is tracked as a follow-up issue";
**no such issue exists**.
**Fix:** run `./test/run.sh` in CI minus the SDL-dependent captures
(or with `xvfb`), and actually file the lane-3 issue so the claim in
`ci.yml` is true.

### OBS-25 · No linting anywhere
`hygiene · S · open`
No linter config exists for ~10 k lines of Python (the C side at least
has `-Wall -Wextra -Werror` in the test gate). Several audit findings
(bare `except`, swallowed exceptions) are exactly what `ruff` rules
`E722`/`BLE001`/`S110` flag mechanically.
**Fix:** `ruff` pinned in `requirements-dev.txt`, config in
`pyproject.toml`, wired into `test/run.sh` and CI. Start with the
bug-shaped rules, not the style ones.

### OBS-26 · design-qa.md contradicts the physical review
`docs · S · open`
`design-qa.md` still says the physical AMOLED gate is outstanding and
points at `work/design-qa/…`, a path that doesn't exist —
contradicting `AGENTS.md` and the 2026-08-13 review that marked the
static gate PASSED. Doc drift is this repo's most-repeated mistake
(five correction commits in two days — see lessons).
**Fix:** retire the file or rewrite it to point at
`docs/superpowers/reviews/` as the live QA record.

### OBS-27 · Telemetry exists but nothing watches it
`firmware · M · open`
The 10 s heap line (`main/main.c:227-235`) logs the exact numbers whose
collapse predicted the 2026-08-06 panel freeze — and does nothing else:
no threshold, no warning, no on-screen hint. There are also **zero
counters** in the firmware (no fetch-failure, reconnect, or uptime
counts), so "how often did WiFi drop this week?" has no answer even with
a monitor attached.
**Fix:** low-water `ESP_LOGW` thresholds on the two numbers that
mattered (internal free, largest DMA block); a handful of counters
(reboots via OBS-03, WiFi drops, fetch failures) logged periodically —
groundwork for a later on-device diagnostics view (which would go
through the AMOLED gate).

### OBS-29 · An agent-status tailer test is load-flaky
`test · S · open`
During this branch's runs, `test_inode_churn_enforces_identity_cap_before_next_discovery`
(`test_agent_status.py`) failed once in six full-suite runs on a loaded
Linux container — `base-secret` survived in `tailer._identities` past the
identity cap — then passed five-for-five in isolation immediately after.
Suspect: timing-sensitive eviction/verify scheduling stretching under CPU
load. Platform-dependent test assumptions are an established theme
(`3743042`, and the lessons entry on CI's fresh VM).
**Fix:** drive the eviction deterministically in the test (injected clock
or forced verify schedule) instead of relying on wall-clock behavior.

### OBS-30 · Unmapped models reach the panel as raw ids
`server · S · open`
`MODEL_LABELS` (`agent_status.py:56`) names six models; `prices.json`
prices roughly a hundred and ten. `normalize_model` falls through to the
raw lowercase id for the rest, so the panel mixes typeset labels
(`OPUS 5`) with bare ids (`claude-opus-4-8`) depending on which model the
agent happens to pick. Worse, `_bounded_display` clips at 24 bytes to
match `TK_AGENT_MODEL_CAP`, so a dated id truncates mid-string:
`claude-haiku-4-5-20251001` renders as `claude-haiku-4-5-2025100`. Found
via a fork on a T-Display-S3 showing `gpt-5.6-terra` next to its
typeset siblings; the three `gpt-5.6-*` variants are now mapped, the
underlying fallthrough is not.
**Fix:** derive the label from the id (family + version, uppercased,
dated suffix dropped) and keep the map for exceptions only — then a new
model is styled on arrival instead of on the next hand edit.

### OBS-28 · Pin logging config on purpose
`firmware · S · open`
`sdkconfig.defaults` deliberately pins flash, PSRAM, LVGL, and mbedTLS
with reasoned comments — but nothing about logging: default level,
console routing, panic behavior, TWDT are all inherited IDF defaults
that an IDF bump can silently change. `LV_USE_LOG` is off, which
compiles out the launcher's only report when an app is skipped for an
API-version mismatch (`platform/torget_ui.c:182-186`) — that safety
valve currently fails silently.
**Fix:** pin `CONFIG_LOG_DEFAULT_LEVEL`, console, panic + TWDT choices
(with the same style of comment the file already uses), enable
`LV_USE_LOG` routed to `ESP_LOG`.
