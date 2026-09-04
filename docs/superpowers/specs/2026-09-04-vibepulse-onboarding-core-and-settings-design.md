# VibePulse Onboarding, Core, and Settings Design

**Date:** 2026-09-04

**Status:** Proposed

**Scope:** First-run onboarding (flash, Wi-Fi, computer pairing), the
boundary between the core and optional add-ons, a KEY3 settings page with
saved feature switches, the vibecoder-facing README, and the order in which
these ship without removing anything that works today.

## Plain-English outcome

A vibecoder — someone who builds with Claude Code or Codex — puts a screen
on the shelf that shows their usage, without reading a runbook:

1. Plug the board into the computer and click **Install** in the browser.
   *(new)*
2. Put the screen on its own USB power. It shows a Wi-Fi QR at once. Scan
   it with a phone, pick the network. *(built)*
3. The glass says **Install VibePulse on your computer** with a QR and a
   short URL, then **Looking for your computer…**. One command on the Mac
   or PC. The glass says which computer it found and shows the usage pages.
   *(new screen; the discovery behind it is built)*

Everything else — burn rate, Max Tracker, the value page, GitHub, answering
Needs You from the panel, the relays, sound — stays in the firmware and is
turned on from a settings page on the glass or a command on the computer.

## The one rule: nothing is removed

This design is additive. Each of these stays exactly as it is today until a
later, separate decision says otherwise:

- every page in the tileview (`VIEW_CLAUDE_FABLE` … `VIEW_VALUE`) and every
  default it has today;
- every independent switch in the README table (Claude interactions, Codex
  interactions, numbers relay, interaction relay, live agent status relay,
  GitHub) and its default-off posture;
- the OTA consent model in `docs/ota.md`: the maintenance window opens only
  from the device, the upload needs the token, the window closes on its own;
- the phone-first Wi-Fi setup window in `docs/wifi.md`, its 90-second
  self-open on a stranded panel, and `tools/wifi-here.sh`;
- `secrets.h` as the **immutable floor**: every macro in it keeps working.
  What changes is that the floor stops being *required* to get a panel
  running;
- `docs/agent-setup.md` as the runbook a coding agent follows.

A reader who finds a sentence in this document that removes a working
feature should treat that sentence as a mistake in the document.

## Goals

- A new user reaches "my usage on the glass" with one cable, one phone, and
  one command, and never opens an editor.
- The README speaks to vibecoders first: what you need, then "start your
  agent with this line". The manual path stays, below.
- The panel guides the user through setup on its own display wherever it
  can: Wi-Fi QR, computer QR, "looking for…", "found…", and honest copy
  when something is missing.
- Optional features are switches, not builds. One release binary serves
  every user.
- Existing panels behave identically after every step of this plan.

## Non-goals

- Running without a computer. The usage numbers exist only on the machine
  where Claude Code or Codex is signed in. A cloud account that would let
  the panel read them directly is out of scope and would put a user
  credential on a $30 device.
- Bluetooth or serial Wi-Fi provisioning. See *Hardware posture*.
- Changing what the numbers relay, interaction relay, or status relay carry.
- Deciding a leaner set of default pages for fresh installs. That is a
  one-line change once switches exist; it is deliberately not part of this
  design.
- Home Assistant, lights, or other new integrations. They arrive later as
  add-ons behind the same switch mechanism and are not designed here.

## Hardware posture

Stated per `CLAUDE.md`: silicon-capable / board-wired / firmware-enabled /
physically verified on the named unit.

- **Board.** Waveshare ESP32-S3-Touch-AMOLED-2.16 is the only unit VibePulse
  is verified on. The README hardware table has one row; a second row
  requires the same physical gate, never a datasheet.
- **Power over the flash cable.** A computer USB port often cannot feed the
  running AMOLED; the board bounces off the bus (README, *Power matters*). Consequence: Wi-Fi
  provisioning over the flash cable (Improv-Serial) is **rejected** for
  this board. Flashing happens in download mode with the screen dark, and
  Wi-Fi setup happens on the shelf through the existing SoftAP + QR window.
- **Wi-Fi.** 2.4 GHz only. Six networks in NVS plus the compiled floor.
  Firmware-enabled and physically used daily.
- **Discovery.** `_vibepulse._tcp.local` client is firmware-enabled and
  verified in the v1.0.0 Windows lifecycle run.
