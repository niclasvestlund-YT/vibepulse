# Lovable Pulse

An optional VibePulse page for people who build with Lovable: credits left,
plan and how fresh the number is. It sits in the same carousel as
Claude and Codex and leaves those pages unchanged.

## Flow

```text
Chrome billing page ── local extension ── loopback POST ──┐
                                                       ├─ tokenserver ── LAN ── panel
Optional Lovable MCP (get_workspace, OAuth, read-only) ───┘
```

- **Official source (currently missing balance):** `https://mcp.lovable.dev`. Login uses
  OAuth 2.1 with PKCE, dynamic client registration and a loopback redirect;
  scopes `workspaces:read offline`. `list_workspaces` runs once at login to pick
  a workspace; after that only `get_workspace` is called.
- **Secrets:** tokens live in the macOS Keychain (service
  `se.torget.vibepulse.lovable`, account `oauth`). The panel never gets them,
  and the relay never publishes Lovable data.
- **Honesty:** only values the source actually returns are shown. When
  `get_workspace` names a total grant and a reset/expiry date, the Mac
  forwards `grantTenths`, `resetSeconds` and (with a period start)
  `periodSeconds`; square panels can show `OF 100 · RESETS IN 61 DAYS` and
  count down themselves between polls. Without those fields: balance only.
  A separately named daily balance appears as `5 DAILY LEFT`; a named daily
  allowance without a balance appears as `5 DAILY INCLUDED`. The value is
  never inferred from `PRO` or another plan name, so other plans work without
  hard-coded assumptions. Old values are marked `CACHED`; missing ones show a dash.

## Round 1.75 glass

The round board is deliberately simpler:

- **Large number:** credits left in the main period.
- **One orange bezel ring:** time left until reset, of the whole period. It is
  only shown when both reset and period values are explicitly available.
- **Compact daily row:** separately named daily credits left. It has no ring
  and stays hidden when the source does not provide a daily value.

The workspace name and reset sentence are omitted from the round face. The
plan sits below the Lovable identity, while freshness has its own quiet row.

The ring drains clockwise from twelve and dims for `CACHED`. A full ring keeps
a tiny gap rather than hiding its end cap. Square boards retain the detailed
text layout.

## Payload

```json
{"v":1,"enabled":true,"stale":false,"creditsTenths":12400,
 "ageSeconds":130,"plan":"PRO","workspace":"NICLAS STUDIO"}
```

Optional, only with a balance and only when named by the source:
`"grantTenths":1000,"resetSeconds":5270400,"periodSeconds":7862400`,
`"dailyCreditsTenths":50,"dailyGrantTenths":50`.

On the round face, a positive daily balance is pink and zero is neutral gray.
The row only shows the daily amount left; usage is not displayed.

Before the first read, `creditsTenths`/`ageSeconds` are left out; `"login":true`
means the Mac needs `lovable_monitor.py login`. The panel parser rejects any
other field.

## Setup

Use the browser path below for the currently available credit balance. You need
an existing VibePulse tokenserver, Chrome signed into Lovable on **the same
computer**, and firmware from this branch containing the Lovable page.

**Evidence:** the local host receiver and a one-time real browser reading reached
the round 1.75 display on macOS; the owner photographed the result. Automatic
Chrome extension installation/refresh has not yet been validated end to end.
Windows browser integration is also unverified. This is an experimental fallback,
not an official Lovable integration or a claim of cross-browser support.

### Browser fallback (Chrome, optional)

