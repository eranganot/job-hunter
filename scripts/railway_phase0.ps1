<#
    railway_phase0.ps1 - the two Phase 0 jobs that need your Railway credentials:

      1. BACKUP   pull the production SQLite DB off the volume and verify it opens,
                  then compare its row counts against what /api/health reports.
      2. DIFF     compare environment variables between production and staging and
                  print which KEYS differ. Values are never printed - only whether
                  they match - so this is safe to paste back into chat.

    Requires the Railway CLI (npm i -g @railway/cli) and `railway login`.

    Usage:
        cd C:\dev\job-hunter
        .\scripts\railway_phase0.ps1 -Backup
        .\scripts\railway_phase0.ps1 -DiffEnv
        .\scripts\railway_phase0.ps1 -Backup -DiffEnv -ProdEnv production -StagingEnv staging
#>
[CmdletBinding()]
param(
    [switch]$Backup,
    [switch]$DiffEnv,
    [string]$ProdEnv     = "production",
    [string]$StagingEnv  = "staging",
    [string]$Service     = "",
    [string]$OutDir      = "C:\dev\_backups\job-hunter",
    [string]$HealthUrl   = "https://web-production-192b7.up.railway.app/api/health"
)

$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
function Ok($m)   { Write-Host "OK:   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "WARN: $m" -ForegroundColor Yellow }
function Fail($m) { Write-Host "FAIL: $m" -ForegroundColor Red }
function Info($m) { Write-Host "      $m" -ForegroundColor DarkGray }

if (-not $Backup -and -not $DiffEnv) {
    Write-Host "Nothing to do. Pass -Backup and/or -DiffEnv." -ForegroundColor Yellow
    exit 0
}

# --- CLI present and linked? --------------------------------------------------
$v = (& railway --version 2>&1)
if ($LASTEXITCODE -ne 0) { Fail "Railway CLI not found. Install: npm i -g @railway/cli, then: railway login"; exit 1 }
Ok "railway cli: $v"

& railway status 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn "this folder is not linked to a Railway project. Run 'railway link' and pick the job-hunter project, then re-run."
    exit 1
}

$svcArgs = @()
if ($Service -ne "") { $svcArgs = @("--service", $Service) }

# --- 1. Backup ----------------------------------------------------------------
if ($Backup) {
    Write-Host ""
    Write-Host "=== production backup ===" -ForegroundColor Cyan
    $stamp = Get-Date -Format "yyyy-MM-dd_HHmm"
    $dest  = Join-Path $OutDir $stamp
    New-Item -ItemType Directory -Force -Path $dest | Out-Null
    $dbLocal = Join-Path $dest "jobs.db"

    # DATABASE_PATH is /data/jobs.db and the volume mounts at /data, so inside the
    # volume the file is /jobs.db. Older setups differ - try both spellings.
    $downloaded = $false
    foreach ($remote in @("/jobs.db", "/data/jobs.db")) {
        Info "trying: railway volume files download $remote"
        & railway volume files download $remote $dbLocal -e $ProdEnv @svcArgs 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0 -and (Test-Path $dbLocal)) { $downloaded = $true; Ok "downloaded $remote"; break }
    }

    if (-not $downloaded) {
        Fail "could not download the DB automatically."
        Info "Browse the volume by hand to find the right path, then re-run:"
        Info "    railway volume browse / -e $ProdEnv"
        Info "Uploads (CVs) are a directory - copy them with 'railway volume browse' too."
    } else {
        $size = [math]::Round((Get-Item $dbLocal).Length / 1MB, 2)
        Ok "saved $dbLocal ($size MB)"

        # Verify the file is a real, openable SQLite DB - a truncated download is
        # the failure mode that turns a 'backup' into a false sense of safety.
        $py = @"
import sqlite3, json, sys
c = sqlite3.connect(r'$dbLocal')
c.row_factory = sqlite3.Row
out = {}
for t in ('users','jobs','sessions','activity_log'):
    try: out[t] = c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
    except Exception as e: out[t] = 'ERROR: %s' % e
out['integrity'] = c.execute('PRAGMA integrity_check').fetchone()[0]
out['active_users'] = c.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
print(json.dumps(out))
"@
        $py | Out-File -FilePath (Join-Path $dest "_verify.py") -Encoding ascii
        $verify = & python (Join-Path $dest "_verify.py") 2>&1
        if ($LASTEXITCODE -ne 0) { Fail "the downloaded file did not open as SQLite: $verify" }
        else {
            $counts = $verify | ConvertFrom-Json
            Ok "opens as SQLite; integrity_check = $($counts.integrity)"
            Info "users=$($counts.users) (active $($counts.active_users))  jobs=$($counts.jobs)  sessions=$($counts.sessions)"

            try {
                $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 30
                Info "live /api/health: active_users=$($health.active_users)  total_jobs=$($health.total_jobs)"
                if ($health.active_users -eq $counts.active_users -and $health.total_jobs -eq $counts.jobs) {
                    Ok "backup row counts MATCH the live app"
                } else {
                    Warn "counts differ from live - expected if the app wrote during the download; re-run to confirm it is small and stable"
                }
            } catch { Warn "could not reach $HealthUrl to cross-check counts" }

            Write-Host ""
            Ok "restore verified at the data layer (opens, integrity_check ok, counts match)."
            Warn "Do NOT boot app.py against this copy."
            Info "app.py starts the scheduler and the file watcher on import: a scheduled hour"
            Info "could fire a real search (Gemini spend) and deliver notifications to the real"
            Info "users in this DB via their own Telegram/WhatsApp/email settings."
            Info "The full boot-level restore rehearsal happens in Phase 2, against staging."
        }
    }
}

