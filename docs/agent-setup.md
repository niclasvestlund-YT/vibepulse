# Setting up VibePulse — a runbook for coding agents

You are most likely here because someone handed you this repo and said
"set this up for me". This file is the procedure: do the steps in order and
verify each one before moving on. Everything here is English; much of the
deeper documentation is Swedish, which is fine to read as-is.

Work through **Preflight** first — half of all setup failures are decided
there.

For a Windows host, keep
[Windows host setup and recovery](windows-setup.md) open beside this hardware
runbook. It covers the standalone Codex CLI, Task Scheduler, Private firewall,
startup health, lifecycle proof, and the exact public evidence boundary.

## What is not part of this repo

`AGENTS.md`, `README.sv.md` and some code comments refer to things the
maintainer has locally and you almost certainly do not have. None of them
are required, and the build gates on their absence:

| Reference | What to do |
|---|---|
| `~/Solelkollen/components`, `~/Buddy/components` | Ignore. These are separate products in their own repos. Absent → the build prints `saknas` and builds VibePulse only. Expected, and what you want. |
| The `Solceller` repo, `docs/roadmap-hyllskarmen.md`, "P-numbers" | Ignore. Project history you cannot open. |
| `spec/*.yaml` hardware registries | Read, never edit. They are validated evidence with their own tests. |

## Rules you must not break

- **Never flash the board without the user explicitly telling you to.** A
  build is free; writing firmware to their hardware is not. Ask, then flash.
- **Never invent numbers.** Missing data renders as dashes, everywhere. If
  you touch UI code, keep that property.
- **Don't promote anything to "physically verified"** in `spec/` — that
  requires the user looking at the actual panel.
- For any UI/visual change, read `.claude/skills/iterating-esp32-amoled-ui/SKILL.md`
  first. Setup work (this file) does not need it.

## Preflight

Confirm all five before touching anything. Ask the user for anything you
cannot determine yourself.

1. **macOS or Windows?** Both serve data. On Windows the Claude token comes
   from `%USERPROFILE%\.claude\.credentials.json` instead of the keychain,
   and state lives under `%LOCALAPPDATA%\VibePulse\`. The supplied
   `install-windows-task.ps1` adds autostart through Task Scheduler; its
   bounded diagnostic log lives at
   `%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log`. On Linux
   the firmware still builds and the simulator still runs, but the service
   finds no Claude token at all — there is no keychain and the credential
   file is read only on Windows
   ([#2](https://github.com/niclasvestlund-YT/vibepulse/issues/2)).
2. **Do they have the board?** Waveshare ESP32-S3-Touch-AMOLED-2.16. No
   board → skip to [Simulator only](#simulator-only-no-board).
3. **Is their WiFi 2.4 GHz?** The ESP32-S3 cannot see 5 GHz at all. This is
   the single most common "it won't connect" cause. Ask; don't assume.
4. **ESP-IDF 5.5 installed?** `idf.py --version`. If missing, point them at
   the [install guide](https://docs.espressif.com/projects/esp-idf/en/stable/esp32s3/get-started/index.html);
   it is a large download, so let them start it before you continue.
5. **Python 3.11+?** `python3 --version`. Needed for the tokenserver.

## Step 1 — secrets.h

```sh
cp secrets.h.example secrets.h
```

Then edit `secrets.h`. Two separate things must be right:

- `TG_WIFI_SSID` / `TG_WIFI_PASS` — their 2.4 GHz network. This one still
  belongs here: it is the **immutable floor** the panel falls back to, and
  the only network it knows before it has ever been anywhere. Every network
  *after* the first is taught to the panel at the place itself, with no
  rebuild — see [wifi.md](wifi.md).
- **Replace `DIN-MAC` in `TK_VIBEPULSE_BASE_URL`** with a reachable fallback.
  On macOS, use the Mac's Bonjour name. On Windows, use an active LAN IPv4
  address and reserve it in the router. Current firmware first discovers
  `_vibepulse._tcp.local`; the compiled URL remains the fail-closed path when
  multicast or the optional host advertiser is unavailable.

Those `#define`s ship active on purpose, with an obvious placeholder. Do not
comment them out or delete them: `components/app_tokens/net.c` guards every
fetch behind `#ifdef TK_TOKENS_URL`, so an undefined URL compiles the fetch
out entirely and the firmware then compiles cleanly, boots cleanly, connects
to WiFi cleanly — and shows dashes forever, with no error anywhere to tell
you why. A wrong hostname at least shows up in the serial log.