- **Speaker.** ES8311 codec and amplifier: SoC-capable yes, board-wired
  yes, BSP support yes, firmware path yes, **unit verified: unknown**
  (`spec/hardware-capabilities.yaml`, `audio.speaker-output`). The physical
  inventory does not establish that a speaker is fitted. Consequence: the
  Sound switch may appear in settings but stays greyed with the copy
  *not verified on this unit* until the speaker gate passes. No sound is
  promised before that.
- **KEY3.** GPIO18, active low; short tap and ~3 s hold are already
  distinguished by `tg_button_policy` in `main/main.c`.

## Audience and the README

The README is written for vibecoders. Structure, in reading order:

1. what the screen is (tagline, hero, the problem);
2. **Start here** — three lines that route the reader (added 2026-09-04);
3. what's on screen, core pages first;
4. **What you need** — the four prerequisites and the hardware/computer
   support tables (added 2026-09-04);
5. **Setup, the vibecoder way** — a copy-paste start line for Claude Code
   and one for Codex; the agent follows `docs/agent-setup.md` and asks before
   it flashes (rewritten 2026-09-04);
6. the manual path, OTA, take-it-with-you, simulator;
7. the add-ons, each with its switch and its status.

Text moves only when a later step in *Sequencing* needs it; nothing is
deleted from the README as part of restructuring.

## Onboarding on the glass

The display is the guide. States, in order, with the copy rule that the
glass never shows dashes without saying why (the honesty invariant from
`AGENTS.md`):

### 1. First boot, no network

Today the setup window opens after 90 s without an IP. Change: when NVS holds
no networks **and** the compiled floor is empty, open it immediately. A panel
that has a saved or compiled network keeps the 90-second behaviour, so an
existing panel sees no change.

### 2. Joined, no computer yet

New screen. Layout follows the Wi-Fi setup screen's language (one dominant
QR, one line of instruction, one secondary control):

- headline **Install VibePulse on your computer**;
- QR encoding the setup URL, and the same URL as text, short enough to type;
- status line **Looking for your computer…** driven by the existing
  DNS-SD client;
- after a bounded time (proposal: 3 minutes) the status line becomes
  specific: *Still looking. Is the VibePulse service running? Same Wi-Fi as
  this screen?* It never claims a failure it cannot see;
- secondary control **Manual setup** reveals the panel's IP and the
  fallback host-URL entry for people who compiled one.

### 3. Found

The header names the computer it pinned to: the service already advertises
itself as `VibePulse-<hostname>`, so the name costs nothing new. The usage pages appear. This is the same
tileview as today.

### 4. Settings (KEY3 hold)

See *Settings page*.

Every new or changed screen needs exact 480 × 480 simulator captures at
meaningful stages and a static physical review before any motion, per
`.claude/skills/iterating-esp32-amoled-ui/SKILL.md`.

## The computer side: the ladder

| Rung | What the user does | Needs | Unlocks |
|---|---|---|---|
| **0 · Usage** | one install command, once | Python 3.11+ (brought by the package manager) | Claude / Codex quota pages; the panel finds the computer by itself |
| **1 · Needs You** | `vibepulse_setup.py install` → Claude, Codex, both | hooks / Codex plugin reviewed, device pairing | answer questions and permissions from the panel |
| **2 · Away from home** | `vibepulse_setup.py relay install` | a Cloudflare account | numbers, and optionally answers and live status, on any Wi-Fi |
| **3 · Extras** | one flag each | nothing new | GitHub stars, plan cost on the value page, later Home Assistant |

Rungs 1–3 exist. Rung 0 today is five steps (install Python, clone, install
the discovery dependency, run `tokenserver.py`, install autostart) and is
the computer-side equivalent of the `secrets.h` wall.

Rung 0 target: one line per OS that installs the service, its one
dependency, autostart, and the network advert together, with nothing to
configure.

- **macOS:** a Homebrew tap. `brew install <tap>/vibepulse` brings Python;
  `brew services start vibepulse` is launchd. The existing
  `tools/vibepulse_macos_service.py` remains the path for people who run
  from a checkout.
- **Windows:** a PowerShell one-liner that installs Python via `winget` when
  it is missing and then runs the shipped
  `tools/tokenserver/install-windows-task.ps1`. No new installer logic.
- **Linux:** the setup page says *not yet*, matching
  `docs/platform-support.md` and issue #2.

