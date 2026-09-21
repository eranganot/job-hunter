<#
    cutover_migrate.ps1 - RAILWAY_RUNBOOK.md steps 2.1 to 2.4 in one script.

    Why it exists: the runbook told you to pass C:\dev\_backups\job-hunter\jobs.db
    to sqlite_to_pg.py, but railway_phase0.ps1 -Backup writes to a timestamped
    folder (<OutDir>\<yyyy-MM-dd_HHmm>_<env>\<volume>-jobs.db). The path never
    existed. This finds the file itself, and refuses the three mistakes that
    would make a "successful" copy wrong:

      1. the Railway CLI linked to the wrong environment (the backup AND the
         Postgres server are both taken from whatever is linked),
      2. a backup that is stale - every write after it is lost at the flip,
      3. more than one candidate volume, with no way to say which is live.

    Nothing here touches the web service or its variables. SQLite is only read.
    The one write is into jobhunter_prod, and only after you type COPY.

    Usage (from C:\dev\job-hunter):
        .\scripts\cutover_migrate.ps1                 # backup + dry run + copy + verify
        .\scripts\cutover_migrate.ps1 -UseExisting    # skip the backup, use the newest one
        .\scripts\cutover_migrate.ps1 -VerifyOnly     # re-run the verification only
#>
[CmdletBinding()]
param(
    [switch]$UseExisting,
    [switch]$VerifyOnly,
    [string]$BackupRoot  = "C:\dev\_backups\job-hunter",
    [string]$Database    = "jobhunter_prod",
    [int]$MaxAgeMinutes  = 60
)

$ErrorActionPreference = "Continue"
function Ok($m)   { Write-Host "OK:   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "WARN: $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "FAIL: $m" -ForegroundColor Red; exit 1 }
function Info($m) { Write-Host "      $m" -ForegroundColor DarkGray }

$repo = Split-Path -Parent $PSScriptRoot
Set-Location $repo

# --- 1. Which environment is the CLI linked to? -------------------------------
Write-Host "=== 1. Railway link ===" -ForegroundColor Cyan
$status = (& railway status 2>&1 | Out-String)
Info ($status.Trim() -replace "`r?`n", " | ")
$envName = ""
if ($status -match "(?im)^\s*Environment:\s*(\S+)") { $envName = $Matches[1] }
if ($envName -eq "") { Fail "could not read the linked environment from 'railway status'. Run: railway link" }
if ($envName -ne "production") {
    Fail ("the CLI is linked to '" + $envName + "', not 'production'. The backup and the Postgres server both come from the linked environment. Run: railway environment production")
}
Ok "linked to production"

# --- 2. Backup (or pick the newest one) ---------------------------------------
Write-Host ""
Write-Host "=== 2. Backup ===" -ForegroundColor Cyan
if (-not $UseExisting -and -not $VerifyOnly) {
    & (Join-Path $PSScriptRoot "railway_phase0.ps1") -Backup -OutDir $BackupRoot
    if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne $null) { Fail "the backup script failed - read its output above." }
}
if (-not (Test-Path $BackupRoot)) { Fail ("no backup folder at " + $BackupRoot + ". Run without -UseExisting.") }
$latest = Get-ChildItem $BackupRoot -Directory | Where-Object { $_.Name -like "*_production" } |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
if (-not $latest) { Fail ("no *_production backup under " + $BackupRoot) }
$dbs = @(Get-ChildItem $latest.FullName -Filter "*-jobs.db")
if ($dbs.Count -eq 0) { Fail ("no *-jobs.db in " + $latest.FullName) }
if ($dbs.Count -gt 1) {
    # Prefer the live service's volume; 'job-hunter' was deleted on 2026-09-07.
    $web = @($dbs | Where-Object { $_.Name -like "web-*" })
    if ($web.Count -eq 1) { $dbs = $web; Warn "several volumes backed up - using the 'web' service's copy" }
    else { Fail ("more than one candidate: " + (($dbs | ForEach-Object { $_.Name }) -join ", ") + " - check the backup script's MATCHES line and pass the right one to sqlite_to_pg.py by hand.") }
}
$src = $dbs[0].FullName
$age = [int]((Get-Date) - $dbs[0].LastWriteTime).TotalMinutes
Ok ("source: " + $src)
Info ("taken " + $age + " minute(s) ago")
if ($age -gt $MaxAgeMinutes -and -not $VerifyOnly) {
    Fail ("that backup is " + $age + " minutes old. Anything written to production since then would be lost at the flip. Run again without -UseExisting.")
}

$py = @("scripts/sqlite_to_pg.py", $src, "--database", $Database, "--allow-prod")

if ($VerifyOnly) {
    Write-Host ""
    Write-Host "=== verify only ===" -ForegroundColor Cyan
    & railway run --service Postgres python @py --verify-only
    exit $LASTEXITCODE
}

# --- 3. Dry run ---------------------------------------------------------------
Write-Host ""
Write-Host "=== 3. Dry run (writes nothing) ===" -ForegroundColor Cyan
& railway run --service Postgres python @py --dry-run
if ($LASTEXITCODE -ne 0) { Fail "dry run failed - nothing was written. Read the output above." }

Write-Host ""
Write-Host "Check the backup output said MATCHES the live app. Then above: 'target : jobhunter_prod on junction.proxy.rlwy.net' (or your proxy host)," -ForegroundColor Yellow
Write-Host "users = 9, jobs around 2,600, and every table 'target has 0'." -ForegroundColor Yellow
$answer = Read-Host "Type COPY to write into $Database (anything else stops here)"
if ($answer -ne "COPY") { Warn "stopped - nothing was written."; exit 0 }

# --- 4. Copy, then verify -----------------------------------------------------
Write-Host ""
Write-Host "=== 4. Copy ===" -ForegroundColor Cyan
& railway run --service Postgres python @py
if ($LASTEXITCODE -ne 0) { Fail "copy failed. Production is untouched (it still serves SQLite). Paste the output back." }

Write-Host ""
Write-Host "=== 5. Verify (row counts + value checksums) ===" -ForegroundColor Cyan
& railway run --service Postgres python @py --verify-only
if ($LASTEXITCODE -ne 0) { Fail "verification found differences. Do NOT set the variables. Paste the output back." }

Write-Host ""
Ok "jobhunter_prod holds a verified copy. Next: RAILWAY_RUNBOOK.md step 2.5 (the three variables, together)."
Info ("Do it soon: writes to production after " + $dbs[0].LastWriteTime + " are not in the copy.")
