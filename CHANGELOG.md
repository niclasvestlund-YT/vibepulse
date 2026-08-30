# Changelog

Notable changes to VibePulse. Release notes for tagged versions are published
on the [releases page](https://github.com/niclasvestlund-YT/vibepulse/releases).

## Unreleased

### Fixed

- The wall-powered ESP32 now disables Wi-Fi modem sleep and carries a bounded
  VibePulse transport watchdog. After at least one good quota response, a
  still-associated panel with a configured numbers relay now recycles Wi-Fi at
  60 seconds, wakes the quota task, waits for a new IP before retrying, and
  performs one controlled restart if no real success follows within another 45 seconds. Cold boot is
  disarmed until a real success, preventing restart loops during upstream
  outages. Reusable encrypted relay clients are reset on every failure exit
  instead of retaining a half-open transport.
- The VibePulse Codex plugin advances to `0.1.7`. Its bounded SessionStart
  check now classifies the local server, plugin/server source drift, provider
  freshness, saved Claude credential risk, and direct panel contact as
  separate states. It injects only a content-free diagnosis into the task and
  never treats startup silence as approval. Doctor now surfaces recent
  direct panel polling without misclassifying a healthy relay-only setup, and
  the skill distinguishes fresh provider data from the device-side
  power/network/firmware hop, including computer-USB power limits and
  panel-compatible relay probes. It now also requires a repeated physical
  question beyond the stale window and recognizes the ping-alive/HTTP-stalled
  failure boundary instead of treating one post-boot success as durable. Its
  runbook also distinguishes multi-host DNS-SD ambiguity from stale data and
  routes shared-panel questions through the encrypted interaction relay. The
  troubleshooting flow now distinguishes the original credential incident
  from a fresh-source/ping-alive panel HTTP stall and describes the staged
  firmware recovery without claiming it has passed before physical proof.
- The local VibePulse MCP bridge now accepts the bounded `_meta` request field
  emitted by current Codex clients during tool discovery and invocation, so
  the physical `APPROVE` question remains available instead of failing MCP
  startup or rejecting an otherwise valid call.
- macOS now has a path-safe LaunchAgent installer/validator. It resolves the
  requested durable checkout, atomically rewrites the plist, preserves
  recognized private runtime configuration without printing it, and performs
  a real `bootout` + `bootstrap` so launchd cannot retain an old worktree. A
  failed bootstrap restores and reloads the previous service configuration.
- The LaunchAgent installer now retries only the short, observed post-`bootout`
  bootstrap race before rolling back; permanent failures remain fail-closed.
- The ESP-IDF dependency lock now records the exact resolver result used by the
  verified ESP32-S3 build, keeping flash candidates reproducible and clean.
- Windows Task Scheduler installations can now persist the optional public
  GitHub source, named Claude/Codex plan labels, and explicit per-provider
  subscription costs. The background service no longer drops the GitHub Stars
  or API-versus-subscription inputs that work in a foreground launch.

## v1.0.0 — 2026-08-28

Release notes:
[v1.0.0 — Windows joins the shelf](docs/releases/2026-08-28-windows-joins-the-shelf.md).

### Added

- Optional `_vibepulse._tcp.local` discovery lets one panel stay pinned to a
  healthy tokenserver and move between advertising macOS/Windows hosts after
  a bounded failure. The compiled URL remains the multicast-blocked fallback,
  and the host advert carries only protocol version and port.
- A versioned host-platform support matrix and reproducible Windows release
  gate now separate CI portability, real-host service evidence, and the
  physical panel loop. Linux remains explicitly unsupported until its XDG,
  credential, systemd, real-host, and panel gates pass.
- The Windows Task Scheduler installer has a non-mutating `-ValidateOnly`
  mode, rejects Python older than 3.11 before registration, and is parsed plus
  dry-validated on every Windows CI run. The runner's stdout/stderr capture,
  bounded rotation, and a path containing spaces plus non-ASCII characters
  are exercised there as well.
- Windows autostart now keeps a bounded diagnostic log under
  `%LOCALAPPDATA%\VibePulse\Logs` instead of discarding stdout/stderr; provider
  choices remain in the private saved config rather than the scheduled command.
- Setup doctor no longer rejects a healthy Python 3.11+ interpreter because
  of a cross-platform whitespace mismatch in its exact sentinel.
- A security policy, contribution guide, and pull-request evidence checklist
  document private reporting, secret handling, platform-claim discipline, and
  the difference between CI, real-host, and physical-panel validation.
- A sanitized post-v0.7.1 Windows checkpoint pins every real-host observation
  to its exact commit and keeps firewall, lifecycle, recent-panel, and physical
  rows explicitly failed or not tested instead of inheriting an older pass.
- A real Windows host now advertises `_vibepulse._tcp.local`, allowing the same
  panel to move between healthy Mac and Windows tokenservers without changing
  a compiled address. Discovery remains LAN-only and publishes no credential,
  prompt, account, or quota data.
- A sanitized exact-revision Windows report records a clean checkout, the full
  tokenserver suite, Task Scheduler start and watchdog recovery, bounded logs,
  real Claude/Codex source health, Private-only LAN reachability, recent panel
  polling, and the canonical physical `NEEDS YOU` → `APPROVE` answer loop.

### Changed

- VibePulse reaches `v1.0.0`: the core product promise—always-visible quota,
  live agent state, and an explicit human answer from the physical panel—is
  now exercised on both macOS and a real Windows host. The same Windows
  candidate subsequently passed real sign-out/sign-in, sleep/resume, and one
  full reboot with the scheduled service and recent panel polling intact.
- The Windows support matrix now records the completed core, physical, and
  persistent-lifecycle gates while retaining their exact revision boundary.

### Fixed

- The VibePulse Codex plugin advances to `0.1.2`. Its skill, setup doctor,
  and host smoke check now distinguish a currently live Claude process source
  from an expired saved fallback credential. Recovery is re-read locally
  within 15 seconds and no longer prescribes an unnecessary tokenserver
  restart.
- Windows Task Scheduler installation now uses the Windows 10-compatible
  `IgnoreNew` instance policy and explicitly stops only its own running task
  during an idempotent update; the previous `StopExisting` enum value parsed
  but failed before registration on a real Windows 10 host.
- Add a public Windows host installation, startup-health, troubleshooting,
  recovery, and physical-validation runbook for developers and coding agents.
- Windows Codex discovery now prefers OpenAI's standalone per-user CLI and
  rejects Store-managed `WindowsApps` aliases that can resolve successfully
  but fail with Access Denied under Task Scheduler or other background hosts.
  The README and tokenserver guide include the official install and doctor
  verification commands.
- The optional Codex MCP bridge now gives a panel question the complete
  120-second human-answer window instead of allowing Codex's shorter default
  tool deadline to turn a healthy physical flow into computer fallback.
- The ESP-IDF mDNS component is locked in the dependency manifest so clean
  firmware builds reproduce the Mac/Windows discovery code used by the panel.

## v0.7.1 — 2026-08-27

Release notes:
[v0.7.1 — health and panel reliability](docs/releases/2026-08-27-health-and-panel-reliability.md).

### Added

- Startup diagnostics now expose content-free Claude credential readiness and
  recent physical panel polling. `doctor` and the host smoke check warn before
  the readable credential expires and distinguish an authenticated client
  from a live panel path without returning tokens or panel addresses.
- A canonical Codex → panel → touch → Codex smoke test and recovery table now
  cover missing recommendations, physical fit/privacy fallback, stale
  worktree flashes, and font-glyph failures. Silence and computer fallback
  remain failures, never implicit approval.

### Changed

- The VibePulse Codex plugin is `0.1.1`. Its skill carries the verified short
  physical smoke payload and pre-flash version comparison so a fresh task does
  not invent a longer, buttonless diagnostic prompt.
- Relay publishing performs its first potentially expensive producer scan on
  its background thread, allowing the local tokenserver to bind immediately
  at login.

### Fixed

- Claude's general weekly quota no longer goes stale merely because the OAuth
  copies visible to the tokenserver expired while Claude Desktop kept working.
  A strict passive fallback reads only the official client's bounded local
  percentage history and reuses a still-valid authenticated reset; named
  Fable/Opus limits remain honestly stale until OAuth recovers.
- Claude login state can no longer make startup health look green when the
  separate credential readable by VibePulse is near expiry or already dead.
  The root endpoint reports only `ready`, `expiring`, `expired`, `unavailable`,
  or `unknown` plus whole minutes remaining; local recovery is rechecked every
  15 seconds.
- Mixed-case, numeric, and punctuated project names no longer render as boxes
  on the Needs You attract screen. The label now uses the existing full-ASCII
  `plex_ui_21` raster and is guarded by simulator and physical Swedish-copy
  checks.

## v0.7.0 — 2026-08-23

Codex joins the answerable Needs You flow, the panel gains phone-first Wi-Fi
onboarding, and the host service becomes portable across macOS and Windows.
Optional encrypted interaction and live-status relays keep the panel useful
when it and the computer are on unrelated ordinary internet Wi-Fi. Illustrated
notes: [Codex and any Wi-Fi](docs/releases/2026-08-23-codex-and-any-wifi.md).

### Added

- **Codex interactions on the panel.** The optional plugin bridges supported
  questions and a narrow safe-command approval tier into the shared Needs You
  UI. Provider/view-bound verdicts, bounded text, fail-closed setup, and strict
  allowlists keep unknown, mutating, secret-bearing, or ambiguous requests on
  the computer.
- **Encrypted Needs You across unrelated Wi-Fi.** A user-owned Cloudflare
  Durable Object mailbox carries fixed-size end-to-end encrypted request and
  verdict frames. It is separate from the numbers relay, uses outbound HTTPS
  only, and stays off until a provider, bounded detail, and the relay are each
  explicitly enabled.
- **Encrypted live agent status across unrelated Wi-Fi.** A third independent
  transport carries only minimized Claude/Codex rows, never the pending
  decision. Direct LAN wins; stale relay rows clear honestly.
- **Phone-first Wi-Fi setup.** The panel shows a scannable QR, serves a local
  network picker, tests credentials before saving them, and keeps every old
  recovery network after a failed trial. The top-right Wi-Fi mark now appears
  consistently across the launcher, apps, Needs You, OTA, and setup.

- **The panel travels.** It remembers six places in NVS and joins the one
  that worked most recently; arriving somewhere new no longer means editing
  `secrets.h`, rebuilding and flashing over USB — which OTA could never fix,
  since OTA needs the network the panel cannot reach. Two ways to teach it a
  place: `tools/wifi-here.sh` on the Mac hands over the network it is
  already on (reading the password from the keychain, one prompt, nothing
  typed), or the panel raises `VibePulse-setup` with a phone-first QR; the
  temporary password stays behind the Manual Setup fallback. Its captive
  portal lists what the panel's *own* radio can see.
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
  The numbers-only boundary is enforced from three directions
  (`test/test_relay_boundary.py`, `test_publisher.py`, the Worker's path
  allowlist): the relay carries *numbers* (quota, burn rate, Max Tracker,
  GitHub), never *activity*. The separately enabled encrypted interaction and
  live-status relays use a different Worker, credentials, protocol, and
  privacy boundary. Full designs: `docs/relay.md` and
  `docs/interaction-relay.md`.
- **Windows autostart** for the tokenserver
  (`tools/tokenserver/install-windows-task.ps1`): a scheduled task running
  as the logged-in user (never SYSTEM — the credential file lives in the
  user profile), restarting on failure, with state in
  `%LOCALAPPDATA%\VibePulse\`. The current background task does not persist
  stdout/stderr; use the root health endpoint or run manually for diagnostic
  logs. Closes the gap in issue #3.
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

### Desktop support

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

  Native Task Scheduler autostart, immediate start, restart-on-failure, and
  saved interaction-provider choices complete the Windows host path in this
  release. Reported by Erik Elfström, who found the original gaps while
  porting a fork to a LilyGO T-Display-S3.
- Renewed Claude credentials are detected and published promptly. A stale
  Claude Desktop process token can no longer leave a valid new login hidden
  behind cached `401` data until the next long probe interval.

## v0.6.0 — 2026-08-16

- **Needs You becomes an input device for Claude Code.** A held question or
  supported permission takes over the panel; a tap reveals the bounded view
  and APPROVE / DENY / LEAVE IT returns a signed verdict to the same live
  session. Walking away always falls back to the terminal.
- The shared LVGL takeover was rebuilt around the approved
  attract → decision → payoff flow, including long-text fit guards and a
  private fallback state.
- The LVGL pool moved to PSRAM to restore the internal DMA headroom the AMOLED
  flush needs, fixing a physical panel freeze.

## v0.5.0 — 2026-08-15

- Added the **value multiple** page: priced month-to-date Claude/Codex token
  usage divided by the plan cost the user explicitly declares. Unknown prices
  degrade to a dash instead of being guessed.
- Added the optional **GitHub project pulse**: stars/forks page plus a named
  new-star takeover, with screen, notification, and future sound as separate
  default-off switches.
- Fixed Codex resume/fork replay overcounting by grouping rollouts by session
  and using the most complete copy. Illustrated notes:
  [value and GitHub](docs/releases/2026-08-15-value-and-github.md).

## v0.4.0 — 2026-08-14

- Added the consent-gated A/B OTA platform: physical KEY3/touch consent,
  authenticated inactive-slot upload, image verification, a 15-second boot
  health gate, and automatic rollback.
- Added UPDATE READY, the OTA progress ring, and a boot screen driven by real
  Wi-Fi/time/data signals.
- Hardened the tokenserver against rejected tokens, concurrent probing, 429
  penalties, and stale build delivery. Illustrated notes:
  [OTA platform](docs/releases/2026-08-14-ota-platform.md).

## v0.3.0 — 2026-08-14

- Added the observability map, transition logs, smoke-test contract, backlog,
  and lessons log so a stale or foreign tokenserver is visible instead of
  looking healthy.
- The completion alert gained its first measured pulse and physical motion
  review; text and provider marks remain solid for readability.

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
