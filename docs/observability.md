# Observability: every log this system generates

VibePulse spans two machines — a screen with no persistent storage and a
Python service on a Mac or Windows PC — and each produces evidence in a different place,
with a different lifetime, in a different language. This doc maps all of
it: where each log lives, how long it survives, what a healthy one looks
like, and a periodic **comb routine** for reading through them to catch
problems before they become symptoms on the screen.

Companion docs:

- **[observability-backlog.md](observability-backlog.md)** — the known
  gaps and the queue of fixes. Anything odd you find during a comb that
  isn't already there gets added there.
- **[lessons.md](lessons.md)** — what has already bitten us and what it
  taught. Read it before touching pollers, parsers, staleness logic, or
  the launchd setup: most of this system's sharp edges have a story.

This doc describes the system **as it is**, including what is *not*
logged. Several sources below are honest about being blind spots; the
backlog IDs in parentheses track closing them.

## The log map

| # | Source | Where it lives | Survives |
|---|--------|----------------|----------|
| 1 | Firmware serial console | USB, only while a monitor is attached | nothing — not even a reboot |
| 2 | Tokenserver stderr | terminal; `~/Library/Logs/torget-tokenserver.log` under launchd; `%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log` under Task Scheduler | durable; self-rotated at ~5 MB (tail kept in `.old`) |
| 3 | `GET /` diagnostic endpoint | `http://<host>:8737/`, live state | process lifetime |
| 4 | Server state files | `~/Library/Application Support/VibePulse/` on macOS; `%LOCALAPPDATA%\VibePulse\` on Windows | durable (8 d / 400 d retention) |
| 5 | The screen itself | dashes, `STALE`, `NO DATA` | live only |
| 6 | CI logs | GitHub Actions | per-run |

Fastest health check: the **smoke test** automates comb steps 1–4 in one
command — `python3 tools/tokenserver/smoke.py` (exit 0 ok / 1 warnings /
2 failures).

### 1. Firmware serial console

The only log the device has. ESP-IDF `ESP_LOGx` over the USB console —
attach with `idf.py monitor -p /dev/cu.usbmodem101` (ESP-IDF env sourced).
Eleven tags:

| Tag | Owner | Talks about |
|-----|-------|-------------|
| `torget` | `main/main.c` | boot, WiFi candidate hunt, SNTP, heap, brightness, MADCTL |
| `rotation` | `main/rotation.c` | IMU reads, display rotation |
| `torget-http` | `components/torget_net/torget_http.c` | every failed GET: error name, status, cap, and a **redacted target** — `<scheme>://<host> via LAN` or `… via relä`, never the path; `LAN svarade inte, provar reläet` when a fetch fails over to the relay |
| `tokens` | `components/app_tokens/net.c` | /api/tokens + /api/max-tracker polls |
| `agent-net` | `components/app_tokens/agent_net.c` | /api/agent-status poll (1 Hz, backing off to 30 s on consecutive misses; log rate-limited to 30 s) |
| `github-net` | `components/app_tokens/github_net.c` | the optional /api/github poll (30 s, backing off to 300 s) |
| `needs-you-net` | `components/app_tokens/needs_you_net.c` | signed verdict/panic POSTs (LAN only, never the relay) |
| `interaction-relay` | `components/app_tokens/interaction_relay_net.c` | optional encrypted request/verdict and live-status transport; logs readiness/failure only, never decrypted fields |
| `boot-health` | `components/torget_ota/boot_health.c` | the 15 s boot-health gate: proofs landed, rollback verdicts |
| `ota-service` | `components/torget_ota/ota_service.c` | maintenance window open/close, upload progress, image gates |
| `wifi-setup` | `components/torget_wifi/wifi_setup.c` | setup window open/close, scan counts, received credentials (SSID only — passwords are never logged) |
| `wifi-creds` | `components/torget_wifi/wifi_creds.c` | the remembered-network list in NVS: stores, rejects, corrupt-blob recovery |

