# Changelog

Notable changes to VibePulse. Release notes for tagged versions are published
on the [releases page](https://github.com/niclasvestlund-YT/vibepulse/releases).

## Unreleased

### Added

- **Startup health for Needs You.** Every new Codex task now checks the local
  bridge, saved Codex route, relay state, and a content-free proof that the
  panel has polled recently. Two close LAN polls are required before the
  result can turn green; one curl, USB enumeration, silence, or relay setup
  alone never claims the glass is reachable. It and the general setup doctor
  now also flag saved Codex modes (`never`, `auto_review`, or
  `danger-full-access`) that suppress user permission cards. The doctor
  includes the relay/device-key pairing checks too, and finds an installed
  Python 3.11+ instead of falsely stopping at macOS's older system Python.
- **The panel travels.** It remembers six places in NVS and joins the one
  that worked most recently; arriving somewhere new no longer means editing
  `secrets.h`, rebuilding and flashing over USB — which OTA could never fix,
  since OTA needs the network the panel cannot reach. Two ways to teach it a
  place: `tools/wifi-here.sh` on the Mac hands over the network it is
  already on (reading the password from the keychain, one prompt, nothing
  typed), or the panel raises `VibePulse-setup` with the password on the
  glass and serves a captive portal listing what its *own* radio can see.
  The window opens by itself after 90 s without an IP, or on a 3 s KEY3
  hold, and closes after ten minutes — the access point, HTTP server and DNS
  responder do not exist outside it (the lazy-surface rule from the
  2026-08-14 freeze). The `secrets.h` networks stay as an immutable floor
  underneath, so no entry can ever cost a USB rescue, and the setup window
  can never write firmware. Full reference: `docs/wifi.md`.
- The **relay**, end to end: the panel can now get its numbers from
  anywhere with internet, instead of only from the same LAN as the
  service. Born the same evening as the travel work: a guest network's
  client isolation kept the panel from reaching the Mac while internet
  worked fine, and no code on the panel could fix that. Three parts, one
  boundary:
  - *Panel*: fetches try the LAN first and fall back to the mailbox
    (`TK_VIBEPULSE_RELAY_URL` in `secrets.h` — commented out by default;
    without it nothing changes).
  - *Service*: `--publish <url>` POSTs the same three payloads the LAN
    endpoints serve — send-on-change plus a 5-minute heartbeat, staying
    inside Cloudflare KV's 1 000 free writes/day by design. Several
    machines may publish to one mailbox; every send names its publisher.
  - *Mailbox*: a ~150-line Cloudflare Worker (`tools/relay/`) that merges
    freshest-per-pool on read using the observation stamps the staleness
    logic already carries — Claude from whichever machine asked Anthropic
    last, Codex from whichever machine ran Codex last.
  The boundary is enforced from three directions
  (`test/test_relay_boundary.py`, `test_publisher.py`, the Worker's path
  allowlist): the relay carries *numbers* (quota, burn rate, Max Tracker,
  GitHub), never *activity* (agent status, Needs You, the device key's
  answer path — those stay on the LAN). Full design: `docs/relay.md`.
- **Windows autostart** for the tokenserver
  (`tools/tokenserver/install-windows-task.ps1`): a scheduled task running
  as the logged-in user (never SYSTEM — the credential file lives in the
  user profile), restarting on failure, logs in `%LOCALAPPDATA%\VibePulse\`.
  Closes the gap in issue #3.
- **Hold KEY3 twice to reach WiFi setup on a connected panel.** The setup
  window used to open only when the panel had no network — you could not
  pre-load the phone hotspot at home before a trip. Now a second full 3 s
  hold while the update window is open switches to WIFI SETUP. Any release
  before three seconds still just closes (the 2026-08-16 escape hatch is
  untouched); the port-80 handover between the two windows' HTTP servers is
  owned by the setup guard, so they never collide.

  **Hardware status, honestly:** the first physical exercise of this path
  wedged the panel twice (2026-08-17; rolled back to the previous release
  over USB). Suspected DMA starvation by the access point — the exact
  2026-08-16 freeze anatomy — pending the incident's serial log.
  `window_open()` is now bracketed by two host-tested DMA gates (refuse below 3x
  the flush's contiguous block — calibrated against v0.5.0's measured
  40-47 kB healthy baseline, so a healthy panel is never refused — abort
  below 2x after the APSTA switch) with per-stage DMA logging. The gates are defensive, not a
  verification: the setup window stays unproven on hardware until a
  supervised run passes.
- The glass explains a missing network instead of showing dashes. After 60 s
  without an IP it names the network being hunted and translates the radio's
  own disconnect reason — "NOT SEEN - 2.4 GHZ ONLY", "WRONG PASSWORD". The
  reason codes were already in the serial log; a shelf gadget nobody has a
  cable to could not show them.

### Changed

- **CI now runs the whole host gate**, not a subset (OBS-24). A `host-gate`
  job executes the same `./test/run.sh` as the bench on every push — the C
  test binaries, wiring and capacity tests, the Mbed TLS crypto vectors
  (against a sparse clone of the IDF-pinned sources) and the SDL landmark
  captures under `xvfb-run`. Only the JS suites are skipped (`--skip-js`);
  their own jobs still run them — the Worker suite npm-cached in the
  interaction-relay job, the relay mailbox test in the tokenserver job.
  The tokenserver module list
  moved to `test/tokenserver-suite.txt` — one list shared by `run.sh` and
  CI, with a completeness guard so a new test module cannot silently stay
  outside the gate (the PR #11 lesson, made structural). Two
  `test_vibepulse_codex_plugin.py` cases learned Linux along the way: the
  doctor-probe expectation now resolves `/bin/sh` (a dash symlink on
  Debian-family runners), and the descendant-kill assertion accepts a
  SIGKILLed orphan that pid 1 has not reaped yet.

### Fixed

- Hook review instructions now say where `/hooks` actually exists: inside the
  interactive Codex CLI in a terminal, not in the desktop task composer. The
  doctor and setup output use the same wording, so "no commands" in the
  desktop composer is no longer misdiagnosed as a missing VibePulse hook.
- Open networks were refused in silence. Every network was applied with
  `threshold.authmode = WIFI_AUTH_WPA2_PSK`, so an open café or airport
  network — the common case on the road — was rejected before it was tried,
  with nothing in the log pointing at the threshold. The authmode now
  follows each network: open where the password is blank.

- The panel names all three GPT-5.6 variants. `gpt-5.6-sol` had a typeset
  screen label while its siblings `terra` and `luna` fell through to their
  raw lowercase ids — the price table knew all three, the screen knew one,
  so the agent tile read `gpt-5.6-terra` next to a properly set `OPUS 5`. A
  test now also holds every label inside `TK_AGENT_MODEL_CAP`, reading the
  cap from the firmware header rather than restating it. Spotted on Erik
  Elfström's T-Display-S3 fork. The wider fallthrough — ~110 priced models,
  six named ones, and dated ids that truncate mid-string — is written up as
  OBS-30 rather than fixed here.
- CI's tokenserver job runs the same eleven test modules as `test/run.sh`.
  The lists had drifted four suites apart — `test_value_meter`,
  `test_update_prices`, `test_codex_usage` and `test_interactions` ran only
  in the local gate — which is exactly how a green CI hid a runtime
  `NameError` in the rebased Windows branch (PR #11): the missing
  `test_interactions` catches it immediately.

### Added

- The tokenserver reads Claude's OAuth token on Windows. Claude Code has no
  keychain integration there, so `claude login` writes the same
  `{"claudeAiOauth": {...}}` record the macOS keychain holds to a plain file,
  `%USERPROFILE%\.claude\.credentials.json`; the probe now reads it when
  running on Windows and skips the two macOS-only sources (`security`,
  `pgrep` for Claude Desktop's injected token) that cannot exist there. macOS
  behaviour is untouched.

  Two things had to give way for that read to be reachable at all: `fcntl`
  is not importable on Windows, so the module could not even load, and the
  machine-wide single-probe lock was built on `flock`. The import is now
  guarded and the lock takes `msvcrt.locking` where `flock` is missing —
  same non-blocking gate, different syscall — so the 429 guard survives the
  port instead of quietly disappearing with it.

  The Codex half works there too. Its quota read spawns `codex app-server`
  and polled stdout with `select.select`, which on Windows accepts sockets
  and never pipes; it now reads through a queue fed by a daemon thread, the
  same code on every platform. That path had no test at all — every existing
  test mocked the reader out and exercised only the parser — so it now has
  three, driving a real subprocess through the real pipe for the reply,
  timeout and immediate-death cases. Writing them turned up a leak worth
  fixing on its own: the pipes were never closed, leaving three descriptors
  per poll to the garbage collector in a service that polls every 30 s and
  never restarts.

  State and logs moved off the hardcoded `~/Library` paths to a per-platform
  directory — `%LOCALAPPDATA%\VibePulse\` on Windows, unchanged on macOS.
  The old paths worked literally on Windows but planted a `Library` tree in
  the user profile that nothing else on the machine recognises.

  What remains for [#3](https://github.com/niclasvestlund-YT/vibepulse/issues/3)
  is autostart: the launchd plist has no Windows equivalent, and `smoke.py`
  now finds the right state directory but still tells you to run `launchctl`.
  Reported by Erik Elfström, who found it porting a fork to a LilyGO
  T-Display-S3.
- The completion alert finally pulses. The accent outline and icon ring
  breathe (full → 39 % → full, ease-in-out, four 1200 ms cycles filling the
  PULSE phase exactly) and then rest; text and the provider icon stay solid
  for readability. Proven pixel-by-pixel in the simulator and reviewed on
  the physical panel
  ([review](docs/superpowers/reviews/2026-08-14-completion-pulse-physical-motion.md)).
  The static attention gate now permits exactly this one animation and pins
  its shape.

## v0.2.1 — 2026-08-13

Server fixes verified live on a real installation the same evening; the
firmware alert fix reaches a device on its next flash.

### Fixed

- Repeated probe failures now slow the probe down (120 → 240 → 480 s cap), so
  a dead token can never again hammer the API every two minutes for hours —
  the pattern that earned tonight's 429 penalty. A successful probe restores
  the normal pace. The root endpoint also reports `rev` and `startedAt`, so a
  stale running process (wrong directory, old code) is visible in one curl.
- The Claude probe backs off on HTTP 429: it stops the cycle immediately (no
  second token source, no header probe — extra traffic only extends the
  penalty) and rests for at least ten minutes, honouring a longer
  `Retry-After` when the API sends one. `claudeProbe` shows
  `usage_http_429 + backoff_until_HH:MM` while resting.
- The Claude probe no longer requires an active 5-hour session window to
  count as successful. Between windows the usage API reports the session row
  with a lapsed reset, and the probe used to discard the still-valid weekly
  numbers, fall back to the header probe, and report its 401 instead — so the
  screen lost all Claude data for the gap after every window ended. Weekly
  and per-model figures now go through on their own; the session field shows
  a dash until the next window opens. The header-probe fallback also appends
  its outcome (`; fallback_http_…`) instead of overwriting the usage status,
  so `claudeProbe` keeps the evidence.
- The tokenserver's Claude probe no longer trusts a stale token frozen into a
  long-lived Claude Desktop child process. `ps eww` reports the environment as
  of process launch, so a Desktop child that outlives its token kept serving
  an expired value that outranked a fresh `/login` in the keychain — the
  screen sat on `http_401` until Claude Desktop was quit. The probe now tries
  each token source in order and falls back on 401/403.
- Firmware: full-screen alerts (NEEDS YOU, DONE, ERROR) now require the state
  change to be fresher than 2 minutes after boot too, not only on the first
  snapshot. Waiting states that are hours old — rediscovered after a
  tokenserver outage or restart — no longer take over the screen; they appear
  in the header only. Reaches a device on its next flash.

### Known

- The alert's pulse phase has no visual effect yet: the 4.8 s PULSE phase and
  the STATIC phase render identical frames, so the alert appears without any
  attention-drawing motion. An actual pulse is motion work gated behind the
  AMOLED review protocol (simulator frames, static physical review, measured
  motion on the panel).

## v0.2.0 — 2026-08-13

One app: VibePulse is the only app in the repository and the screen boots
into it. Corrected README claims (the six real pages, the privacy scope of
what the screen receives and what a lost screen carries). `secrets.h.example`
ships its URLs active with a `DIN-MAC` placeholder instead of commented out.
New `docs/agent-setup.md` runbook for coding agents. Companion apps resolve
during ESP-IDF early expansion; the host test gate runs headless on Linux.

## v0.1.0 — 2026-08-13

First public release. Its tag predates the history cleanup and no longer
builds from a fresh clone; superseded by v0.2.0.
