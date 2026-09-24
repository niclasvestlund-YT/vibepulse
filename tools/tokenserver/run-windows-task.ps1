<#
Task Scheduler entrypoint for the VibePulse tokenserver.

The scheduled task calls this wrapper instead of pythonw.exe directly so the
normal unattended installation has a bounded diagnostic log. The wrapper
contains no provider choices; tokenserver.py reads those from its saved config.
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Python,
    [Parameter(Mandatory = $true)]
    [string]$Server,
    [string]$CodexBinDir = "",
    [string]$CodexHome = "",
    [string]$GithubRepo = "",
    [switch]$Lovable,
    [ValidateSet("auto", "browser", "mcp")]
    [string]$LovableSource = "auto",
    [string]$ClaudePlan = "",
    [string]$CodexPlan = "",
    [string]$ClaudePlanCostUsd = "",
    [string]$CodexPlanCostUsd = "",
    [string]$PublishUrl = "",
    [string]$PublishName = ""
)

$ErrorActionPreference = "Stop"
$LogCapBytes = 5MB
$LogTailBytes = 256KB

if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw "VibePulse Python interpreter is missing: $Python"
}
if (-not (Test-Path -LiteralPath $Server -PathType Leaf)) {
    throw "VibePulse tokenserver is missing: $Server"
}
if ($CodexBinDir) {
    $Codex = Join-Path $CodexBinDir "codex.exe"
    $CodexCmd = Join-Path $CodexBinDir "codex.cmd"
    if (-not (Test-Path -LiteralPath $Codex -PathType Leaf) -and
            -not (Test-Path -LiteralPath $CodexCmd -PathType Leaf)) {
        throw "VibePulse Codex bin directory is invalid"
    }
    $env:Path = $CodexBinDir + [IO.Path]::PathSeparator + $env:Path
}
if ($CodexHome) {
    if (-not (Test-Path -LiteralPath $CodexHome -PathType Container)) {
        throw "VibePulse Codex home directory is invalid"
    }
    $env:CODEX_HOME = $CodexHome
}

$LocalAppData = $env:LOCALAPPDATA
if (-not $LocalAppData) {
    $LocalAppData = Join-Path $env:USERPROFILE "AppData\Local"
}
$LogDir = Join-Path $LocalAppData "VibePulse\Logs"
$LogPath = Join-Path $LogDir "torget-tokenserver.log"
$OldLogPath = "$LogPath.old"
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)

function Rotate-VibePulseLog {
    if (-not (Test-Path -LiteralPath $LogPath -PathType Leaf)) { return }
    $Length = (Get-Item -LiteralPath $LogPath).Length
    if ($Length -le $LogCapBytes) { return }

    # The wrapper owns the append operation, so it can keep exactly one tail
    # without renaming a file that Python still has open. This also works in
    # Windows PowerShell 5.1 and for non-ASCII profile paths.
    $InputStream = [System.IO.File]::Open(
        $LogPath,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::Read
    )
    try {
        $Keep = [Math]::Min([int64]$LogTailBytes, $InputStream.Length)
        $InputStream.Seek(-$Keep, [System.IO.SeekOrigin]::End) | Out-Null
        $Tail = New-Object byte[] $Keep
        $Read = $InputStream.Read($Tail, 0, $Tail.Length)
        if ($Read -lt $Tail.Length) {
            $Tail = $Tail[0..($Read - 1)]
        }
        [System.IO.File]::WriteAllBytes($OldLogPath, $Tail)
        $InputStream.SetLength(0)
    } finally {
        $InputStream.Dispose()
    }
}

function Write-VibePulseLogLine {
    param([AllowEmptyString()][string]$Text)
    Rotate-VibePulseLog
    [System.IO.File]::AppendAllText(
        $LogPath,
        $Text + [Environment]::NewLine,
        $Utf8NoBom
    )
}

Rotate-VibePulseLog

$ServerArgs = @("-u", $Server)
if ($Lovable) {
    $ServerArgs += @("--lovable", "--lovable-source", $LovableSource)
}
if ($GithubRepo) {
    $ServerArgs += @("--github-repo", $GithubRepo)
}
if ($ClaudePlan) {
    $ServerArgs += @("--claude-plan", $ClaudePlan)
}
if ($CodexPlan) {
    $ServerArgs += @("--codex-plan", $CodexPlan)
}
if ($ClaudePlanCostUsd) {
    $ServerArgs += @("--plan", "claude=$ClaudePlanCostUsd")
}
if ($CodexPlanCostUsd) {
    $ServerArgs += @("--plan", "codex=$CodexPlanCostUsd")
}
if ($PublishUrl) {
    $ServerArgs += @("--publish", $PublishUrl)
    if ($PublishName) { $ServerArgs += @("--publish-name", $PublishName) }
}

try {
    # Merge the native streams into a low-volume line pipeline. The wrapper,
    # rather than a permanently open redirect handle, can then enforce the
    # size cap during a long-running task. Tokenserver deliberately logs state
    # transitions instead of requests, so per-line appends stay cheap.
    $PreviousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $Python @ServerArgs 2>&1 | ForEach-Object {
        Write-VibePulseLogLine -Text $_.ToString()
    }
    $ExitCode = $LASTEXITCODE
    $ErrorActionPreference = $PreviousErrorActionPreference
    if ($null -eq $ExitCode) { $ExitCode = 1 }
    # A force-stopped Windows child commonly reports -1, which PowerShell
    # forwards as 0xFFFFFFFF. Task Scheduler does not reliably apply
    # RestartOnFailure to that sentinel. Normalize every native failure to
    # the conventional process exit code 1.
    if ($ExitCode -ne 0) { exit 1 }
    exit 0
} catch {
    $ErrorActionPreference = "Stop"
    $Timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    Write-VibePulseLogLine -Text (
        "$Timestamp wrapper failure: $($_.Exception.GetType().Name): " +
        $_.Exception.Message
    )
    throw
}
