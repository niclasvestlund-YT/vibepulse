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
   it with a phone, pick the network. *(the QR portal is built; showing it
   at once on an empty panel is new, step 4 — today
   `tg_wifi_setup_should_open()` waits the 90-second `TG_WIFI_SETUP_AUTO_US`)*
3. The glass says **Install VibePulse on your computer** with a QR and a
   short URL, then **Looking for your computer…**. One command on the Mac
   or PC. The glass says which computer it found and shows the usage pages.
   *(new screen; the discovery behind it is built)*

Everything else — burn rate, Max Tracker, the value page, GitHub, answering
Needs You from the panel, the relays, sound — stays available and is turned
on from a settings page on the glass or a command on the computer. Two of
them, Needs You answering and the relays, need panel-side provisioning that
is a later step (*Sequencing* 7) before they work on a release binary; until
then they work exactly as today, from a build with `secrets.h` filled in.

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
  every user **for the core (rung 0)**; *Runtime configuration* says what a
  release binary cannot do until step 7.
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

The header names the computer it pinned to — once discovery can supply the
name. The service advertises itself as `VibePulse-<hostname>`, but the
firmware's discovery client keeps only the `IP:port` origin today
(`select_result()` in `components/torget_net/service_discovery.c`; NVS
persists the origin alone). Step 6 therefore includes retaining the instance
label in discovery state and exposing it through the endpoint API, with a
test, before the header may show a name. Until then the header shows the
origin it is polling, never a name it did not receive. The usage pages
appear. This is the same tileview as today.

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
  ├─ PAIR        opens a timed pairing window for the computer (OTA token;
  │              later the device key and relay settings)
  └─ ABOUT       firmware version, IP, computer found. Never a secret.
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
- The compile-time flags stop deciding what is *compiled* and only seed
  each switch's **default**. Today `#if TK_GITHUB_SCREEN_ENABLED` and
  `#if TK_GITHUB_NOTIFICATIONS_ENABLED` in `usage_screen.c`, the
  `TK_GITHUB_URL` guard in `github_net.c`, and `#if TK_GITHUB_SOUND_ENABLED`
  in `project_star_chime.c` omit the page, popup, fetch task, and chime
  from a flag-0 build, and no NVS value can bring back code that was never
  compiled. Step 3 turns those guards into runtime checks, so every image
  carries the features and the macro only sets the default. A flag set to
  1 in a build is then the floor for that switch, exactly like the Wi-Fi
  floor. The flags are not removed and not renamed; new switches may add
  `TK_LABS_*` names alongside. Because the GitHub page and its fetch task
  are then always resident, the internal-RAM budget is re-measured on the
  unit before step 3 ships (display, TLS, and Wi-Fi already compete for
  it, per `spec/hardware-capabilities.yaml`).
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
| OTA token | `TG_OTA_TOKEN` | **derived independently on both sides** from a password-authenticated key exchange seeded by the on-glass pairing code, inside a device-opened PAIR window (see *The pairing window*). Neither the code nor the token is ever transmitted. Stored in the panel's NVS runtime slot and in the computer's per-user credential file. **Never rendered on the glass**: not in ABOUT, not in a QR, not in a log | two slots: the compiled token is an immutable floor that pairing never touches; the runtime slot is **replaced** by each new physically opened, code-authenticated pairing |
| Needs You device key | `TK_VIBEPULSE_DEVICE_KEY` | the same pairing window, later step (*Sequencing* 7) | a compiled key is still honoured |
| Relay configuration | `secrets.h` plus Kconfig. The relay clients are **compiled out** unless `CONFIG_TK_VIBEPULSE_INTERACTION_RELAY` / `…_AGENT_STATUS_RELAY` and their build-time secrets are set (`components/app_tokens/CMakeLists.txt`) | later step (*Sequencing* 7): clients compiled into the release image and gated at runtime, settings provisioned through the pairing window | until then a relay user builds from source exactly as today |
| GitHub / sound flags | compile-time | FEATURES switch with the flag as floor | unchanged |

The decisive consequence: a build from an **empty** `secrets.h` becomes a
useful panel **for rung 0** — Wi-Fi, discovery, the usage pages, and OTA
after pairing. It does not yet cover Needs You answering (device key) or any
relay; those need the runtime provisioning in step 7, and until it lands a
relay user rebuilds from source as today. "One binary serves every user" is
true only once step 7 ships, and the README must not say it before.

### The pairing window and the OTA consent model

`docs/ota.md` requires three independent factors before firmware can be
written: physical presence, knowledge of the token, and a time window. The
pairing window keeps all three and puts nothing persistent on the glass:

1. **Presence** — PAIR is a Settings entry, reachable only by the KEY3 hold
   on the device, and it opens a ten-minute window like the Wi-Fi window.
2. **Binding to this computer** — while the window is open the glass shows
   a short-lived **pairing code** (proposal: six digits, random per window,
   valid only for that window). `vibepulse pair <code>` must present it.
   This is the same model as the Wi-Fi window's access-point password:
   whoever cannot see the screen cannot enrol. The code is not the token
   and expires with the window, so showing it exposes nothing durable.