A healthy boot shows: the `boot:` banner (project name and git-describe
version from the app descriptor, build date/time, IDF version, and the
decoded reset reason — `strömpåslag` is normal; `PANIK`,
`TASKVAKTHUND` or `BROWNOUT` mean the previous run died and this line is
your only witness), `N ihågkomna nät i NVS` and `N nät i jaktlistan`
(the remembered-network list and the candidate hunt — `docs/wifi.md`),
the WiFi scan table (deliberately permanent — it is
the ground truth for "which networks can the 2.4 GHz-only S3 actually
see"), `WiFi uppe ("...")`, `tid synkad`, then steady-state `hämtning ok`
lines every 30 s and a `heap:` line every 10 s.

**Blind spots to know about:**

- Nothing persists. A panic prints a backtrace and reboots; if no monitor
  was attached at that second, the evidence never existed. The boot
  banner names the *reason* for the last restart, but there is still no
  coredump partition and no reboot counter (OBS-02, OBS-03).
- The log level and console routing are inherited IDF defaults, not
  pinned in `sdkconfig.defaults` like everything else is (OBS-28).
- Fetch failures name a *redacted* target (scheme, host, and whether LAN
  or the relay was tried) because the relay URL's path is a credential.
  ESP-IDF's own `HTTP_CLIENT` tag would print the whole request line at
  `DEBUG`, but `ESP_LOGD` is compiled out at the inherited default level;
  raising it reopens that (OBS-35).
- Serial-monitoring a *running* board is physically unverified: the panel
  draw can bounce the board off a computer USB port
  (`docs/superpowers/reviews/2026-08-13-max-tracker-physical-static.md`).
  Expect to need a powered hub or a PSU/data split; treat a monitor
  session that keeps disconnecting as a power symptom, not a firmware one.

### 2. Tokenserver stderr

Timestamped `logging` output on stderr. The rule is **transitions, not
state**: a probe status change logs one line, then silence until it
changes again — so a healthy week is a handful of lines and anything
repeating deserves attention. What a healthy boot looks like:

```
2026-08-13 21:21:47 INFO startar: rev 7385cb3
2026-08-13 21:21:47 INFO förstaskanning 2.3 s: … tokens idag, …
2026-08-13 21:21:47 INFO serverar http://0.0.0.0:8737/api/tokens, …
2026-08-13 21:23:47 INFO claude-probe: start -> usage_http_200 + ok
```

- **`claude-probe: X -> Y`** — every probe status transition: a 401
  appearing, a 429 backoff starting, and the recovery back to ok.
- **`agent-status <context>: <ErrorName>`** — throttled to one per error
  type per 30 s, deliberately content-free (privacy: never a path or
  message from your sessions).
- **`500 på /api/…` + traceback** — any route serving a 500 now logs its
  cause; the LAN response stays the sanitized `{"error": ...}` contract.
  A traceback in this log is a server bug worth filing.
- Access logging stays muted (a 30 s poll must not fill the file), but
  HTTP-level *errors* log again — the old mute silenced both.

Under launchd (`se.torget.tokenserver.plist`) both streams append to
**`~/Library/Logs/torget-tokenserver.log`** — visible in Console.app,
survives reboot, and the server self-rotates it at startup past ~5 MB
(tail preserved in `.old`). A missing `~/.claude/projects` no longer
crash-loops: the server logs one warning and waits for the directory,
with `ThrottleInterval` as the backstop. The plist hardcodes
`WorkingDirectory` to `~/Torget/tools/tokenserver`; if the repo lives
elsewhere, launchd is silently running *different code than you're
editing* — that exact trap cost an hour once and is why `GET /` reports
`rev` ([lessons.md](lessons.md)) and why the smoke test compares it to
your checkout.

Under Task Scheduler, `install-windows-task.ps1` starts
`run-windows-task.ps1`, which appends both streams to
**`%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log`** as the signed-in
user. The wrapper rotates an oversized file before Python starts; the
tokenserver's own hourly identity-checked rotation guard keeps the running
process bounded too. One `.old` file preserves the previous tail. The
scheduled command contains the checkout/interpreter paths and optional
numbers-relay URL, but provider/detail choices stay in the saved private
configuration.

Still invisible from the log: per-request keychain nuance (OBS-20) and
the probe's backoff-streak value (OBS-18) — those live only on `GET /`
or nowhere yet.

### 3. `GET /` — the richest diagnostic surface

```
curl -s http://localhost:8737/ | python3 -m json.tool
```

Returns live server state, added after real debugging nights:

- `rev` + `srcFingerprint` + `startedAt` — which code is actually
  serving, since when. `rev` should equal
  `git -C <repo> rev-parse --short HEAD`; `srcFingerprint` is a content
  hash of the loaded source taken at startup, which catches what rev
  cannot — a dirty worktree, or files edited after the process started
  (the smoke test compares both). A recent `startedAt` you didn't cause
  means crash-looping (see comb step 2).
- `claudeProbe` — the quota probe's status string. The full
  value→meaning→action table lives in
  [agent-setup.md](agent-setup.md); headline values:
  `usage_http_200 + ok` (healthy), `no_claude_oauth_token`,
  `usage_http_401`, `usage_http_429 + backoff_until_HH:MM`,
  `usage_request_failed: <Type>`, `probe_crashed: <Type>` (the probe
  itself hit a bug — the log has the traceback). On macOS a
  `no_claude_oauth_token` carries the keychain's own word after a colon
  (OBS-20): `keychain_denied_or_locked (exit N)` is the prompt clicked
  Deny or a locked keychain, `keychain_no_entry` never logged in on this
  account, `keychain_timeout` a prompt left unanswered,
  `keychain_security_missing` / `keychain_malformed` the tool or the
  record itself. The string is assembled per probe cycle and published
  once, so it never reads half-built.
- `claudeProbeStreak` / `claudeProbeIntervalS` / `claudeProbeCooldownLeftS`
  / `claudeProbeAgeS` — the backoff behind `claudeProbe` (OBS-18):
  consecutive failed cycles, the current gap between cycles (240 s,
  doubling per miss to 960 s; 15 s while waiting on a local token), seconds left
  of a 429 rest (`null` when not resting) and seconds since the last
  completed cycle (`null` before the first). Dashes on the screen look
  the same whether the probe is failing every four minutes or resting;
  these say which. The smoke test prints them beside a non-ok status.
  All of them, the status string, the credential block and the header
  evidence are copied under one lock, so a response never pairs one
  cycle's status with another's numbers.
- `claudeCredential` — the content-free pre-expiry guard for the saved Claude
  Code credential: `ready`, `expiring`, `expired`, `unavailable`, or
  `unknown`, plus whole `expiresInMin` when known, and on macOS a `reason`
  beside `unavailable` (the same keychain word as in `claudeProbe`). It
  never contains OAuth token values or account data. Startup, doctor, and the smoke test warn 30
  minutes before expiry instead of waiting for Fable to become stale.
- `claudeLocalUsage` — the passive Claude Desktop fallback for the general
  week: `fresh_applied` means the official local plan history is newer than
  the readable OAuth copy and was combined with a still-valid authenticated
  reset; `oauth_newer` means the normal probe already has newer truth.
  `fresh_without_reset` refuses to invent a reset, while `missing`, `stale`,
  `invalid*`, and `unsupported` explain why the local file was not trusted.
  This fallback never marks the named Fable/Opus model pool fresh.
- `ratelimitHeaders` / `unknownRateLimitBuckets` — header names seen by
  the fallback probe in the **most recent** cycle; a cycle that never
  reached the fallback (the usage contract answered, or every token was
  rejected first) publishes them empty, so they never sit hours-old
  beside a current failure. A non-empty `unknownRateLimitBuckets` means
  Anthropic added a bucket we don't map yet: file it.
- `usageComputeOk` / `usageComputeFailingForS` — whether the recompute
  behind `/api/tokens` is healthy. `false` means the served token totals
  are frozen at their last good value while *looking* fresh; the smoke
  test turns this into a FAIL, and the log has the cause
  (`usage-omräkningen kraschade`).
- `usageTotals` — `{state, placeholder, sinceS|ageS}`: what the four
  volume counters on `/api/tokens` are right now. `refreshing` = the first
  history scan is still running and the counters are placeholder zeros
  (`placeholder: true`, `sinceS` since start; quota percentages in the
  same payload are live); `ready` = the last completed scan, `ageS` old;
  `failing` = the recompute is crashing: frozen (`ageS`) or, if no scan
  ever completed, still placeholders. The same block rides on
  `/api/tokens`, but **only to a client that sends `X-VibePulse-Accepts:
  usage-totals`**; any other client gets HTTP 503 in the error form with
  the block beside it, so an older panel keeps its last values instead of
  applying zeros. Firmware from 2026-09-10 sends the header and leaves the
  value page alone while `placeholder` is true. Smoke: `refreshing` is a
  VARN, never a FAIL. Doctor: `WAIT` for `refreshing`, `FIX` for `failing`.
- `maxTrackerSaveOk` / `maxTrackerSaveFailingForS` — whether the Max
  Tracker state file can be written. `false` (typically `ENOSPC` or a
  permissions change) means observations are held in memory and retried
  on the next mark (OBS-10); the smoke test warns, the doctor prints FIX,
  the log has the cause throttled to one line per five minutes.
- `interactions.relay` / `interactions.agentStatusRelay` — independent saved readiness for
  encrypted approvals and encrypted live rows. `off` is the safe default;
  `disabled` includes a content-free reason, never agent/project text.

The firmware relay diagnostic snapshot is also content-free. Its
`status_polls_ok`, `status_applied`, and `status_cleared` counters distinguish
healthy empty polling, accepted live rows, and the one-shot stale clear when a
debugger or future local diagnostic surface reads it. They are reset on app
start and are not persistent telemetry.

Not exposed yet, so invisible from outside: the probe's failure streak
and slowed interval, and any Codex-side probe status (OBS-18). This
endpoint is also absent from the runbook (OBS-23) — this section is
currently its only documentation.

### 4. Server state files

`~/Library/Application Support/VibePulse/`:

| File | Content | Retention |
|------|---------|-----------|
| `usage-history.json` | quota trend points, ≥15 min apart | 8 days |
| `quota-cache.json` | last-known quota truths + reset times | until reset passes |
| `max-tracker.json` | daily peaks, streaks, backfill watermarks | 400 days |

All three are written atomically (temp + fsync + rename + parent-directory
fsync, OBS-21). An unreadable one — invalid JSON, non-UTF-8 bytes, or the
wrong top-level shape — is **quarantined, not overwritten** (OBS-11): the
store moves it to `<name>.corrupt-<UTC stamp>` beside the original, logs
one `tokenserver.state` WARNING with the file and reason, and starts
empty. During a comb, a `*.corrupt-*` file in this directory is a finding:
the bytes are usually mostly intact and worth a look before deleting.
`python3 -m json.tool < file > /dev/null` is still the quick parse check.

### 5. The screen itself

The display is a diagnostic surface with exactly three words, all
governed by the honesty invariant (never invented zeros):

- **Dashes** — no data ever received for that field. Before first fetch,
  or the source is genuinely absent. Persistent dashes = fetch/config
  problem, use the symptom table in [agent-setup.md](agent-setup.md).
- **`STALE`** — data exists but the last `/api/tokens` success is >120 s
  old, or the server marked its own numbers stale. Freeze-frame, not
  live.
- **`NO DATA` / `USAGE UNAVAILABLE`** — the per-field honest absence.

Caveat: the 120 s freshness clock is fed **only by `/api/tokens`**. If
the max-tracker or agent-status feed dies while `/api/tokens` keeps
succeeding, their pages keep reading as live (OBS-09). Until fixed, a
"LIVE" header is not proof for those two feeds.

Current relay-configured firmware has a separate recovery guard for this quota
clock. It arms only after a real success and only while Wi-Fi still reports an
association. At 60 seconds without another quota success it recycles the
station transport and wakes the quota task, which waits for a new IP before
retrying. If no real success follows within another 45 seconds, it restarts the device once to
discard wedged HTTP/TLS task state. A reboot starts disarmed until a new real
success, so a persistent upstream outage cannot become a reboot loop. LAN-only
builds deliberately do not arm the guard: a sleeping host is normal, not proof
that the radio is wedged. A release PASS still requires recent direct polling
and a repeated physical interaction after the stale window; either recovery
action firing is evidence of recovery work, not health evidence by itself.

When the final escalation calls `esp_restart()`, a complemented RTC marker
survives exactly that software reset. The next boot validates both words and
the ESP reset reason, clears the marker, and adds only
`X-VibePulse-Recovery-Boot: http-stall-v1` to local HTTP requests. The
tokenserver accepts only that exact single header after the normal two-poll
panel confirmation and reports the content-free boolean as
`GET /` → `interactions.panel.httpStallRecoveryBoot`. Power-on, brownout,
malformed/duplicate headers, loopback requests, and a lone LAN request cannot
claim it. This closes the wall-power observability gap, but it is still
self-reported recovery evidence—not proof that the glass stayed fresh or that
an interaction completed.

The trusted Codex plugin `SessionStart` hook consumes this same local health
surface plus `/api/tokens` under a sub-second bounded deadline. It emits only a
fixed fault class: server/API unavailable, plugin-server version drift,
provider stale, panel LAN waiting/device path stale, healthy, or healthy after
self-recovery. It does not copy dynamic paths, revisions, addresses, account
data, quotas, or probe strings into the task.

### 6. CI logs

GitHub Actions, four jobs: a `host-gate` job that runs the same
`./test/run.sh` as the bench (C test binaries, visual landmarks under
xvfb, crypto vectors, hardware registries, skill contracts, the
tokenserver suite), the jobs covering the JS suites it skips via
`--skip-js` (the npm-cached interaction-relay job runs the Worker suite;
the tokenserver matrix job runs the relay mailbox one), and an ESP-IDF
firmware build. Since OBS-24 closed (2026-08-21), red
`./test/run.sh` means red CI too — the remaining gap is only
platform-shaped (CI is Linux; macOS-only quirks still need the bench).

## Known bad signatures

Verbatim strings worth grepping for, and what they mean:

| Signature | Source | Meaning / action |
|-----------|--------|------------------|
| `WiFi tappat ("…", orsak 201)` | fw `torget` | network invisible: wrong SSID or 5 GHz-only. 15/204 = bad password. |
| `ingen tid från SNTP ännu` | fw `torget` | clock unset — but fetches proceed anyway and TLS fails as generic transport errors (OBS-15). Treat later cert/transport noise as *this*. |
| `hämtning misslyckades: ESP_ERR_… (http://<värd>:8737 via LAN)` | fw `torget-http` | transport failure. The target is scheme + host + route only: the relay's path is `/u/<secret>` and *is* its access key, so no fetch log may carry a path (`docs/relay.md`). Wrong hostname and wrong port still show up here; *which* endpoint failed comes from the `tokens`/`github-net` line beside it. `okänd adress via …` means the address had no scheme or host at all. |
| `oväntad statuskod 404 (https://<värd> via relä)` | fw `torget-http` | the target answered but not with 200. `via relä` says the cloud mailbox answered, `via LAN` the local tokenserver — the two are otherwise indistinguishable now that the path is gone. |
| `kroppen större än … byte, avvisad` | fw `torget-http` | payload over cap — server-side schema growth. See lessons: the 1058-byte incident. |
| `hämtningen avvisad, värden står kvar` | fw `tokens` | fetch rejected. If no `torget-http` line explains it, the parser rejected the schema — suspect server/firmware version skew (OBS-22). |
| `agentstatus avvisad: transportfel, IO-fel (öppna/läsa)` | fw `agent-net` | the agent feed's connection or read failed (OBS-12). This poller drives `esp_http_client` itself, not through `torget-http`, so there is no companion line with the target: the line stands alone, and the host it was polling is the one the service discovery or `TK_AGENT_STATUS_URL` chose at that time. Before 2026-09-10 this line always said `ESP_FAIL` whatever the cause. An over-cap body is checked before the transport result and logs as the next row, so `transportfel, överflöde` never appears. |
| `agentstatus avvisad: svar större än N byte` / `HTTP 503` / `ogiltigt format` | fw `agent-net` | the host answered but the response was not applied: over the cap, non-200, or the parser rejected it (schema skew, OBS-22). Each of these counts as a miss for the backoff below. |
| `N missar i rad — hämtar var N s tills tjänsten svarar` / `tjänsten svarar igen efter N missar` | fw `tokens` | the tokens poller slowing down (30 s doubling to 300 s) and recovering (OBS-13). Logged on the transition only, never per miss: one line per step is the whole outage story. |
| `agentstatus: N missar i rad — pollar var N ms …` / `agentstatus svarar igen efter N missar` | fw `agent-net` | same for the agent feed (1 s doubling to 30 s). A miss is any response that was not applied, so a host that answers 200 with a rejected body backs off too. |
| `max tracker: N missar i rad — hämtar var N s` | fw `tokens` | same for the Max Tracker poll (5 min doubling to 30 min). |
| `GitHub-flödet: N missar i rad — hämtar var N s …` / `GitHub-flödet svarar igen efter N missar` | fw `github-net` | same for the optional GitHub feed (30 s doubling to 300 s). |
| `agentstatus kunde inte skapa HTTP-klient` | fw `agent-net` | agent feed **dead until reboot**; screen shows a frozen header meanwhile (OBS-12). |
| `heap: internt … DMA största …` | fw `torget` | every 10 s. Watch the DMA largest block: its collapse predicted the 2026-08-06 panel freeze. Nothing alerts on it yet (OBS-27). |
| `overlaykostnad <namn>: LVGL-pool +N B …, internt ±N B …` | fw `torget` | three lines, once at boot: what each permanent top-layer overlay (wifi-setup, settings, ota) costs. The pool figure is PSRAM (LVGL's TLSF pool lives there since the 2026-08-16 freeze fix); the internal figure is the control — a zero delta means that overlay does not touch internal RAM at all. This is the measured budget the AMOLED rule requires for a persistent layer, so read it after any flash that adds or grows one. |
| `Guru Meditation` / `abort()` / backtrace | fw | panic. Capture the whole backtrace *now* — it will not survive the reboot (OBS-02). |
| `Task watchdog got triggered` | fw | a task starved IDLE — the only hang ever seen on hardware surfaced this way. |
| `omstartsorsak PANIK` / `TASKVAKTHUND` / `BROWNOUT` | fw boot banner | the previous run died and this line is the only witness. BROWNOUT → suspect the power supply first. |
| `hittar inte … — finns Claude Code på den här maskinen?` | server | logged once at boot; the server waits for the directory instead of crash-looping. Seeing it repeatedly means something else is killing the process. |
| `500 på /api/…` + `Traceback` | server log | a route served the sanitized error-form and this is its cause — a server bug, file it. Any traceback *without* a `500 på` line above it is doubly interesting. |
| `usage-omräkningen kraschade` | server log | `/api/tokens` is serving frozen totals that look fresh. `usage-omräkningen frisk igen` closes the episode; until it appears, distrust the day/month numbers. |
| `ratelimit-header: …` | server stdout | the *fallback* probe engaged — the primary usage endpoint returned nothing mappable. Not part of a healthy boot despite what the README implies (OBS-23). |
| `claudeProbe: usage_http_429 + backoff_until_…` | `GET /` | rate-limited; probe is resting ≥10 min. Do not restart the server to "fix" it — that resets the backoff and feeds the penalty (see lessons: the 429 night). |

## The comb routine

Run every week or two, and after any incident. Every step is a command
plus a question; an agent asked to **"comb the logs"** follows this list
top to bottom and reports findings against the backlog. With the
tokenserver on the host computer and the board on its shelf, steps 1–5 need no
hardware handling at all.

Steps 1–4 are automated: **`python3 tools/tokenserver/smoke.py`** runs
them as one command (exit 0/1/2 = ok/warnings/failures). Start there;
the manual detail below is for interpreting what it flags — and steps
5–7 are judgment calls no script makes for you.

1. **Identity.** `curl -s http://localhost:8737/ | python3 -m json.tool`.
   Does `rev` match `git rev-parse --short HEAD` in the repo launchd runs
   from (check `WorkingDirectory` in the plist — not necessarily this
   checkout)? Is `startedAt` older than the last reboot, i.e. no silent
   crash-looping?
2. **Probe health.** Same payload: `claudeProbe` should be
   `usage_http_200 + ok`. Anything else → the table in
   [agent-setup.md](agent-setup.md). `unknownRateLimitBuckets` non-empty
   → new upstream bucket, file a backlog item.
3. **The log file.** On macOS, `wc -c
   ~/Library/Logs/torget-tokenserver.log`; on Windows inspect
   `%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log`. A missing file
   under launchd or Task Scheduler means the service never reached its
   logging entrypoint.
   `grep -c serverar` — more than one per intended restart means
   crash-looping. `grep -n Traceback` — any hit is a bug; the `500 på`
   line above it names the route. `grep -c 'agent-status'` — a large
   count means a persistent throttled error has been repeating every
   30 s. `grep 'claude-probe:'` — the transition history: when did
   things break, when did they recover.
4. **State files.** For each file in the platform state directory
   (`~/Library/Application Support/VibePulse/` on macOS or
   `%LOCALAPPDATA%\VibePulse\` on Windows): does it parse
   (`python3 -m json.tool < f > /dev/null`)? Is the mtime recent for
   `usage-history.json` (should move every ≤15 min while you work)? Did
   `max-tracker.json` shrink dramatically since last comb (silent
   corruption reset, OBS-11)?
5. **Screen truth.** Glance at the panel: dashes or `STALE` anywhere data
   should be live? Remember the max-tracker/agent caveat (OBS-09): a live
   quota page does not vouch for the other feeds — compare the heatmap
   against `curl -s http://localhost:8737/api/max-tracker`.
6. **Device serial (when practical).** Attach `idf.py monitor` (mind the
   power caveat in source 1), watch one full poll cycle: any signature
   from the table above? Note the DMA largest-block number and compare
   with the last comb. If the board rebooted since last time you can't
   tell today (OBS-01) — that is the point of that backlog item.
7. **Close the loop.** Every oddity becomes either a backlog entry
   (observability-backlog.md, with the evidence you just collected), a
   fix now (small + obvious), or a lessons entry (root-caused stories).
   Update the `Last combed:` line at the top of the backlog. A comb that
   files nothing and updates the date is a legitimate result.

## How findings flow

```
comb / incident
      │
      ▼
observability-backlog.md   (queue: what to look into and fix)
      │  fixed
      ▼
CHANGELOG.md ### Fixed     (release-facing summary)
      │  when there is a story
      ▼
lessons.md                 (root cause → the rule we now follow)
```

Commit messages stay the primary narrative — write the full story there
as before — but lessons.md is the index that makes those stories
findable without `git log -p`.
