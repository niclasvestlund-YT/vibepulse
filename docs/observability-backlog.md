# Observability backlog

The queue of "things we know we can't see, and things that fail silently"
— found by a full audit of the firmware, the tokenserver, and the process
around them (2026-08-13). How these get found and worked is described in
[observability.md](observability.md); stories behind them live in
[lessons.md](lessons.md).

**Last combed: 2026-08-30 (persistent panel HTTP-stall incident).**

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
`firmware · M · done in source (2026-09-10), physically unverified` —
128K `coredump` partition, `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH` (ELF,
CRC32), `CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT` pinned, and
`coredump_note()` in `app_main` logs when a dump is present and how to
read it. CI builds it; the next flash session has to make it panic once
(`CONFIG_TORGET_BOOT_HEALTH_FORCE_FAIL` is not a panic — an `assert(0)`
behind a debug switch is the honest test). `test/test_firmware_diagnostics.py`
pins the rows. Original problem:
No `CONFIG_ESP_COREDUMP_*` anywhere, no coredump row in `partitions.csv`
— a panic prints a backtrace to a console that is almost never attached,
then reboots. The evidence never existed. `partitions.csv` documents 16 MB
flash with deliberate headroom ("marginalen är gratis"), so a 64 K
coredump partition is free.
**Fix:** add coredump partition + `CONFIG_ESP_COREDUMP_ENABLE_TO_FLASH=y`
(+ pin `CONFIG_ESP_SYSTEM_PANIC_PRINT_REBOOT` explicitly, see OBS-28);
document `idf.py coredump-info` retrieval in the runbook.

### OBS-03 · Reboot ledger in NVS
`firmware · S · done in source (2026-09-10), physically unverified` —
`reboot_ledger_note()` in `main/main.c`: namespace `torget_boot`, keys
`boots`, `panic`, `wdt`, `brownout`, one `omstartsliggare:` line after the
banner, never a stop. Original problem:
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
`server · S · done (2026-09-10)` — `state_files.quarantine_corrupt` moves
the file to `<name>.corrupt-<UTC stamp>` and logs one WARNING (file and
reason, never contents) before the store starts empty; all three loaders
use it, for invalid JSON, non-UTF-8 bytes and wrong top-level shape alike.
Tested per store. Original problem:
All three state stores respond to a corrupt file by starting empty with
no message: `max_tracker.py:1095-1102` (up to **400 days** of history
plus backfill watermarks), `quota_cache.py:112`, `usage_history.py:81`.
Recovery is impossible because the corrupt bytes get overwritten by the
next save.
**Fix:** on parse failure, rename the file to `<name>.corrupt-<ts>` and
log loudly before starting fresh. The data is usually 99 % intact —
quarantining preserves the forensics and the option to hand-repair.

### OBS-12 · Fetch failures discard their own diagnosis
`firmware · S · done in source (2026-09-10)` — (a) `agent_net.c` logs the
real three-valued fetch result (`IO-fel (öppna/läsa)` / `överflöde`), (b)
was already a retry rather than task suicide by the time this was worked
and now sits on the backoff ladder, (c) `torget_http.c` logs
`kunde inte skapa HTTP-klient (<redacted target>)`. Original problem:
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

### OBS-32 · Saved-credential expiry impersonated the active quota source
`host · S · done (2026-08-29)`
The root endpoint intentionally reported saved Claude credential readiness
separately from `claudeProbe`, but doctor, smoke, and the Codex skill read an
expired saved fallback as if it proved the current process source was dead.
That contradiction appeared in production when `claudeProbe` was
`usage_http_200 + ok`, Fable was fresh, and `claudeCredential` was expired.
Doctor also prescribed a server restart even though credentials are reread
locally every 15 seconds. The tools and versioned plugin 0.1.2 skill now name
all three dimensions—active probe, saved fallback, and served stale flags—and
tests pin both the live-source and genuinely stale cases.

