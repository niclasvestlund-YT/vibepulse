# VibePulse

[![CI](https://github.com/niclasvestlund-YT/vibepulse/actions/workflows/ci.yml/badge.svg)](https://github.com/niclasvestlund-YT/vibepulse/actions/workflows/ci.yml)

![VibePulse: quota, a NEEDS YOU alert, and the Max Tracker heatmap](docs/img/hero.png)

**A little always-on screen for your shelf that shows what your AI coding
agents are doing — taps you on the shoulder when one is stuck waiting for
you, and (if you want) lets you answer it with a tap on the glass. It packs
too: one command moves it onto whatever WiFi you are on today.**

Claude Code and Codex usage, live agent activity, and a full-screen
**NEEDS YOU** alert you can answer with a tap. A ~$30 ESP32-S3 panel plus a
core, pure-stdlib Python service on your Mac or Windows PC. Local mode needs no
VibePulse account and keeps agent activity on your LAN. The optional
numbers-only relay can carry quota data across isolated WiFi; a separate,
default-off encrypted interaction relay can carry supported Needs You
decisions without requiring the panel and computer to share a LAN. A third,
independent **Live agent status relay** can keep the Claude/Codex activity rows
current across ordinary internet WiFi. Every cloud feature is off by default;
only the encrypted interaction/status relay adds the pinned Python
`cryptography` dependency.

## The problem

When you run coding agents all day, two things are invisible:

- **How much quota is left.** You usually find out you're at the wall when a
  long task dies halfway through — not before you start it.
- **When an agent stopped.** It asks one yes/no question and then just sits
  there. You're in another window. Sometimes for twenty minutes.

Both answers already exist, buried in a terminal you're not looking at.
VibePulse moves them onto a screen you can't miss: one glance from across
the room, no window to switch to, no menu bar to squint at.

> **Status:** v1.0.0. The core shelf-screen loop is real and physically
> exercised on macOS and Windows: see quota, see an agent waiting, and answer
> a supported prompt on the glass. Windows core, physical answer loop, and
> persistent sign-in/sleep/reboot lifecycle are verified. The project is still
> active, optional integrations remain opt-in, and every platform claim stays
> tied to its recorded evidence.

## Latest release: v1.0.0

The first major release makes Windows a first-class VibePulse host and records
the real physical proof: clean source, full tests, Task Scheduler plus
watchdog, bounded logs, real Claude/Codex sources, Private-LAN reachability,
recent panel polling, and a human **NEEDS YOU → APPROVE → Ja** round trip.
The same panel can discover and fail over between advertising Mac and Windows
hosts. The real Windows host also passed sign-out/sign-in, sleep/resume, and a
full reboot without losing the scheduled service or leaving the panel stale.
Silence and computer fallback are still never approval.

### Windows v1 verification

| Gate | Result |
|---|---|
| Clean host, complete tokenserver suite | **PASS** — 788 tests, 11 named skips, 0 failures/errors |
| Task Scheduler, immediate start, exact-PID watchdog, bounded logs | **PASS** |
| Real Claude/Codex sources, Private-only firewall, LAN and discovery | **PASS** |
| Recent panel polling and **NEEDS YOU → APPROVE → Ja** | **PASS** |
| Sign-out/sign-in, sleep/resume, full reboot | **PASS** |
| Post-transition freshness | **PASS** — bounded convergence, then 12/12 fresh samples after sign-in and reboot |
| Release/lifecycle PR CI and merged-main CI | **PASS** — 14/14 and 7/7 jobs |

The tested host runtime was exact revision `bee5d8c`; tag `v1.0.0` resolves to
`ab3ce92`, with documentation/tests only between them. The lifecycle evidence
was merged to `main` at `4d1c47d`. This is exact-revision evidence: a future
runtime change must pass the gate again.

[Read the v1.0.0 notes](docs/releases/2026-08-28-windows-joins-the-shelf.md)
· [Full Windows evidence](docs/superpowers/reviews/2026-08-28-windows-v1-full-lifecycle.md)
· [Verified merged-main CI](https://github.com/niclasvestlund-YT/vibepulse/actions/runs/33214257872)
· [Full changelog](CHANGELOG.md)
· [Compare v0.7.1...v1.0.0](https://github.com/niclasvestlund-YT/vibepulse/compare/v0.7.1...v1.0.0)

Contributing or validating another host? Read
[CONTRIBUTING.md](CONTRIBUTING.md), the
[host support matrix](docs/platform-support.md), and
[SECURITY.md](SECURITY.md) before sharing logs or test evidence.

## What's on screen

Six core pages, swipe or auto-rotate, plus the always-present value-multiple
page (it shows the dollar total once agents log priced usage, but the
multiple itself stays dashed — `SET YOUR PLAN COST` — until you pick a named
plan tier or state your exact cost) and an optional, compile-time-gated
GitHub project pulse. Every image below is an exact 480×480 frame — the
simulator renders
the same pixels as the panel.

<table>
<tr>
<td width="50%"><img src="docs/img/vibepulse-claude-week.png" alt="Claude weekly quota at 73%" width="100%"></td>
<td valign="top">

**Usage** — Claude's weekly and heaviest-model-weekly quota, plus Codex's
weekly quota. Each with a reset countdown and how much you've burned today.

</td>
</tr>
<tr>
<td><img src="docs/img/vibepulse-needs-you.png" alt="Full-screen NEEDS YOU alert" width="100%"></td>
<td valign="top">

**NEEDS YOU** — when an agent blocks on your input, the whole screen turns
into the alert, in that provider's colour, naming the project it's waiting
on. Tap to dismiss — or, with the opt-in Needs You bridge, **tap to answer
it** without switching windows
([see below](#answer-claude-or-codex-from-the-panel)).

</td>
</tr>
<tr>
<td><img src="docs/img/vibepulse-agent-working.png" alt="Live header showing the working model and effort" width="100%"></td>
<td valign="top">

**Live agent monitor** — the header shows which agents are working right
now, with model and effort, on every page. `2 CHATS ACTIVE` when several
are running.

</td>
</tr>
<tr>
<td><img src="docs/img/vibepulse-burn-rate.png" alt="Burn rate forecast" width="100%"></td>
<td valign="top">

**Burn rate** — a forecast per provider: on pace, running out early (and
when), or how much head-room is left at reset.

</td>
</tr>
<tr>
<td><img src="docs/img/vibepulse-max-tracker.png" alt="Max Tracker heatmap for Codex" width="100%"></td>
<td valign="top">

**Max Tracker** — a GitHub-style heatmap of your daily quota peaks, with
coding streaks and max counters, per provider. Red cells are days you
maxed out.

</td>
</tr>
</table>

Both providers get equal treatment — same pages, same alert, their own
accent colour:

**Codex weekly quota**

![Codex weekly quota](docs/img/vibepulse-codex-week.png)

**Codex NEEDS YOU alert**

![Codex NEEDS YOU alert](docs/img/vibepulse-codex-needs-you.png)

**Claude Max Tracker**

![Claude Max Tracker](docs/img/vibepulse-max-tracker-claude.png)

### Answer Claude or Codex from the panel

The panel becomes an *input device*. With the opt-in Needs You bridge, when
Claude Code or Codex blocks on a supported question or permission, the
takeover appears and **a tap answers it in the same live session** — no window
to switch to. The computer must be awake and the tokenserver must be running.
Direct mode uses the LAN. The separate encrypted interaction relay works when
the panel and computer use unrelated ordinary internet Wi-Fi: both sides make
outbound HTTPS connections, so there is no router reconfiguration, inbound
port, public Mac, or VPN. Cloudflare handles only fixed-size ciphertext; see
the [privacy and setup guide](docs/interaction-relay.md).

<table>
<tr>
<td width="33%"><img src="docs/img/needs-you/vibepulse-needs-you-attract.png" alt="A mascot in a depleting countdown ring — something is waiting" width="100%"></td>
<td width="33%"><img src="docs/img/needs-you/vibepulse-needs-you-question.png" alt="The decision screen: the question and Claude's recommended answer" width="100%"></td>
<td width="33%"><img src="docs/img/needs-you/vibepulse-needs-you-payoff.png" alt="A short ON IT payoff beat after you answer" width="100%"></td>
</tr>
</table>

**Attract → decision → done.** A held prompt surfaces as a mascot in a
depleting countdown ring; a tap reveals it; **APPROVE** commits the agent's
explicitly recommended option (or **LEAVE IT** hands it back to the computer),
and the flow closes on a short "ON IT" beat. The panel signs every verdict with a
key shared only with your computer — it can answer a prompt that computer was
already going to ask about, and nothing more. Walking away always costs
nothing: an unanswered prompt just falls back to the terminal. Setup is in
[docs/agent-setup.md](docs/agent-setup.md).

For Codex, only its narrow safe-command tier can show **ALLOW ONCE**. Unknown,
mutating, secret-bearing, or text that does not fit stays on the computer;
silence never means approval. Recommended questions are equally strict: Codex
must mark one of two or three options itself. VibePulse never guesses.

After a firmware or Codex-bridge change, use the canonical physical smoke test
in [docs/agent-setup.md](docs/agent-setup.md#post-flash-physical-codex-smoke-test).
A pass requires visible **APPROVE**, a real panel tap, and the matching answered
result back in Codex. A waiting screen, timeout, **LEAVE IT**, or computer
fallback is not a pass.

### Independent switches

VibePulse is open source, so installing one part never silently enables
another. Each row is an independent switch and every interaction/cloud choice
starts off:

| Switch | What it does | Default |
|---|---|---|
| **Claude interactions** | Lets Claude Code questions and permissions reach the panel | Off |
| **Codex interactions** | Lets supported Codex questions and permissions reach the panel | Off |
| **Numbers relay** | Publishes only quota, reset, Max Tracker, and optional public GitHub numbers | Off |
| **Interaction relay** | End-to-end encrypted question/verdict mailbox for unrelated WiFi | Off |
| **Live agent status relay** | End-to-end encrypted Claude/Codex activity rows for unrelated WiFi | Off |
| **GitHub** | Shows one public repository's page and/or star notification | Off |

Installing the Codex plugin does not enable Codex interactions. Setup asks
whether to enable Claude, Codex, both, or neither, and whether bounded detail
may reach the panel. The old `--interactions` is a legacy alias for Claude only;
use the explicit setup command for new installations. The numbers relay and
interaction relay are different privacy choices and neither is enabled by the
plugin. Installing the Codex plugin does not enable the encrypted interaction
relay or the live agent status relay.

### Optional GitHub project pulse

One public `owner/repository` can add a deliberately sparse seventh page:
the current star count is the hero and forks are the only secondary metric.
The same raster covers every data provenance, so the glass never lies about
freshness:

**Live**

![GitHub tile showing a live star and fork count](docs/img/github/sim-live.png)

**Cached / stale**

![GitHub tile showing a cached star and fork count](docs/img/github/sim-cached.png)

**Waiting (no data)**

![GitHub tile waiting on its first fetch](docs/img/github/sim-missing.png)

The page and star moments are independent switches. A new star can therefore
briefly take over the current VibePulse view even when the GitHub page is not
in rotation. It covers the previous page with a quiet black stage, shows a
large filled star, the repository, the stargazer when GitHub supplies one,
the new total, and `TAP TO DISMISS`; otherwise it returns to the exact
previous page after two minutes.

<img src="docs/img/github/sim-star-popup.png" alt="Full-screen popup celebrating a new GitHub star" width="320">

The computer service polls GitHub's public API and republishes a small, validated
LAN payload. The ESP32 never talks to GitHub, and a GitHub timeout or rate
limit cannot stall the Claude/Codex endpoints. Configure it with:

```
python3 tools/tokenserver/tokenserver.py --github-repo owner/repository
```

On Windows, persist the same source in Task Scheduler instead of relying on a
foreground shell:

```powershell
.\tools\tokenserver\install-windows-task.ps1 `
  -GithubRepo "owner/repository"
```

Then opt into `TK_GITHUB_SCREEN_ENABLED` and/or
`TK_GITHUB_NOTIFICATIONS_ENABLED` in your gitignored `secrets.h`. Both are
off by default. No GitHub token is required for a public repository.

`TK_GITHUB_SOUND_ENABLED` is a separate default-off gate for the 258 ms
A5-to-C#6 chime. The sequence and failure-isolated playback contract are in
place, but the current target intentionally registers no codec backend until
the physical speaker and display-DMA budget have passed device testing. A
missing or failed sound backend never delays the popup or any network path.

### It never makes numbers up

<table>
<tr>
<td width="35%"><img src="docs/img/vibepulse-no-data.png" alt="No-data state showing dashes instead of zeros" width="100%"></td>
<td valign="top">

Before the first successful fetch, and whenever a source is missing, you get
dashes — never a placeholder `0%` that you might believe. If the service
goes away, the last good numbers stay on screen and get marked stale rather
than silently drifting.

On current firmware a wall-powered panel also protects itself against the
specific failure where Wi-Fi still looks associated but application HTTP has
stopped. Once the quota feed has worked at least once, and only when an
independent numbers relay is configured, 60 seconds without a fresh response
recycles Wi-Fi and wakes the quota task, which waits for a new IP before
retrying. If no real success follows within another 45 seconds, the device restarts once to clear
wedged HTTP/TLS state. A reboot is disarmed until a new real success, so a real
internet outage cannot become a restart loop. LAN-only installations never
perform this automatic recovery just because their computer is asleep.
After that controlled restart, the panel sends a fixed content-free recovery
marker on local requests. `GET /` exposes it only as
`interactions.panel.httpStallRecoveryBoot`; setup doctor and the next Codex
startup can therefore distinguish a recovered wall-powered panel without a
USB serial cable. It contains no hostname, address, account, or usage value.

Run Claude only, or Codex only, and the other half simply shows dashes.

</td>
</tr>
</table>

### Are you getting your money's worth?

<table>
<tr>
<td width="35%"><img src="docs/img/vibepulse-value-ahead.png" alt="Value multiple showing 3.12x — $312 via API against a $100 plan" width="100%"></td>
<td valign="top">

The usage pages answer *how much have I spent?*. The
[**value multiple**](docs/value-multiple.md) answers the question you
actually have every month: it prices the tokens your agents already logged
at list API rates and divides by what you pay. It's its own page on the
swipeable strip, alongside GitHub — neither replaces the other.

</td>
</tr>
</table>

```
python3 tools/tokenserver/tokenserver.py --claude-plan max5x --plan claude=100
```

The equivalent persistent Windows setup is explicit per provider and never
guesses what you pay:

```powershell
.\tools\tokenserver\install-windows-task.ps1 `
  -ClaudePlan max5x -ClaudePlanCostUsd "100" `
  -CodexPlan pro -CodexPlanCostUsd "20"
```

It counts cache tokens, which is the whole point — a real record here reads
2 input and 4 output against 23 655 cache-read, so pricing only input and
output understates it by 577x.

Rates are not hand-maintained: they are generated from a public price
catalogue by `tools/tokenserver/update_prices.py` and committed, so the
server stays offline and refreshing is one command. An unknown model degrades
the figure to a dash rather than being silently free.

## How it works

```
      your computer                          your shelf
┌────────────────────────────┐          ┌──────────────┐
│ ~/.claude/projects/*.jsonl │          │              │
│ ~/.codex/sessions/*.jsonl  │ ───────► │   ESP32-S3   │
│ rate-limit headers         │          │    AMOLED    │
└────────────────────────────┘          └──────────────┘
     tokenserver.py :8737           plain JSON over your LAN,
     pure Python stdlib                polled every 30 s
```

A tiny Python service on your Mac or Windows PC reads your local Claude Code / Codex logs
and rate-limit headers, and serves plain numbers over your LAN. The screen
polls it every 30 seconds. Your OAuth token never leaves the computer; the screen
only ever receives percentages, counts and coarse status.

The computer must be on for fresh local data. It does not have to stay in the
same house when a relay is enabled, but it does have to run the tokenserver so
there is something to publish. A phone hotspot is fine after it has been taught
to the panel; captive portals and 5 GHz-only networks are not.

The startup/doctor health check also guards Claude's saved usage credential.
`GET /` exposes only `claudeCredential.status` and whole minutes remaining—
never an access or refresh token—and warns 30 minutes before expiry. This
matters because Claude Desktop can remain logged in after the separate
credential readable by VibePulse has aged out; stale Fable data is never
reported as current. Read that guard together with `claudeProbe` and the
`/api/tokens` stale flags: a successful probe plus a fresh model-week flag
means the current source is live even if the saved fallback is expired. That
is a future recovery risk, not a reason to restart the tokenserver.

Codex plugin `0.1.7` turns the trusted `SessionStart` hook into a real bounded
health check. It reads only the two loopback JSON endpoints, follows no
redirects, times out in under a second, and injects one content-free class into
the new task:

| Startup class | What it proves | First action |
|---|---|---|
| `HEALTHY` | Provider data and recent direct panel polling are fresh | None |
| `HEALTHY AFTER DEVICE SELF-RECOVERY` | The same, after the bounded HTTP-stall restart | Keep observing past the stale window; this is evidence, not a physical PASS |
| `PROVIDER DATA STALE` | Claude and/or Codex source data is stale | Run setup doctor and tokenserver smoke; inspect the named provider |
| `DEVICE PATH STALE` / `PANEL LAN WAITING` | Host data is fresh but direct glass polling is stale or unconfirmed | Check panel power, network, discovery, and firmware before restarting a healthy host |
| `SERVICE VERSION DRIFT` | The loaded plugin and live tokenserver came from different source revisions | Repair all integrations from one durable checkout and start a new task |
| `SERVER UNAVAILABLE` / `LOCAL API DEGRADED` | The local service or its diagnostic contract is unavailable | Run setup doctor and tokenserver smoke |

The hook diagnoses; it never approves, flashes, refreshes credentials, or
silently rewrites service configuration. Firmware self-recovery remains
bounded and fail-closed, while host repair remains an explicit setup action.

## What you need

- **[Waveshare ESP32-S3-Touch-AMOLED-2.16](https://www.waveshare.com/esp32-s3-touch-amoled-2.16.htm)**
  (~$30). No soldering, just a USB-C cable. It's the same board Clawdmeter
  uses, so if you already own one you're 10 minutes away.
- **A Mac or Windows PC** running the tokenserver. Claude and Codex quota
  collection are supported on both. Direct LAN mode needs the panel to reach
  that computer; the optional relays remove the same-WiFi requirement. The
  exact support/evidence boundary is maintained in
  **[Host platform support](docs/platform-support.md)**; Windows release
  installation and recovery use the public
  **[Windows host runbook](docs/windows-setup.md)**, while release candidates
  use the reproducible
  **[Windows validation gate](docs/windows-validation.md)**. “Host supported”
  does not mean every later candidate has passed the physical Windows loop;
  the v1 runtime's latest sanitized checkpoint is a
  **[FULL PASS](docs/superpowers/reviews/2026-08-28-windows-v1-full-lifecycle.md)**,
  while future runtime revisions require a fresh run rather than inheriting
  that result.
- **Claude Code and/or Codex.** Either alone is fine.
- **2.4 GHz WiFi.** The ESP32-S3 can't see 5 GHz networks.

## Setup, the vibecoder way

Clone the repo, open your coding agent inside it (Claude Code, Codex,
Cursor, whatever you run), and say:

> Set up VibePulse for me: help me fill in secrets.h, build and flash the
> board over USB, and start the tokenserver on this Mac or Windows PC.

The repo is built for this. `CLAUDE.md` and `AGENTS.md` point your agent
straight at **[docs/agent-setup.md](docs/agent-setup.md)** — an English
runbook written for agents, with a verification after every step, the traps
that actually cost people an evening, and a symptom→fix table. That's the
whole onboarding.

Reading rather than running? That runbook is also the fastest way to
understand how the pieces fit together.

## Setup, the manual way

The commands below show the macOS path. Windows is supported for the host
service too; use the Windows ESP-IDF environment and the OS-specific
[Windows host runbook](docs/windows-setup.md) for the standalone Codex CLI,
host address, firewall, Task Scheduler, startup health, and recovery steps.

1. Install [ESP-IDF 5.5](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/index.html)
   and `brew install cmake ninja`
2. Clone this repo, then:

   ```
   cp secrets.h.example secrets.h   # fill in WiFi + your Mac's hostname (2 min)
   . ~/esp/esp-idf/export.sh
   idf.py set-target esp32s3
   idf.py build
   idf.py -p /dev/cu.usbmodem101 flash
   ```

   **Don't miss this:** in `secrets.h`, point the `TK_VIBEPULSE_BASE_URL`
   fallback at a reachable host by replacing the `DIN-MAC` placeholder. Those URLs ship
   active on purpose — a wrong hostname is visible in the log, whereas an
   undefined URL compiles the fetch out entirely and the screen boots fine
   and shows dashes forever. Use your Mac's Bonjour name
   (`scutil --get LocalHostName`) rather than an IP, so the same firmware
   works on your home network and on a phone hotspot. Current firmware also
   discovers `_vibepulse._tcp.local`, so several Mac/Windows tokenservers can
   be available without compiling their addresses into the panel.

   Board not showing up under `/dev/cu.usbmodem*`? Hold **BOOT**, tap
   **RESET**, release **BOOT** and it re-enumerates in download mode.

   **Power matters:** flash with the board in download mode (screen dark).
   A computer USB port often cannot feed the running firmware. The AMOLED
   panel's draw makes the board bounce off the bus or hang, which looks
   like a flaky cable. After flashing, run the screen from its own USB
   power supply, not your computer.
   The firmware disables Wi-Fi modem sleep because the panel is an
   always-powered live display; do not re-enable it without repeating a
   sustained stale-window and interaction test on physical hardware.
3. Start the core service on your computer. Its core remains pure Python
   stdlib. Install the small optional discovery dependency when the panel
   should find this Mac/PC automatically:

   ```
   python3 -m pip install -r requirements-discovery.txt
   python3 tools/tokenserver/tokenserver.py
   ```

   Without that package the configured URL path works exactly as before.

   On macOS, validate and install autostart from this durable checkout with
   `python3 tools/vibepulse_macos_service.py validate` followed by the explicit
   `install` command. It atomically rewrites and fully reloads the LaunchAgent,
   so launchd cannot keep running a deleted PR worktree. Full details:
   [tools/tokenserver/README.md](tools/tokenserver/README.md).

## Over-the-air updates

After the first USB flash, the screen updates itself over WiFi. The consent
chain is deliberate and three-factor: a **physical 3-second hold on KEY3**
opens a ten-minute maintenance window (the glass shows an UPDATES ON ring
with the lease draining clockwise), a **64-hex token** from `secrets.h`
authenticates the upload, and the window **closes itself** — a short KEY3
press closes it early. No button, no update; a script can never open the
window for you. (The same hold on a panel *without* a network opens the
WiFi setup window instead — the window that can actually help there. See
[Take it with you](#take-it-with-you).)

```
idf.py build
tools/ota-flash.sh <device-ip>     # waits for your KEY3 hold, then uploads
```

The device verifies the image (magic, chip, project, SHA-256), writes it to
the **inactive A/B slot** (`ota_0`/`ota_1`, 5 MB each — see
`partitions.csv`), reboots into it, and a **boot-health gate** must approve
the new image within 15 seconds — display, UI, scheduler, NVS and memory
proofs — or the bootloader rolls back to the previous slot automatically.
USB-C remains the rescue path and is never written by an OTA. After an OTA
reboot the window re-arms itself once, so a build-test-build session needs
one hold, not one per build.

The tokenserver announces the newest build on your computer
(`otaAvailableVersion` on `/api/tokens`); when the screen runs an older
version it takes the glass with an **UPDATE READY** notice — hold KEY3 to
receive — or answer the on-glass LATER/UPDATE pills by touch; tapping
UPDATE opens the window just like the hold does. A snooze returns every
hour until installed. Full lifecycle reference: [docs/ota.md](docs/ota.md).

**If someone tells you "this project has no OTA":** they are reading a tree
where `partitions.csv` still has a single `factory` partition. The OTA
foundation replaced that table (A/B slots + `otadata`) — check the branch
you are on before concluding anything, and never assume the flash layout
without reading `partitions.csv` in the checkout you are actually building.

## Take it with you

The panel remembers up to six places. Arrive somewhere new and it needs the
network once; every visit after that it joins by itself.

<p align="center">
  <img src="docs/img/vibepulse-wifi-searching.png" width="31%" alt="VibePulse explains that the saved phone hotspot is not visible and reminds the user that the panel needs 2.4 GHz Wi-Fi">
  &nbsp;
  <img src="docs/img/vibepulse-wifi-setup.png" width="31%" alt="VibePulse Wi-Fi setup screen with a large phone-scannable QR code and one Manual Setup control">
  &nbsp;
  <img src="docs/img/vibepulse-wifi-signal.png" width="31%" alt="The shared launcher with a neutral three-bar Wi-Fi signal icon at the top right">
</p>
<p align="center"><em>Real 480×480 frames from the shared LVGL firmware renderer: recovery, phone-first QR setup, and the global signal indicator.</em></p>

The normal setup path needs only the panel and a phone:

1. **Scan the QR** on the panel. It joins your phone to the temporary
   `VibePulse-setup` network; it does not contain your destination Wi-Fi
   password. The normal screen keeps the QR dominant; tap **Manual Setup**
   only if you need to see the temporary name, password, and local address.
2. The local setup page should open. If it does not, open
   `http://192.168.4.1/` yourself. A browser label such as **Not Secure** is
   expected here: this is a short-lived, device-local page with no internet
   route, not a public website.
3. Pick a **2.4 GHz** network and tap **Join** once. For a secured network,
   the password field names the selected network and is required. For an open
   network the password field disappears because no password is needed. The
   ESP32-S3 cannot see 5 GHz-only networks. On an iPhone hotspot, enable
   *Maximize Compatibility*.
4. Keep the phone nearby while the glass says JOINING. The new credentials
   are remembered **only after the panel connects** successfully. If the
   password is wrong or the network disappears, old saved networks remain
   available and the panel tells you what to retry.

On a Mac there is also an optional one-command shortcut:

```
tools/wifi-here.sh
```

It reads the network your Mac is already on, takes that password out of your
keychain (macOS asks you — that prompt is the consent), hands it to the panel
over its temporary access point, and gives the Mac's Wi-Fi back. The phone
flow remains the universal path and needs no computer or command line.

The small neutral Wi-Fi symbol is global: zero bars plus a slash means the
panel is disconnected; one to three bars describe only its connection to the
local access point. It **does not mean internet** access, tokenserver reachability,
or relay health. During setup the complete symbol means setup mode, not a
successful destination join.

The setup window opens on its own after 90 seconds without a network, or
immediately on a 3-second KEY3 hold. Before that, at 60 seconds, the glass
stops being coy: it names the network it is hunting and what the radio
actually answered ("NOT SEEN - 2.4 GHZ ONLY", "WRONG PASSWORD") instead of
showing dashes and letting you guess.

On a panel that already *has* a network, hold twice: the first 3-second
hold opens the update window, a second full hold switches it to WIFI
SETUP. That is how you pre-load the phone hotspot at home before a trip —
no need to wait until the panel is stranded somewhere.

Two things stay true by design. The networks in `secrets.h` remain an
**immutable floor** — setup can add places, never remove your home network,
so a bad entry can never cost you a USB rescue. And the setup window
**cannot write firmware**: it touches the network list and nothing else,
while OTA keeps its own token and its own gate.

Honest limits: captive portals (the panel cannot click "I agree"), guest
networks with client isolation, and WPA2-Enterprise are all still out of
reach. The network that always works on the road is the one you bring —
your phone's hotspot, with *Maximize Compatibility* on. Teach the panel
that one once and it follows you everywhere. Full reference:
[docs/wifi.md](docs/wifi.md).

And for the networks that *do* connect but wall the panel off from your
machine (client isolation, IoT VLANs): the optional **relay** puts the
numbers in a tiny mailbox on the internet — a ~150-line Cloudflare Worker
on your own account — and the panel falls back to it whenever the LAN
does not answer. Quota, burn rate, Max Tracker and the GitHub pulse
follow you anywhere with WiFi. Agent activity stays local unless you separately
opt in to one or both encrypted activity features. The **Interaction relay**
carries only bounded Needs You views and verdicts. The independent **Live
agent status relay** carries the minimized Claude/Codex rows the panel already
renders. Both use fixed-size, end-to-end encrypted ciphertext; Cloudflare
never receives question, command, project basename, activity, or verdict
content in plaintext. Cloudflare can still see connection IPs, timing and a
random mailbox identifier. The computer must be awake and tokenserver must be
running, but it may use a different ordinary internet connection from the
panel. Several machines can feed the numbers mailbox
(a Mac that sleeps, an always-on PC) and the freshest source wins per
number. Numbers setup: [docs/relay.md](docs/relay.md). Encrypted decisions and
live status: [docs/interaction-relay.md](docs/interaction-relay.md).

## No hardware? Run the simulator

```
brew install sdl2 cmake ninja
cmake -S sim -B sim/build -G Ninja && ninja -C sim/build
./sim/build/torget-sim
```

(On Debian/Ubuntu: `apt-get install libsdl2-dev cmake ninja-build` instead.)

Same code, same fonts, same pixels as the device — it builds the real
platform and VibePulse against the real LVGL, and feeds it the recorded
fixtures in `sim-fixtures/` through the same parsers the board runs. Every
device screenshot in this README is an unmodified simulator frame (the
banner just places three of them side by side), and the physical panel was
reviewed against them ([review](docs/superpowers/reviews/2026-08-13-max-tracker-physical-static.md)).

Keys: `[` / `]` change VibePulse page, `S` cycles agent status, `M` cycles
Max Tracker fixtures, `T` re-feeds tokens, `G` simulates a new GitHub star,
`L` opens the launcher.

## Privacy

- In local mode, agent activity and usage stay on your LAN; the screen only
  ever receives percentages, counts and coarse status — a project name, a
  model, an effort level.
- No prompts, no code, no commands, no file contents are stored or served.
  The service keeps only content-free quota points (at most one per 15
  minutes, kept 8 days) for the trends.
- Your OAuth token never leaves the computer.
- The optional numbers relay publishes only quota, reset, Max Tracker, and
  optional public GitHub numbers. The separate interaction and live-status
  relays send fixed-size end-to-end encrypted ciphertext; all three are off
  by default and independently controlled.
- If the optional GitHub module is enabled, the computer anonymously reads
  only public repository and stargazer metadata from GitHub. In local mode,
  the ESP32 still talks only to that computer over your LAN.
- A lost or stolen screen leaks your WiFi credentials and the LAN hostname
  or address of your computer — both of which you rotate yourself, not in any
  cloud.

## Tweak it

<table>
<tr>
<td width="30%"><img src="docs/img/launcher.png" alt="The Torget launcher showing VibePulse" width="100%"></td>
<td valign="top">

VibePulse is an app on **Torget**, a deliberately small LVGL 9 app platform
for this panel. An app is one component exporting
`torget_app_t { name, icon, create, enter, leave }`; the platform owns WiFi,
the panel, brightness and the launcher.

This repo ships exactly one app, so that's all you get on the screen — one
binary, one thing, nothing to wonder about. The platform can hold several
apps at once (that's what the launcher is for), but any others live in their
own repos and are only built in if you check them out.

</td>
</tr>
</table>

Design rules: true black background, IBM Plex, dashes instead of invented
zeros, and provider accents locked to Claude `#D97757` and Codex `#6F78FF`.

```
platform/            app contract + launcher + fonts (IBM Plex)
main/                ESP32 host layer: boot, WiFi, SNTP, app registry
components/app_*     the app (VibePulse lives in app_tokens/)
tools/tokenserver/   the computer service (core is Python stdlib)
sim/                 SDL simulator, the whole platform on your computer
test/                host tests, run with ./test/run.sh (no ESP-IDF needed)
spec/                hardware truth + UI design system
```

The deeper docs (architecture, writing an app, hardware traps) are in
[README.sv.md](README.sv.md), in Swedish, because this started as a Swedish
hobby project. Your agent reads Swedish just fine.

## Hardware knowledge

Hardware truth — capabilities, sources and which claims are verified on a
real unit — lives in the validated registries under `spec/`. Read
`spec/hardware.md` before any hardware-dependent work, and don't promote a
capability to "verified" without a physical check.

`./test/run.sh` is the host gate that enforces those registries, alongside
the C core tests and the Python suites. No ESP-IDF required, but it does
need a reproducible Python:

```sh
python3.12 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt
./test/run.sh
```

Python 3.11+ is required. The script uses the activated environment's Python
by default; set `PYTHON_BIN` to point at a different 3.11+ interpreter.

## FAQ

- **Windows for the tokenserver?** Yes. Claude Code has no
  keychain there, so `claude login` writes the same
  `{"claudeAiOauth": {...}}` record to
  `%USERPROFILE%\.claude\.credentials.json` and the service reads it; the
  Codex app-server read and the single-probe lock no longer depend on
  macOS-only syscalls; state lives under `%LOCALAPPDATA%\VibePulse\`.
  For autostart, run the shipped Task Scheduler installer from the repo root:
  `powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1`.
  It runs as your signed-in user, starts immediately, restarts on failure, and
  keeps interaction-provider choices in the tokenserver's saved config.
  The Codex desktop app alone is not enough for the background quota read:
  its Store-managed `codex` alias can resolve but still be denied by Windows.
  Install OpenAI's standalone CLI once in PowerShell:
  `powershell -ExecutionPolicy ByPass -c "irm https://chatgpt.com/codex/install.ps1 | iex"`.
  VibePulse prefers its stable per-user executable under
  `%LOCALAPPDATA%\Programs\OpenAI\Codex\bin` and ignores `WindowsApps`
  aliases. Open a new PowerShell window, run `codex --version`, then rerun
  `python tools\vibepulse_setup.py doctor`. The background task writes bounded
  stdout/stderr to
  `%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log`; the server's
  rotation guard keeps one `.old` tail. Use `-ValidateOnly` to verify the
  checkout and Python 3.11+ interpreter without changing Task Scheduler. The
  complete install, hook-review, firewall, startup-health, physical-test, and
  recovery procedure is the [Windows host runbook](docs/windows-setup.md).
  The v1.0 core service, physical answer loop, sign-out/sign-in,
  sleep/resume, and reboot all passed on a real Windows PC; see the
  [full sanitized evidence](docs/superpowers/reviews/2026-08-28-windows-v1-full-lifecycle.md).
  A later runtime revision still requires a fresh pass through the complete
  [Windows validation gate](docs/windows-validation.md); release evidence is
  never inherited across untested code.
- **Linux for the tokenserver?** Not yet —
  [#2](https://github.com/niclasvestlund-YT/vibepulse/issues/2). The Ubuntu
  tokenserver CI lane is portability evidence, not a support claim: current
  `main` still needs XDG paths, Linux credential selection, systemd user
  service lifecycle, and a real-host + panel validation report. See
  [Host platform support](docs/platform-support.md).
- **Other boards or panel sizes?** Not yet. The platform is pinned to this
  exact panel so one pixel-perfect build stays pixel-perfect, but a port is
  a contained job (BSP, layout constants, fonts) —
  [#5](https://github.com/niclasvestlund-YT/vibepulse/issues/5).
- **Cursor, Gemini CLI, other providers?** Not yet —
  [#4](https://github.com/niclasvestlund-YT/vibepulse/issues/4).
- **Just Claude, no Codex (or vice versa)?** Works. The other half shows
  dashes.
- **Does it need internet?** Local-only mode does not. The optional relays and
  GitHub pulse do; each stays off until you enable it.

## License

MIT © Niclas Vestlund

The "Claude" and "Codex" names and icons belong to Anthropic and OpenAI.
They appear here only to identify which provider a number belongs to, they
are not covered by the MIT license, and they will be removed on request.
The IBM Plex fonts are used under the SIL Open Font License
([platform/fonts/LICENSE-OFL.txt](platform/fonts/LICENSE-OFL.txt)).

This is my first open source release. Issues and PRs are very welcome, and
if VibePulse ends up on your shelf, a ⭐ helps others find it.

Built by [Niclas Vestlund](https://niclasvestlund.se).
