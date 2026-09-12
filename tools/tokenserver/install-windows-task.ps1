<#
Register the tokenserver as a scheduled task on Windows, so it survives
logout and reboot -- the gap in issue #3: the service existed but died with
the terminal window.

Run in PowerShell from the repo root:

  powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 `
      -PublishUrl "https://<your-mailbox>/u/<secret>"

  # without a relay (LAN serving only):
  powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1

  # optional GitHub and value pages (only name plans you actually pay for):
  powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 `
      -GithubRepo "owner/repository" -ClaudePlan max5x `
      -ClaudePlanCostUsd "100" -CodexPlan pro -CodexPlanCostUsd "20"

  # uninstall:
  powershell -ExecutionPolicy Bypass -File tools\tokenserver\install-windows-task.ps1 -Uninstall

Design choices, in line with the rest of the repo:

- The task runs as THE LOGGED-IN USER, not SYSTEM. The tokenserver reads
  %USERPROFILE%\.claude\.credentials.json -- a SYSTEM service would have
  read the wrong profile and given the process more rights than it needs.
- A hidden PowerShell wrapper runs python.exe and writes a bounded log to
  %LOCALAPPDATA%\VibePulse\Logs\torget-tokenserver.log. A single .old file
  keeps restarts and long runs from growing without bound.
- Interaction providers and detail are read from the tokenserver's saved config.
  Keep those choices out of the scheduled command so setup changes cannot go
  stale here. The optional publish arguments below are numbers-relay settings,
  not interaction-provider choices.
- GitHub monitoring and subscription costs are host-display inputs, not
  interaction permissions. The installer carries those explicit, non-secret
  choices to the background process so Windows matches a foreground launch.
- No secret in the registered command line except the relay URL, which
  the user chose to give -- the same exposure level as secrets.h.
- A logon trigger starts the service and a five-minute watchdog starts it
  again if the process has died. `IgnoreNew` makes the watchdog harmless
  while it is already running -- a shelf service must get back up by
  itself, just like the launchd plist.
#>
param(
    [string]$PublishUrl = "",
    [string]$PublishName = "",
    [string]$GithubRepo = "",
    [string]$ClaudePlan = "",
    [string]$CodexPlan = "",
    [string]$ClaudePlanCostUsd = "",
    [string]$CodexPlanCostUsd = "",
    [switch]$ValidateOnly,
    [switch]$Uninstall
)

$ErrorActionPreference = "Stop"
$TaskName = "VibePulse tokenserver"

if ($ValidateOnly -and $Uninstall) {
    throw "-ValidateOnly and -Uninstall cannot be combined"
}

if ($GithubRepo -and
        $GithubRepo -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') {
    throw "-GithubRepo must use owner/repository"
}
if ($ClaudePlan -and $ClaudePlan -notin @("pro", "max5x", "max20x")) {
    throw "-ClaudePlan must be pro, max5x, or max20x"
}
if ($CodexPlan -and $CodexPlan -notin @("plus", "pro")) {
    throw "-CodexPlan must be plus or pro"
}
foreach ($Cost in @($ClaudePlanCostUsd, $CodexPlanCostUsd)) {
    if ($Cost -and $Cost -notmatch '^(?:0|[1-9][0-9]*)(?:\.[0-9]{1,2})?$') {
        throw "Plan costs must be positive USD values with at most two decimals"
    }
    if ($Cost -and [decimal]::Parse(
            $Cost, [Globalization.CultureInfo]::InvariantCulture) -le 0) {
        throw "Plan costs must be greater than zero"
    }
}

function Resolve-VibePulsePython {
    # Task Scheduler inherits a much smaller PATH than an interactive shell.
    # Resolve the real interpreter at install time and reject Python versions
    # the tokenserver does not support instead of registering a task that can
    # only fail after the next login.
    $candidates = @()
    $py = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($py) {
        try {
            $pyLauncher = $py.Source
            $resolved = & $pyLauncher -3 -c `
                "import sys; print(sys.executable if sys.version_info >= (3, 11) else '')"
            if ($LASTEXITCODE -eq 0 -and $resolved) {
                $candidates += $resolved.Trim()
            }
        } catch {
            # Continue to explicit python.exe/python3.exe candidates.
        }
    }
    foreach ($name in @("python.exe", "python3.exe")) {
        $command = Get-Command $name -ErrorAction SilentlyContinue
        if ($command) { $candidates += $command.Source }
    }

    foreach ($candidate in $candidates | Select-Object -Unique) {
        try {
            & $candidate -c `
                "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"
            if ($LASTEXITCODE -eq 0) { return $candidate }
        } catch {
            # Try the next candidate and fail once with one actionable message.
        }
    }
    throw "VibePulse requires Python 3.11 or newer. Install it, reopen PowerShell, and rerun this installer."
}

function Resolve-VibePulseCodexBinDir {
    $Candidates = @()
    if ($env:LOCALAPPDATA) {
        $Standalone = Join-Path $env:LOCALAPPDATA `
            "Programs\OpenAI\Codex\bin\codex.exe"
        if (Test-Path -LiteralPath $Standalone -PathType Leaf) {
            $Candidates += $Standalone
        }
    }
    foreach ($Name in @("codex.exe", "codex.cmd")) {
        $Command = Get-Command $Name -ErrorAction SilentlyContinue
        if ($Command -and $Command.Source) {
            $Candidates += $Command.Source
        }
    }
    foreach ($Candidate in $Candidates | Select-Object -Unique) {
        try {
            & $Candidate --version *> $null
            if ($LASTEXITCODE -eq 0) {
                return Split-Path -Parent $Candidate
            }
        } catch {
            # Codex is optional; keep checking candidates.
        }
    }
    return ""
}