On macOS, use the Bonjour name instead of a raw IP so the same binary works at
home and on a phone hotspot:

```sh
scutil --get LocalHostName     # e.g. "Niclas-MacBook" -> Niclas-MacBook.local
```

Install the optional discovery advertiser in the tokenserver's exact Python
environment on every Mac/PC that may serve the panel:

```sh
python3 -m pip install -r requirements-discovery.txt
```

Without it, the tokenserver remains stdlib-only and the compiled URL behaves
exactly as before. With it, several computers may advertise simultaneously;
the panel pins one healthy origin and changes only after failure. On Windows,
`ipconfig` plus a DHCP reservation still makes the compiled fallback durable.

**Verify:** `secrets.h` has a non-empty SSID and no `DIN-MAC` placeholder:

```sh
grep -q 'DIN-MAC' secrets.h && echo "PLACEHOLDER STILL THERE" || echo "host set"
```

It must print `host set`. If the placeholder is still there,
the board's fallback will name a host that does not exist. Discovery may still
find an advertising service, but a release must not depend on hiding a broken
fallback. On macOS, replace a raw IP with the Bonjour name. On Windows,
document the DHCP reservation that keeps the fallback IPv4 stable. A stale
compiled address cost an entire evening of network debugging before anyone read the URL
(`docs/lessons.md` 2026-08-17).

Ask the user for the WiFi password. Do not guess it, and do not commit
`secrets.h` — it is gitignored, keep it that way.

## Step 2 — build

```sh
. ~/esp/esp-idf/export.sh      # their install path may differ
idf.py set-target esp32s3
idf.py build
```

**Verify:** the build ends with a `torget.bin` size/partition summary and no
error (the CMake project is `torget`, the platform; VibePulse is an app
inside it). Status lines saying `Solelkollen saknas` and `~/Buddy saknas`
are expected on a fresh clone — that is the build telling you it found none
of the maintainer's companion apps, which is exactly what you want.

## Step 3 — flash (only with the user's go-ahead)

Ask first. Then:

```sh
idf.py -p /dev/cu.usbmodem101 flash    # confirm the real port first
```

On macOS, find the port with `ls /dev/cu.usbmodem*`. On Windows, use the
board's `COM` port instead, for example `idf.py -p COM5 flash`, after
confirming it in Device Manager or the ESP-IDF terminal. Two hardware facts
decide whether this works, both learned the hard way:

- **Flash in download mode, with the panel dark.** Hold **BOOT**, tap
  **RESET**, release **BOOT**. The board re-enumerates as a ROM device.
- **A computer USB port often cannot power the running firmware.** The AMOLED
  panel's current draw makes the board bounce off the bus or hang. This looks
  exactly like a bad cable and is not one. After flashing, run the screen from
  its own USB power supply.

**Verify:** the flash log says `Hash of data verified`, then the panel lights
up after reset.

## Step 4 — tokenserver

The core local service is pure stdlib, with nothing to install. The optional
encrypted interaction/status relay adds the pinned dependency in
`requirements-interaction-relay.txt`; install it only when enabling that
relay.

```sh
python3 tools/tokenserver/tokenserver.py
```

The computer must be on for the panel to receive fresh data. On the same LAN,
the panel talks directly to it. On WiFi with client isolation, the optional
numbers relay can still carry quota data as long as this service is running;
it does not publish agent activity or Needs You prompts.

**Verify**, from the same computer:

```sh
curl -s localhost:8737/ | python3 -m json.tool
```

Read `claudeProbe` and `claudeLocalUsage` in that output. The first explains
the authenticated source; the second explains whether Claude Desktops
content-free local plan history safely keeps the general week fresh while a
readable OAuth copy is expired:

