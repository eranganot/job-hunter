<#
    smoke.ps1 - prove a deploy is alive, on the expected schema, and still
    enforcing its auth gates. Supersedes smoke_phase0.ps1.

    Read-only. Sends no writes, creates no users, submits no applications.
    Safe to run against production.

    Works on Windows PowerShell 5.1 and PowerShell 7+ (uses .NET HttpWebRequest
    rather than Invoke-WebRequest -SkipHttpErrorCheck, which is 7-only).

    Usage:
        .\scripts\smoke.ps1                                 # production
        .\scripts\smoke.ps1 -BaseUrl https://web-staging-8e79.up.railway.app -SkipLocal
        .\scripts\smoke.ps1 -ExpectedSchema 3               # pin the schema version
#>
[CmdletBinding()]
param(
    [string]$BaseUrl = "https://web-production-192b7.up.railway.app",
    [int]$ExpectedSchema = 0,          # 0 = read it from local migrations.py
    # Which engine the box is supposed to be serving. "" = do not care, but the
    # refusal check below still runs. Pin it to "postgres" during a cutover so a
    # silent fallback to SQLite fails the smoke instead of passing it.
    [ValidateSet("", "sqlite", "postgres")]
    [string]$ExpectBackend = "",
    # Railway swaps containers on a variable change; for a minute or so the OLD
    # build still answers. Smoking that build and believing the result is how
    # the staging Postgres flip first read as a failure (2026-09-07). Pass this
    # only when you deliberately mean to smoke a box that is not on local HEAD.
    [switch]$AllowStaleDeploy,
    [switch]$SkipLocal
)

# Native commands (python, git) write to stderr for harmless warnings; under
# 'Stop' PowerShell 5.1 turns that into a terminating error. Judge by exit code.
$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$BaseUrl = $BaseUrl.TrimEnd("/")
$script:pass = 0
$script:fail = 0

# Fail fast on an unusable -BaseUrl. Without this, a placeholder like
# https://<staging-url> produces 15 identical "hostname could not be parsed"
# failures that look like the app is down when nothing was ever requested.
if ($BaseUrl -match "[<>]") {
    Write-Host ""
    Write-Host "FAIL: -BaseUrl is still a placeholder: $BaseUrl" -ForegroundColor Red
    Write-Host "      Get the real URL for the linked service with:" -ForegroundColor DarkGray
    Write-Host "          railway domain" -ForegroundColor White
    Write-Host "      (run 'railway service' first if it says no service is linked)" -ForegroundColor DarkGray
    Write-Host ""
    exit 2
}
try { $null = [System.Uri]::new($BaseUrl) } catch {
    Write-Host ""
    Write-Host "FAIL: -BaseUrl is not a valid URL: $BaseUrl" -ForegroundColor Red
    Write-Host "      Expected something like https://job-hunter-staging.up.railway.app" -ForegroundColor DarkGray
    Write-Host ""
    exit 2
}

function Check($name, [scriptblock]$test) {
    try {
        $result = & $test
        if ($result -eq $true) { Write-Host ("PASS  " + $name) -ForegroundColor Green; $script:pass++ }
        else { Write-Host ("FAIL  " + $name + "  -> " + $result) -ForegroundColor Red; $script:fail++ }
    } catch {
        Write-Host ("FAIL  " + $name + "  -> " + $_.Exception.Message) -ForegroundColor Red
        $script:fail++
    }
}

# Status code + Location + body, without following redirects and without
# throwing on 3xx/4xx/5xx. Works on PS 5.1 and 7.
function Get-Status($url) {
    $req = [System.Net.HttpWebRequest]::Create($url)
    $req.AllowAutoRedirect = $false
    $req.Timeout = 30000
    $req.UserAgent = "job-hunter-smoke/1.0"
    $resp = $null
    try {
        $resp = $req.GetResponse()
    } catch [System.Net.WebException] {
        $resp = $_.Exception.Response
        if ($null -eq $resp) { throw }
    }
    $code = [int]$resp.StatusCode
    $loc  = $resp.Headers["Location"]
    $body = ""
    try {
        $stream = $resp.GetResponseStream()
        $reader = New-Object System.IO.StreamReader($stream)
        $body   = $reader.ReadToEnd()
        $reader.Close()
    } catch { }
    $resp.Close()
    return @{ Code = $code; Location = $loc; Body = $body }
}

