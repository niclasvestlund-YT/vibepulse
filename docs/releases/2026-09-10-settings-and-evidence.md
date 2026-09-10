<!-- GitHub-ready v1.1.0 release body. Intentionally starts without an H1. -->

VibePulse v1.1.0 gives the panel a menu and a memory. A three-second hold on
the one user button opens **SETTINGS** on the glass, so the update window and
the Wi-Fi setup are chosen rather than guessed from whether the panel happens
to have an address. A service restart no longer sends the glass STALE for
minutes: the first answer is immediate and says which numbers are still
placeholders. And when the panel does crash, it now leaves evidence — a
coredump in flash and a reboot ledger in NVS — instead of a backtrace on a
serial console nobody had attached.

<p align="center">
  <img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.1.0/docs/img/vibepulse-settings-menu.png" width="31%" alt="The SETTINGS menu on the panel: UPDATE, WIFI and ABOUT">
  &nbsp;
  <img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.1.0/docs/img/vibepulse-settings-no-address.png" width="31%" alt="The same menu on a panel with no network: UPDATE greyed out, WIFI live">
  &nbsp;
  <img src="https://raw.githubusercontent.com/niclasvestlund-YT/vibepulse/v1.1.0/docs/img/vibepulse-settings-about.png" width="31%" alt="The ABOUT page showing the firmware version and the panel's address">
</p>

## SETTINGS on the glass

Hold **KEY3** for three seconds and a menu opens: **UPDATE** opens the
ten-minute OTA maintenance window, **WIFI** opens the phone-first setup
window, **ABOUT** shows the firmware version and the address. Without an
address UPDATE is greyed out — a window with no address could never receive
an upload — and the address is live, so losing Wi-Fi while the menu is up
greys it out there and then. Any KEY3 release closes the menu, the same
escape the two windows already had.

The consent model is unchanged: the menu is reachable only from the device,
the token and the ten-minute window are untouched, and the menu and the
**UPDATE READY** takeover are mutually exclusive so the menu can never open
behind something you cannot see. The button arbitration that decides all of
this moved into pure platform code shared byte-identically by the panel and
the simulator, and it is pinned by a table of eight invariants plus one
continuous journey test. The OTA runbooks and `tools/ota-flash.sh` now say
the right gesture — they used to tell you to wait for a ring that never came.

## An honest warm-up

The tokenserver's first history scan used to run under the cache lock, so
every `/api/tokens` request queued behind it; on a Mac with a large history
the scan took 211 s, the panel's polls timed out one after another, and the
glass went STALE two minutes into every restart of a healthy service
(issue #62). The first request now answers at once. The scan runs in the
background, and the response carries live quota percentages plus a new
additive `usageTotals` block that says what the four volume counters are:
`refreshing` (placeholders), `ready`, or `failing` (the recompute is crashing
and the measurement is not coming).

Placeholders reach only a client that says it understands them
(`X-VibePulse-Accepts: usage-totals`); everyone else gets the contract's
error form, which already-flashed firmware rejects by design. New firmware
sends the header, applies the live quota rings, and leaves the value page and
the keep-awake burn rate alone until the counters are measured. Never
invented zeros; counters never go backwards.

## Evidence after a crash

**Built in CI, not yet flashed or physically verified.** The panel on the
shelf still runs `v1.0.0-25-g054db68`; the run sheet for bringing it up is
`docs/flash-session-2026-09.md`, and that session is the physical gate for
everything in this section.

- A 128K `coredump` partition holds an ELF dump of every task's stack from
  the last panic; the next boot logs `coredump i flash … idf.py coredump-info`
  when one is there. The partition row is appended after `ota_1` in free
  flash, and OTA never writes the table, so one USB
  `idf.py -p <port> partition-table-flash` is needed before a dump can land.
  The boot log says so until then.
- A reboot ledger in NVS: `omstartsliggare: boot #N …; efter PANIK a,
  vakthund b, BROWNOUT c`, right after the boot banner, counted since the
  ledger was initialized or NVS last erased. "Did it reboot while I was
  away?" is one serial line.
- `sdkconfig.defaults` pins the log level (INFO, which compiles `ESP_LOGD`
  and the `HTTP_CLIENT` request-line leak out structurally), panic
  print-and-reboot, the warn-only task watchdog with its idle-task
  subscriptions, and LVGL's own log at WARN. Because defaults never migrate a
  stale generated `sdkconfig`, a configure-time guard refuses to build blind
  and names the missing values and the fix.
- Every device poller backs off from a dead service instead of hammering it:
  first miss at the normal cadence, then doubling to a cap (agent status
  1 → 30 s, tokens 30 → 300 s, Max Tracker 5 → 30 min, GitHub 30 → 300 s),
  reset on the first success, transitions logged. A response the screen
  cannot apply counts as a miss too.
- Every permanent overlay logs what it costs in LVGL-pool and internal RAM
  on every boot, so the AMOLED memory budget is measured rather than
  remembered.

## The host got harder to fool

- A corrupt state file (`max-tracker.json` with up to 400 days of history,
  `quota-cache.json`, `usage-history.json`) is moved aside as
  `<name>.corrupt-<UTC stamp>` and logged, never silently wiped by the next
  save; all three writers now fsync the parent directory.
- `GET /` says why the Claude probe is idle — streak, interval, seconds left
  of a 429 rest, age of the last cycle — assembled per cycle and published
  under one lock, so it never reads half-built. On macOS a missing token
  carries the keychain's own word (`keychain_denied_or_locked (exit N)`,
  `keychain_no_entry`, `keychain_timeout`, …) and `docs/agent-setup.md` maps
  each to its fix.