### OBS-33 · Fresh server data hid an unproved device hop
`host + firmware · S · done (2026-08-30)` — local and relay token payloads
were fresh while the physical glass remained stale and `GET /` reported no
confirmed direct panel poll. A generic Python relay probe also received a
Cloudflare `403` that the panel-compatible User-Agent did not. Doctor now
surfaces `ready`/`waiting`/`stale` direct-poll evidence without calling a
relay-only network broken, and the versioned skill requires separate checks of
source, LAN, real-client relay, power, and firmware generation. The installed
unit received a separately authorized firmware candidate, but it later failed
sustained dedicated-power operation; OBS-34 records the bounded escalation
added from that result. Silence from passive serial remains non-evidence.

### OBS-34 · A disconnect-only recovery had no hard-stop evidence
`firmware · S · done (2026-08-30)` — the first watchdog candidate could
recycle Wi-Fi after a sustained quota stall, but had no second level if the
HTTP/TLS task did not make a real success afterward. The physical panel became
stale again while the host and relay stayed fresh and the board answered ICMP;
the exact lower-level trigger was unavailable without serial on dedicated
power. The policy now recycles at 60 seconds, wakes the quota task, waits for a
new IP before retrying, and escalates to one device restart after a further 45
seconds. Success resets
the incident; a reboot is disarmed until a new success, preventing a persistent
upstream outage from becoming a restart loop. Host tests pin every transition.
Physical dedicated-power acceptance remains separate evidence.

---

### OBS-37 · The heap low-water cannot answer the flush question, and nothing tracks the DMA block between samples
`firmware · M · open` — on `v1.0.0-67-ge51b79f`, physically observed
2026-09-06 over ~30 minutes of uptime on `torget-home-01`. Two distinct
signals, which should not be conflated:

**1. Memory.** The firmware's own guard fires on the low half of the block's
normal oscillation — 76 of roughly 120 samples in the first 20 minutes, and
1 083 of 2 527 (43 %) over the whole session, including hours when nothing was
wrong (the correction further down has the detail):

```
W (307120) torget: LÅGT DMA-block: 19456 byte (flush behöver 11520) — nära fryströskeln
```

The sampled largest DMA block stays in a **19 456–31 744 B** band over these
first 20 minutes (the guard fires below twice the flush size, 23 040 B, so it
fires on that band without any block being too small); the lowest sampled block
of the whole session was **16 384 B**, during the 141 s window open measured
further down. The separately tracked `lägsta någonsin` figure fell to
**11 143 B**. **What that figure is, precisely:** `main/main.c:650` prints
`heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL)`, and ESP-IDF computes
that by summing, over every internal heap region, that region's own lifetime
minimum. It is not a DMA-block size (the only block figure on the line is the
sampled `DMA största`), and it is not a snapshot of any single instant either:
the regions' minima can come from different moments, so the sum can be lower
than the total that was ever free at once. So this is not a measured DMA block
below 11 520 B, which the first version of this entry said, and it does not
prove a moment at which an 11 520 B allocation was impossible, which the second
version claimed. What it does say: the internal regions were, each at its own
worst moment, squeezed to a combined 11 143 B by t=935 s. Whether a flush
allocation ever fails is unmeasured. The `heap:` line does print that summed minimum — it is how the progression below was reconstructed — so the probe does not miss the minima advancing. What it cannot show is the instant, or the largest DMA block at that instant: nothing tracks the block's own minimum between samples, and the sampled block never read below 16 384. **A soak that watches only the sampled block will report "steady" while the summed minimum advances, and neither figure answers whether a flush allocation ever failed** — that is what makes this P1: the evidence cannot answer the question. The low-water
progression was
`44199 → 19167 (t=33 s) → 18451 (t=605 s) → 11191 (t=843 s) → 11143 (t=935 s)`
over the first ~16 minutes. It did not stop there: the later updates below
record 11 139, 10 179 and 9 623 within the first 45 minutes, and the soak's
9 355, where it settled.

**2. Lock contention.** Seventeen occurrences of, verbatim:

```
E (316778) esp_lvgl:adapter: esp_lv_adapter_lock(751): Failed to acquire LVGL lock
```

roughly one every two minutes, not escalating. This is a mutex acquisition
timeout — someone holds the LVGL lock too long — and is **not** the same
failure mode as the DMA figure above. Treating them as one thing was the first
wrong turn in the investigation.