| `claudeProbe` | Meaning | What to do |
|---|---|---|
| `usage_http_200 + ok` | Working. Limits parsed. | Nothing |
| `not_run` | Probe has not fired yet | It runs every 120 s — wait |
| `no_claude_oauth_token` | No Claude Desktop / Claude Code token found | Have them sign in to Claude Code on this computer |
| `token_expired_…` | Token found but expired; Claude may still say logged in because login state and the exported usage credential are different | The service rechecks locally every 15 s. `claudeLocalUsage: fresh_applied` can keep the general week live; for Fable, start a **new Claude Code CLI turn** and send one short message so Claude's supported client refreshes Keychain |
| `usage_http_401` / `usage_http_403` | Every token source rejected (on macOS the probe tries Claude Desktop's process token, then the keychain, and falls back automatically; on Windows there is only `%USERPROFILE%\.claude\.credentials.json`) | Re-authenticate in Claude Code |
| `usage_http_200 + no_mapped_limits` | Authenticated, but nothing in the usage response mapped (a `; fallback_…` suffix records the header-probe outcome) | Plan may not expose limits; Codex half still works |
| `usage_request_failed: …` | Network/DNS failure from the computer | Check the computer's own connectivity |
| `usage_http_429 + backoff_until_HH:MM` | Rate-limited by the API; the probe rests until the shown time | Wait — it retries by itself |
| `probe_crashed: <Type>` | The probe itself hit a bug (crash before it could classify the failure) | Read `~/Library/Logs/torget-tokenserver.log` on macOS or `%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log` on Windows; worth filing |

Codex is read separately from its local app-server, so a bad `claudeProbe`
never explains missing Codex numbers, and vice versa.

Also read `claudeCredential` on the same `GET /` response. It contains only a
safe status and whole minutes remaining—never either OAuth token. `expiring`
starts 30 minutes before failure; `expired` is actionable even when
`claude auth status` still says logged in. `python3 tools/vibepulse_setup.py
doctor`, Codex `SessionStart`, and `python3 tools/tokenserver/smoke.py` all
consume this guard. Do not confuse it with the active source outcome: if
`claudeProbe` is `usage_http_200 + ok` and the relevant `/api/tokens` stale
flag is false, current quota is live while the expired saved credential is a
future recovery risk. Start a new Claude Code CLI turn to refresh that saved
fallback. The service notices locally within 15 seconds; it does not need a
restart, call an undocumented refresh endpoint, or mutate the refresh token.

Plugin `0.1.7` also performs a read-only startup classification from fixed
loopback endpoints. `PROVIDER DATA STALE` means investigate Claude/Codex;
`DEVICE PATH STALE` means provider data is already fresh and the next checks
are panel power, network, discovery, and firmware; `SERVICE VERSION DRIFT`
means the installed plugin and live tokenserver are from different checkout
generations. `SERVER UNAVAILABLE` means the local service did not answer the
sub-second probe. The hook never restarts a service, refreshes a credential,
or treats its own timeout as approval. Run doctor plus smoke for the detailed
evidence, then start a new Codex task after an explicit repair.

Then check the endpoints the screen polls:

```sh
curl -s localhost:8737/api/tokens
curl -s localhost:8737/api/agent-status
curl -s localhost:8737/api/max-tracker
```

Optional plan badges: `--claude-plan {pro,max5x,max20x}`, `--codex-plan
{plus,pro}`. Cosmetic labels only, never used in any percentage maths.

For autostart on login, see [../tools/tokenserver/README.md](../tools/tokenserver/README.md).
Treat the launchd plist, Codex plugin, MCP registration, and marketplace as four
separate absolute-path integrations: setup being enabled is not enough. They
must resolve to one clean, durable checkout. On macOS, use
`python3 tools/vibepulse_macos_service.py validate` and then the explicit
`install` command instead of copying a hardcoded plist between worktrees. The
installer reloads with `bootout` + `bootstrap`; then run doctor and smoke
against the configured port and start a new Codex task. The smoke result must identify the expected live
revision and source fingerprint; old log warnings are not new failures unless
their timestamps are after the reload.
Windows installers should follow the complete
[Windows host runbook](windows-setup.md), not copy a Task Scheduler command in
isolation.

## Step 5 — end-to-end

The screen polls every 30 seconds, so wait that long before judging. Then
confirm with the user that real numbers replaced the dashes.

## Needs You — answer Claude or Codex from the panel (optional)

This turns the panel from a monitor into an input device. It is all off by
default. Installing the Codex plugin does not enable Codex interactions, and
enabling one provider does not enable the other, the numbers relay, the
encrypted interaction relay, or GitHub.

The computer must be awake and running the tokenserver. Direct answers use the
LAN. The separate encrypted interaction relay can work across unrelated
internet Wi-Fi using outbound HTTPS only; it remains default-off and is not
enabled by these steps or by installing the Codex plugin.

### 1. Pair the panel

One shared secret authenticates answers. Generate one value and put the same
value on the panel and computer; never paste the real value into an issue or
commit it:

```sh
python3 -c "import secrets; print(secrets.token_hex(32))"
```

- In the gitignored `secrets.h`, set
  `#define TK_VIBEPULSE_DEVICE_KEY "…64 hex…"`, then rebuild and flash only
  after the user approves Step 3. Without it, the sender is display-only.
- On the computer, write the value to `~/.vibepulse-device-key` and run
  `chmod 600 ~/.vibepulse-device-key`, or export `VIBEPULSE_DEVICE_KEY`.

### 2. Choose providers explicitly

Run the guided setup from the repo root:

```sh
python3 tools/vibepulse_setup.py install
```

Choose `off`, `claude`, `codex`, or `both`. Then choose whether bounded
question/command detail may reach the local panel; the safe default is no.
This saves the choices for the tokenserver and installs the optional Codex
adapter. The installer also gives the VibePulse MCP tool a bounded 130-second
deadline so Codex does not cancel the panel's 120-second answer window at its
shorter default. It does not start a cloud relay. A non-interactive install
with no provider choice leaves both providers off.

Useful lifecycle commands:

```sh
python3 tools/vibepulse_setup.py status
python3 tools/vibepulse_setup.py doctor
python3 tools/vibepulse_setup.py disable codex
python3 tools/vibepulse_setup.py uninstall codex
```

`disable codex` turns off only Codex. `uninstall codex` removes the VibePulse
Codex plugin/MCP and disables Codex; it preserves Claude, relay, GitHub,
device-key, and unrelated Codex settings. It does not delete the repository or
the shared device key.

To opt in to encrypted decisions across isolated Wi-Fi, first read the exact
privacy boundary in [interaction-relay.md](interaction-relay.md). Enable at
least one provider and detail above, install the pinned Python and Worker
dependencies, then run:

```sh
python3 -m pip install -r requirements-interaction-relay.txt
cd tools/interaction-relay && npm ci && npx wrangler login && cd ../..
python3 tools/vibepulse_setup.py relay install \
  --url https://vibepulse-interaction-relay.YOUR-SUBDOMAIN.workers.dev \
  --yes-e2e-cloud
python3 tools/vibepulse_setup.py relay status
python3 tools/vibepulse_setup.py relay doctor
```

The installer generates the mailbox and role credentials; it does not print
them or flash the board. Enable `TK_VIBEPULSE_INTERACTION_RELAY` separately in
`idf.py menuconfig`, rebuild, and ask before flashing. Restart a running
tokenserver after changing saved choices.

Disable traffic without deleting credentials, or remove only this relay:

```sh
python3 tools/vibepulse_setup.py relay disable
python3 tools/vibepulse_setup.py relay uninstall --keep-worker
python3 tools/vibepulse_setup.py relay uninstall --delete-worker
```

These commands preserve Claude/Codex provider choices, the Codex package,
GitHub, numbers relay, repository, and shared device key. Captive portals,
offline networks, and blocked Worker domains still fall back to the computer.

The service command remains plain:

```sh
python3 tools/tokenserver/tokenserver.py
```

It loads the saved choices. Do not put provider or detail switches into a
launchd/Task Scheduler command, where they can go stale. `--interactions` is a
legacy alias for Claude only. Current installations should use the setup tool.

### 3. Review hooks instead of bypassing trust

For Codex, open Codex and run `/hooks`. Review the VibePulse `SessionStart` and
`PermissionRequest` command hooks and explicitly trust them. Then **Start a new
Codex task** so the newly trusted hooks, skill, and MCP tool are loaded. Run
`python3 tools/vibepulse_setup.py doctor` again; doctor reports the review
state but never bypasses it.

Codex permissions use a narrow safe-command tier. Only recognized read-only,
test, and build commands can offer **ALLOW ONCE**. Unknown commands, mutations,
secrets, truncated text, free-form questions, and questions without exactly
one explicit recommendation use the computer fallback. Timeout and silence
also return to the computer; nothing is approved by silence.

For an old Claude-only panel, `--legacy-claude-panel-v1` is available solely
as an explicit compatibility switch. **legacy Claude v1 is insecure** because
its verdict is not bound to provider and exact rendered view; it is off by
default and must never be used for Codex.

### 4. Add Claude hooks only if Claude was selected

Point Claude Code's hooks at the bridge on loopback (Claude Code blocks HTTP
hooks that resolve to the LAN, which is why the bridge splits loopback-in and
LAN-out). In Claude Code settings:

   ```json
   {
     "hooks": {
       "PreToolUse": [{
         "matcher": "AskUserQuestion",
         "hooks": [{
           "type": "http",
           "url": "http://127.0.0.1:8737/api/hook/question",
           "timeout": 120,
           "statusMessage": "Waiting for VibePulse…"
         }]
       }],
       "PermissionRequest": [{
         "matcher": ".*",
         "hooks": [{
           "type": "http",
           "url": "http://127.0.0.1:8737/api/hook/permission",
           "timeout": 120,
           "statusMessage": "Waiting for VibePulse…"
         }]
       }]
     }
   }
   ```

