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
    [string]$VolumeName  = "",
    [switch]$SkipUploads,
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
    # Which environment is the CLI actually linked to? Production and a seeded
    # staging can hold identical row counts, so counts cannot tell them apart -
    # and mislabelling a staging copy as a production backup is how someone ends
    # up believing they have a safety net they do not have.
    $statusText = (& railway status 2>&1 | Out-String)
    if ($statusText -match "(?im)^\s*Environment:\s*(.+?)\s*$") { $linkedEnv = $Matches[1] } else { $linkedEnv = "unknown" }
    Write-Host ("      linked environment: " + $linkedEnv) -ForegroundColor Cyan
    if ($linkedEnv -notmatch "(?i)^prod") {
        Warn ("this is NOT production - the copy will be labelled '" + $linkedEnv + "'")
        if (-not $PSBoundParameters.ContainsKey("HealthUrl")) {
            Info "skipping the live /api/health cross-check (it points at production by default)."
            Info "Pass -HealthUrl <this environment's url> to compare against the right app."
            $HealthUrl = ""
        }
    }

    $stamp = (Get-Date -Format "yyyy-MM-dd_HHmm") + "_" + ($linkedEnv -replace "[^A-Za-z0-9]", "")
    $dest  = Join-Path $OutDir $stamp
    New-Item -ItemType Directory -Force -Path $dest | Out-Null

    # Preflight: does this CLI even have the volume commands, and what is mounted?
    # (Never hide the CLI's own output - the first version of this script did, and
    # that is precisely what made the first failure undiagnosable.)
    Write-Host "-- volume preflight --" -ForegroundColor DarkGray
    $vhelp = (& railway volume --help 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $vhelp -match "(?i)unrecognized|unknown|invalid") {
        Fail "this Railway CLI has no 'volume' command."
        Info "You are on an older CLI. Upgrade and re-run:"
        Info "    npm i -g @railway/cli@latest"
        Info "Then: railway volume list"
        exit 1
    }
    # Facts established 2026-09-07 against this project:
    #  - volume subcommands reject the global -e/--service flags; they use the link.
    #  - non-interactive calls REQUIRE --volume ("Volume must be specified via
    #    --volume in non-interactive mode").
    #  - this project has FOUR volumes, and TWO of them mount /data:
    #    job-hunter-volume (service 'job-hunter') and web-volume (service 'web').
    #    Which one holds the live DB is decided below by evidence, not assumption.
    $vlist = (& railway volume list 2>&1 | Out-String)
    Write-Host $vlist -ForegroundColor DarkGray

    # Parse "Volume: / Attached to: / Mount path:" blocks.
    $vols = @()
    $cur = $null
    foreach ($line in ($vlist -split "`r?`n")) {
        if ($line -match "^\s*Volume:\s*(.+?)\s*$")      { if ($cur) { $vols += $cur }; $cur = [pscustomobject]@{ Name=$Matches[1]; Service=""; Mount="" } }
        elseif ($line -match "^\s*Attached to:\s*(.+?)\s*$") { if ($cur) { $cur.Service = $Matches[1] } }
        elseif ($line -match "^\s*Mount path:\s*(.+?)\s*$")  { if ($cur) { $cur.Mount   = $Matches[1] } }
    }
    if ($cur) { $vols += $cur }

    if ($VolumeName -ne "") {
        $candidates = $vols | Where-Object { $_.Name -eq $VolumeName }
        if (-not $candidates) { Fail "no volume named '$VolumeName' in this environment."; exit 1 }
    } else {
        # App volumes only: skip the database engines' own storage.
        $candidates = $vols | Where-Object { $_.Mount -eq "/data" -and $_.Name -notmatch "(?i)postgres|redis" }
    }
    if (-not $candidates) { Fail "no candidate app volume found - pass -VolumeName explicitly."; exit 1 }
    Info ("candidate volumes: " + (($candidates | ForEach-Object { $_.Name + " (" + $_.Service + ")" }) -join ", "))

    # Pull jobs.db from EVERY candidate, then let row counts say which one is live.
    $found = @()
    $sawNoSshKey = $false
    $sawSshAuthFail = $false
    foreach ($v in $candidates) {
        Write-Host ""
        Info ("--- volume: " + $v.Name + "  service: " + $v.Service + " ---")
        # Flag placement per Railway's CLI reference: -v/--volume goes on the
        # 'files' GROUP, before the subcommand. Putting it after the subcommand
        # gives "unexpected argument '--volume' found" while omitting it gives
        # "Volume must be specified via --volume in non-interactive mode".
        $listing = (& railway volume files --volume $v.Name list / 2>&1 | Out-String)
        Write-Host $listing -ForegroundColor DarkGray

        $target = Join-Path $dest ($v.Name + "-jobs.db")
        $out = (& railway volume files --volume $v.Name download /jobs.db $target 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0 -and (Test-Path $target)) {
            Ok ("downloaded /jobs.db from " + $v.Name)
            $found += [pscustomobject]@{ Volume=$v.Name; Service=$v.Service; Path=$target }
        } else {
            if ($out.Trim()) { Write-Host ("      -> " + $out.Trim()) -ForegroundColor DarkGray }
            if (($out + $listing) -match "(?i)No SSH keys found") { $sawNoSshKey = $true }
            if (($out + $listing) -match "(?i)SSH authentication failed") { $sawSshAuthFail = $true }
            Warn ("no /jobs.db on " + $v.Name)
        }

        # CVs live in /uploads. 'files download' handles directories, so a real
        # backup includes them - a DB without the CVs is not a restorable backup.
        $ups = (& railway volume files --volume $v.Name list /uploads 2>&1 | Out-String)
        if ($LASTEXITCODE -eq 0 -and $ups.Trim()) {
            Info ("uploads/ on " + $v.Name + ":")
            Write-Host $ups -ForegroundColor DarkGray
            if (-not $SkipUploads) {
                $upDir = Join-Path $dest ($v.Name + "-uploads")
                Info ("downloading /uploads -> " + $upDir)
                $uout = (& railway volume files --volume $v.Name download /uploads $upDir 2>&1 | Out-String)
                if ($LASTEXITCODE -eq 0 -and (Test-Path $upDir)) {
                    $n = @(Get-ChildItem -Recurse -File $upDir).Count
                    Ok ("uploads backed up: " + $n + " file(s)")
                } else {
                    Warn "uploads download failed:"
                    if ($uout.Trim()) { Write-Host ("      -> " + $uout.Trim()) -ForegroundColor DarkGray }
                }
            }
        }
    }

    if ($found.Count -eq 0) {
        # The CLI's volume file access runs over SSH and enumerates ~/.ssh/*.pub.
        # It does NOT consult ssh-agent, so "No SSH keys found" is purely local -
        # it happens before any network call.
        if ($sawSshAuthFail) {
            Fail "the SSH key on this machine is not registered with Railway."
            Info "Register it, then re-run this script:"
            Info ""
            Info "    railway ssh keys add"
            Info ""
            Info "It picks up ~/.ssh/*.pub. Verify with: railway ssh keys list"
            exit 1
        }
        if ($sawNoSshKey) {
            Fail "Railway's volume file access needs an SSH key on this machine."
            Info "Generate one (Windows 10+ ships OpenSSH), then re-run this script:"
            Info ""
            Info "    ssh-keygen -t ed25519 -C `"railway`" -f `"$env:USERPROFILE\.ssh\id_ed25519`""
            Info ""
            Info "Press Enter at the prompts to accept the defaults. The CLI looks only in"
            Info "~/.ssh for *.pub files - an agent-held key (1Password, forwarded agent) is"
            Info "invisible to it. If Railway then rejects the key, add the contents of"
            Info "id_ed25519.pub to your Railway account settings and try again."
            exit 1
        }
        Fail "no /jobs.db found on any candidate volume - the CLI output above says why."
        Info "Next, by hand (note the flag goes on 'files', before the subcommand):"
        Info "    railway volume files --volume job-hunter-volume list /"
        Info "    railway volume files --volume web-volume list /"
        Info "  or interactively: railway volume browse / --volume <name>"
        Info "  or: railway ssh    then: ls -la /data"
    } else {
        if ($found.Count -gt 1) {
            Warn ("jobs.db exists on " + $found.Count + " volumes - live /api/health decides which one production uses.")
        }

        # What the live app reports, to compare every candidate against.
        $health = $null
        if ($HealthUrl -ne "") {
            try { $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 30 } catch { Warn "could not reach $HealthUrl to cross-check counts" }
        }
        if ($health) { Info "live /api/health: active_users=$($health.active_users)  total_jobs=$($health.total_jobs)" }

        # Verify each download is a real, openable SQLite DB - a truncated file is
        # the failure mode that turns a 'backup' into a false sense of safety.
        $verifyScript = Join-Path $dest "_verify.py"
        @"
import sqlite3, json, sys
c = sqlite3.connect(sys.argv[1])
out = {}
for t in ('users','jobs','sessions','activity_log'):
    try: out[t] = c.execute('SELECT COUNT(*) FROM ' + t).fetchone()[0]
    except Exception as e: out[t] = 'ERROR: %s' % e
try:
    out['active_users'] = c.execute("SELECT COUNT(*) FROM users WHERE is_active=1").fetchone()[0]
    out['integrity'] = c.execute('PRAGMA integrity_check').fetchone()[0]
    out['last_job'] = (c.execute('SELECT MAX(found_date) FROM jobs').fetchone()[0] or '')
except Exception as e:
    out['integrity'] = 'ERROR: %s' % e
print(json.dumps(out))
"@ | Out-File -FilePath $verifyScript -Encoding ascii

        $live = @()
        foreach ($f in $found) {
            Write-Host ""
            $size = [math]::Round((Get-Item $f.Path).Length / 1MB, 2)
            Info ("--- " + $f.Volume + " (service " + $f.Service + ")  " + $size + " MB ---")
            $verify = & python $verifyScript $f.Path 2>&1
            if ($LASTEXITCODE -ne 0) { Fail "did not open as SQLite: $verify"; continue }
            $counts = $verify | ConvertFrom-Json
            Ok "opens as SQLite; integrity_check = $($counts.integrity)"
            Info "users=$($counts.users) (active $($counts.active_users))  jobs=$($counts.jobs)  sessions=$($counts.sessions)  newest job=$($counts.last_job)"

            if ($health -and $health.active_users -eq $counts.active_users -and $health.total_jobs -eq $counts.jobs) {
                Ok ("MATCHES the live app at " + $HealthUrl + " -> " + $f.Volume + " is the volume it is using")
                $live += $f.Volume
            } elseif ($health) {
                Warn ("does NOT match live (" + $health.active_users + " users / " + $health.total_jobs + " jobs) - stale or a different service's data")
            }
        }

        Write-Host ""
        if ($live.Count -eq 1) {
            Ok ("[" + $linkedEnv + "] DB backed up from " + $live[0] + " -> " + $dest)
        } elseif ($live.Count -eq 0) {
            Warn "no downloaded copy matches the live counts. Either the app wrote during the download (re-run and see if it is stable) or production reads a volume this script did not try."
        } else {
            Warn ("more than one volume matches live counts: " + ($live -join ", ") + " - inspect before trusting either.")
        }

        if ($found.Count -gt 1) {
            Write-Host ""
            Warn "This project has two app services with a /data volume ('job-hunter' and 'web')."
            Info "Only one of them serves the live domain. Before Phase 2 touches the data layer,"
            Info "confirm which service the domain points at and whether the other is a stale leftover."
        }

        Write-Host ""
        Warn "Do NOT boot app.py against these copies."
        Info "app.py starts the scheduler and the file watcher on import: a scheduled hour"
        Info "could fire a real search (Gemini spend) and deliver notifications to the real"
        Info "users in this DB via their own Telegram/WhatsApp/email settings."
        Info "The full boot-level restore rehearsal happens in Phase 2, against staging."
        Write-Host ""
        Info "CVs are a directory (/uploads) - 'files download' handles single files only."
        Info "To back those up: railway volume browse /uploads --volume <name>"
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