- Agent rows typeset any model id on arrival (`HAIKU 4.5`, `GPT-5.4 MINI`,
  `MYTHOS PREVIEW`) instead of clipping unknown ids mid-string, and `effort`
  is read from where Claude Code actually writes it.
- The panel no longer prints the relay's secret URL on a failed cloud fetch;
  failure lines name a redacted target. A photo in `docs/img/` that carried
  GPS coordinates was re-encoded without them.
- `ruff` runs first in the host gate with bug-shaped rules only; the first
  sweep found 43 things, including a backfill loop that swallowed every
  exception.
- `tools/snapshot.sh` bundles every ref before any history rewrite, tested
  on macOS as well as Linux; `/repo-cleanup` proposes deletions with
  evidence before touching anything; `doctor` names a saved Codex mode that
  silently prevents approvals from reaching the panel.

## The tokenserver speaks English

Everything under `tools/tokenserver/` — modules, tests, the smoke test, the
README, the launchd plist and the Windows installer — is translated (issue
#12). Runtime keys, persisted file formats, API fields and exit codes are
byte-identical. Log signatures moved with it; if you grep the service log,
update these:

| Before | After |
|---|---|
| `startar: rev` | `starting: rev` |
| `förstaskanning …` | `first scan …` |
| `serverar http://…` | `serving http://…` |
| `500 på /api/…` | `500 on /api/…` |
| `usage-omräkningen kraschade` / `frisk igen` | `usage recompute crashed` / `healthy again` |
| `hittar varken … finns Claude Code …` | `found neither … is Claude Code or Codex on this machine?` |
| smoke `[VARN]`, `röktest: …` | `[WARN]`, `smoke test: N ok, N warnings, N failures` |

The smoke test and the runbook's comb step count both the old and the new
start line, because the log outlives an upgrade.

## What was not re-verified

- The Windows v1 host claim (core service, physical answer loop, sign-in,
  sleep and reboot lifecycle) is pinned to the v1.0.0 runtime `bee5d8c` and
  is **not inherited** by this release's host code. The
  [Windows validation procedure](https://github.com/niclasvestlund-YT/vibepulse/blob/v1.1.0/docs/windows-validation.md)
  still describes how to earn it again.
- The firmware changes above are CI-built only. Nothing in this release was
  flashed to a panel.

## Upgrade

```sh
git fetch --tags origin
git switch --detach v1.1.0
python3 tools/vibepulse_setup.py status
```

Restart the host service so it runs this code (macOS:
`launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver`; Windows: stop
and start the `VibePulse tokenserver` scheduled task as
`docs/windows-setup.md` describes), then run
`python3 tools/tokenserver/smoke.py`. Firmware goes through the existing
consent-gated OTA path; the coredump partition additionally needs the one-time
USB partition-table flash named above.

This release remains source-only. **Do not attach `torget.bin`**: every local
firmware build contains that installation's Wi-Fi credentials and may contain
its private device key.

Full history:
[v1.0.0...v1.1.0](https://github.com/niclasvestlund-YT/vibepulse/compare/v1.0.0...v1.1.0).