Claude Code can emit both hooks for one `AskUserQuestion`: the dedicated
question hook contains the choices the panel should show, while the broad
permission hook may repeat the same internal tool. VibePulse keeps the
dedicated question and immediately returns no decision for that duplicate
permission. This prevents a second generic `AskUserQuestion` card from
replacing or queueing behind the real question; every unrelated permission
still follows the normal approval path.

Fail-safe by design: a held hook that times out or is left alone renders no
decision, so Claude Code falls back to its normal terminal prompt. This is the
same computer fallback as Codex. A managed/enterprise `allowedHttpHookUrls`
policy can silently block HTTP hooks — if the panel never reacts, check that
first.

On the glass: a tap opens the decision; APPROVE / DENY / LEAVE IT answer it; on
the private screen a tap hands it to the terminal. KEY3 held ~1.5–3 s and
released is the panic — deny everything parked; the 3 s hold still opens OTA.

### Post-flash physical Codex smoke test

After a firmware flash or a VibePulse/Codex setup change, send one exact short
question through `mcp__vibepulse__ask`:

- header `Test`
- question `Ser du APPROVE?`
- `Ja` — description `APPROVE syns` — recommended
- `Nej` — description `APPROVE saknas`

A pass requires all of the following: the panel opens the question, shows the
recommended `Ja` card plus **APPROVE** and **LEAVE IT**, accepts the physical
tap on **APPROVE**, and the waiting call returns `status: answered`,
`option_index: 0`, `answer: Ja`. The non-recommended option stays on the
computer by design. `DENY` is used for readable permission cards, not as the
second button for a recommended question.