function Resolve-VibePulseCodexHome {
    $Candidate = $env:CODEX_HOME
    if (-not $Candidate) {
        $Candidate = [Environment]::GetEnvironmentVariable(
            "CODEX_HOME", "User")
    }
    if ($Candidate -and
            (Test-Path -LiteralPath $Candidate -PathType Container)) {
        return (Resolve-Path -LiteralPath $Candidate).Path
    }
    return ""
}

function New-VibePulseTaskSettings {
    # The Task Scheduler XML schema supports StopExisting, but Microsoft's
    # ScheduledTasks PowerShell cmdlet exposes only Parallel, Queue, and
    # IgnoreNew. Stop the one owned task explicitly before replacement and
    # use the portable IgnoreNew policy for later duplicate starts.
    # RestartOnFailure/Count is an unsigned byte in the Task Scheduler
    # schema. Values above 255 can register but are not executed reliably.
    $RestartCount = 255
    return New-ScheduledTaskSettingsSet `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -RestartCount $RestartCount -RestartInterval (New-TimeSpan -Minutes 5) `
        -ExecutionTimeLimit (New-TimeSpan -Seconds 0) `
        -MultipleInstances IgnoreNew
}

if ($Uninstall) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Task '$TaskName' removed. The process already running is not affected;"
    Write-Host "stop it via the port:  Get-NetTCPConnection -LocalPort 8737 -State Listen |"
    Write-Host "  Select -Expand OwningProcess | ForEach-Object { Stop-Process -Id `$_ }"
    exit 0
}

# The repo root is two steps up from this script -- the task must survive
# being registered from any directory whatsoever.
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$Server = Join-Path $RepoRoot "tools\tokenserver\tokenserver.py"
if (-not (Test-Path $Server)) { throw "cannot find $Server" }
$Runner = Join-Path $RepoRoot "tools\tokenserver\run-windows-task.ps1"
if (-not (Test-Path $Runner)) { throw "cannot find $Runner" }

