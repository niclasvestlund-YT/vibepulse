# Setting up VibePulse — a runbook for coding agents

You are most likely here because someone handed you this repo and said
"set this up for me". This file is the procedure: do the steps in order and
verify each one before moving on. Everything here is English; much of the
deeper documentation is Swedish, which is fine to read as-is.

Work through **Preflight** first — half of all setup failures are decided
there.

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

1. **macOS?** The tokenserver reads macOS log paths and the keychain. On
   Linux/Windows the firmware still builds and the simulator still runs, but
   the data service will not work — see issues
   [#2](https://github.com/niclasvestlund-YT/vibepulse/issues/2) /
   [#3](https://github.com/niclasvestlund-YT/vibepulse/issues/3).
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

- `TG_WIFI_SSID` / `TG_WIFI_PASS` — their 2.4 GHz network.
- **Replace `DIN-MAC` in `TK_VIBEPULSE_BASE_URL`** with their Mac's Bonjour
  name.

Those `#define`s ship active on purpose, with an obvious placeholder. Do not
comment them out or delete them: `components/app_tokens/net.c` guards every
fetch behind `#ifdef TK_TOKENS_URL`, so an undefined URL compiles the fetch
out entirely and the firmware then compiles cleanly, boots cleanly, connects
to WiFi cleanly — and shows dashes forever, with no error anywhere to tell
you why. A wrong hostname at least shows up in the serial log.

Use the Bonjour name, not an IP, so the same binary works at home and on a
phone hotspot:

```sh
scutil --get LocalHostName     # e.g. "Niclas-MacBook" -> Niclas-MacBook.local
```

**Verify:** `secrets.h` has a non-empty SSID, and

```sh
grep -q 'DIN-MAC' secrets.h && echo "PLACEHOLDER STILL THERE" || echo "hostname set"
```

prints `hostname set`. If it still says the placeholder is there, the board
will look for a host that does not exist and every page will stay on dashes.

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

Find the port with `ls /dev/cu.usbmodem*`. Two hardware facts decide whether
this works, both learned the hard way:

- **Flash in download mode, with the panel dark.** Hold **BOOT**, tap
  **RESET**, release **BOOT**. The board re-enumerates as a ROM device.
- **A computer USB port often cannot power the running firmware.** The AMOLED
  panel's current draw makes the board bounce off the bus or hang. This looks
  exactly like a bad cable and is not one. After flashing, run the screen from
  its own USB power supply.

**Verify:** the flash log says `Hash of data verified`, then the panel lights
up after reset.

## Step 4 — tokenserver

Pure stdlib, nothing to install:

```sh
python3 tools/tokenserver/tokenserver.py
```

**Verify**, from the same Mac:

```sh
curl -s localhost:8737/ | python3 -m json.tool
```

Read `claudeProbe` in that output — it is the single most useful diagnostic
in the whole system, and it tells you exactly why Claude's numbers are or
are not arriving:

| `claudeProbe` | Meaning | What to do |
|---|---|---|
| `usage_http_200 + ok` | Working. Limits parsed. | Nothing |
| `not_run` | Probe has not fired yet | It runs every 120 s — wait |
| `no_claude_oauth_token` | No Claude Desktop / Claude Code token found | Have them sign in to Claude Code on this Mac |
| `token_expired_…` | Token found but expired | Re-authenticate in Claude Code |
| `usage_http_401` / `usage_http_403` | Every token source rejected (the probe tries Claude Desktop's process token, then the keychain, and falls back automatically) | Re-authenticate in Claude Code |
| `usage_http_200 + no_mapped_limits` | Authenticated, but nothing in the usage response mapped (a `; fallback_…` suffix records the header-probe outcome) | Plan may not expose limits; Codex half still works |
| `usage_request_failed: …` | Network/DNS failure from the Mac | Check the Mac's own connectivity |
| `usage_http_429 + backoff_until_HH:MM` | Rate-limited by the API; the probe rests until the shown time | Wait — it retries by itself |

Codex is read separately from its local app-server, so a bad `claudeProbe`
never explains missing Codex numbers, and vice versa.

Then check the endpoints the screen polls:

```sh
curl -s localhost:8737/api/tokens
curl -s localhost:8737/api/agent-status
curl -s localhost:8737/api/max-tracker
```

Optional plan badges: `--claude-plan {pro,max5x,max20x}`, `--codex-plan
{plus,pro}`. Cosmetic labels only, never used in any percentage maths.

For autostart on login, see [../tools/tokenserver/README.md](../tools/tokenserver/README.md).

## Step 5 — end-to-end

The screen polls every 30 seconds, so wait that long before judging. Then
confirm with the user that real numbers replaced the dashes.

## When it does not work

| Symptom | Cause | Fix |
|---|---|---|
| Screen boots, everything is dashes, forever | `DIN-MAC` never replaced in `secrets.h`, or the `TK_*` defines were removed | Set the real Bonjour name, rebuild, reflash |
| Dashes, and the Mac's URL is set | tokenserver not running, or Mac asleep, or firewall | Start it; check `curl localhost:8737/` |
| Dashes only for Claude, Codex fine (or vice versa) | That provider's source is unavailable | Check `claudeProbe`; the other half working is by design |
| Never joins WiFi | Network is 5 GHz | 2.4 GHz only. iPhone hotspot: enable "Maximize Compatibility" |
| "This project has no OTA" / partitions.csv shows one factory partition | Reading a tree from before the OTA foundation (A/B slots + otadata + `components/torget_ota/`) | Check which branch/commit the checkout is on; read `partitions.csv` in THAT tree before concluding. OTA workflow: `tools/ota-flash.sh <ip>` + a 3 s KEY3 hold |
| Panel shows stale quota / empty Fable weekly in the morning | Upstream 429 penalty from the shared account bucket | Self-heals: dead tokens are never resent, the penalty persists across restarts, deltas serve from cache. Check `claudeProbe` on `curl localhost:8737/` |
| Panel shows stale while powered from the computer USB port | The Mac port cannot feed WiFi TX bursts — fetches time out | Expected on Mac USB; run from wall power. Logs stay valid on Mac USB, data does not |
| OTA boots always show state 0xffffffff and the health gate always rests | `sdkconfig` generated before the rollback line landed in `sdkconfig.defaults` (defaults only apply on fresh generation) | `grep BOOTLOADER_APP_ROLLBACK sdkconfig` — set `=y`, rebuild, and USB-flash ONCE (the bootloader carries the logic; OTA never writes it) |
| No `/dev/cu.usbmodem*` | Not in download mode | Hold BOOT, tap RESET, release BOOT |
| Flash starts then dies; board hangs | USB port cannot power the panel | Download mode to flash; own PSU to run |
| Numbers freeze and go stale | Service or LAN dropped | Last good values are kept deliberately; restart the service |
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