The setup page (the URL behind the panel's QR) detects the OS from the
browser and shows that command first, with tabs for the other. It states
the one real prerequisite above the command: *Claude Code and/or Codex must
be installed and signed in on this computer.*

Two decisions this rung needs:

1. **"Pure stdlib" stops being a rule for the service.** It was a virtue for
   hand-running a script. Once the service is a package, `zeroconf` is a
   dependency and discovery is the default, not an optional extra. The
   compiled URL in `secrets.h` remains the fallback.
2. **User-facing name is `vibepulse`.** The user installs VibePulse and runs
   `vibepulse …`. File names (`tokenserver.py`) do not change.

## Settings page

A ~3 s KEY3 hold opens **Settings** instead of jumping straight to one
window. The two windows it used to open are the first two entries:

```
hold KEY3 ~3 s
  ├─ UPDATE      the existing OTA maintenance window, unchanged
  ├─ WIFI        the existing Wi-Fi setup window, unchanged
  ├─ FEATURES    on/off switches, saved in NVS
  └─ ABOUT       firmware version, IP, computer found, device identity
```

- **Consent model unchanged.** The menu is reachable only from the device,
  so physical presence is still required for UPDATE; the token and the
  ten-minute window are untouched. "Hold twice to reach Wi-Fi" becomes a
  menu entry.
- **The UPDATE READY takeover and its UPDATE pill are unchanged.**
- **A short KEY3 tap still means "next app"** and still ends an OTA chain.

### FEATURES

| Switch | Default | Notes |
|---|---|---|
| Burn rate page | on | today: always present |
| Max Tracker pages | on | today: always present |
| Value page | on | today: always present, dashed until a plan cost is set |
| GitHub page | off | today: `TK_GITHUB_SCREEN_ENABLED` |
| GitHub star popup | off | today: `TK_GITHUB_NOTIFICATIONS_ENABLED` |
| Sound | off, **greyed** | *not verified on this unit* until the speaker gate passes |

Rules:

- Defaults equal today's behaviour, so an existing panel is unchanged.
- A compile-time flag set to 1 in a build sets that switch's **default** to
  on for that build. The flags become the floor, exactly like the Wi-Fi
  floor. They are not removed and not renamed; new switches may add
  `TK_LABS_*` names alongside.
- A switch whose feature needs the computer (GitHub needs a repository
  configured on the service, the value page needs a plan cost) shows
  *set up on your computer* next to the switch. The states already exist:
  `/api/github` answers with a *disabled* snapshot when no repository is
  configured, and the value page already renders dashed until a plan cost
  is set. The switch row reuses them; the panel never shows an empty page.
  (`GET /` is diagnostics the screen never parses; it is not the source.)
- Switches apply **on the next boot**, and the page says so. The tileview
  is built once at start-up under the LVGL lock; rebuilding it live is a
  memory and DMA risk on this board for no user benefit.
- Storage is NVS, namespace `vp_features`, one `u8` per switch, absent means
  default. No switch is written by anything but the FEATURES page.

## Runtime configuration: what stops being compile-time

| Item | Today | After | Floor behaviour |
|---|---|---|---|
| Wi-Fi networks | `secrets.h` + NVS | NVS first | compiled networks stay as the immutable floor (already true) |
| Service address | `TK_VIBEPULSE_BASE_URL`, required | DNS-SD by default; optional URL via *Manual setup* | a compiled URL is still honoured as the fallback |
| OTA token | `TG_OTA_TOKEN` | generated on the device at first boot, shown in ABOUT, entered once on the computer | a compiled token is still honoured |
| Needs You device key | `TK_VIBEPULSE_DEVICE_KEY` | rung 1 pairing; out of scope for phase 1 | unchanged |
| Relay configuration | `secrets.h` | rung 2; out of scope for phase 1 | unchanged |
| GitHub / sound flags | compile-time | FEATURES switch with the flag as floor | unchanged |

The decisive consequence: a build from an **empty** `secrets.h` becomes a
useful panel. That is what allows one release binary.

### Release binary and the rule about `torget.bin`

`AGENTS.md` says `torget.bin` is never attached to a release because it
contains the user's Wi-Fi credentials and device key. That rule is correct
and stays. It becomes precise instead of absolute: **a binary built with a
populated `secrets.h` is never published.** CI builds the release binary from
the checked-in `secrets.h.example` with every value empty and publishes
`vibepulse-<tag>.bin` plus the ESP Web Tools `manifest.json`. A gate in CI
fails the release if any secret macro in the build is non-empty.

### Web installer

A GitHub Pages site (proposal: `installer/` in this repo) with the ESP Web
Tools button pointing at the release manifest. Chrome and Edge only; the
page says so and points Safari and Firefox users to the manual path. The
page shows the BOOT + RESET sequence for download mode, because on this
board that step is not optional.

## Repository structure: core and add-ons

- **Firmware:** existing components stay where they are. New optional
  features live under `components/labs_*`; new optional host code under
  `tools/labs/`.
- **Docs:** `docs/labs/README.md` is the index of every optional feature —
  its switch, its rung, and its status: *verified add-on* or *experiment*.
  The README links to it from *Start here*.
- **Naming:** "Labs" is the folder and the index. A feature in Labs is not
  second-class; it is optional and its verification status is stated.

## Sequencing

Each step ships on its own, leaves the previous path working, and can be
reverted alone.

1. README prerequisites. **Done 2026-09-04.**
2. README front door for vibecoders, `.gitignore` hygiene, this spec.
   **This change.**
3. Settings page and NVS switches. Firmware only; defaults equal today.
4. Runtime configuration: DNS-SD default, device-generated OTA token,
   compiled values honoured as floor. Immediate-open Wi-Fi window on an
   empty panel.
5. CI release binary from an empty `secrets.h`, secret gate, web installer.
6. Pairing screen on the glass, setup page, Homebrew tap and Windows
   one-liner.
7. Later and separately: leaner defaults for fresh installs; `docs/labs/`
   index; Home Assistant as an add-on.

## Branch hygiene (snapshot 2026-09-04)

Recorded so the cleanup is a decision, not an accident.

- **Fully merged (`ahead=0`), 25 branches:** deletable without loss after
  the owner's approval; every commit is reachable from `main`.
- **Foreign history, 2 branches** (`claude/vibeonchip-repo-uowgeg`,
  `worktree-max-tracker`): a different root commit; not this repo's work.
  Delete after the owner's approval.
- **`niclas/wip/keepboth-20260904`:** a release body byte-identical to the
  one on `main`. Delete.
- **`niclas/wip/wifi-dma-calibration-20260904`:** its core (the Claude
  credential-expiry guard, the loopback check, source-drift states) reached
  `main` through PRs #55–#63. Genuinely unmerged: (a) detection of Codex
  `approval_policy = "never"` and `sandbox_mode = "danger-full-access"` in
  doctor and startup health, with the *Codex approval switch* docs; (b)
  three `docs/lessons.md` entries dated 2026-08-26. Salvage those two as a
  fresh branch off `main`, then delete the WIP branch.
- **`fix/vibepulse-http-hard-recovery-20260830`:** five commits, may have
  been squash-merged; verify before deleting.
- **Local Mac:** 17 worktrees under `~/Torget`; prune the ones whose branch
  is merged once the remote branches are gone.

## Verification contract

### Host and integration tests

- README doc tests gain: the four prerequisite rows, the hardware and
  computer tables, and both start lines (`claude "…"`, `codex "…"`).
- Settings copy tests in the style of `test/test_wifi_setup_wiring.py`:
  menu entries, the *set up on your computer* and *not verified on this
  unit* strings, the *applies on next boot* line.
- A simulator unit test for the NVS switch round trip: absent → default,
  written → read back, compile-time floor → default on.
- A discovery wiring test for a build with no compiled service URL: the
  fetch task exists and polls DNS-SD; today an undefined URL compiles the
  fetch out entirely.
- CI release gate: build from `secrets.h.example`, assert every secret
  macro is empty, publish the binary and manifest.

### Exact 480 × 480 simulator captures

Pairing screen in *looking*, *still looking*, and *found*; Settings menu;
FEATURES list with one greyed switch. Same renderer as the panel; the README
and release reuse the frames.

### Build and physical gates

- First boot to usage pages on the named unit from a build with an empty
  `secrets.h`. This is the gate that proves the design.
- UPDATE from the Settings menu on the unit, then a real OTA.
- Speaker gate before the Sound switch stops being greyed.
- The existing tokenserver suite and the Windows validation gate are
  unchanged and must stay green.

## Release-facing explanation

VibePulse now starts on the glass. Flash it from the browser, scan a QR to
put it on Wi-Fi, scan a second one to install the service on your Mac or PC,
and your Claude and Codex usage appears. Everything you could turn on before
is still there, behind a settings page you open with the same KEY3 hold that
opens updates today.