As observed on 2026-09-24, both the [public MCP documentation](https://docs.lovable.dev/integrations/lovable-mcp-server#identity-and-workspaces)
and the [server skill](https://mcp.lovable.dev/skill.md) describe credits in `get_workspace`, but the authenticated tool schema
and actual reply omit them. `get_me` and `list_workspaces` also omit balances;
none of the 40 advertised tools exposes a credit/billing balance. OAuth discovery
advertises `workspaces:read`, but no separate credit or billing scope. This is
evidence of a documentation/service mismatch, not proof about private APIs or
future availability. The MCP monitor remains available through `auto` and `mcp`.

The optional local Chrome extension under `tools/lovable-browser/` reads the
**rendered billing page** and forwards a small allowlisted snapshot to the local tokenserver.
It does not export cookies or OAuth tokens, inspect project code/chat, or call
undocumented Lovable endpoints. It is an unofficial UI integration and may need
updating when Lovable changes its page. Chrome grants host access to lovable.dev
and 127.0.0.1; the content script runs only on `/settings/billing`.

### 1. Prepare the local extension

In Lovable, open **Settings → Plans & credit usage**. Note the workspace name
in the sidebar and confirm this is the balance you want. From the repo root:

```sh
python3 tools/lovable-browser/setup_browser.py --workspace-name "Your workspace" --port 8737
```

On Windows use `py -3` in place of `python3`. Match the exact workspace name,
including punctuation and case. Use your existing tokenserver port if different.
The command prepares files; it does not install anything in Chrome. No Lovable
OAuth login or token is required in browser mode.

### 2. Enable the source on your existing tokenserver

Add `--lovable --lovable-source browser` to its existing command, preserving all
other provider, repository and relay options. A manual example is:

```sh
python3 tools/tokenserver/tokenserver.py --lovable --lovable-source browser
```

Do not start a second process on the same port. For an installed macOS service,
add those two options to the existing LaunchAgent's `ProgramArguments`, then
reload the changed plist (kickstart alone does not reload launchd configuration):

```sh
launchctl bootout gui/$(id -u)/se.torget.tokenserver
launchctl bootstrap gui/$(id -u) "$HOME/Library/LaunchAgents/se.torget.tokenserver.plist"
launchctl kickstart -k gui/$(id -u)/se.torget.tokenserver
```

Alternatively set `VIBEPULSE_LOVABLE=1` and `VIBEPULSE_LOVABLE_SOURCE=browser` in
the service environment. For Windows, preserve the existing task action and
follow [the scheduled-task restart instructions](windows-setup.md). Do not rerun
the installer with missing options: that can remove existing configuration.

### 3. Load it in Chrome

Open `chrome://extensions`, enable **Developer mode**, choose **Load unpacked**,
and select the directory printed by setup:

- macOS: `~/Library/Application Support/VibePulse/lovable-browser-extension`
- Windows: `%LOCALAPPDATA%\VibePulse\lovable-browser-extension`

Approve only the permissions described above, on your own machine. If Chrome is
managed and disallows unpacked extensions, an administrator-approved distribution
is needed; do not bypass that policy. Open a Lovable project in the same Chrome
profile. A separate billing tab should appear. Leave it in the background. The
extension popup shows its latest delivery status; `ON` means a reading was
accepted by the local server, not that the physical display was inspected.

### 4. Check the host response

```sh
curl http://127.0.0.1:8737/api/lovable
```

On Windows use `curl.exe`. A successful response contains `creditsTenths`,
`ageSeconds`, and `stale:false`. Divide `creditsTenths` by ten to compare it
with the Lovable page. Leave the project open and check again after about a
minute: `ageSeconds` should drop as a newly loaded page is observed. Closing
Chrome should eventually produce `stale:true`, not a made-up zero.

### 5. Connect the display

Use the same Mac/PC LAN address and port as the other URLs in your own
`secrets.h`. `localhost` on the display would mean the display itself:

```c
#define TK_LOVABLE_URL "http://<computer-LAN-IP>:8737/api/lovable"
```

If the flashed firmware already has this URL and the Lovable page, no flash is
needed. Otherwise build/install using your **actual board's guide** linked from
[hardware requirements](../README.md#what-you-need). Identify the unit before
building; a 1.75 round display needs `TORGET_BOARD=waveshare_175`, 466 × 466,
not the default 2.16 profile. A USB port name alone does not identify the board.
Check the board registry and named unit before building. Maintainer machines may also have optional
Solelkollen/Buddy companion checkouts auto-detected by CMake. Inspect the app
registry/build configuration; for a VibePulse-only image, explicitly point
`TORGET_SOLELKOLLEN_DIR` and `TORGET_BUDDY_DIR` at nonexistent component paths
instead of accidentally including a companion app. See
[the maintainer build notes](../AGENTS.md#status-2026-09-24-v120).

Hold the Settings button for three seconds (**BOOT**, not RESET, on the 1.75).
Tap **LABS → MORE → LOVABLE PAGE ON → MORE → RESTART NOW**. Reopen VibePulse
and swipe to Lovable. The panel polls about every 60 seconds. Claude/Codex
visibility can be changed independently on the last Labs page.

### Everyday behavior and privacy

While a project is open in any normal window of that Chrome profile, the extension
maintains a background billing tab, marked `#vibepulse`, and reloads it about once
a minute. It never reloads an editor or a tab currently being viewed. Leave the
helper in the background. Chrome needs to be running, logged in, with the intended
workspace selected. Other browsers/profiles and incognito are not covered.

Only a matching workspace name is accepted (pinned by setup). On workspace rename,
rerun setup. Names are not unique IDs: people with identically named workspaces
must give them distinct names before using this fallback. The ingress checks
loopback peer, Host, extension identity header, Origin, JSON type, payload size,
numeric bounds and observation age. It provides no website CORS access. Local
processes are within the trust boundary; the extension ID is not a password.

`GET /api/lovable` remains the same firmware contract. In `auto` mode it prefers a healthy official balance when newer or when the
browser reading is stale. Browser readings are persisted locally and become `CACHED` after three
minutes without updates. Unchanged DOM is not repeatedly labelled fresh; a new
page load or changed balance is required. Closing Chrome does not erase the last
reading. No new firmware is needed for this fallback.

The main credits value comes from the accessible meter's **credits left** text,
not its percentage or maximum. The monthly allowance and daily **build** balance
are separately named. Daily chat percentage is not a build-credit count and is
not forwarded. A renewal date without a year/time and a grant expiry are not
guessed into reset timestamps, so the reset ring remains absent.

### Troubleshooting

| Symptom | What to check |
|---|---|
| No billing tab | Extension enabled, a `/projects/…` page open, same Chrome profile, host permissions granted. |
| Popup says not delivered | Tokenserver running with `--lovable`; port matches setup; exact workspace name matches. |
| No balance, only `stale:true` | No accepted reading yet. Check login and rendered billing fields; page changes can break the reader. |
| Value becomes CACHED | Keep the helper tab in the background. Sleep, closing Chrome, login expiry or no open project stops fresh readings. |
| Host JSON works but screen is empty | LAN URL in the installed firmware, same network, Labs enabled, restart applied. |
| Switched workspace or renamed it | Rerun setup with the intended name, restart tokenserver and reload the extension. |
| No ring/reset countdown | Expected for browser readings: dates without year/time are not guessed. |

### Updating or removing the fallback

After updating this repository, rerun setup with the same workspace and port,
click the extension's **Reload** button in Chrome, and restart tokenserver. If
Chrome keeps an old content script in a billing tab, reload that billing tab too.
No editor reload or firmware flash is needed for a host-only reader change.

To disable, remove/disable **VibePulse Lovable Credits** in Chrome and close its
`#vibepulse` billing tab. Remove `lovable-browser.json` and, if desired,
`lovable-browser-cache.json` from the VibePulse host state directory. Restart
tokenserver. Removing `--lovable` disables both sources and the host endpoint.

### Moving to official credits later

The display contract is shared by both sources, so an upstream balance field
can be supported on the computer without redesigning the firmware.

```sh
python3 tools/tokenserver/lovable_monitor.py login
python3 tools/tokenserver/lovable_monitor.py probe
```

The login opens a browser for consent and stores OAuth in the OS credential
store described above. Probe prints a redacted response: check whether credits
are actually present before switching. `--lovable-source auto` enables both
sources and selects a fresh source by age; `--lovable-source mcp` disables the
browser ingress entirely. Neither mode invents fields missing upstream. If
Lovable uses a new field shape, update the host extractor and its fixtures.

## Preview

```sh
TORGET_LABS_MASK=32 sim/build/torget-sim --vibepulse-lovable-qa
# round glass:
cmake -S sim -B sim/build175 -G Ninja -DTORGET_BOARD=waveshare_175
cmake --build sim/build175
TORGET_LABS_MASK=32 sim/build175/torget-sim --vibepulse-lovable-qa
```

Writes waiting, login, live, cached, daily-empty, large and small native captures
(480 × 480 for 2.16; 466 × 466 for 1.75) from
`sim-fixtures/lovable*.json`.
