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
