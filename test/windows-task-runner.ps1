<# Non-mutating Windows CI test for the Task Scheduler entrypoint. #>
$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Runner = Join-Path $RepoRoot "tools\tokenserver\run-windows-task.ps1"
$Python = (Get-Command python.exe -ErrorAction Stop).Source
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) (
    "VibePulse runner å " + [guid]::NewGuid().ToString("N")
)
$PreviousLocalAppData = $env:LOCALAPPDATA
$PreviousPath = $env:Path

try {
    New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null
    $env:LOCALAPPDATA = $TempRoot
    $Fixture = Join-Path $TempRoot "fixture med å.py"
    @'
import sys
import os
print("stdout-ok")
print("stderr-ok", file=sys.stderr)
print("codex-path-ok" if os.environ["PATH"].split(os.pathsep)[0] ==
      os.environ["VIBEPULSE_TEST_CODEX_DIR"] else "codex-path-bad")
print("codex-home-ok" if os.environ.get("CODEX_HOME") ==
      os.environ["VIBEPULSE_TEST_CODEX_HOME"] else "codex-home-bad")
expected = [
    "--lovable", "--lovable-source", "browser",
    "--github-repo", "owner/repository",
    "--claude-plan", "max5x",
    "--codex-plan", "pro",
    "--plan", "claude=100",
    "--plan", "codex=20",
]
print("optional-pages-ok" if sys.argv[1:] == expected else
      "optional-pages-bad")
'@ | Set-Content -LiteralPath $Fixture -Encoding UTF8
    $CodexDir = Join-Path $TempRoot "Codex bin å"
    New-Item -ItemType Directory -Path $CodexDir -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $CodexDir "codex.exe") `
        -Force | Out-Null
    $env:VIBEPULSE_TEST_CODEX_DIR = $CodexDir
    $CodexHome = Join-Path $TempRoot "Codex home å"
    New-Item -ItemType Directory -Path $CodexHome -Force | Out-Null
    $env:VIBEPULSE_TEST_CODEX_HOME = $CodexHome

    $LogDir = Join-Path $TempRoot "VibePulse\Logs"
    $LogPath = Join-Path $LogDir "torget-tokenserver.log"
    $OldLogPath = "$LogPath.old"
    New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
    $Filler = New-Object byte[] (5MB + 4096)
    [System.IO.File]::WriteAllBytes($LogPath, $Filler)
    [System.IO.File]::AppendAllText($LogPath, "rotation-sentinel")

    & $Runner -Python $Python -Server $Fixture -CodexBinDir $CodexDir `
        -CodexHome $CodexHome -GithubRepo "owner/repository" -Lovable -LovableSource browser `
        -ClaudePlan max5x -CodexPlan pro `
        -ClaudePlanCostUsd "100" -CodexPlanCostUsd "20"
    if ($LASTEXITCODE -ne 0) {
        throw "runner returned $LASTEXITCODE"
    }

    if (-not (Test-Path -LiteralPath $OldLogPath -PathType Leaf)) {
        throw "runner did not create the rotated .old tail"
    }
    if ((Get-Item -LiteralPath $OldLogPath).Length -gt 256KB) {
        throw "rotated tail exceeds 256 KiB"
    }
    $OldText = [System.IO.File]::ReadAllText($OldLogPath)
    if (-not $OldText.Contains("rotation-sentinel")) {
        throw "rotated tail lost its final marker"
    }
    $CurrentText = [System.IO.File]::ReadAllText($LogPath)
    if (-not $CurrentText.Contains("stdout-ok")) {
        throw "stdout was not captured"
    }
    if (-not $CurrentText.Contains("stderr-ok")) {
        throw "stderr was not captured"
    }
    if (-not $CurrentText.Contains("codex-path-ok")) {
        throw "Codex bin directory was not prepended to PATH"
    }
    if (-not $CurrentText.Contains("codex-home-ok")) {
        throw "Codex home was not passed to the child process"
    }
    if (-not $CurrentText.Contains("optional-pages-ok")) {
        throw "GitHub and subscription display inputs were not forwarded"
    }

    $FailingFixture = Join-Path $TempRoot "fixture failure å.py"
    @'
import sys
sys.exit(-1)
'@ | Set-Content -LiteralPath $FailingFixture -Encoding UTF8
    & powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass `
        -File $Runner -Python $Python -Server $FailingFixture *> $null
    if ($LASTEXITCODE -ne 1) {
        throw "negative child exit was not normalized to 1"
    }
    Write-Host "VibePulse Windows task runner: OK"
    exit 0
} finally {
    Remove-Item Env:VIBEPULSE_TEST_CODEX_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:VIBEPULSE_TEST_CODEX_HOME -ErrorAction SilentlyContinue
    $env:LOCALAPPDATA = $PreviousLocalAppData
    $env:Path = $PreviousPath
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
