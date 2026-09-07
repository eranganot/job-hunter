<#
    ship_phase0.ps1 - commit and deploy Phase 0 of EXECUTION_PLAN_PUBLIC_LAUNCH.md

    The Claude sandbox has no push credentials, so this is the hand-off:
    it re-verifies the phase locally, shows you exactly what will ship,
    asks once, then commits and pushes. Railway deploys on push.

    Usage:
        cd C:\dev\job-hunter
        .\scripts\ship_phase0.ps1
        .\scripts\ship_phase0.ps1 -SkipTests      # only if you just ran them
#>
[CmdletBinding()]
param(
    [string]$Message = "",
    [switch]$SkipTests,
    [switch]$Force
)

# NOT 'Stop': in Windows PowerShell 5.1, any native command (python, git) that
# writes to stderr - a pytest DeprecationWarning, a git progress line - becomes a
# terminating error under 'Stop'. Every native call below is checked by exit code
# instead, which is the thing that actually reports success.
$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$repo = "C:\dev\job-hunter"

function Fail($msg) { Write-Host "FAIL: $msg" -ForegroundColor Red; exit 1 }
function Ok($msg)   { Write-Host "OK:   $msg" -ForegroundColor Green }
function Info($msg) { Write-Host "      $msg" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "=== Phase 0 ship ===" -ForegroundColor Cyan

# --- 1. Right repo? -----------------------------------------------------------
if ((Get-Location).Path -ne $repo) {
    Info "Switching to $repo"
    Set-Location $repo
}
if (-not (Test-Path "app.py")) { Fail "app.py not found - this is not the Job Hunter repo." }
if (-not (Test-Path "EXECUTION_PLAN_PUBLIC_LAUNCH.md")) { Fail "EXECUTION_PLAN_PUBLIC_LAUNCH.md missing - wrong clone?" }

$remote = (& git remote get-url origin 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { Fail "git remote failed: $remote" }
if ($remote -notmatch "eranganot/job-hunter") { Fail "origin is '$remote', expected eranganot/job-hunter." }
Ok "repo: $repo -> $remote"

$branch = (& git rev-parse --abbrev-ref HEAD 2>&1 | Out-String).Trim()
if ($branch -ne "main" -and -not $Force) { Fail "on branch '$branch', expected main (use -Force to override)." }
Ok "branch: $branch"

# --- 2. Up to date with origin? ----------------------------------------------
& git fetch origin --quiet 2>&1 | Out-Null
$behind = (& git rev-list --count "HEAD..origin/main" 2>&1 | Out-String).Trim()
if ($behind -notmatch '^\d+$') { Fail "could not compare with origin/main: $behind" }
if ($behind -ne "0" -and -not $Force) {
    Fail "local main is $behind commit(s) behind origin/main. Pull first: git pull --ff-only"
}
Ok "in sync with origin/main"

# --- 3. Tests -----------------------------------------------------------------
if (-not $SkipTests) {
    Write-Host ""
    Write-Host "Running the suite (expect 181 passed)..." -ForegroundColor Cyan
    $out  = (& python -m pytest -q 2>&1 | Out-String)
    $code = $LASTEXITCODE
    $lines = $out -split "`r?`n"
    $summary = ($lines | Where-Object { $_ -match "passed|failed|error" } | Select-Object -Last 1)
    if ($code -ne 0) {
        foreach ($f in ($lines | Where-Object { $_ -match "^(FAILED|ERROR) " })) {
            Write-Host ("      " + $f.Trim()) -ForegroundColor Yellow
        }
        Write-Host ("      " + $summary) -ForegroundColor DarkGray
        Fail "tests failed - nothing was committed. Re-run 'python -m pytest -q' for the full trace."
    }
    Write-Host ("      " + $summary.Trim()) -ForegroundColor DarkGray
    Ok "tests green"
} else {
    Info "tests skipped (-SkipTests)"
}

# --- 4. Show what ships -------------------------------------------------------
Write-Host ""
Write-Host "Changes to be committed:" -ForegroundColor Cyan
git status --short
Write-Host ""
git diff --stat
Write-Host ""

if ([string]::IsNullOrWhiteSpace($Message)) {
$Message = @"
Phase 0: canonical clone, route/isolation harness, staging + backup tooling

- CLAUDE.md: C:\dev\job-hunter is now the single source of truth. This clone was
  81 commits behind; fast-forwarded to origin/main and the uncommitted work from
  C:\Users\erang\job-hunter carried over. Both old copies are archived.
- tests/test_routes.py: first route-level harness for the HTTP handler - boots the
  real Handler on an ephemeral port against a temp DB. Covers auth gates on every
  page and API, admin role separation, and tenant isolation (a second user cannot
  read, mutate, bulk-mutate, stage or apply-to another user's jobs). Suite 147 -> 181.
- scripts/: ship, smoke and railway helpers for this phase.
- STATUS.md: Phase 0 entry incl. the root cause of the admin-role test-order failure.
"@
}

Write-Host "Commit message:" -ForegroundColor Cyan
Write-Host $Message -ForegroundColor DarkGray
Write-Host ""

if (-not $Force) {
    $answer = Read-Host "Commit and push to main? (y/N)"
    if ($answer -ne "y") { Write-Host "Aborted - nothing committed." -ForegroundColor Yellow; exit 0 }
}

# --- 5. Commit and push -------------------------------------------------------
& git add -A 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "git add failed." }
& git commit -m $Message 2>&1 | Write-Host
if ($LASTEXITCODE -ne 0) { Fail "commit failed." }
& git push origin main 2>&1 | Write-Host
if ($LASTEXITCODE -ne 0) { Fail "push failed - check credentials or network." }

$head = (& git rev-parse --short HEAD 2>&1 | Out-String).Trim()
Ok "pushed $head to origin/main"
Write-Host ""
Write-Host "Railway is now building. When it goes live, verify with:" -ForegroundColor Cyan
Write-Host "    .\scripts\smoke_phase0.ps1" -ForegroundColor White
Write-Host ""
