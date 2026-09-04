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
  `brew services start vibepulse` is launchd. **Homebrew itself is not
  assumed:** the setup page's macOS command is a shell one-liner that
  installs Homebrew from its official bootstrap when `brew` is missing and
  then runs those two commands, so a fresh Mac needs no prior tooling. A
  user who declines that is offered the checkout path instead. The existing
  `tools/vibepulse_macos_service.py` remains the path for people who run
  from a checkout.
- **Windows:** a PowerShell one-liner that installs Python via `winget` when
  it is missing and then runs the shipped
  `tools/tokenserver/install-windows-task.ps1`. No new installer logic.
- **Linux:** the setup page says *not yet*, matching
  `docs/platform-support.md` and issue #2.

The setup page (the URL behind the panel's QR) detects the OS from the
browser and shows that command first, with tabs for the other. It states
the one thing the command cannot supply, above the command itself: *Claude
Code and/or Codex must be installed and signed in on this computer.*
Everything else the command needs — Homebrew on macOS, Python on either
platform — it installs itself.

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
  carries the features and the macro only sets the default. **Feature
  macros are defaults, not floors:** when NVS holds no value for a switch
  the switch equals the macro; an explicit saved value overrides the macro
  in both directions, so a user can turn off a feature a build turned on.
  The word *floor* is reserved for credentials that must never be removed
  (Wi-Fi networks, tokens) and does not apply to feature switches. The
  flags are not removed and not renamed; new switches may add `TK_LABS_*`
  names alongside. Because the GitHub page and its fetch task
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
| OTA token | `TG_OTA_TOKEN` | **derived independently on both sides** from a password-authenticated key exchange seeded by the on-glass pairing code, inside a device-opened PAIR window (see *The pairing window*). Neither the code nor the token is ever transmitted. Stored in the panel's NVS runtime slot and in the computer's per-user, per-panel credential store. **Never rendered on the glass**: not in ABOUT, not in a QR, not in a log | two slots: the compiled token is an immutable floor that pairing never touches; the runtime slot is **replaced** by each new physically opened, code-authenticated pairing |
| Needs You device key | `TK_VIBEPULSE_DEVICE_KEY` | the same pairing window, later step (*Sequencing* 7) | a compiled key is still honoured |
| Relay configuration | `secrets.h` plus Kconfig. The relay clients are **compiled out** unless `CONFIG_TK_VIBEPULSE_INTERACTION_RELAY` / `…_AGENT_STATUS_RELAY` and their build-time secrets are set (`components/app_tokens/CMakeLists.txt`) | later step (*Sequencing* 7): clients compiled into the release image and gated at runtime, settings provisioned through the pairing window | until then a relay user builds from source exactly as today |
| GitHub / sound flags | compile-time | FEATURES switch; the macro seeds the default and an explicit saved value overrides it | unchanged |

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
   **How the command finds the panel:** no new discovery protocol, but a
   small new registry. By the time PAIR is useful the panel has already
   found the computer over DNS-SD and polled it. Today the service keeps
   only a transient candidate host in `_record_panel_poll` and then the
   last-seen time and route; `_panel_health_snapshot` deliberately omits
   the address, and nothing lists panels. Step 4 adds a bounded in-memory
   **recent-panels registry** to the service (address, running firmware
   version, first and last seen, poll count; at most eight entries; an
   entry expires after the existing panel-fresh window) and a **loopback-only local route** that lists it,
   using the `_is_loopback` check the service already applies to its
   plugin endpoints. Addresses only, never a secret. `vibepulse pair` reads
   that route and picks the panel seen most recently; when more than one
   is fresh it lists them and refuses to guess; and `--device <ip>` is
   always accepted, which is why the PAIR screen shows the panel's IP next
   to the code. The panel's pairing endpoint exists only while its window
   is open.
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
   second attempt after a success is refused. The computer stores the
   derived token in a **per-user, per-panel credential store**, following
   the device key's precedent of a git-ignored file in the home directory:
   `~/.vibepulse/ota-tokens.json` (directory 0700, file 0600), a map from
   the panel's **device id** to its token and last known address, with a
   `VIBEPULSE_OTA_TOKEN` environment override that applies to whatever
   target is named. The device id is stable and non-secret — derived from
   the SoC's factory MAC (`esp_efuse_mac_get_default`; silicon-provided,
   the firmware wiring is new) — shown in ABOUT, and sent by the panel
   inside the PAKE-protected exchange so the computer files the token
   under the right panel. Two panels paired from one account therefore
   hold two distinct tokens and never overwrite each other. Today
   `tools/ota-flash.sh` parses `TG_OTA_TOKEN` from the repository-root
   `secrets.h` and nothing else, which a package install (Homebrew, no
   checkout) does not have, and a file inside a Homebrew Cellar would
   vanish on upgrade. Step 4 therefore changes the uploader —
   `tools/ota-flash.sh` and the packaged `vibepulse update` alike — to
   resolve the credential **from the pairing record, never from the
   network**: the record is selected by `--device <id>`, or by the target
   address (`--device <ip>` or the `.ota-device` file) matching exactly one
   record; with no unique match the uploader refuses and asks for
   `--device <id>`. It never asks the panel which credential to load, so
   an impostor endpoint cannot name another panel's id and collect that
   panel's bearer.
   **Addresses change; identities do not.** The panel adds its non-secret
   device id and its running firmware version to every poll as
   `X-VibePulse-Device` and `X-VibePulse-Version` headers, next to the
   `X-VibePulse-Recovery-Boot` header `torget_http.c` already sets (a
   repository-wide search finds no version header on polls today, so both
   are new). After an update, polls carry the opaque
   `X-VibePulse-Boot-Proof` described under *The paired upload never sends
   the token*, but only once the boot-health gate has accepted the image. The recent-panels registry maps id to *current* address and
   running version, and stores the boot proof verbatim without verifying
   it, as long as the panel keeps polling. The uploader resolves the address for a record in
   this order: `--device <id> --at <ip>` when given explicitly; the
   registry's current address for that id; the record's last-known
   address. A bare `.ota-device` IP matches a record either by last-known
   address or by the id the registry reports for that address, so DHCP
   churn needs neither re-pairing nor hand-editing the store. Any address
   obtained this way is only a place to *try*: the authenticated upload
   below decides whether the panel there is the paired one, and after a
   successful upload the record's last-known address is updated.
   **The paired upload never sends the token.** A separate "prove you
   hold the token, then here is the bearer" step is rejected: an impostor
   at the target address can relay the nonce to the real panel while its
   window is open, return the real answer, and then collect the bearer.
   Such a proof shows the token exists *somewhere*, not that the endpoint
   is the paired panel. So the firmware request itself is authenticated
   and there is no bearer to hand over:

   1. The uploader asks the panel at the chosen address for a challenge:
      `GET /ota/challenge`, which exists only inside the UPDATE window and
      returns the panel's device id plus a fresh, single-use nonce that
      expires with the window.
   2. It checks the returned device id against the selected record and
      aborts on mismatch — an early exit, not the security boundary.
   3. It computes `auth = HMAC-SHA256(token, "vibepulse-ota-v2" ‖ device
      id ‖ nonce ‖ declared SHA-256 ‖ declared Content-Length)` and sends
      the image with `X-VibePulse-Device`, `X-VibePulse-Nonce`,
      `X-VibePulse-Auth`, and the `X-VibePulse-SHA256` header the upload
      already carries today. The token never leaves the computer.
   4. **The panel authenticates the headers before it touches flash.** It
      recomputes the MAC over the header fields alone with each populated
      slot's token — `runtime` and/or `compiled`, so both slots stay usable
      when they coexist — and only on a match does it consume the nonce and
      call `esp_ota_begin`. An unauthenticated request is rejected without
      reading its body, so a LAN peer cannot make the inactive partition
      erase or accept a single unauthenticated write during a window
      (today `ota_service.c` streams straight into `esp_ota_begin` /
      `esp_ota_write` once the bearer and the SHA-256 header are present).
      It then streams the body while hashing, as it already does, and
      aborts the update if the streamed digest differs from the declared
      one. The existing image checks in `docs/ota.md` then run exactly as
      today.
   5. **The result is authenticated too.** The panel answers with
      `X-VibePulse-Ack = HMAC-SHA256(token, "vibepulse-ota-ack-v2" ‖ device
      id ‖ nonce ‖ SHA-256(image) ‖ result ‖ version written)`, keyed by
      the slot that matched. The uploader verifies it against its own token
      before reporting anything, so an unpaired endpoint that accepted the
      bytes cannot fabricate a success. Following the honesty invariant,
      the uploader has exactly three states and none of them is guessed:
      - **unknown** — no ack, or an ack that fails verification. Nothing
        proves the selected panel received anything. Never shown as
        delivered.
      - **delivered** — a valid ack. The paired panel holds the image;
        whether it boots it is not yet known.
      - **running** — an **authenticated boot proof**, never a bare poll.
        The panel stores the update nonce in NVS next to the pending image
        (the boot-health gate already keeps state there across a reboot),
        and adds `X-VibePulse-Boot-Proof = HMAC-SHA256(token,
        "vibepulse-boot-v2" ‖ device id ‖ nonce ‖ running version)`, keyed
        by the slot that authenticated the upload.
        **The proof waits for the health gate, not for the first poll.**
        On a freshly updated image the partition is `PENDING_VERIFY` and
        `boot_health.c` decides between acceptance and rollback no earlier
        than `TG_HEALTH_MIN_UPTIME_US` (8 s) and no later than
        `TG_HEALTH_DEADLINE_US` (15 s), while the quota task starts
        polling well before that. Emitting the proof on the first polls
        would let the updater say *running* about an image that is still
        one failed check away from rollback. The panel therefore sends the
        header only after `esp_ota_mark_app_valid_cancel_rollback()` has
        succeeded, which is exactly the moment the image stops being
        revocable. Polls before that carry the device id and version as
        usual and prove nothing.
        The service holds no token: the recent-panels registry stores that
        header verbatim as an opaque value, and `vibepulse update` verifies
        it against its own token and the nonce it issued. The proof is
        single-use; the panel clears the nonce once it has sent it for a
        bounded number of polls. A LAN peer that polls with the public
        device id and version but no valid proof never produces *running*,
        and a panel that rolled back never marked itself valid, so it sends
        no proof and stays *delivered, not running*.
      A panel with only a compiled token, updated over the legacy bearer
      path, gets none of this: the updater reports *sent* and shows the
      version the panel later polls with as *reported by the panel,
      unauthenticated*, exactly as honest as today.

   What a relay gains: nothing durable. Forwarding the exchange to the real
   panel installs exactly the image the user chose on exactly the panel they
   chose; the MAC is bound to that image and that nonce, so no other image
   and no second use. The impostor ends up holding no token and no reusable
   credential.

   **Legacy path, unchanged.** An upload carrying `Authorization: Bearer
   <compiled token>` keeps working exactly as today, so existing panels and
   the developer flow are untouched; it keeps the plaintext-bearer exposure
   `docs/ota.md` already documents. `tools/ota-flash.sh` and the packaged
   `vibepulse update` use the authenticated request whenever they hold a
   runtime token and fall back to the bearer only for a compiled one. This
   *does* change the upload path for paired panels, deliberately: step 4
   updates `docs/ota.md` so the *Knowledge* factor reads "proven, never
   transmitted" for a paired panel.
   Lookup order for the token is unchanged: environment, the per-panel
   store, then `secrets.h` when a checkout exists. A single-panel user
   never sees the map; the developer flow keeps working unchanged.