$PythonConsole = Resolve-VibePulsePython
$CodexBinDir = Resolve-VibePulseCodexBinDir
$CodexHome = Resolve-VibePulseCodexHome
# Task Scheduler starts a hidden PowerShell wrapper. Keep python.exe rather
# than pythonw.exe so stdout/stderr can be captured in the durable log.
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$Settings = New-VibePulseTaskSettings
$LogonTrigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$WatchdogTrigger = New-ScheduledTaskTrigger -Once `
    -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes 5)

$RunnerArgs = "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass" +
    " -File `"$Runner`" -Python `"$PythonConsole`" -Server `"$Server`""
if ($CodexBinDir) {
    $RunnerArgs += " -CodexBinDir `"$CodexBinDir`""
}
if ($CodexHome) {
    $RunnerArgs += " -CodexHome `"$CodexHome`""
}
if ($GithubRepo) {
    $RunnerArgs += " -GithubRepo `"$GithubRepo`""
}
if ($ClaudePlan) {
    $RunnerArgs += " -ClaudePlan `"$ClaudePlan`""
}
if ($CodexPlan) {
    $RunnerArgs += " -CodexPlan `"$CodexPlan`""
}
if ($ClaudePlanCostUsd) {
    $RunnerArgs += " -ClaudePlanCostUsd `"$ClaudePlanCostUsd`""
}
if ($CodexPlanCostUsd) {
    $RunnerArgs += " -CodexPlanCostUsd `"$CodexPlanCostUsd`""
}
if ($PublishUrl) {
    $RunnerArgs += " -PublishUrl `"$PublishUrl`""
    if ($PublishName) { $RunnerArgs += " -PublishName `"$PublishName`"" }
}

$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument $RunnerArgs `
    -WorkingDirectory $RepoRoot

# ValidateOnly deliberately constructs every ScheduledTasks object before it
# exits. PowerShell parser success did not catch a real-host enum mismatch;
# this dry run now exercises the module's runtime parameter conversion while
# still avoiding task lookup, registration, start, stop, or removal.
if ($ValidateOnly) {
    $Version = & $PythonConsole -c `
        "import sys; print('.'.join(map(str, sys.version_info[:3])))"
    Write-Host "VibePulse Windows installer validation: OK"
    Write-Host "  repo:    $RepoRoot"
    Write-Host "  server:  $Server"
    Write-Host "  runner:  $Runner"
    Write-Host "  python:  $PythonConsole ($Version)"
    Write-Host "  task objects: runtime construction passed"
    Write-Host "  GitHub page source: $([bool]$GithubRepo)"
    Write-Host "  subscription costs: $([bool]($ClaudePlanCostUsd -or $CodexPlanCostUsd))"
    Write-Host "  action:  no Task Scheduler changes were made"
    exit 0
}

$ExistingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($ExistingTask -and $ExistingTask.State -eq "Running") {
    Stop-ScheduledTask -TaskName $TaskName
    $Deadline = (Get-Date).AddSeconds(15)
    do {
        Start-Sleep -Milliseconds 100
        $ExistingTask = Get-ScheduledTask -TaskName $TaskName
    } while ($ExistingTask.State -eq "Running" -and (Get-Date) -lt $Deadline)
    if ($ExistingTask.State -eq "Running") {
        throw "The existing VibePulse tokenserver task did not stop safely"
    }
}

Register-ScheduledTask -TaskName $TaskName -Action $Action `
    -Trigger @($LogonTrigger, $WatchdogTrigger) `
    -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Task '$TaskName' registered and started."
Write-Host "  server:  $Server"
if ($PublishUrl) { Write-Host "  relay:   $PublishUrl" }
if ($GithubRepo) { Write-Host "  GitHub:  configured" }
if ($ClaudePlanCostUsd -or $CodexPlanCostUsd) {
    Write-Host "  plans:   configured"
}
Write-Host "  state:   $env:LOCALAPPDATA\VibePulse\"
Write-Host "  log:     $env:LOCALAPPDATA\VibePulse\Logs\torget-tokenserver.log"
Write-Host "Verify:  curl http://localhost:8737/  (claudeProbe should show ok)"