Write-Host ""
Write-Host "=== smoke: $BaseUrl ===" -ForegroundColor Cyan
Write-Host ""

# --- Local suite --------------------------------------------------------------
if (-not $SkipLocal) {
    Write-Host "-- local --" -ForegroundColor Cyan
    Check "python compiles the app" {
        & python -m py_compile app.py db.py auth.py apply_engine.py ai_analysis.py 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) { $true } else { "py_compile exit $LASTEXITCODE" }
    }
    Check "full test suite green" {
        # --tb=line gives one line of REASON per failure. Without it a failing
        # run prints only the test name, which is not enough to diagnose from.
        $out  = (& python -m pytest -q --tb=line 2>&1 | Out-String)
        $code = $LASTEXITCODE
        $summary = ($out -split "`r?`n" | Where-Object { $_ -match "passed|failed|error" } | Select-Object -Last 1)
        if ($code -eq 0) { Write-Host ("      " + $summary.Trim()) -ForegroundColor DarkGray; return $true }
        # On failure, name the tests - a count alone is not actionable.
        $lines = $out -split "`r?`n"
        foreach ($f in ($lines | Where-Object { $_ -match "^(FAILED|ERROR) " })) {
            Write-Host ("      " + $f.Trim()) -ForegroundColor Yellow
        }
        # The --tb=line reasons: "/path/to/test.py:123: AssertionError: ..."
        foreach ($r in ($lines | Where-Object { $_ -match "^[A-Za-z]:?[\\/].*\.py:\d+:" } | Select-Object -First 5)) {
            Write-Host ("      " + $r.Trim()) -ForegroundColor DarkYellow
        }
        return $summary.Trim()
    }
    Check "route harness present" {
        if (Test-Path "tests\test_routes.py") { $true } else { "tests\test_routes.py missing" }
    }
    Write-Host ""
}