4. **Time and confirmation** — on success the glass shows
   **PAIRED · \<origin of the computer\>** and the window closes at once;
   the computer treats only the panel's success response as success.
   Outside a window the pairing endpoint does not exist, following the
   lazy-surface rule the Wi-Fi window uses.

Two token slots exist, and each is usable only through its own path:

- the **compiled** slot (`TG_OTA_TOKEN`), the immutable floor: pairing
  never reads, replaces, or removes it. It is the **only** slot the legacy
  `Authorization: Bearer` upload is checked against, and it is also
  accepted by the authenticated request;
- the **runtime** slot in NVS, which pairing fills, and which a fresh
  physically opened, code-authenticated window **replaces**. The previous
  runtime token is invalid the moment the new exchange completes. It is
  honoured **only** through the authenticated request: a bearer equal to
  the runtime token is rejected with 403, so no tool that reads the
  per-panel store can ever put that secret on the wire in plaintext.

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
   PAKE-derived OTA token (never transmitted), **and the PAIR screen that
   shows its code and the panel's IP** — the code has to be readable off
   the glass for `vibepulse pair <code>` to exist at all, so that screen
   ships with the window, not later. Also the per-panel credential store
   keyed by device id, the uploader resolving the credential by target,
   compiled values honoured as floor, and the immediate-open Wi-Fi window
   on an empty panel.
