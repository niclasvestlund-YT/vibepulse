# Windows host setup and recovery

This is the public installation runbook for the VibePulse **Windows host**.
It is written so that a developer or coding agent can install, diagnose, and
verify the service without access to the maintainer's computer or history.
For release certification, continue with
[Windows release validation](windows-validation.md); installation success is
not the same as a physical end-to-end release pass.

The commands below must run as the Windows user who actually uses Claude Code
or Codex. Do not install the service as `SYSTEM`: VibePulse deliberately reads
that user's local credentials, Codex sessions, configuration, and state.

## 1. Install the host prerequisites

Open PowerShell and verify:

```powershell
git --version
py -3 -c "import sys; print(sys.version); raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
```

VibePulse requires Git and Python 3.11 or newer. If either command fails,
install the missing tool with the Windows package manager (`winget`, present
on Windows 10 1809 and later), then open a new PowerShell window so the
updated `PATH` applies:

```powershell
winget install --id Git.Git -e
winget install --id Python.Python.3.12 -e
```

Without `winget`, download the installers from
[git-scm.com/download/win](https://git-scm.com/download/win) and
[python.org/downloads/windows](https://www.python.org/downloads/windows/);
tick *Add python.exe to PATH* in the Python installer. Rerun the two
verification commands above before continuing.

Clone the repository into a
stable path that will still exist after the next sign-in:

```powershell
git clone https://github.com/niclasvestlund-YT/vibepulse.git $HOME\vibepulse
Set-Location $HOME\vibepulse
git status --short
```

The last command should be empty. Task Scheduler records this checkout's
absolute path, so moving or deleting it later breaks autostart until the
installer is rerun from the new path.

### Codex needs the standalone CLI

The Codex desktop app and the background-safe Codex CLI are separate Windows
installation surfaces. A Store-managed `WindowsApps` alias can appear in
`Get-Command codex` and still fail with access denied from a background task.
Install OpenAI's standalone CLI:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://chatgpt.com/codex/install.ps1 | iex"
```

Close PowerShell, open a new one, and verify both execution and login:

```powershell
codex --version
codex login status
```

VibePulse prefers
`%LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe` and ignores
`WindowsApps` aliases. Do not point `AVIARY_CODEX_BIN` at a Store alias; if a
local wrapper reports `codex CLI not found on PATH`, fix the standalone CLI
installation or set that variable to the real standalone executable.

Claude users should also sign in with Claude Code on this Windows account.
Windows stores the readable OAuth record under
`%USERPROFILE%\.claude\.credentials.json`; never paste that file into an
issue or validation report.

## 2. Choose the interaction providers explicitly

Quota display works independently of panel interactions. To install the
optional Codex plugin/MCP bridge and enable Codex questions with bounded
detail:

```powershell
Set-Location $HOME\vibepulse
py -3 tools\vibepulse_setup.py install --providers codex --detail
py -3 tools\vibepulse_setup.py status
py -3 tools\vibepulse_setup.py doctor
```

Use `--providers claude`, `both`, or `off` when that is the intended scope.
Installing the plugin does **not** enable the encrypted relay, agent-status
relay, GitHub, or another provider.

Open Codex, run `/hooks`, and review the exact VibePulse `SessionStart` and
`PermissionRequest` commands before trusting them. Then start a **new Codex
task** so the hook, skill, and MCP tool are loaded from a fresh session. Doctor
can report that review is needed, but it never bypasses Codex trust.

Expected healthy doctor lines for a Codex installation include:

```text
PASS Python executable
PASS Codex executable
PASS Codex plugin
PASS Codex MCP
PASS Tokenserver
```

Provider choices are saved under `%LOCALAPPDATA%\VibePulse\`; they do not
belong in the Task Scheduler command, where an old choice could silently win
after setup changes.

## 3. Validate and install autostart

Choose any optional host-display inputs once and reuse the exact same values
for validation and installation. Omit providers you do not pay for; VibePulse
never guesses a subscription cost:

```powershell
$VibePulseHost = @{
  GithubRepo = "owner/repository"
  ClaudePlan = "max5x"
  ClaudePlanCostUsd = "100"
  CodexPlan = "pro"
  CodexPlanCostUsd = "20"
}
```

`GithubRepo` enables the public repository source. The panel firmware must
also be built with `TK_GITHUB_SCREEN_ENABLED=1` for the GitHub page and/or
`TK_GITHUB_NOTIFICATIONS_ENABLED=1` for star notifications. Those device-side
choices are independent of Windows and remain off in a clean default build.

First verify the exact interpreter, checkout, and runtime construction of the
ScheduledTasks action, trigger, and settings without changing Windows:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 `
  -ValidateOnly @VibePulseHost
```

Then install the signed-in user's scheduled task:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 `
  @VibePulseHost
Get-ScheduledTask -TaskName "VibePulse tokenserver" |
  Select-Object TaskName, State
Get-ScheduledTaskInfo -TaskName "VibePulse tokenserver"
```

The installer resolves a real Python 3.11+ executable at install time because
Task Scheduler has a smaller `PATH` than interactive PowerShell. It starts the
service immediately, restarts it after failure, and writes a bounded log to:

```text
%LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log
```

The wrapper and running service keep the log near 5 MB and preserve one `.old`
tail. A missing log means the scheduled process did not reach its logging
entry point. A traceback is a bug worth reporting; do not include credentials,
prompts, commands, or raw session data in the report.

Verify startup identity rather than merely seeing a Python process:

```powershell
$Health = Invoke-RestMethod http://127.0.0.1:8737/
$Health | Select-Object service, rev, srcFingerprint, startedAt,
  claudeProbe, claudeCredential,
  @{Name='panel'; Expression={$_.interactions.panel}}
py -3 tools\tokenserver\smoke.py
```

`smoke.py` exits `0` for healthy, `1` for warnings, and `2` for failures. The
root endpoint exposes content-free health only. `rev` and `srcFingerprint`
answer which checkout and source are actually running; they catch the common
case where an old scheduled task serves code from another worktree.

The startup panel state is evidence, not decoration:

- `waiting`: no confirmed non-loopback panel poll yet;
- `ready`: two recent panel polls from the same client were observed;
- `stale`: the panel was confirmed earlier but is no longer polling recently.

Never turn `waiting` or `stale` into a pass.

## 4. Choose direct LAN or the optional encrypted relay

For direct LAN, find the active Private-network IPv4 address:

```powershell
Get-NetConnectionProfile | Select-Object Name, NetworkCategory
ipconfig
```

Install the optional advertiser in the exact Python environment used by the
scheduled task, then restart the task through the normal installer flow:

```powershell
py -3 -m pip install -r requirements-discovery.txt
```

`GET /` must then report `discovery.status: ready`. Current firmware browses
`_vibepulse._tcp.local`, caches the last healthy origin in NVS, and can choose
another advertising Mac/PC after a bounded failure. Reserve the fallback IPv4
in the router anyway: multicast can be blocked and discovery must never make
direct LAN less reliable than the configured path.

Inspect before changing the firewall:

```powershell
Get-NetFirewallRule -ErrorAction SilentlyContinue |
  Where-Object DisplayName -Like "*VibePulse*" |
  Select-Object DisplayName, Enabled, Direction, Action, Profile
```

If direct LAN is required and no rule exists, run elevated PowerShell and open
only TCP 8737 on Private networks:

```powershell
New-NetFirewallRule -DisplayName "VibePulse tokenserver" -Direction Inbound `
  -Protocol TCP -LocalPort 8737 -Action Allow -Profile Private
```

Never open the Public profile. From another device on the same LAN, test the
PC's real address—not `localhost`:

```powershell
Test-NetConnection -ComputerName <PC-LAN-IP> -Port 8737
```

The optional interaction relay can bridge unrelated Wi-Fi using outbound
HTTPS, but it is a separate, explicit, end-to-end encrypted setup. Follow
[Interaction relay](interaction-relay.md); plugin installation alone never
enables it.

## 5. Verify the providers without exposing account data

Run the read-only diagnostics:

```powershell
py -3 tools\vibepulse_setup.py doctor
$Tokens = Invoke-RestMethod http://127.0.0.1:8737/api/tokens
$Tokens | Select-Object v, claudeWeekStale, claudeModelWeekStale,
  codexWeekStale
```

Do not paste the full endpoint into an issue. Report only presence/type,
staleness, safe status, and revisions.

Two failures that look similar are intentionally separate:

- Claude can remain logged in while the exported usage credential readable by
  VibePulse has expired. Check both `claudeCredential` and `claudeProbe`;
  Fable/Opus remains honestly stale until a supported Claude client refreshes
  the readable credential.
- Codex can be logged in through the desktop app while the Store alias is not
  executable by the background service. `PASS Codex executable` plus a fresh
  `codexWeekStale: false` proves the standalone CLI/app-server path instead.

## 6. Close the physical loop

Before a panel test, compare the firmware build checkout's
`git describe --tags --always --dirty` with the service's
`otaAvailableVersion`. A valid build from an old worktree can immediately show
**UPDATE READY** and invalidate the test.

Use this exact Codex question:

- header: `Test`
- question: `Ser du APPROVE?`
- `Ja` — `APPROVE syns` — recommended
- `Nej` — `APPROVE saknas`

Pass only when the panel visibly shows **APPROVE**, a human taps it, and the
waiting call returns `status: answered`, `option_index: 0`, `answer: Ja`.
Silence, timeout, panel absence, **LEAVE IT**, computer fallback, or
**SOMETHING IS WAITING** without buttons is `FAIL` or `NOT TESTED`, never an
implicit approval.

## 7. Prove lifecycle recovery

Before announcing Windows support, verify all of these against the exact
candidate under test:

1. the task starts immediately after installation;
2. terminating only the tokenserver process produces an automatic restart;
3. sign-out/sign-in starts it again;
4. sleep/resume restores fresh endpoints and panel polling;
5. one full Windows reboot restores the task, current provider sources, recent
   panel polling, and the physical question path.

Record each row as `PASS`, `FAIL`, or `NOT TESTED`. A green unit-test suite or
`State: Ready` in Task Scheduler does not substitute for these lifecycle and
physical checks.

## Recovery and removal

| Symptom | Likely cause | Action |
|---|---|---|
| `codex_wrap: codex CLI not found on PATH` | A local wrapper cannot find the standalone CLI | Reopen PowerShell after installing the CLI; point `AVIARY_CODEX_BIN` only at the real standalone executable if the wrapper requires it |
| `Get-Command codex` works but VibePulse gets access denied | WindowsApps/Store alias | Install the standalone CLI and rerun doctor |
| `FIX Python executable` | Python is older than 3.11 or Task Scheduler cannot resolve it | Install current Python, reopen PowerShell, rerun `-ValidateOnly`, then reinstall the task |
| `FIX Codex plugin` or `FIX Codex MCP` | Optional integration is absent or stale | Rerun setup install for the intended provider; review `/hooks`; start a new Codex task |
| Hooks say `need review` | New or changed commands have not been trusted | Inspect exact commands in `/hooks`; never bypass trust automatically |
| Root endpoint is healthy but panel is `waiting` | Wrong PC address, firewall, client isolation, relay off, or panel elsewhere | Test the real LAN IP from another device; verify saved relay state separately |
| Only **LEAVE IT** appears | No single option was explicitly recommended | Use the canonical short smoke question with exactly one genuine recommendation |
| **SOMETHING IS WAITING** has no buttons | The request failed the physical fit/privacy gate | Finish on the computer; do not count it as a panel pass |
| Fable is stale while Claude still works | Login state and readable usage credential diverged | Inspect `claudeCredential`, `claudeProbe`, and `claudeLocalUsage`; refresh through a supported Claude client |
| Task runs old code | Installer still points at an older checkout | Compare `rev`/`srcFingerprint`, rerun the installer from the intended stable checkout |
| No scheduled-task log | Wrapper never started or its recorded path/interpreter disappeared | Inspect task action/result; rerun `-ValidateOnly` and reinstall from the stable checkout |

Disable only a provider while preserving the package:

```powershell
py -3 tools\vibepulse_setup.py disable codex
```

Remove only the optional Codex integration:

```powershell
py -3 tools\vibepulse_setup.py uninstall codex
```

Remove autostart without deleting state, the repository, or a running process:

```powershell
powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 -Uninstall
```

Keep secrets and private state out of commits and reports. A useful public
failure report contains Windows/Python/Codex versions, the exact VibePulse
revision, safe doctor/smoke lines, scheduled-task state/result, filtered root
health, and the narrow physical outcome—nothing from credentials or sessions.
