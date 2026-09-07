<#
    stage_seed.ps1 - push the sanitized seed onto the STAGING volume.

    Phase 0.2. Run this AFTER staging has a volume attached at /data and
    DATABASE_PATH / UPLOADS_DIR set (see the runbook), and after
    scripts/sanitize_db.py has produced the seed.

    It refuses to run unless `railway status` shows a non-production
    environment - uploading a sanitized DB over production would destroy
    every real account.

    Usage:
        railway environment            # switch the link to Staging first
        .\scripts\stage_seed.ps1 -SeedDb "C:\dev\_backups\job-hunter\<stamp>\staging-seed.db"
        .\scripts\stage_seed.ps1 -SeedDb ... -VolumeName web-volume
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$SeedDb,
    [string]$VolumeName = "",
    [string]$CvPdf = "",
    [switch]$Force
)

$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

function Ok($m)   { Write-Host "OK:   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "WARN: $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "FAIL: $m" -ForegroundColor Red; exit 1 }
function Info($m) { Write-Host "      $m" -ForegroundColor DarkGray }

if (-not (Test-Path $SeedDb)) { Fail "seed not found: $SeedDb" }
if ($CvPdf -eq "") { $CvPdf = Join-Path $PSScriptRoot "assets\staging-cv.pdf" }

Write-Host ""
Write-Host "=== seed staging ===" -ForegroundColor Cyan

# --- 1. Refuse to touch production -------------------------------------------
$status = (& railway status 2>&1 | Out-String)
Write-Host $status -ForegroundColor DarkGray
if ($LASTEXITCODE -ne 0) { Fail "not linked to a Railway project. Run: railway link" }

if ($status -match "(?im)^\s*Environment:\s*(.+?)\s*$") { $env_name = $Matches[1] } else { $env_name = "unknown" }
if ($env_name -match "(?i)^prod") {
    Fail "the link points at '$env_name'. This script writes over the DB - switch first: railway environment"
}
if ($env_name -eq "unknown" -and -not $Force) {
    Fail "could not read the environment from 'railway status'. Re-run with -Force only if you are certain it is staging."
}
Ok "environment: $env_name"

# --- 2. Prove the seed is sanitized before it leaves this machine -------------
$check = & python -c @"
import sqlite3,sys
c=sqlite3.connect(r'$SeedDb')
bad=c.execute(\"SELECT COUNT(*) FROM users WHERE email NOT LIKE '%@example.test'\").fetchone()[0]
tok=c.execute(\"SELECT COUNT(*) FROM user_profiles WHERE COALESCE(telegram_token,'')<>'' OR COALESCE(twilio_auth_token,'')<>'' OR COALESCE(email_smtp_pass,'')<>''\").fetchone()[0]
jobs=c.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]
users=c.execute('SELECT COUNT(*) FROM users').fetchone()[0]
print(f'{bad} {tok} {jobs} {users}')
"@ 2>&1
if ($LASTEXITCODE -ne 0) { Fail "could not read the seed as SQLite: $check" }
$parts = ($check -split '\s+')
if ([int]$parts[0] -gt 0) { Fail "seed still contains $($parts[0]) real email(s) - re-run scripts/sanitize_db.py" }
if ([int]$parts[1] -gt 0) { Fail "seed still contains $($parts[1]) notification secret(s) - re-run scripts/sanitize_db.py" }
Ok "seed is sanitized: $($parts[3]) users, $($parts[2]) jobs, no real emails, no credentials"

# --- 3. Pick the volume -------------------------------------------------------
if ($VolumeName -eq "") {
    $vlist = (& railway volume list 2>&1 | Out-String)
    Write-Host $vlist -ForegroundColor DarkGray
    $vols = @(); $cur = $null
    foreach ($line in ($vlist -split "`r?`n")) {
        if     ($line -match "^\s*Volume:\s*(.+?)\s*$")      { if ($cur) { $vols += $cur }; $cur = [pscustomobject]@{ Name=$Matches[1]; Mount="" } }
        elseif ($line -match "^\s*Mount path:\s*(.+?)\s*$")  { if ($cur) { $cur.Mount = $Matches[1] } }
    }
    if ($cur) { $vols += $cur }
    $app = $vols | Where-Object { $_.Mount -eq "/data" -and $_.Name -notmatch "(?i)postgres|redis" }
    if (-not $app) { Fail "no /data volume in '$env_name'. Attach one first: railway volume add --mount-path /data --service web" }
    if ($app.Count -gt 1) { Fail ("more than one /data volume: " + (($app | ForEach-Object { $_.Name }) -join ", ") + " - pass -VolumeName") }
    $VolumeName = $app[0].Name
}
Ok "volume: $VolumeName"

# --- 4. Upload ----------------------------------------------------------------
Info "uploading the seed DB -> /jobs.db"
$out = (& railway volume files --volume $VolumeName upload $SeedDb /jobs.db --overwrite 2>&1 | Out-String)
if ($LASTEXITCODE -ne 0) { Write-Host $out -ForegroundColor DarkGray; Fail "DB upload failed" }
Ok "uploaded /jobs.db"

if (Test-Path $CvPdf) {
    Info "uploading the placeholder CV -> /uploads/staging/cv.pdf"
    $out = (& railway volume files --volume $VolumeName upload $CvPdf /uploads/staging/cv.pdf --overwrite 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0) { Write-Host $out -ForegroundColor DarkGray; Warn "CV upload failed - profiles will show a missing CV" }
    else { Ok "uploaded /uploads/staging/cv.pdf" }
} else {
    Warn "no placeholder CV at $CvPdf - skipping"
}

Write-Host ""
Ok "staging seeded."
Info "Next:"
Info "  1. Restart the staging service so it opens the new DB file:"
Info "         railway redeploy"
Info "  2. Verify:  .\scripts\smoke_phase0.ps1 -BaseUrl <staging-url> -SkipLocal"
Info "  3. Log in as user1@example.test / staging-only-1234  (that account keeps role=admin)"
Write-Host ""