5. The `AGENTS.md` release-rule change as its own maintainer decision; only
   then the CI release binary from an empty `secrets.h`, the secret gate,
   and the web installer.
6. The computer-pairing screen from *Onboarding on the glass* (the
   **Install VibePulse on your computer** QR, *Looking for your
   computer…*, *Found*), the setup page behind that QR, discovery
   retaining the instance label (with test), Homebrew tap and Windows
   one-liner.
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
- A simulator unit test for the NVS switch round trip: absent → the
  macro's value, written → read back, and a saved `0` overriding a macro
  set to `1` (macros are defaults, never floors, for feature switches).
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
  packaged updater resolve the credential by the target panel's device id
  from the environment, then `~/.vibepulse/ota-tokens.json`, then
  `secrets.h`; that two panels paired from one account get distinct
  entries and the uploader picks the right one; and that the store works
  from a directory that is not a checkout.
- Recent-panels registry: a test that the service records up to eight
  distinct polling addresses with first/last seen and count, expires them
  after the panel-fresh window, exposes them only to loopback callers, and
  never includes a token or code.
- Pairing target: a test that `vibepulse pair` selects the most recently
  seen panel from that registry, lists and refuses to guess when several
  are fresh, and accepts `--device <ip>`.
- Authenticated upload, header-first: a test that a request with a wrong
  or missing `X-VibePulse-Auth` is rejected before `esp_ota_begin` is
  called and before any body byte is read; that a body whose streamed
  digest differs from the declared one aborts the update; that a bearer
  equal to the runtime token is rejected with 403; and that the legacy
  bearer path still accepts only the compiled token.