**Comparison against the previous image.** The panel previously ran
`v1.0.0-33-g51e8d0e-dirty`, which showed a 40 960 B block with a 76 435 B
internal-free low-water and no warnings at all. The difference is not the new overlays —
all three report `internt +0 B`, and opening and closing SETTINGS twelve times
inside two minutes did **not** move the lowest-ever figure off 11 143. The
difference first blamed — the initial hypothesis, discarded below — was that
the old image **did no TLS at all**: zero
`esp-x509-crt-bundle: Certificate validated` lines across its whole uptime, no
encrypted interaction relay, and every payload marked `stale=1`, a roomy heap
on a panel that was not doing its job, against a new image that completes a
handshake every ~2.5 s. The interval analysis that follows supports no
immediate TLS mechanism, delayed contention stays merely possible, and no
trigger was identified for any low-water step; the old/new comparison is the
measured part, the attribution is not.

**Correlation result — no client is implicated.** For all 17 lock failures,
the interval back to the nearest preceding handshake was `min 178 ms, max
3495 ms, mean 1795 ms` against a handshake interval of ~2461 ms. Causation
would cluster these near zero; a uniform distribution would mean ~1230 ms. The
observed spread is broader than uniform, so `Certificate validated` precedes
the failures only because it precedes everything. **The TLS hypothesis is
unsupported by this data.** One outlier was noted and turned out to carry no mechanism: `t=826595` is
preceded not by a handshake but by a display rotation (`rotation: roterade
till läge 0`, `MADCTL 0xA0`). But `main/rotation.c` releases the adapter lock
before it emits that line and only schedules the redraw, so the ordering does
not identify rotation as the lock holder; it stays an observation, not a lead,
and does not by itself rank UI activity above the other periodic tasks.