3. **Knowledge, without the token ever crossing the LAN** — the code is not
   sent and the token is not sent. Both sides run a password-authenticated
   key exchange seeded by the code (proposal: SRP6a, which ESP-IDF ships as
   `protocomm` security 2, or SPAKE2+; the choice is made at implementation
   by what the panel and the host's pinned crypto can both run) and derive
   the token from the resulting session key with HKDF. A passive observer
   of the exchange — a sniffing or ARP-spoofing LAN peer included — learns
   nothing that yields the token; an active peer without the code fails
   the exchange and burns one of three attempts, after which the window
   closes. The panel completes **one** exchange per window, stores the
   derived token in NVS, and never displays, logs, or re-transmits it; a
   second attempt after a success is refused. The computer stores the same
   derived token in a **per-user credential file**, following the device
   key's existing precedent: `~/.vibepulse-ota-token`, mode 0600, with a
   `VIBEPULSE_OTA_TOKEN` environment override. Today `tools/ota-flash.sh`
   parses `TG_OTA_TOKEN` from the repository-root `secrets.h` and nothing
   else, which a package install (Homebrew, no checkout) does not have, and
   a file inside a Homebrew Cellar would vanish on upgrade. Step 4 therefore
   changes the uploader — `tools/ota-flash.sh` and the packaged
   `vibepulse update` alike — to look up the token in this order:
   environment, per-user file, then `secrets.h` when a checkout exists, so
   the existing developer flow keeps working unchanged.
   *Scope note:* at upload time the bearer still travels as `docs/ota.md`
   specifies today; this spec changes enrolment only and does not claim to
   improve the upload path. Making the upload prove possession without
   sending the bearer is a candidate for a later step, not part of this
   one.
4. **Time and confirmation** — on success the glass shows
   **PAIRED · \<origin of the computer\>** and the window closes at once;
   the computer treats only the panel's success response as success.
   Outside a window the pairing endpoint does not exist, following the
   lazy-surface rule the Wi-Fi window uses.

Two token slots exist and the panel accepts a bearer matching either:

- the **compiled** slot (`TG_OTA_TOKEN`), the immutable floor: pairing
  never reads, replaces, or removes it;
- the **runtime** slot in NVS, which pairing fills, and which a fresh
  physically opened, code-authenticated window **replaces**. The previous
  runtime token is invalid the moment the new exchange completes.

That is how rotation and recovery work: a compromised runtime token, or a
lost paired computer, is fixed by opening PAIR again and pairing again,
with no NVS erase and no USB. A panel with only a compiled token behaves
exactly as today.

### Release binary and the rule about `torget.bin`

`AGENTS.md` says `torget.bin` is never attached to a release because it
contains the user's Wi-Fi credentials and device key. **That rule stands as
written until the maintainer changes it, and this spec does not change it.**
Renaming the artifact would not satisfy it. Step 5 is therefore two decisions
in order: first a separate `AGENTS.md` change, approved by the maintainer in
its own PR, that rewrites the rule as *a binary built with a populated
`secrets.h` is never published*; only after that may CI build a release
binary from the checked-in `secrets.h.example` with every value empty and
publish the complete flash set described under *Web installer* plus the ESP
Web Tools `manifest.json`, with a gate that fails the release if any secret
macro in the build is non-empty.
Until that rule change is merged, releases stay source-only and the web
installer cannot ship.

### Web installer

A GitHub Pages site (proposal: `installer/` in this repo) with the ESP Web
Tools button pointing at the release manifest. Chrome and Edge only; the
page says so and points Safari and Firefox users to the manual path. The
page shows the BOOT + RESET sequence for download mode, because on this
board that step is not optional.

**A fresh board needs more than the app image.** `partitions.csv` is an A/B
layout (`nvs`, `otadata`, `phy_init`, `ota_0`, `ota_1`); the first USB flash
writes the bootloader, the partition table, the OTA initial-data image, and
the application into `ota_0`, each at its own offset. An ESP Web Tools
manifest cannot conjure the missing regions from an application binary, so
the release publishes either one merged factory image produced with
`esptool.py merge_bin`, or every offset-specific part as a multi-part
manifest. In both cases the parts and offsets come from the build's
`flasher_args.json`, never from hand-typed numbers, and the secret gate
covers every published part. OTA afterwards keeps using the application
image alone, exactly as today.

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
   The `#if TK_GITHUB_*` guards become runtime checks seeded by the
   macros, with the flag-0 toggle test and a re-measured RAM budget.
4. Runtime configuration: DNS-SD default, the PAIR window with the
   PAKE-derived OTA token (never transmitted), the per-user credential
   file and the uploader's lookup order, compiled values honoured as floor.
   Immediate-open Wi-Fi window on an empty panel.
5. The `AGENTS.md` release-rule change as its own maintainer decision; only
   then the CI release binary from an empty `secrets.h`, the secret gate,
   and the web installer.
6. Pairing screen on the glass, setup page, discovery retaining the
   instance label (with test), Homebrew tap and Windows one-liner.
7. Runtime provisioning for the device key and the relay settings through
   the PAIR window; relay clients compiled into the release image and gated
   at runtime. Only after this does one binary serve every rung.
8. Later and separately: leaner defaults for fresh installs; `docs/labs/`
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
- Pairing: a test that no capture and no label in the rendered tree ever
  contains the OTA token; that the pairing endpoint is absent outside the
  window; that a captured transcript of the exchange contains neither the
  code nor the token; that an exchange with a wrong code fails and three
  failures close the window; that a second exchange after success is
  refused; that a new pairing invalidates the previous runtime token; and
  that pairing never touches the compiled token.
- Uploader credential lookup: a test that `tools/ota-flash.sh` and the
  packaged updater read the token from the environment, then
  `~/.vibepulse-ota-token`, then `secrets.h`, and that the per-user file
  path works from a directory that is not a checkout.
- Feature switches: a simulator test that a build with every GitHub flag
  at 0 creates the GitHub page, the star popup, and the fetch task once the
  NVS switches are on, and omits them when off; and a recorded
  internal-RAM measurement on the unit with the features resident.
- Release manifest: a test that every part in the build's
  `flasher_args.json` is present in the published manifest (or merged
  image) at the same offset, so a fresh board boots from the web installer.
- Discovery: a wiring test that the DNS-SD instance label survives
  `select_result()` and is exposed through the endpoint API.

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