- Credential selection and authenticated upload: a test that the uploader
  selects the record by id or by a unique address match and refuses
  otherwise; that `--device <id>` resolves the current address from the
  registry when the panel's IP has changed, then the last-known address,
  and that `--at <ip>` overrides both; that a successful upload updates the
  record's last-known address; that no request in a paired upload contains
  the token (transcript assertion); that the panel rejects a MAC over a
  different image, a reused nonce, an expired nonce, and a wrong device
  id; that a captured request replayed after its nonce is consumed is
  rejected; that a panel with both slots accepts a MAC keyed by either
  token; that the uploader reports success only on a valid keyed ack and
  treats a missing or wrong ack as *unknown* and never as delivered; that the
  legacy bearer upload still works with a compiled token; and that the
  challenge endpoint is absent outside the window.
- Poll identity, version, and boot proof: a test that every panel poll
  carries `X-VibePulse-Device` with the device id and `X-VibePulse-Version`
  with the running firmware version; that the registry keys entries by the
  id, records the version, and stores `X-VibePulse-Boot-Proof` verbatim
  without verifying it; that the updater's *running* state appears only
  when a boot proof verifies against its token and the nonce it issued;
  that a poll with the correct id and version but no proof, a wrong proof,
  or a proof for an earlier nonce never produces *running*; that no proof
  is emitted while the running partition is `PENDING_VERIFY`, so a panel
  whose health gate later rolls back stays *delivered, not running*; and
  that none of the headers ever carries a token or code.
- Feature switches: a simulator test that a build with every GitHub flag
  at 0 creates the GitHub page, the star popup, and the fetch task once the
  NVS switches are on, and omits them when off; that a build with a flag at
  1 and a saved 0 keeps the feature off; that an absent value follows the
  macro; and a recorded internal-RAM measurement on the unit with the
  features resident.
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
