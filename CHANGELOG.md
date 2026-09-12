# Changelog

Notable changes to VibePulse. Release notes for tagged versions are published
on the [releases page](https://github.com/niclasvestlund-YT/vibepulse/releases).

## Unreleased

### Added

- **Claude Code's statusLine as a quota source (host side).**
  `python3 tools/vibepulse_setup.py statusline install --yes-single-account`
  points Claude Code's `statusLine` at a generated launcher; the bridge
  (`tools/tokenserver/statusline_bridge.py`) keeps the session and weekly
  rate-limit windows Claude Code already hands that command, then runs the
  status line you had before with the same stdin. The tokenserver uses a
  fresh sample ahead of the OAuth probe per window (later reset wins;
  within a window the higher figure), slows the probe to 30 min while both
  windows are covered, and never touches the model week. `statusline
  status`, `doctor`, `smoke.py` and `GET /` (`claudeStatusline`) report
  it. Single-account slice of the 2026-09-10 spec; account binding is not
  implemented, and Windows install is refused for now.

- SETTINGS → LABS stores independent choices for burn rate, Max Tracker,
  API-equivalent Value, the GitHub page and star popups. Choices apply after
  restart. Fresh sample configurations start with quotas/activity; upgrades
  using existing configurations retain their initial views. Disabled pages
  and optional polling tasks are not created. Physical selector review is
  pending; standalone clocks and quotes remain future experiments.

## v1.1.0 — 2026-09-10

Release notes:
[v1.1.0 — SETTINGS on the glass, evidence after a crash](docs/releases/2026-09-10-settings-and-evidence.md).

### Added

- **A panic leaves evidence, and the panel counts its reboots (OBS-02,
  OBS-03, OBS-28, OBS-35).** In source and CI-built, **not yet flashed or
  physically verified**: a 128K `coredump` partition (appended after
  `ota_1`; OTA never writes the table, so one USB
  `idf.py partition-table-flash` is needed before a dump can land, and
  the boot log says so until then) with ELF coredumps to flash on panic, a `coredump i flash … idf.py coredump-info` notice at the
  next boot, and a reboot ledger in NVS (`omstartsliggare: boot #N sedan
  liggaren initierades; efter PANIK a, vakthund b, BROWNOUT c`, counted
  since the ledger was initialized or NVS last erased, never claimed as
  "since first flash"; a read, write or commit failure logs which step and
  no counts) right after the boot banner, so "did it reboot while I was
  away?" is one serial line. `sdkconfig.defaults` now
  pins the log level (INFO, maximum equals default, which compiles
  `ESP_LOGD` and with it the `HTTP_CLIENT` request-line leak out
  structurally), panic print-and-reboot, the task watchdog, and LVGL's own
  log at WARN, each with its reason. Because defaults never migrate an
  existing generated `sdkconfig` (the 2026-08-19 lesson), the root CMake
  now refuses to configure when the effective config has lost the coredump
  writer, the ELF format, the panic-then-reboot choice, the INFO default
  or the log ceiling, the task watchdog (or has its panic option on: the
  watchdog is warn-only, pinned off in the defaults) or the LVGL log with
  its printf sink and WARN level (`cmake/torget_diagnostics_guard.cmake`,
  naming the missing values and the `idf.py reconfigure` fix). The ledger
  counters saturate at their ceiling instead of wrapping to zero. `test/test_firmware_diagnostics.py`
  holds the pins and exercises the guard both ways; `docs/observability.md`
  has the retrieval steps, and its signature table now points a panic at
  the dump and the ledger instead of calling the banner the only witness;
  a `Task watchdog got triggered` warning is transient serial evidence
  only (warn-only, so no reboot, dump or ledger count), and a
  chip-attributed watchdog reset counts in the ledger but has a dump only
  when the separate `coredump i flash` notice follows the banner.

- **The panel backs off from a dead service instead of hammering it
  (OBS-13, OBS-12).** In source and CI-built, **not yet flashed**: every
  device poller ran at a fixed cadence no matter what, so a stopped
  tokenserver got a connect attempt every second from the agent-status
  poller alone, all day. A small pure policy (`poll_backoff_policy.c`,
  host-tested) now lets the first miss through at the normal cadence, then
  doubles the wait per consecutive miss up to a cap (agent status 1 s to
  30 s, tokens 30 s to 300 s, Max Tracker 5 to 30 min, the optional GitHub
  feed 30 s to 300 s) and resets on the first success, logging only the
  transitions. A miss is a response the screen could not apply, not merely
  a dead host: a service answering 200 with a body the parser rejects
  backs off the same way. The tokens poller's recovery notification still
  cuts a long wait short. `docs/observability.md` maps the new log lines. Two diagnostic holes
  closed on the way: the agent poller names the real fetch outcome
  (`IO-fel` vs `överflöde`) instead of a collapsed `ESP_FAIL`, and the HTTP
  helper's one silent failure path (no memory for a client) now logs, with
  the target redacted like every other line there.

- **A linter, at last (OBS-25).** `ruff` is pinned in `requirements-dev.txt`
  (and `pyproject.toml` carries the same pin as `required-version`, so a
  venv with another release is refused instead of linting differently from
  CI), configured in `pyproject.toml` with bug-shaped rules only (pyflakes, bare
  `except`, bugbear, `try/except/pass`, pylint errors, `global` declared for
  a name never assigned) and runs first in `test/run.sh`, so CI's host gate
  runs it too. The first sweep found 43 things across ~10 k lines. Every
  remaining `try/except/pass` is now a named boundary (`# noqa: S110 -
  <why>`) rather than an unexplained swallow, and the one that mattered is
  fixed: the Max Tracker backfill loop caught every exception and dropped
  it, so a bad session file could stop the heatmap's history from ever
  filling in with nothing in the log; it now logs one line per ten minutes.
  Sixteen closures over loop variables in tests are bound explicitly, three
  `zip()` calls state `strict=True`, three dead imports and one dead
  variable are gone, and `_probe_limits` no longer declares `global` for
  three names it only reads. Catching `Exception` (74 sites, all
  deliberate) and the `global` statement itself are not enabled; the config
  says why.

- **The Claude probe says why it is idle, and never half a status (OBS-18,
  OBS-20).** `GET /` now carries the probe's backoff beside `claudeProbe`:
  `claudeProbeStreak`, `claudeProbeIntervalS`, `claudeProbeCooldownLeftS`
  and `claudeProbeAgeS` (the cadence is 240 s, doubling per miss to
  960 s), so dashes on the screen can be told apart as "failing every
  four minutes" versus "resting after a 429"; the smoke test prints them
  next to a non-ok status. The status string is assembled per cycle and
  published once, together with the streak and timestamp, under the lock
  the HTTP threads read with, where it used to grow with `+=` on the probe
  thread and could be served half-built; a 429's rest is published in that
  same section as the status that explains it; `GET /` copies status,
  backoff, rest, credential and header evidence in one locked read, and
  header evidence (`ratelimitHeaders`, `unknownRateLimitBuckets`) is now
  the current cycle's only — a cycle that never reached the fallback, was
  held by another instance or crashed publishes it empty — never
  hours-old names beside a fresh failure. On macOS the keychain read no
  longer folds every failure into one shrug: `no_claude_oauth_token`
  carries `keychain_denied_or_locked (exit N)` (Deny on the prompt, or a
  locked keychain), `keychain_no_entry`, `keychain_timeout`,
  `keychain_security_missing`, `keychain_malformed` or
  `keychain_entry_without_token`, the same word sits in
  `claudeCredential.reason`, `docs/agent-setup.md` maps each to its fix,
  and `claude-keychain: X -> Y` logs the transitions.

- **`tools/snapshot.sh`** — one verified bundle of every ref, plus the
  pseudo-refs `--all` does not cover (`ORIG_HEAD`, `MERGE_HEAD`, `FETCH_HEAD`
  and the rest, per worktree, including the extra parents a multi-line one
  holds), to run before anything that rewrites history. It refuses on a shallow clone, which is
  the trap that nearly cost 433 commits during the work above:
  `git rev-parse --is-shallow-repository` answers `true` when the clone *is*
  truncated, and a bundle taken from it restores a fraction of the history
  without complaining. It also refuses a destination inside the repository,
  verifies what it wrote by reading it back, and deletes the file if
  verification fails. Now a work rule in `AGENTS.md`. Written for BSD
  userland as well as GNU: the first draft parsed worktrees with awk's
  `RS="\0"`, trimmed with `head -c -1` and called `mktemp` without a
  template — three things that work on Linux and none of which work on
  macOS, the very machine where the rule makes the tool mandatory.

- **`/repo-cleanup`** — a two-phase cleanup command. Phase one only produces
  an evidence table with a keep-list and an uncertainty list; phase two
  executes the approved subset, one commit per category with `./test/run.sh`
  between. It encodes the traps that make this repository different: the
  docs are load-bearing and asserted by tests, C symbols reach the build
  through CMake and Kconfig rather than callers, and the committed fonts are
  generated on purpose.

- **The OTA rules now reach Codex.** `AGENTS.md` and `CLAUDE.md` are the same
  rules for two different agents, and the section saying the maintenance
  window opens only from the device, that the sender gates exist because a
  stale build once froze the panel, and that the launchd service must be
  restarted, was in only one of them.

- **`tools/snapshot.sh` has host tests, on macOS as well as Linux.** The
  backup `AGENTS.md` makes mandatory before any history rewrite had no
  coverage at all: no entry in `test/run.sh`, no test file, no shellcheck.
  Three consecutive review rounds each found a real defect in that one file,
  and every one was found by a reviewer building a repository shape by hand.
  `test/test_snapshot_tool.py` now runs the tool against ten synthetic
  repositories and asserts both the exit code and the published artifacts: an
  ordinary repository publishes a `.bundle` plus its `.refs` sidecar at mode
  0600 and clones back; a bare repository and a repository whose bundle
  advertises no HEAD both exit 0; a shallow clone is refused with
  `--unshallow` named; a destination inside a checkout is refused directly,
  via `..`, via a linked worktree, and via a worktree whose path contains a
  newline; a `prunable` registration neither refuses nor crashes; a run from a
  linked worktree lands beside the **main** checkout; and a byte flipped in
  the pack is caught — by the tool's own fetch into a temp bare repository,
  because `git bundle verify` reads the header only and calls a corrupted
  pack "okay".

  The portability half is not decoration. Three of the five defects — `awk`
  with NUL as `RS`, `head -c -1`, `mktemp` without a template — are invisible
  on Linux, where GNU's tools do exactly what the script asks; they only bite
  on the maintainer's own machine. A new `Snapshot tool` CI job therefore runs
  the same file on `ubuntu-latest` **and** `macos-latest`, and the test carries
  static guards for that class so a reintroduced GNU-ism goes red on ubuntu
  the minute it is written. `shellcheck tools/snapshot.sh` runs alongside as a
  complement, not a substitute: it flags neither `RS="\0"` nor a missing
  `mktemp` template. Each of the five defects was reverted one at a time and
  the test watched go red before this landed.

- **The screenshots in `docs/img/` are checked against the simulator.**
  `test/test_docs_frame_drift.py` compares every checked-in 480 × 480 frame
  with what this build actually renders, and the answer is blunt: of
  twenty-nine frames it can verify **four**. The Wi-Fi indicator was redrawn
  in `d5be82d` — a thick white fan became a thin grey one — and most frames
  still show the old glyph, so README, `docs/wifi.md` and the release bodies
  have been showing a screen this firmware cannot produce. The four the
  simulator reproduces exactly are pinned byte-for-byte and rejected if they
  gain a colour profile or EXIF orientation, which would change what a
  browser paints without changing a pixel. The other twenty-five are
  quarantined by name *and* by a digest of the file — including eight whose
  indicator box is simply blank, which proves nothing either way and had
  been passing silently. Re-capturing them is a separate, deliberate
  documentation change.

- **`doctor` and Codex startup health now name a saved Codex mode that
  silently prevents approvals from reaching the panel.** This is the failure
  that looks exactly like a broken panel: the bridge is green, the panel
  polls, and **APPROVE / DENY** never arrives — because `approval_policy =
  "never"`, `approvals_reviewer = "auto_review"` or `sandbox_mode =
  "danger-full-access"` means no permission event is ever created. The panel
  cannot see a setting on the user's computer, so the host is the only place
  that can say so, and the startup check now says it *before* reporting a
  reachable service healthy. Reads the three mode names and nothing else in
  the file, stops at the first table header so a value under `[profiles.x]`
  is never read as a top-level one, and stays quiet when there is no
  `config.toml` — most working installs have none, and a check that cries
  wolf is skipped like any other. Rescued from
  `niclas/wip/wifi-dma-calibration-20260904` before that branch is deleted.

- **The KEY3 flow is tested as a journey, not only as a table.**
  `test/test_key3_arbitration.c` pins eight invariants separately, which is
  the right shape for them — but every cell in that table is set up by hand,
  and a table where all cells pass says nothing about the *transitions*
  between them. `test/test_key3_flow.c` walks one continuous trip through
  the whole flow, feeding each tick's output back as the next tick's input
  the way `main.c` does, and injects the events the button does not control
  (the setup window opening itself, the notice arriving, windows expiring)
  where they really occur. It also asserts no world state is a dead end.

- **`docs/manual-test-key3.md`** — the short list of things only the panel
  can settle, deliberately excluding everything already automated.

- **Every permanent overlay now reports what it costs, on every boot.** The
  three top-layer overlays — Wi-Fi setup, SETTINGS and the OTA ring — are
  built once at start and kept for the whole run, and the AMOLED rule
  forbids that without a measured memory budget. The measurement is now
  automatic rather than remembered: each create is bracketed and logs
  `overlaykostnad <name>: LVGL-pool +N B …, internt ±N B …`. Two numbers
  because they answer different questions. LVGL's allocator pool is TLSF in
  **PSRAM** (moved there as the 2026-08-16 freeze fix), so object trees come
  out of its 256 KiB rather than internal RAM; the internal figure is the
  control that shows whether a create takes internal memory anyway. A zero
  delta retires the starvation worry for that layer with evidence instead of
  argument, and a non-zero one puts the cost in the log.

- A **SETTINGS** menu on a 3 s KEY3 hold. The hold used to derive which window
  you wanted from whether the panel had an IP; it now opens a menu with
  UPDATE, WIFI and ABOUT and lets you say. The consent model is unchanged —
  the menu is reachable only from the device, so physical presence is still
  required for UPDATE, and the token and the ten-minute window are untouched.
  Without an address UPDATE is greyed out and cannot be picked, because an
  update window with no address could never receive an upload; UPDATE is the
  only row that goes dark, leaving WIFI as the one that can fix it, and the
  address is live rather than a snapshot — lose Wi-Fi
  while the menu is up and UPDATE greys out there and then, instead of
  offering a window that could no longer receive anything. ABOUT shows the
  firmware version and the address, with a dash for anything missing. There is
  deliberately no "computer found" row: the only available signal is a boot
  latch that never clears, so it would have read FOUND forever after one
  fetch. Any KEY3 release closes the menu, the same escape the two windows
  have. The menu and the UPDATE READY takeover are mutually exclusive: the
  hold does nothing at all while that notice is up, and a notice that arrives
  while the menu is open closes it. The notice is a UI state rather than an
  open maintenance window, so without both edges the menu opened invisibly
  behind it and reappeared on LATER. Answer the takeover with its own UPDATE
  and LATER pills. Against everything else the menu keeps itself on top, which
  it has to re-assert rather than inherit from creation order — the NO NETWORK
  page redraws its countdown every second and lifts itself each time, and that
  is precisely the state where the WIFI row is what you need. FEATURES and
  PAIR from the design spec are not in this step: FEATURES needs the
  internal-RAM budget re-measured on the unit, and PAIR belongs to a later
  step.

### Changed

- **The tokenserver directory speaks English (issue #12).** Every module,
  test, the smoke test, the README, the launchd plist and the Windows
  installer under `tools/tokenserver/` are translated: comments,
  docstrings, CLI help, console output and log lines. The log signatures
  the runbook names move with it (`starting: rev`, `first scan …`,
  `serving http://…`, `500 on /api/…`, `usage recompute crashed` /
  `healthy again`, `found neither … — is Claude Code or Codex on this
  machine?`), as do the smoke test's tags (`[WARN]`, summary line
  `smoke test: N ok, N warnings, N failures`) and the SessionStart hook's
  advice. Runtime keys, persisted file formats, API fields and exit codes
  are byte-identical; a handful of test fixtures keep non-ASCII text on
  purpose because they exist to prove UTF-8 handling.

- KEY3's arbitration — which of the glass's owners gets the button on a given
  tick — moved out of `main/main.c` into a pure `tg_button_arbitrate()` in
  `platform/`, shared byte-identically by the panel and the simulator. It was
  110 lines of decisions in the host layer that `sim/main.c` could not reach:
  the simulator called `torget_settings_open()` directly during static QA, so
  it was not the authority for the gesture, the escape, the intent handoff or
  the asynchronous notice close, against `AGENTS.md` on both counts. Both hosts
  now read inputs and apply outputs and decide nothing. No behaviour changed —
  the point of the move is that the behaviour is now provable: the eight
  invariants behind it, each with a real incident, are pinned as a table of
  host tests rather than by reading source, including the notice close that
  fires from `maintenance_ui_task()` with no button event to hang a test on.
  The simulator drives the real gesture through `poll_keys()` (hold K for
  three seconds), and the composite state where a notice arrives over an open
  menu is captured at 480x480 for the first time. The consent model is
  strengthened rather than restated: the arbitration reaches no service at all
  and has no output that opens the maintenance window, so the only way there
  remains a finger on the menu's UPDATE row.

### Fixed

- **A healthy service restart showed STALE on the glass for minutes.** The
  tokenserver's first history scan ran *under the cache lock* inside
  `get_snapshot`, so every `/api/tokens` request queued behind it. On a Mac
  with a large Claude/Codex history the scan took 211 s, the panel's polls
  timed out one after another, and the glass went STALE two minutes into
  every restart of a service whose credentials, discovery and relay were
  all fine (issue #62). The first request now answers at once: the scan
  runs in the background, and the response carries live quota percentages
  plus a new additive block `usageTotals` that says what the four volume
  counters are: `refreshing` (placeholder zeros, `placeholder: true`,
  `sinceS` since start), `ready` (`ageS` old) or `failing` (the recompute
  is crashing, OBS-08: frozen with `ageS`, or still placeholders if no
  scan ever completed). The block is captured under the same lock and from
  the same read as the counters, so it can never describe a different
  snapshot than the one it rides on. The completed scan is swapped in
  atomically; a scan that crashes is retried on the normal thirty-second
  cadence, never per request. **Placeholders never reach a client that
  would apply them:** the service serves them only to a request carrying
  `X-VibePulse-Accepts: usage-totals`, and answers everyone else with the
  contract's error form (HTTP 503, `usageTotals` beside it), which the
  already-flashed firmware rejects by design and keeps its last good
  values, going honestly STALE two minutes later exactly as before. New
  firmware sends the header, parses `usageTotals.placeholder`, applies the
  live quota rings but leaves the value page and the keep-awake burn rate
  untouched during a warm-up: never invented zeros, counters never go
  backwards. The firmware fetch log and the simulator say "volym ej
  uppmätt än" for such a sample instead of printing `0.00 Mtok idag`.
  When the block says `failing` the recompute is crashing and the
  measurement is not coming, whether the counters are placeholders or
  frozen at the last good scan: the firmware then shows dashes on the
  value page instead of carrying an old figure that would look fresh poll
  after poll, keeps the panel asleep rather than waking on a frozen burn
  rate, and logs a warning (fixture `tokens-volume-failing.json`). The same block is on `GET /`, the numbers publisher does not
  send a placeholder to the relay, the SessionStart hook reports `SERVICE
  WARMING UP` (and `VOLUME RECOMPUTE FAILING`) as their own classes, the
  setup doctor prints `WAIT` for a warm-up and `FIX` for a failing
  recompute, and the smoke test warns instead of failing. A full disk is
  visible too: `GET /` carries `maxTrackerSaveOk` /
  `maxTrackerSaveFailingForS` while the state file cannot be written (the
  observations stay in memory and retry, as OBS-10 already arranged), the
  doctor and smoke test name it, and a test proves one `ENOSPC` on the
  atomic write leaves both the previous file and memory intact. Firmware
  side built in CI, not flashed: an OTA to this build is what turns the
  post-restart STALE into a live panel; until then the old behaviour holds.

- **Unmapped models reached the panel as raw ids (OBS-30).** The agent
  rows typeset six model ids by hand (`OPUS 5`, `GPT-5.6 SOL`, ...) and let
  the other hundred-odd in `prices.json` fall through as raw lowercase,
  clipped at 24 bytes mid-string: `claude-haiku-4-5-20251001` rendered as
  `claude-haiku-4-5-2025100` next to its typeset siblings. The label is now
  derived from the id (family, version and variant uppercased, the dated
  suffix dropped: `HAIKU 4.5`, `OPUS 4.8`, `SONNET 3.7`, `GPT-5.4 MINI`,
  `O4 MINI`, `MYTHOS PREVIEW`), so a model the agent picks tomorrow is
  typeset on arrival. The hand map stays for exceptions only, and a test
  proves every priced model derives an uppercase label that fits the
  panel's column.

- **A corrupt state file was wiped, silently, by the next save (OBS-11).**
  All three stores (`max-tracker.json` with up to 400 days of history,
  `quota-cache.json`, `usage-history.json`) answered unreadable bytes by
  starting empty with no message, and the next save overwrote the evidence.
  Each now moves the file aside as `<name>.corrupt-<UTC stamp>` beside the
  original and logs one WARNING naming the file and the reason (never the
  contents), then starts empty. The bytes are usually 99 % intact, so the
  option to look or hand-repair is kept. Wrong-shape JSON counts too, and
  a non-UTF-8 `max-tracker.json`, which used to raise out of the
  constructor and stop the service from starting, is quarantined the same
  way. One helper (`state_files.py`) holds the rule for all three, and the
  quarantine rename gets the same directory fsync as a save, so the kept
  copy is durable before the warning says it is. Two more edges from the
  review: a file that exists but cannot be read (permissions, I/O) makes
  the store start empty *and refuse to save*, since a rename needs only
  the directory's permission and would have overwritten it; and a
  provider section that is a dict but not the `{v, days, weeks,
  backfill}` shape `save()` writes is quarantined rather than skimmed.
  The usage history keeps its new state in memory when the replace has
  landed but the directory fsync failed (disk and memory agree), instead
  of rolling back and dropping that sample on the next save.

- **Two of three state writers stopped one fsync short of durable
  (OBS-21).** `quota-cache.json` always did the full atomic dance: file
  fsync, rename, parent-directory fsync. `max-tracker.json` and
  `usage-history.json` stopped at the file fsync, so a power cut right
  after the rename could bring back the previous file or none; the one
  with 400 days in it was the least protected. Both now fsync the parent
  through the same shared helper (a no-op on Windows, which has no
  directory descriptors, exactly as the quota cache already handled it).

- **`effort` was null for every Claude job.** `agent_status.py` read it
  from inside the API `message`, beside `model`. Claude Code writes it on
  the transcript *record*, beside `type` and `version`: measured on a live
  2.1.267 transcript, 125 of 125 assistant records carried `effort` at the
  top level and none nested, while `model` really does live inside
  `message`. `/api/agent-status` therefore served `effort: null` for every
  Claude job since the field was added. The classifier now falls back to the
  record's own `effort` when the nested one is absent, through the same
  12-byte control-free bound; `tool_input` is still never a source. Found
  by the companion-features audit (`docs/companion-features-brainstorm.md`,
  "Fix first").

- **Five SessionStart tests inherited the developer's own Codex settings.**
  `session_start.py` reads the saved `approval_policy`, `approvals_reviewer`
  and `sandbox_mode` from `$CODEX_HOME/config.toml`, else
  `~/.codex/config.toml`, *before* it checks service health, on purpose: a
  saved `never` is the failure that looks exactly like a broken panel. Run
  on a machine whose real config says `never`, that same rule turned five
  service-health tests red on an unchanged checkout (issue #93). The test
  harness now hands every script an empty `CODEX_HOME` unless a test
  supplies its own; the explicit permission-mode tests still pass theirs
  and still prove the production check. A new test poisons the fallback
  home directory and asserts the isolation wins.

- **The host gate could not go green anywhere but Europe.** Every Max
  Tracker test stamped its synthetic records at a fixed UTC hour such as
  `T10:00:00Z` and expected the store to file them under that same calendar
  date. The store deliberately owns a record by its *local* day
  (`max_tracker.py`: "dygnsgränsen är Macens, inte UTC:s"), so west of
  UTC-10 the record was the previous evening and at UTC+12 a noon stamp was
  already the next morning. Issue #66 reported the seeded ownership test
  from Los Angeles; the same class took down thirteen more tests in
  Pacific/Pago_Pago and one in Auckland, and one in `test_tokenserver.py`.
  A `_local_stamp(day, hour)` helper now builds every stamp from local
  wall-clock time and converts it to UTC, so expectation and store apply
  one rule. The seeded test records ground truth against the local owning
  day of the stamp it actually wrote. Verified green in seven zones from
  UTC-11 to UTC+14. No user-facing data was ever affected: the store was
  right, the expectations were not.

- **The `secrets.h` check in the setup runbook failed every correct setup.**
  Step 1 of `docs/agent-setup.md` verified the host placeholder with an
  unanchored `grep -q 'DIN-MAC' secrets.h`. `secrets.h.example` says
  `DIN-MAC` twice: in the `TK_VIBEPULSE_BASE_URL` define and in the comment
  telling you to replace it. Following the runbook replaces the define and
  keeps the comment, so the check printed `PLACEHOLDER STILL THERE` on every
  correct file. This is the guard for the mistake that once cost an evening
  of network debugging (`docs/lessons.md`, 2026-08-17); a guard that always
  fires teaches the reader to ignore it. The match is now anchored to the
  URL (`://DIN-MAC`) rather than to `#define`, because the define spans two
  lines, and the runbook says why. Reported in issue #64.

- **The panel printed the relay's secret URL every time a cloud fetch
  failed.** All three failure paths in `components/torget_net/torget_http.c`
  logged the address they had just failed on. When the fetch had failed over
  to the numbers relay that address was the cloud mailbox URL, and its path
  `/u/<secret>` *is* the access control — anyone handed a serial capture or a
  pasted observability log after a relay outage could read the panel's
  figures and overwrite them. `docs/relay.md` has said "Never print the
  secret URL in logs or a shared transcript" since the relay shipped; the
  code had simply never been revisited after a credential-bearing address
  started flowing through the same helper. Failure lines now name a redacted
  target — `<scheme>://<host> via LAN` or `… via relä` — which still
  separates a wrong hostname from a wrong port from a dead relay, and drops
  the path, query, fragment and any userinfo. The redaction is unconditional
  rather than relay-only, so a fourth failure path inherits it instead of
  having to remember it, and it lives in one pure helper
  (`net_log_target.c`) that `test/test_net_log_target.c` host-tests directly.
  `test/test_relay_boundary.py` now parses every `ESP_LOG*` call in the fetch
  client and fails if one names a raw address.

- **A photo in `docs/img/` carried the coordinates it was taken at.**
  `docs/img/github/glass-live.png` was an iPhone photograph committed
  straight off the camera, and its EXIF held a full GPS IFD — latitude,
  longitude, altitude, a ten-metre error estimate and the timestamp. This
  repository gitignores `.ota-device` precisely because a LAN address is too
  revealing to share; the secrets discipline was built around text, and a
  camera writes the location into a binary nobody opens. Re-encoded through
  a fresh image with no metadata, and resized from 3024 × 4032 and 6.9 MB —
  a quarter of the whole repository — to 1050 × 1400 and 1.0 MB, which is
  still twice what the page renders. The other 58 tracked images were swept:
  three carry benign EXIF with no GPS. **The original blob is still in
  `main`'s history and on GitHub.** Removing it needs a force-push that the
  repository ruleset refuses; the rewrite is built and documented in
  `docs/lessons.md`, waiting for that rule to be lifted.

- **Nine documented claims that the code contradicted.** The host-gate recipe
  in `README.md`, `README.sv.md` and `docs/agent-setup.md` installed only
  `requirements-dev.txt` while `test/run.sh` exits 1 without `cryptography`
  — and run.sh's own error pointed the reader back at the recipe that failed
  them. `docs/agent-setup.md` said the Claude probe fires every 120 s
  (it is 240). README and `docs/wifi.md` described a one-to-three-bar Wi-Fi
  indicator; the firmware draws two states and its own comment says why.
  `docs/ota.md` sent agents to a branch that does not exist. The tokenserver
  README said stale numbers become dashes (they stay, relabelled `CACHED`),
  told you to define `TK_TOKENS_URL` by hand (it derives from
  `TK_VIBEPULSE_BASE_URL`), and presented the rate-limit headers as the
  primary usage source when they are the fallback (OBS-23). Three key lists
  described the simulator and none named `K`, `U` or `W` — the keys that
  drive the KEY3 gesture, which `docs/lessons.md` calls the simulator's
  reason for being the spec.

- **`tools/mockups/gen_concept_mockups.py` wrote to one machine's checkout.**
  A hardcoded absolute output path — the repository's only one — so the
  script regenerated nothing anywhere else. Its sibling already resolved the
  path from `__file__`.

- **The OTA runbook told you the wrong gesture, on the terminal, while you
  stood at the panel.** `tools/ota-flash.sh` said "håll KEY3 ~3 s tills
  UPDATES ON-ringen syns" — in its header *and* in the line it prints while
  waiting for the window. The hold has opened SETTINGS since #72; UPDATE in
  the menu is what opens the window. Someone following it would have held
  the button and waited for a ring that never came. `tools/wifi-here.sh`
  had the same error twice — in its header and in the retry advice it
  prints when the Mac cannot reach the panel's access point, which is
  exactly when the operator is stuck and reading carefully. All corrected,
  and `test/test_ota_gesture_docs.py` — which exists to catch this class and
  did not — now reads the two runbooks, understands Swedish verb-first
  phrasing ("Håll KEY3"), normalises whitespace so a line break inside a
  sentence no longer hides it, joins what a shell script actually prints
  across several `echo` lines, and guards the Wi-Fi setup window as well as
  the update window.

- The WiFi setup QR no longer survives the window it belongs to. Only the
  OPEN branch of `torget_wifi_ui_set()` ever managed the canvas, and only
  `HIDDEN` ever cleared it, so every hop from OPEN to another *visible* state
  carried the code along. The one that reaches a user: a setup window that
  times out with still no network goes straight to `SEARCHING`, and a QR for
  an access point that no longer exists sat on top of the honest reason line
  (`NOT SEEN - 2.4 GHZ ONLY`) — inviting a scan that does nothing. The same
  split hid the network *name*: the QR view tucks it away behind the code and
  nothing put it back, so `NO NETWORK`, `JOINING` and `ON THE NET` had stopped
  saying which network they meant. Every control the open view touches now
  gets its visibility set in both branches. New pinned capture
  `wifi-open-to-searching`, which the existing `wifi-searching` frame could
  not catch — that one is taken before any OPEN state, so the canvas has
  never been populated at that point.

- `test_mcp_recovers_after_absolute_drip_deadline` no longer flakes on the
  Windows CI runner. It bounded wall clock taken around `run_mcp()`, which
  spawns a Python subprocess, so interpreter startup counted against a budget
  meant for the transport deadline alone — 0.719 s against a 0.6 s bound on a
  loaded runner, green again on the next commit. It now measures from the stub
  server's own recorded request time, as the sibling deadline test already
  did. The bound is tighter than before, not looser: the two request/response
  cycles run in ~0.13 s and the assertion trips at 0.4 s, so a lengthened or
  removed drip deadline still turns it red.

- The same file's `run_script()` no longer times a Codex plugin script out
  after 4 seconds on a loaded CI runner. The budget is a hang guard, not a
  speed assertion — nothing overrides it and no test asserts that it fires —
  but it had to cover a fresh Python interpreter's startup, and on
  windows-latest it did not: `test_unicode_decision_is_emitted_as_utf8` timed
  out and passed on a second run of the same commit. It is now a named
  `SCRIPT_HANG_TIMEOUT_SECONDS = 30`, still verified to catch a wedged script.

- `test_production_process_timeout_kills_reaps_and_recovers` no longer bounds a
  Windows-only subprocess spawn it never budgeted for. A sweep of every timed
  assertion CI runs on windows-latest found it: `_terminate_process_tree()`
  spawns `taskkill` there, and with the production
  `PIPE_JOIN_TIMEOUT_SECONDS = 1` the permitted worst case was 0.1 + 1 + 1 =
  2.1 s against a 2 s bound — the assertion could have lost to a slow runner
  rather than to the bug. It now caps that constant at 0.25 s the way the two
  POSIX siblings already did and asserts under 4 s; it runs in ~0.1 s and
  still turns red at 5.1 s if the deadline is lengthened. The sweep found
  nothing else: the other three process tests are POSIX-only, and every timed
  bound in the tokenserver modules is in-process.

- `/api/tokens` no longer reports a Codex-only computer's Claude counters as
  measured zeros. A new additive `claudeSourcePresent` flag says when the
  Claude directory is absent, so the zeros are not mistaken for a day with no
  work — by the panel's log, by the simulator, or by a future reader. The
  percentages already came over as `null` and rendered as dashes; this closes
  the same invariant at the API and log boundaries. Flashed panels are
  unaffected: unknown keys are skipped, and an absent flag means present.

- The usage service now starts on a computer that has Codex but not Claude
  Code. Its readiness check waited — before binding the HTTP port — until
  `~/.claude/projects` existed, so on a Codex-only machine the port never
  opened, nothing was advertised over DNS-SD, and the panel found a computer
  it could not poll. Codex usage is read from `~/.codex/sessions` and does not
  need that directory at all. Either provider is now enough to start, and the
  service waits only when neither is present.

- The tokenserver now drains an unread request body before an early
  rejection, so a real Windows hook client sees the `403`/`415`/`404` it was
  sent instead of a connection abort. Rejections on the headers alone (a
  non-loopback `Host`, a disallowed `Origin`, a non-JSON `Content-Type`, a
  disabled route) previously closed the socket with the advertised body still
  unread, and Windows discards an already-sent response when a connection is
  closed that way (`WinError 10053`). It applies the idea the existing `503`
  path already used, on the same 50 ms deadline but not the same byte cap:
  `_reject_busy` drains at most 8 KiB of *headers* and stops at the end of
  them, whereas this drains the advertised *body* under its own 64 KiB cap.
  The bytes are never parsed or logged, so
  rejections still happen before any parsing and nothing is parked. That cap is
  deliberate and it is also the limit of the fix: a body larger than 64 KiB is
  drained only up to the cap, so an early rejection of an over-cap body can
  still abort on Windows. Draining without a bound would hand any peer a
  denial-of-service lever, so the residue is accepted rather than chased.

- A successfully parsed ESP32 quota response now clears the transport-level
  `STALE` state synchronously, rather than waiting for a later LVGL timer tick.
  The refresh is deliberately unconditional so display and app bookkeeping
  self-heal if they drift apart. Sanitized serial logs now include the Claude,
  Fable, Codex, and Max Tracker stale bits carried by each accepted payload.

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

### Removed

- **Eighteen concept-mockup SVGs** under `docs/img/mockups/`. Tracked output
  nothing referenced — the documents link the `.png` beside each one — and
  the two generators reproduce all eighteen byte-identically, verified by
  running them and getting a clean `git status`. Now gitignored, the way
  `platform/fonts/src/` is.

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
