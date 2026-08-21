# Observability: every log this system generates

VibePulse spans two machines — a screen with no persistent storage and a
Python service on a Mac — and each produces evidence in a different place,
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
| 2 | Tokenserver stderr | terminal, or `~/Library/Logs/torget-tokenserver.log` under launchd | durable; self-rotated at ~5 MB (tail kept in `.old`) |
| 3 | `GET /` diagnostic endpoint | `http://<mac>:8737/`, live state | process lifetime |
| 4 | Server state files | `~/Library/Application Support/VibePulse/` | durable (8 d / 400 d retention) |
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
| `torget-http` | `components/torget_net/torget_http.c` | every GET: error name, status, cap, URL; `LAN svarade inte, provar reläet` when a fetch fails over to the relay |
| `tokens` | `components/app_tokens/net.c` | /api/tokens + /api/max-tracker polls |
| `agent-net` | `components/app_tokens/agent_net.c` | /api/agent-status poll (1 Hz, log rate-limited to 30 s) |
| `github-net` | `components/app_tokens/github_net.c` | the optional /api/github poll |
| `needs-you-net` | `components/app_tokens/needs_you_net.c` | signed verdict/panic POSTs (LAN only, never the relay) |
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
  itself hit a bug — the log has the traceback).
- `ratelimitHeaders` / `unknownRateLimitBuckets` — header names seen by
  the fallback probe. A non-empty `unknownRateLimitBuckets` means
  Anthropic added a bucket we don't map yet: file it.
- `usageComputeOk` / `usageComputeFailingForS` — whether the recompute
  behind `/api/tokens` is healthy. `false` means the served token totals
  are frozen at their last good value while *looking* fresh; the smoke
  test turns this into a FAIL, and the log has the cause
  (`usage-omräkningen kraschade`).

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

All three are written atomically (temp + fsync + rename). All three
**silently start over from empty if corrupt** — a bad `max-tracker.json`
discards up to 400 days of history with no message and no backup
(OBS-11). During a comb, validating these files is cheap insurance;
`python3 -m json.tool < file > /dev/null` is enough to know they parse.

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
| `hämtning misslyckades: ESP_ERR_… (http://…)` | fw `torget-http` | transport failure, with URL. Wrong hostname shows up here. |
| `kroppen större än … byte, avvisad` | fw `torget-http` | payload over cap — server-side schema growth. See lessons: the 1058-byte incident. |
| `hämtningen avvisad, värden står kvar` | fw `tokens` | fetch rejected. If no `torget-http` line explains it, the parser rejected the schema — suspect server/firmware version skew (OBS-22). |
| `agentstatus avvisad: transportfel ESP_FAIL` | fw `agent-net` | always literally `ESP_FAIL` — the real cause is discarded before logging (OBS-12). Only says "agent feed unhappy". |
| `agentstatus kunde inte skapa HTTP-klient` | fw `agent-net` | agent feed **dead until reboot**; screen shows a frozen header meanwhile (OBS-12). |
| `heap: internt … DMA största …` | fw `torget` | every 10 s. Watch the DMA largest block: its collapse predicted the 2026-08-06 panel freeze. Nothing alerts on it yet (OBS-27). |
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
tokenserver on the Mac and the board on its shelf, steps 1–5 need no
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
3. **The log file.** `wc -c ~/Library/Logs/torget-tokenserver.log`
   (missing file under launchd means the service never started).
   `grep -c serverar` — more than one per intended restart means
   crash-looping. `grep -n Traceback` — any hit is a bug; the `500 på`
   line above it names the route. `grep -c 'agent-status'` — a large
   count means a persistent throttled error has been repeating every
   30 s. `grep 'claude-probe:'` — the transition history: when did
   things break, when did they recover.
4. **State files.** For each file in
   `~/Library/Application Support/VibePulse/`: does it parse
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