**Cheapest bisection, if one is run.** Three clients are plain `#ifdef` gates
in `secrets.h` and can be removed in a single rebuild; the fourth, Max
Tracker, polls without its define since #98 and is switched off in SETTINGS →
LABS instead (the table keeps its row for the URL's sake):

| Client | Flag | Gate |
|---|---|---|
| Quota poll | `TK_TOKENS_URL` | `components/app_tokens/net.c:45` |
| Max tracker | `TK_MAX_TRACKER_URL` | `components/app_tokens/net.c:170` — since #98 the poller runs without it (discovery or relay); switch the tracker off in SETTINGS → LABS instead |
| Agent status | `TK_AGENT_STATUS_URL` | `components/app_tokens/agent_net.c:32` |
| Numbers relay | `TK_VIBEPULSE_RELAY_URL` | `components/app_tokens/app_tokens_config.h:22` |

Together they account for most of the handshakes, not all: the encrypted
interaction relay and Solelkollen (next paragraph) keep their own HTTPS polls
running. TLS is excluded only if the lock failures survive a build with those
two off as well; then the rotation path becomes the prime suspect. With any
TLS client left running, a surviving failure says nothing about TLS.

Two clients cannot be disabled this way and are traps for anyone trying:
`TK_VIBEPULSE_INTERACTION_RELAY_URL` is read by CMake directly out of
`secrets.h` (`components/app_tokens/CMakeLists.txt:26–41`) and its absence is a
configure-time `FATAL_ERROR`, not a disabled client — the off switches are
`TK_VIBEPULSE_INTERACTION_RELAY` **and** `TK_VIBEPULSE_AGENT_STATUS_RELAY` in
menuconfig (`main/Kconfig.projbuild`): the live-status poller in
`interaction_relay_net.c` keeps polling the same HTTPS relay on the second
switch alone, so a build with only the first off is not TLS-free. `SG_GLANCE_URL` has no `#ifdef`
anywhere and is referenced once, at
`~/Solelkollen/components/app_solelkollen/net.c:59`, outside this repo;
removing the define breaks that component's build, so Solelkollen is switched
off by pointing `TORGET_SOLELKOLLEN_DIR` elsewhere.

**UPDATE, same session — the OTA listener is implicated, not TLS.** Two more
low-water drops were captured after the above was written, and both coincide
with the maintenance window being open. The window was open for a total of
~12 seconds out of ~2 180 seconds of uptime:

```
window 1:  open t=837.1 s -> closed t=846.1 s
           low-water fell 18 371 -> 11 191 at t=843 s      (inside the window)

(1 200 s with no window, uninterrupted TLS churn: low-water moves 4 bytes,
 11 143 -> 11 139)

window 2:  open t=2156.4 s -> closed t=2159.0 s
           low-water fell 11 139 -> 10 179 at t=2164 s     (immediately after)
```

Two out of two, inside 0.5 % of the uptime, while twenty minutes of handshakes
every 2.5 s moved the figure by four bytes. The mechanism is documented in the
firmware and announced in its own log: `OTA-lyssnaren uppe på port 80` on open,
`OTA-lyssnaren stoppad — minnet åter till apparna` on close. `ota_service.c`
states the design directly — the httpd server is born in the guard task when
the window opens and dies when it closes, so a boot without an update has the
same memory profile as a build with no OTA at all.

The `ota` overlay's `internt +0 B` covers the UI layer only, not the listener;
conflating them was the second wrong turn in this investigation.

**This supersedes the bisection plan above.** Do not spend a build on the four
`#ifdef` clients first. The cheap experiment is: open the maintenance window,
watch `lägsta någonsin`, close it, repeat. If the drop reproduces per open, the
listener's allocation is the target and TLS is a bystander.

Note the operational consequence: the internal-free low-water is pushed to its
lowest observed value **precisely while the update window is open** — the
moment the panel is drawing progress UI and is about to receive a firmware
image. Lowest observed so far is a summed low-water of 10 179 B — a figure that, as
above, proves nothing about any single instant.

**SECOND UPDATE, same session — the listener measured: a fixed per-open cost
is disproved, an intermittent effect is not.** The claim above was made on two
coincidences. Three measured
open/close cycles were then run deliberately, reading the heap before, during
and after each:

| | cycle 1 | cycle 2 | cycle 3 |
|---|---|---|---|
| window open for | ~55 s | 141 s | ~50 s |
| internal free, stable while open | ~47 970 | ~47 960 | ~47 970 |
| largest block while open | 17 408–18 432 | 16 384–23 552 | 17 408–21 504 |
| low-water before -> after | 10 179 -> 9 623 | 9 623 -> 9 623 | 9 623 -> 9 623 |
| memory returned on close | full | full | not measured (no post-close reading) |

What the listener actually does is now measured rather than inferred: while the
window is open, internal free sits at **~47 965 in all three cycles, within ten
bytes**, however long the window stays open and whatever the pre-open figure was
(55 079, 65 075 and 55 075 B — the pre-open figure oscillates by ~10 kB on its
own, so the listener's cost is not a fixed delta, and the "~7 kB" first read off
cycle 1 is that cycle's difference only). After close, free returned to the
pre-open band in cycles 1 and 2 (56 859–58 879 and 55 087–68 843 B); cycle 3
has no post-close reading. No leak is visible across the two cycles that have
both readings.

Critically, **it does not lower the low-water mark per open.** Cycles 2 and 3
moved it by zero. Cycle 1's 556-byte drop coincided with an open window; cycles 2 and 3 moved
nothing. That disproves the fixed ~-556 B per-open cost predicted from cycle 1,
but one drop in three deliberate opens, after two window-adjacent drops that
prompted the experiment, does not exclude an intermittent or timing-dependent
listener effect: the listener is unproven as a cause, not refuted, until the
attribution instrumentation below exists.

So the honest state of this item, after three wrong turns:

1. The heap low-water figure and the LVGL lock failures were first treated as one
   problem. They are two: one is memory, the other is a mutex timeout.
2. TLS was blamed next. The interval analysis does not support it — the
   handshakes precede everything because they happen every 2.5 s — so an
   immediate mechanism is unsupported; delayed contention is not ruled out,
   and excluding TLS needs a build with every TLS client off.
3. The OTA listener was blamed third, on two coincidences. Three measured
   cycles disprove a fixed per-open cost; an intermittent effect stays
   unproven, not refuted.

**What remains unexplained is the thing to chase:** the low-water mark walked
from 44 199 down to 9 623 over ~45 minutes in steps
(`44199 -> 19167 -> 18451 -> 11191 -> 11143 -> 11139 -> 10179 -> 9623`) with no
identified trigger for any single step, while the sampled block never went below
16 384. Whatever allocates deeply enough to set those minima is still
unidentified, and the 10 s sample cannot see it. A ring buffer of the last N
allocation failures, or logging the allocation site when a new low-water is set,
would turn this from inference into evidence — and that, not the bisection, is
the fix this item should carry.

Longer opens do drift the sampled block down somewhat (18 432 -> 16 384 over
141 s) without setting a new minimum; worth a look but not the main thread.

**SIX-HOUR UNATTENDED SOAK, 2026-09-06 02:03–08:03 — the low-water plateaus.**
Passive reading only; no interaction at the panel, no build, no window opened.

| hour | samples | block min | block max | low-water | lock fails | `LÅGT` | free MiB |
|---|---|---|---|---|---|---|---|
| 1 | 348 | 19 456 | 31 744 | 9 391 | 14 | 139 | 193 |
| 2 | 350 | 19 456 | 31 744 | 9 391 | 4 | 142 | 1 671 |
| 3 | 354 | 19 456 | 31 744 | 9 371 | 0 | 162 | 1 655 |
| 4 | 355 | 18 432 | 31 744 | 9 355 | 0 | 141 | 1 573 |
| 5 | 355 | 19 456 | 31 744 | 9 355 | 1 | 150 | 1 520 |
| 6 | 356 | 19 456 | 31 744 | 9 355 | 0 | 136 | 1 496 |

Three results, and the first changes how serious this item is:

1. **The walk stops.** The low-water column is each hour's closing value; the
   soak's own 02:03 baseline was not logged separately. From the last measured
   cycle (9 623, minutes before the soak) to hour 6 is **268 bytes**; within
   the six hourly readings the movement is **36 bytes** (9 391 -> 9 355).
   Either figure stands against **34 576 bytes in the first 45 minutes**
   (44 199 -> 9 623). It settled at 9 355 by hour 4 and did not move again. The
   descent was confined to the first 45 minutes and then plateaued; why it
   happened is not established — no trigger was identified for any step — and
   nothing is heading toward zero. The summed low-water settled at 9 355 B,
   under the 11 520 B a flush needs — which, as above, is not a statement about
   any single instant — and that floor is stable.

2. **Lock failures fall off once the panel is left alone.** 14 in hour 1, then
   4, 0, 0, 1, 0. The soak began at 02:03, minutes after the last measured
   window cycle, so hour 1 holds the cool-down after the interactive session but
   no interaction inside the bucket; its 14 cannot be attributed to UI activity
   with this data, and the interaction correlation rests on the interactive
   period before the soak (17 failures across the roughly half hour of uptime
   the interval analysis covers, 10 of them inside its first 20 minutes)
   against hours 2–6. Nineteen in six hours, declining after hour 2.
   This is consistent with UI-linked activity in general (the rotation line
   noted above supplies no mechanism of its own) and points the LVGL-lock
   question first at UI activity. It does not clear the background tasks: five failures (4 in
   hour 2, 1 in hour 5) happened with nobody at the panel, so the network
   clients and the other periodic tasks stay in scope for those.

3. **The block range barely moved.** 18 432–31 744 B over the six hours:
   19 456 as the floor in five of them, 18 432 in hour 4. Stable oscillation,
   no drift.

The panel was still drawing at the end, at ~7.2 hours of uptime, and no alarm
condition fired (low-water < 8 000, log stall, or disk < 60 MiB).

**Correction to the frequency claimed above:** the `LÅGT DMA-block` warning was
described as firing on essentially every 10 s sample. It does not. Over the full
session it fired **1 083 times against 2 527 heap samples — about 43 %**,
tracking the low half of the block's normal oscillation. The warning is noisier
than a real threshold breach, which is itself worth noting: a guard that fires
on 43 % of samples is close to being ignorable, and it fired identically during
the hours when nothing at all was wrong.

Not yet investigated: whether the DMA block ever dips under 11 520 B at all —
the sampled minimum was 16 384 B, and nothing tracks the block between samples
— and, only if it does, whether the flush allocation fails or retries and hides
it. And the DMA
block's own minimum is not tracked at all — `lägsta någonsin` is a total, per
`main/main.c:650`. The instrumentation this item asks for starts with a
`heap_caps_get_largest_free_block(MALLOC_CAP_DMA)` low-water of its own and a
count of failed flush allocations, so the next session measures the block
instead of inferring it from a sum.

---

### OBS-38 · A maintenance window cannot be attributed after the fact
`firmware · S · open` — found 2026-09-06 while trying to answer a simple
question: did the operator open the window, or did it open itself?

`CLAUDE.md` states as non-negotiable that the maintenance window opens **only**
from the device — a 3 s KEY3 hold into SETTINGS, then UPDATE; or the UPDATE pill
on the takeover. The panel cannot afterwards demonstrate that this happened.

Two windows opened at t=837 s and t=2156 s on `v1.0.0-67-ge51b79f`. The operator
states he did not open them and was not at the screen. Both automatic paths are
excluded by the log — the running partition was `0x2`, not `PENDING_VERIFY`, so
the boot re-arm at `ota_service.c:565–578` did not fire (and its own comment says
an esptool-flashed boot is deliberately left closed), and there are zero
`notisen besvarad med JA` lines. The remaining path, a hold into the menu
followed by a tap (`main/main.c:751`), **leaves no log line at all**. The absence
of evidence before the window therefore proves nothing, and the question is
simply unanswerable.

The only reason the second window has any context is an accident: a panic fired
14 seconds earlier and happens to log because it sends a network message
(`needs-you-net: skickade deny`). The gesture itself is invisible.

Fix: log the window's open event **with its trigger source** — hold-into-menu,
notice pill, or boot re-arm — and log the close with its cause (short tap,
timeout, upload complete). One line each. Without it, the consent model is
asserted but not evidenced, and any future "did someone open this?" incident
ends where this one did.

This is P1 rather than P3 because the evidence does not merely go missing; the
log reads as though nothing happened, which is indistinguishable from a window
that opened on its own.

---

## P2 — stop making it worse

### OBS-13 · No backoff anywhere in the firmware
`firmware · M · done in source (2026-09-10) for the four service pollers,
physically unverified` — `poll_backoff_policy.[ch]` (pure, host-tested):
first miss free, then doubling to a cap, reset on success, transitions
only in the log. Wired into agent-status (1 s → 30 s cap; a miss is any
response that was not applied, so a 200 with a rejected body backs off
too), tokens (30 s → 300 s), max-tracker (5 min → 30 min) and the
optional GitHub feed (30 s → 300 s). The recovery task's notification
still cuts a long tokens wait short. WiFi reconnect is OBS-14's, not
done here. Original problem:
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
`server · S · done (2026-09-10)` — `GET /` carries `claudeProbeStreak`,
`claudeProbeIntervalS`, `claudeProbeCooldownLeftS` and `claudeProbeAgeS`
(smoke prints them beside a non-ok status); every cycle starts with
empty header evidence and publishes only what it saw; the status string
is assembled in a per-cycle outcome and swapped in once under
`_limits_lock` (`_ProbeOutcome`, `_publish_probe_outcome`). Original
problem:
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
`server · S · in progress` — (b) done with OBS-25 (2026-09-10): the
backfill loop logs one `max-tracker backfill step failed: <class>: <text>`
line per ten minutes instead of swallowing. (a) still open.
(a) No handler `timeout`/`protocol_version` on the HTTP handler
(`tokenserver.py:1465`): a half-open LAN connection parks a worker
thread in `readline()` forever, uncounted and unlogged.
(b) The max-tracker backfill loop swallows all exceptions at 2 Hz
(`:1554-1559`): a persistent fault (permissions, pathological file)
spins silently forever.
**Fix:** set a socket timeout; log backfill exceptions throttled (same
one-per-type-per-30 s pattern agent_status already uses).

### OBS-20 · Keychain failure is one undifferentiated shrug
`server · S · done (2026-09-10)` — `_read_keychain_oauth` catches
narrowly: missing binary, timeout, exit 44 (no entry), any other exit
(Deny on the prompt or a locked keychain), malformed record, record
without a token. The word rides on `claudeProbe` after
`no_claude_oauth_token:` and in `claudeCredential.reason`, the runbook
table maps each to its fix, and `claude-keychain: X -> Y` logs the
transitions (OBS-04). Original problem:
A blanket `except Exception` around the `security` call
(`tokenserver.py:357-368`) collapses four distinct situations — binary
missing, **user clicked Deny on the keychain prompt**, malformed JSON,
timeout — into `(None, None)`. The README explicitly coaches users
through that prompt; if they deny it, the only trace is
`no_claude_oauth_token` on an endpoint the runbook doesn't mention.
**Fix:** catch narrowly, give each cause its own probe-status suffix and
one logged transition (OBS-04).

### OBS-21 · Two of three state writers skip the directory fsync
`server · S · done (2026-09-10)` — `state_files.fsync_parent` (the
quota_cache pattern, Windows no-op included) now runs after the rename in
`max_tracker._atomic_write` and `usage_history._persist`; quota_cache
delegates to the same helper. A failing parent fsync raises, so the Max
Tracker writer re-marks dirty and retries and `record_many` restores its
memory. Original problem:
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
`ci · M · done (2026-08-21)` — a `host-gate` CI job now runs
`./test/run.sh --skip-js` on every push: all the C test binaries, the
wiring/capacity tests, the Mbed TLS crypto vectors (compiled against a
sparse clone of the IDF-pinned sources, same `IDF_VERSION` as the
firmware job), and the SDL landmark captures under `xvfb-run`. The JS
suites stay in their own jobs (the Worker suite in the npm-cached
interaction-relay job, the relay mailbox one in the tokenserver job) —
that skip is the gate's only CI difference. The tokenserver module list moved to
`test/tokenserver-suite.txt`, shared by `run.sh` and CI, with a
completeness guard in `run.sh` (every `tools/tokenserver/test_*.py` must
be listed). The false "tracked as a follow-up issue" claim is gone from
the `ci.yml` header. Original problem, for the record:
CI = five tokenserver unittest modules + an ESP-IDF build. The 11 C test
binaries, visual landmarks, hardware-registry checks, and skill-contract
tests in `./test/run.sh` never run in CI — every parser-regression class
from the lessons log is guarded only on the maintainer's Mac. The
`ci.yml` header says the full gate "is tracked as a follow-up issue";
**no such issue exists**.

### OBS-25 · No linting anywhere
`hygiene · S · done (2026-09-10)` — `ruff` 0.15.8 pinned in
`requirements-dev.txt`, configured in `pyproject.toml` with bug-shaped
rules only (`F`, `E722`, `B`, `S110`, `PLE`, `PLW0602`; each explained in
the file), run first in `test/run.sh` and therefore in CI's host gate. The
sweep found 43 things: fourteen `try/except/pass` sites (every one is now
a named boundary with a `# noqa: S110 - <why>`, and the Max Tracker
backfill loop, which swallowed every error silently, now logs one line per
ten minutes), sixteen loop-variable closures in tests, three `zip()` calls
without `strict=`, three dead imports, one dead variable, one
`assertRaises(Exception)`, one `raise` without `from`, and three names
declared `global` that the function never assigns. `BLE001` (74 sites,
all resilience boundaries) and `PLW0603` (the tokenserver's module-state
style) are deliberately not enabled; see `pyproject.toml`. Original
problem, for the record:
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
`server · S · done (2026-09-10)` — `agent_status.derive_model_label`
typesets any id (family, version, variant; dated suffix dropped) and
`normalize_model` bounds the raw id wide enough to keep a dated id whole
before deriving, then bounds the label to the 24-byte column.
`MODEL_LABELS` is exceptions only. A test walks every id in `prices.json`.
Original problem:
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

### OBS-31 · A codex wire test is RST-flaky on Windows CI
`test · S · open`
`test_every_json_post_route_rejects_text_plain_before_parsing`
(`test_codex_interactions.py`, subtest `/api/codex/permission`) failed
once on the `windows-latest` tokenserver job with `None != 415` — the
client read **no HTTP status at all** — then passed on the same commit
in the re-run (run 32452343376, attempts 1→2; the run before, with
identical tokenserver code, was green too). The shape is the classic
WinSock race: a server that rejects early and closes without draining
the request body can trigger a connection reset before the client reads
the response — Linux and macOS deliver the buffered response anyway,
Windows drops it. One occurrence in the suite's first two Windows runs
of the full host-gate era; worth a signature here before it becomes a
"CI is unreliable" impression.
**Fix:** make the rejection path drain (or the test client tolerate one
retry of) the unread body on early 4xx, and reproduce under load on a
Windows box before trusting either — a wire test asserting a security
boundary must not be loosened blindly.

### OBS-28 · Pin logging config on purpose
`firmware · S · done (2026-09-10)` — `CONFIG_LOG_DEFAULT_LEVEL_INFO`,
`CONFIG_LOG_MAXIMUM_EQUALS_DEFAULT`, panic print+reboot, TWDT init and
timeout, and `LV_USE_LOG` at WARN via printf, each with its reason in
`sdkconfig.defaults`. Console routing deliberately NOT pinned: the board's
console path (UART vs USB-Serial/JTAG) has not been confirmed on the unit
and a wrong pin would silence the monitor. Original problem:
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

### OBS-36 · The glass shows placeholder zeros as measurements during the first scan
`firmware · S · done in source (2026-09-10), physically unverified` —
closed in the same PR as issue #62 after a Codex review made the point
that a payload old firmware would apply is a payload old firmware WILL
apply. Two halves: the service serves placeholder counters only to a
request carrying `X-VibePulse-Accepts: usage-totals` and answers every
other client with the contract's error form (503), so an already-flashed
panel keeps its last values; firmware from this date sends the header,
parses `usageTotals.placeholder`, applies the live quota rings and leaves
the value page and the keep-awake burn rate untouched while the counters
are placeholders. No new visual state was designed: the value page simply
keeps what it last showed (the pre-data dashes on a cold boot). Original
problem, for the record:
Since issue #62 the tokenserver answers `/api/tokens` at once while its
first history scan runs, with the four volume counters at zero and an
additive `usageTotals` block saying so. The firmware contract requires
numbers for the counters, so the server cannot send `null`, and a parser
that ignores the block would print `0.00 Mtok idag` for the length of the
scan.

### OBS-35 · A raised log level puts the relay secret back on the wire
`firmware · S · done (2026-09-10)` — `CONFIG_LOG_MAXIMUM_EQUALS_DEFAULT=y`
with INFO as default compiles `ESP_LOGD` out; the pin's comment and
`docs/observability.md` say to clamp `HTTP_CLIENT` if anyone raises it.
Original problem:
The panel's own fetch logs are redacted: `torget_http.c` hands every
failure line through `tg_net_log_target()`, which keeps scheme, host and
route and drops the path — the relay's `/u/<secret>` is its whole access
control (`docs/relay.md`). ESP-IDF's `HTTP_CLIENT` tag is not redacted:
`esp_http_client.c` logs the full request line at `ESP_LOGD`
("Write header[%d]: %s") and the `Location` header on a redirect, both of
which contain the secret. Today that is inert — the inherited default log
level compiles `ESP_LOGD` out — so this is a *latent* leak that OBS-28's
"pin logging on purpose" would decide either way.
**Fix:** when OBS-28 pins `CONFIG_LOG_DEFAULT_LEVEL`, pin
`CONFIG_LOG_MAXIMUM_LEVEL` with it, and if a debug level is ever wanted
on a relay-enabled build, clamp the `HTTP_CLIENT` tag
(`esp_log_level_set("HTTP_CLIENT", ESP_LOG_INFO)`) rather than trusting
the operator to remember.
