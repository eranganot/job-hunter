<#
    ship.ps1 - verify, commit and deploy the current phase.

    Generic replacement for ship_phase0.ps1: pass the commit message.
    The Claude sandbox has no push credentials, so this is the hand-off.

    Usage:
        cd C:\dev\job-hunter
        .\scripts\ship.ps1 -Message "Phase 2a: migration runner"
        .\scripts\ship.ps1 -Message "..." -SkipTests
#>
[CmdletBinding()]
param(
    [string]$Message = "",
    [switch]$SkipTests,
    [switch]$Force
)

# NOT 'Stop': in PowerShell 5.1 any native command writing to stderr (a pytest
# warning, a git progress line) becomes a terminating error under 'Stop'.
# Native calls are checked by exit code instead.
$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = "C:\dev\job-hunter"

function Fail($m) { Write-Host "FAIL: $m" -ForegroundColor Red; exit 1 }
function Ok($m)   { Write-Host "OK:   $m" -ForegroundColor Green }
function Info($m) { Write-Host "      $m" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "=== ship ===" -ForegroundColor Cyan

if ((Get-Location).Path -ne $repo) { Info "switching to $repo"; Set-Location $repo }
if (-not (Test-Path "app.py")) { Fail "app.py not found - this is not the Job Hunter repo." }

$remote = (& git remote get-url origin 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "git remote failed: $remote" }
if ($remote -notmatch "eranganot/job-hunter") { Fail "origin is '$remote', expected eranganot/job-hunter." }
Ok "repo: $repo"

$branch = (& git rev-parse --abbrev-ref HEAD 2>&1 | Out-String).Trim()
if ($branch -ne "main" -and -not $Force) { Fail "on branch '$branch', expected main (use -Force)." }
Ok "branch: $branch"

& git fetch origin --quiet 2>&1 | Out-Null
$behind = (& git rev-list --count "HEAD..origin/main" 2>&1 | Out-String).Trim()
if ($behind -notmatch '^\d+$') { Fail "could not compare with origin/main: $behind" }
if ($behind -ne "0" -and -not $Force) { Fail "local main is $behind commit(s) behind origin/main. Run: git pull --ff-only" }
Ok "in sync with origin/main"

if (-not $SkipTests) {
    Write-Host ""
    Write-Host "Running the suite..." -ForegroundColor Cyan
    $out  = (& python -m pytest -q 2>&1 | Out-String)
    $code = $LASTEXITCODE
    $lines = $out -split "`r?`n"
    $summary = ($lines | Where-Object { $_ -match "passed|failed|error" } | Select-Object -Last 1)
    if ($code -ne 0) {
        foreach ($f in ($lines | Where-Object { $_ -match "^(FAILED|ERROR) " })) {
            Write-Host ("      " + $f.Trim()) -ForegroundColor Yellow
        }
        Fail "tests failed - nothing was committed. Run 'python -m pytest -q' for the trace."
    }
    Info $summary.Trim()
    Ok "tests green"
} else { Info "tests skipped (-SkipTests)" }

Write-Host ""
Write-Host "Changes to be committed:" -ForegroundColor Cyan
& git status --short
Write-Host ""
& git diff --stat
# No -Message? Suggest one from what actually changed and let Enter accept it.
if ([string]::IsNullOrWhiteSpace($Message)) {
    $changed = @(& git status --porcelain 2>$null | ForEach-Object { ($_ -replace '^\s*\S+\s+', '').Trim('"') })
    $names   = @($changed | ForEach-Object { Split-Path $_ -Leaf } | Select-Object -Unique)
    if ($names.Count -eq 0) { Fail "nothing to commit - the working tree is clean." }
    $head = ($names | Select-Object -First 4) -join ", "
    $more = if ($names.Count -gt 4) { " (+$($names.Count - 4) more)" } else { "" }
    $suggested = "Update $head$more"

    Write-Host "No -Message given." -ForegroundColor Yellow
    Write-Host "Suggested: $suggested" -ForegroundColor DarkGray
    $typed = Read-Host "Commit message (Enter to accept the suggestion)"
    if ([string]::IsNullOrWhiteSpace($typed)) { $Message = $suggested } else { $Message = $typed }
}

Write-Host ""
Write-Host "Commit message:" -ForegroundColor Cyan
Write-Host $Message -ForegroundColor DarkGray
Write-Host ""

if (-not $Force) {
    $answer = Read-Host "Commit and push to main? (y/N)"
    if ($answer -ne "y") { Write-Host "Aborted - nothing committed." -ForegroundColor Yellow; exit 0 }
}

& git add -A 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "git add failed." }
& git commit -m $Message 2>&1 | Write-Host
if ($LASTEXITCODE -ne 0) { Fail "commit failed." }
& git push origin main 2>&1 | Write-Host
if ($LASTEXITCODE -ne 0) { Fail "push failed - check credentials or network." }

$head = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim()
Ok "pushed $head to origin/main"
Write-Host ""
Write-Host "Railway is building. Verify when it is live:" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke.ps1                                        # production" -ForegroundColor White
Write-Host "    .\scripts\smoke.ps1 -BaseUrl https://web-staging-8e79.up.railway.app -SkipLocal" -ForegroundColor White
Write-Host ""