Silence, timeout, **LEAVE IT**, panel absence, computer fallback, or a private
**SOMETHING IS WAITING** screen without answer buttons is not a pass and never
means approval. Before flashing, record `git describe --tags --always --dirty` from
the exact build checkout and compare it with `otaAvailableVersion`; preview,
test, build, and flash from that same checkout. The full verified evidence and
recovery sequence is in
[the 2026-08-27 physical review](superpowers/reviews/2026-08-27-vibepulse-codex-physical-end-to-end.md).

## When it does not work

After the first USB flash, day-to-day updates go over the air — the full
workflow, consent model and troubleshooting live in [ota.md](ota.md).

| Symptom | Cause | Fix |
|---|---|---|
| Screen boots, everything is dashes, forever | `DIN-MAC` never replaced in `secrets.h`, the Windows LAN IP changed, or the `TK_*` defines were removed | Set the reachable host (Bonjour on macOS; reserved LAN IPv4 on Windows), rebuild, reflash |
| Dashes, and the computer's URL is set | tokenserver not running, computer asleep, or firewall | Start it; check `curl localhost:8737/` |
| Dashes only for Claude, Codex fine (or vice versa) | That provider's source is unavailable | Check `claudeProbe`; the other half working is by design |
| Never joins WiFi | Network is 5 GHz | 2.4 GHz only. iPhone hotspot: enable "Maximize Compatibility". The glass names the reason itself after 60 s |
| Moved to a new place; panel finds nothing | The new network was never taught to it | It raises `VibePulse-setup` after 90 s (or a 3 s KEY3 hold). Run `tools/wifi-here.sh` on the Mac, or join the AP from a phone. Remembered afterwards — [docs/wifi.md](wifi.md) |
| `wifi-here.sh` cannot join the setup AP | The window is closed, or `TG_OTA_TOKEN` is missing so the password is random | Check the glass says WIFI SETUP; without a token run `TG_AP_PASS=<what the glass shows> tools/wifi-here.sh` |
| Panel joined the venue WiFi but still shows dashes | Client isolation, or a captive portal the panel cannot pass | Not fixable on the device. Use the phone hotspot instead |
| "This project has no OTA" / partitions.csv shows one factory partition | Reading a tree from before the OTA foundation (A/B slots + otadata + `components/torget_ota/`) | Check which branch/commit the checkout is on; read `partitions.csv` in THAT tree before concluding. OTA workflow: `tools/ota-flash.sh <ip>` + a 3 s KEY3 hold |
| **UPDATE READY** appears immediately after USB flash | The flashed image is older than the advertised OTA build, often because it came from another worktree | Compare the booted version, `git describe --tags --always --dirty`, and `otaAvailableVersion`; rebuild and flash from the intended checkout |
| A test question shows only **LEAVE IT** | The request has no single explicit recommendation | Use 2–3 short options and mark exactly one genuinely recommended option |
| **SOMETHING IS WAITING** appears with no answer buttons | The question failed the physical fit/privacy gate | Finish on the computer. For a diagnostic only, use the canonical short smoke test above |
| Letters become boxes in project/status text | The UI selected an uppercase-only font | Use `plex_ui_21` for mixed-case/localized text and verify with `RÄKSMÖRGÅS` |
| Panel shows stale quota / empty Fable weekly in the morning | The readable OAuth copy expired/rejected, or an upstream 429 penalty is active | Check `claudeProbe`, `claudeCredential`, `claudeLocalUsage`, and `/api/tokens` together. The named Fable/Opus pool requires a live OAuth source. Start a new Claude Code CLI turn, then allow the automatic 15-second local recheck; restart is not the first recovery step |
| Panel shows stale while powered from the computer USB port | The Mac port cannot feed WiFi TX bursts — fetches time out | Expected on Mac USB; run from wall power. Logs stay valid on Mac USB, data does not |
| Panel becomes stale again after initially working; host and relay are fresh and the panel still answers ping | Wi-Fi association survived while device-side application HTTP stopped | Current relay-configured firmware recycles Wi-Fi after 60 s, wakes the quota task, waits for a new IP before retrying, and restarts once after another 45 s without a real success. Cold boot stays disarmed to prevent outage loops. A recovered boot appears as `interactions.panel.httpStallRecoveryBoot: true` after confirmed polling, so wall-powered recovery remains observable without serial. Power-cycle older firmware, then update; do not call the incident fixed until recent direct polling and a second physical question pass beyond the stale window |
| Numbers stay fresh but questions from this computer never appear; several VibePulse computers share the LAN | LAN discovery can bind the panel to a different healthy tokenserver because DNS-SD result order is not host intent | Enable the end-to-end encrypted interaction relay on every participating host and in this panel's firmware. Direct LAN remains a fast path, but question delivery no longer depends on which Mac/PC answered discovery first |
| OTA boots always show state 0xffffffff and the health gate always rests | `sdkconfig` generated before the rollback line landed in `sdkconfig.defaults` (defaults only apply on fresh generation) | `grep BOOTLOADER_APP_ROLLBACK sdkconfig` — set `=y`, rebuild, and USB-flash ONCE (the bootloader carries the logic; OTA never writes it) |
| No `/dev/cu.usbmodem*` or Windows `COM` port | Not in download mode | Hold BOOT, tap RESET, release BOOT |
| Flash starts then dies; board hangs | USB port cannot power the panel | Download mode to flash; own PSU to run |
| Numbers freeze and go stale | Service, LAN, or the panel's application HTTP path dropped | Last good values are kept deliberately. Run `doctor`; compare source freshness, recent panel polling, and physical glass before restarting anything |
| `./test/run.sh` refuses to start | Unpinned PyYAML/Pillow | See [Hardware knowledge](../README.md#hardware-knowledge) |

## Simulator only (no board)

The whole platform runs on the host against the real LVGL, fed by the
recorded fixtures in `sim-fixtures/` through the same parsers the board uses:

```sh
brew install sdl2 cmake ninja                      # Debian/Ubuntu: apt-get install libsdl2-dev cmake ninja-build
cmake -S sim -B sim/build -G Ninja && ninja -C sim/build
./sim/build/torget-sim
```

Keys: `[` / `]` change page, `S` cycles agent status, `M` cycles Max Tracker
fixtures, `T` re-feeds tokens, `L` opens the launcher.

For a non-interactive check — useful in CI or over SSH — this writes the full
480×480 capture matrix and exits non-zero if any frame fails:

```sh
TORGET_CAPTURE_DIR=/tmp/caps ./sim/build/torget-sim --vibepulse-static-qa
```

## Changing things afterwards

Run the host gate before you hand anything back. It needs pinned versions,
so use the venv:

```sh
python3.12 -m venv .venv && . .venv/bin/activate
python -m pip install -r requirements-dev.txt
./test/run.sh
```

It runs the C core tests, the Python suites, and exact-raster landmark checks
that rebuild the simulator and compare real captures. No ESP-IDF needed.

Architecture, the app contract, and the full list of hardware traps are in
[../README.sv.md](../README.sv.md) (Swedish) and `spec/`.