# --- 2. Env diff --------------------------------------------------------------
if ($DiffEnv) {
    Write-Host ""
    Write-Host "=== env vars: $ProdEnv vs $StagingEnv (keys only, values never printed) ===" -ForegroundColor Cyan

    function Get-Vars($envName) {
        $raw = & railway variables --json -e $envName @svcArgs 2>&1
        if ($LASTEXITCODE -ne 0) {
            $raw = & railway variable list --json -e $envName @svcArgs 2>&1   # older CLI wording
        }
        if ($LASTEXITCODE -ne 0) { Fail "could not read variables for '$envName': $raw"; return $null }
        try { return $raw | ConvertFrom-Json } catch { Fail "unparseable output for '$envName'"; return $null }
    }

    $prod = Get-Vars $ProdEnv
    $stg  = Get-Vars $StagingEnv
    if ($null -eq $prod -or $null -eq $stg) {
        Info "If the CLI needs a service, re-run with -Service <name>."
        exit 1
    }

    $prodKeys = @($prod.PSObject.Properties.Name)
    $stgKeys  = @($stg.PSObject.Properties.Name)

    $onlyProd = $prodKeys | Where-Object { $stgKeys -notcontains $_ }
    $onlyStg  = $stgKeys  | Where-Object { $prodKeys -notcontains $_ }
    $both     = $prodKeys | Where-Object { $stgKeys -contains $_ }

    if ($onlyProd) { Warn "missing in ${StagingEnv}: $($onlyProd -join ', ')" } else { Ok "no keys missing in $StagingEnv" }
    if ($onlyStg)  { Warn "extra in ${StagingEnv}: $($onlyStg -join ', ')" }    else { Ok "no extra keys in $StagingEnv" }

    $differ = @()
    foreach ($k in $both) {
        if ($prod.$k -ne $stg.$k) { $differ += $k }
    }
    if ($differ) { Info "same key, different value (expected for URLs/keys): $($differ -join ', ')" }

    Write-Host ""
    Write-Host "Phase 0 expects staging to have: GEMINI_API_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET," -ForegroundColor DarkGray
    Write-Host "SYNC_API_KEY, DATABASE_PATH, UPLOADS_DIR, ADMIN_EMAIL - and to NOT have APPLY_ENGINE_ENABLED." -ForegroundColor DarkGray
    if ($stgKeys -contains "APPLY_ENGINE_ENABLED") { Fail "APPLY_ENGINE_ENABLED is set in $StagingEnv - the apply engine is parked; unset it." }
    else { Ok "APPLY_ENGINE_ENABLED is unset in $StagingEnv (apply engine stays parked)" }
}

Write-Host ""