# --- Reachability -------------------------------------------------------------
Write-Host "-- reachability --" -ForegroundColor Cyan
$script:health = $null
Check "GET /api/health is 200 and well-formed" {
    $r = Get-Status "$BaseUrl/api/health"
    if ($r.Code -ne 200) { return "status $($r.Code)" }
    $script:health = $r.Body | ConvertFrom-Json
    if ($script:health.status -ne "ok") { return "status field = $($script:health.status)" }
    Write-Host ("      users=" + $script:health.active_users + "  jobs=" + $script:health.total_jobs + "  schema=" + $script:health.schema_version + "  backend=" + $script:health.db_backend) -ForegroundColor DarkGray
    $true
}
# 2026-09-07: production ran for a while on an empty Postgres and reported
# "ok" the whole time, because nothing in the smoke looked at WHICH database
# was behind the app. These two checks are that gap closed.
Check "the box is running the build we think it is" {
    if ($null -eq $script:health) { return "no health payload" }
    $deployed = $script:health.commit
    if ([string]::IsNullOrEmpty($deployed)) {
        Write-Host "      (box reports no commit - predates this check)" -ForegroundColor DarkGray
        return $true
    }
    $local = (& git rev-parse --short=7 HEAD 2>&1 | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { Write-Host "      (no local git: $local)" -ForegroundColor DarkGray; return $true }
    Write-Host ("      deployed=" + $deployed + "  local HEAD=" + $local) -ForegroundColor DarkGray
    if ($deployed -eq $local) { $true }
    elseif ($AllowStaleDeploy) {
        Write-Host "      differs, allowed by -AllowStaleDeploy" -ForegroundColor DarkGray
        $true
    }
    else {
        "deployed build is $deployed but local HEAD is $local - either the deploy has not finished " +
        "(wait and re-run) or you are smoking a different build. -AllowStaleDeploy to proceed anyway."
    }
}
Check "the box is not falling back after refusing its configured database" {
    if ($null -eq $script:health) { return "no health payload" }
    $refused = $script:health.db_backend_refused
    if ([string]::IsNullOrEmpty($refused)) { $true }
    else { "the app refused its Postgres target and is serving SQLite instead: $refused" }
}
Check "the box is serving the engine it is supposed to" {
    if ($null -eq $script:health) { return "no health payload" }
    if ($ExpectBackend -eq "") {
        Write-Host "      (not pinned - pass -ExpectBackend to assert)" -ForegroundColor DarkGray
        return $true
    }
    $got = $script:health.db_backend
    if ($got -eq $ExpectBackend) {
        if ($got -eq "postgres") {
            Write-Host ("      pool: " + ($script:health.db_pool.PSObject.Properties.Name -join ", ")) -ForegroundColor DarkGray
        }
        $true
    } else { "db_backend=$got, expected $ExpectBackend" }
}
Check "deployed schema is at the expected migration version" {
    if ($null -eq $script:health) { return "no health payload" }
    $want = $ExpectedSchema
    if ($want -le 0) {
        # Read the highest version from the local migrations.py so this never
        # needs editing when a migration is added.
        $v = (& python -c "import migrations; print(max(v for v,_n,_f in migrations.MIGRATIONS))" 2>&1 | Out-String).Trim()
        if ($v -match '^\d+$') { $want = [int]$v } else { return "could not read the expected version from migrations.py ($v)" }
    }
    $got = [int]$script:health.schema_version
    if ($got -eq $want) { $true }
    elseif ($got -eq 0) { "deployed box reports schema_version 0 - migrations did not run there" }
    else { "deployed schema_version $got, expected $want - the deploy is behind" }
}
Check "GET /login renders" {
    $r = Get-Status "$BaseUrl/login"
    if ($r.Code -eq 200 -and $r.Body -match "(?i)<!doctype html>") { $true } else { "status $($r.Code)" }
}
# The PWA shell at /app is behind require_auth (app.py ~5538); only the static
# files under /app/* are public. Verified against production 2026-09-07.
Check "GET /app shell requires a session" {
    $r = Get-Status "$BaseUrl/app"
    if ($r.Code -eq 302 -and "$($r.Location)" -match "/login") { $true }
    else { "status $($r.Code) location $($r.Location)" }
}
Check "GET /app/manifest.webmanifest serves (bundle is deployed)" {
    $r = Get-Status "$BaseUrl/app/manifest.webmanifest"
    if ($r.Code -eq 200) { $true } else { "status $($r.Code)" }
}
Check "GET /app/index.html serves the built shell" {
    $r = Get-Status "$BaseUrl/app/index.html"
    if ($r.Code -eq 200 -and $r.Body -match "(?i)<!doctype html>") { $true }
    else { "status $($r.Code) - web_bundle may be missing from the deploy" }
}
Write-Host ""

# --- Auth gates (the part that matters before public signup) ------------------
Write-Host "-- auth gates (anonymous) --" -ForegroundColor Cyan
# NOTE: no .GetNewClosure() here. A closure is bound to a NEW dynamic-module
# scope with its own function table, so script-level helpers (Get-Status) are
# invisible inside it - that is exactly how these checks failed on the first run.
# Check runs the block immediately in the same iteration, so $p is already the
# current value and no closure is needed.
foreach ($p in @("/dashboard", "/settings", "/onboarding", "/admin")) {
    Check "GET $p redirects to /login" {
        $r = Get-Status "$BaseUrl$p"
        if ($r.Code -eq 302 -and "$($r.Location)" -match "/login") { $true }
        else { "status $($r.Code) location $($r.Location)" }
    }
}
foreach ($p in @("/api/me", "/api/jobs", "/api/stats", "/api/activity")) {
    Check "GET $p is not served anonymously" {
        $r = Get-Status "$BaseUrl$p"
        if ($r.Code -eq 302) { $true } else { "status $($r.Code)" }
    }
}
Check "GET /api/admin/users leaks nothing anonymously" {
    $r = Get-Status "$BaseUrl/api/admin/users"
    if ($r.Code -eq 200) { return "served 200 anonymously" }
    if ("$($r.Body)" -match "@") { return "response contained an email address" }
    $true
}
Write-Host ""

# --- Liveness vs usefulness ---------------------------------------------------
Write-Host "-- database and worker --" -ForegroundColor Cyan
Check "the database answers a real query" {
    if ($null -eq $script:health) { return "no health payload" }
    $db = $script:health.db_check
    if ($null -eq $db) { return "no 'db_check' in /api/health - this build predates it" }
    if ($db.ok) {
        Write-Host ("      round trip: " + $db.ms + "ms") -ForegroundColor DarkGray
        $true
    } else { "the box is up but its database is not answering: " + $db.error }
}
Check "no job is claimed and abandoned" {
    # A worker that died mid-job leaves its row RUNNING forever, and from
    # outside that is indistinguishable from an idle worker - both are just a
    # number. The age of the oldest claim is what tells them apart.
    if ($null -eq $script:health) { return "no health payload" }
    $w = $script:health.worker
    if ($null -eq $w) { return "no 'worker' in /api/health - this build predates it" }
    if ($w.unavailable) { return "worker health unavailable: " + $w.unavailable }
    Write-Host ("      worker running: " + $w.running + ", claimed: " + $w.claimed +
                ", oldest claim: " + $(if ($null -eq $w.oldest_claim_age_s) { "none" } else { "$($w.oldest_claim_age_s)s" })) -ForegroundColor DarkGray
    if ($w.stuck) { "a claimed job has gone past the stuck threshold with no heartbeat" }
    else { $true }
}
Write-Host ""

# --- Cost guardrails (Gemini spend ceiling + daily run caps) ------------------
Write-Host "-- cost guardrails --" -ForegroundColor Cyan
Check "health reports today's Gemini spend" {
    if ($null -eq $script:health) { return "no health payload" }
    $llm = $script:health.llm
    if ($null -eq $llm) {
        return "no 'llm' block in /api/health - gemini.py is not wired on this box, so nothing is counting Gemini calls"
    }
    Write-Host ("      day:    " + $llm.day) -ForegroundColor DarkGray
    Write-Host ("      spent:  " + $llm.global.calls + " calls, " + $llm.global.tokens + " tokens") -ForegroundColor DarkGray
    Write-Host ("      limits: " + $llm.limits.global_calls + " calls/day global, " + $llm.limits.user_calls + " calls/day per user") -ForegroundColor DarkGray
    $true
}
Check "the spend ceiling is actually enforcing" {
    if ($null -eq $script:health -or $null -eq $script:health.llm) { return "no llm payload" }
    if ([int]$script:health.llm.limits.enforce -eq 1) { $true }
    else { "JH_LLM_ENFORCE=0 on this box - spend is counted and alerted but nothing is blocked" }
}
Check "at least one ceiling is finite" {
    # All-zero means every ceiling is switched off, which reads identical to
    # 'guardrails shipped' from the outside and is the failure this catches.
    if ($null -eq $script:health -or $null -eq $script:health.llm) { return "no llm payload" }
    $l = $script:health.llm.limits
    $finite = @($l.global_calls, $l.global_tokens, $l.user_calls, $l.user_tokens) |
        Where-Object { [int]$_ -gt 0 }
    if ($finite.Count -gt 0) { $true }
    else { "every JH_LLM_* ceiling is 0 - nothing would ever be blocked" }
}
Write-Host ""
Write-Host "      NOTE: the ceilings above shipped generous on purpose - no Gemini" -ForegroundColor DarkGray
Write-Host "      call in this app had ever been counted before the llm_usage ledger." -ForegroundColor DarkGray
Write-Host "      Re-run this after a week and set JH_LLM_GLOBAL_CALLS from the real" -ForegroundColor DarkGray
Write-Host "      number; it is a Railway variable, no deploy needed." -ForegroundColor DarkGray
Write-Host ""

# --- Apply engine must stay off (parked) --------------------------------------
Write-Host "-- parked-state check --" -ForegroundColor Cyan
Check "health reports the last apply (engine expected parked)" {
    if ($null -eq $script:health) { return "no health payload - /api/health check above failed" }
    Write-Host ("      last_apply:  " + $script:health.last_apply.detail + "  (" + $script:health.last_apply.date + ")") -ForegroundColor DarkGray
    Write-Host ("      last_search: " + $script:health.last_search.detail + "  (" + $script:health.last_search.date + ")") -ForegroundColor DarkGray
    $true
}
Write-Host ""

# --- Result -------------------------------------------------------------------
Write-Host ("=== " + $script:pass + " passed, " + $script:fail + " failed ===") -ForegroundColor Cyan
if ($script:fail -gt 0) { exit 1 }
exit 0
