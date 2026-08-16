# Changelog

Notable changes to VibePulse. Release notes for tagged versions are published
on the [releases page](https://github.com/niclasvestlund-YT/vibepulse/releases).

## Unreleased

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

  This is the token half of
  [#3](https://github.com/niclasvestlund-YT/vibepulse/issues/3) only — log
  paths, state paths and the launchd setup are still macOS-shaped, and the
  Codex half reads its app-server through `select.select` on a pipe, which
  Windows does not support. Reported by Erik Elfström, who found it porting
  a fork to a LilyGO T-Display-S3.
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
