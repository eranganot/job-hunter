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

# Repo-relative files are resolved from THIS SCRIPT's location, never from the
# caller's working directory. Run from the wrong folder and a Test-Path guard
# answers false, so the checks below would skip themselves and report green -
# a gate that disappears when you are not standing in the right place is worse
# than no gate.
$script:RepoRoot     = Split-Path -Parent $PSScriptRoot
$script:LocalIndex   = Join-Path $script:RepoRoot "web_bundle/index.html"
$script:LocalSw      = Join-Path $script:RepoRoot "web_bundle/sw.js"
$script:ContractPath = Join-Path $PSScriptRoot "ui_contract.json"

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
Check "this box is the one running the scheduler" {
    if ($null -eq $script:health) { return "no health payload" }
    $sl = $script:health.scheduler_lock
    if ($null -eq $sl) { Write-Host "      (box predates the scheduler lock)" -ForegroundColor DarkGray; return $true }
    Write-Host ("      enabled=" + $sl.enabled + "  holds_lock=" + $sl.holds_lock) -ForegroundColor DarkGray
    # holds_lock is $null until the first scheduler tick, which is fine - it
    # only means the minute has not come round yet, not that the clock is dead.
    if ($sl.holds_lock -eq $false) {
        return "another instance holds the scheduler lock - this box will never fire a scheduled run"
    }
    $true
}
Check "a crash would reach someone" {
    if ($null -eq $script:health) { return "no health payload" }
    $er = $script:health.error_reporting
    if ($null -eq $er) { Write-Host "      (box predates error reporting)" -ForegroundColor DarkGray; return $true }
    Write-Host ("      " + $er.state) -ForegroundColor DarkGray
    # Not a failure while there are no public signups - but it is printed every
    # run so "nobody is watching for 500s" is never a surprise on launch day.
    if (-not $er.reporting) {
        Write-Host "      NOTE: unhandled exceptions are logged and nothing is alerted." -ForegroundColor DarkGray
    }
    $true
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
        # needs editing when a migration is added. sys.path is pointed at the
        # repo explicitly: this used to depend on the caller's directory, so
        # running the smoke from anywhere else reported "could not read the
        # expected version" - a tooling failure wearing a schema failure's name.
        $py = "import sys; sys.path.insert(0, r'" + $script:RepoRoot + "'); " +
              "import migrations; print(max(v for v,_n,_f in migrations.MIGRATIONS))"
        $v = (& python -c $py 2>&1 | Out-String).Trim()
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

# --- The DEPLOYED /app bundle -------------------------------------------------
#
# web_bundle/ is COMMITTED and Railway does not build the frontend, so whatever
# is in that directory is what users get. A stale or half-copied bundle deploys
# perfectly green: every route answers, the page renders, and the controls are
# simply absent. That is how "settings still cannot do X" survived two rounds
# of review.
#
# Two gates, deliberately different in kind:
#   1. ASSET HASH - complete, and needs no maintenance ever. Vite names each
#      asset by a hash of its CONTENT, so if the deployed file name equals the
#      one in this repo, the deployed bundle is byte-identical to this repo's.
#      That covers every feature, including ones nobody remembered to list.
#   2. UI CONTRACT - scripts/ui_contract.json, shared with tests/test_web_bundle.py.
#      Names each control, so a failure reads "Profile: LinkedIn URL is missing"
#      instead of "hash mismatch". ADD A LINE THERE WHEN YOU SHIP A CONTROL.
Write-Host "-- deployed /app bundle --" -ForegroundColor Cyan
$script:appJs   = ""
$script:appHref = ""
Check "the deployed shell serves a real script bundle" {
    $r = Get-Status "$BaseUrl/app/index.html"
    if ($r.Code -ne 200) { return "index.html status $($r.Code)" }
    $m = [regex]::Match($r.Body, 'src="[^"]*?(assets/[^"]+\.js)"')
    if (-not $m.Success) { return "index.html loads no script" }
    $script:appHref = $m.Groups[1].Value
    $j = Get-Status "$BaseUrl/app/$($script:appHref)"
    if ($j.Code -ne 200) { return "bundle $($script:appHref) status $($j.Code)" }
    if ($j.Body.Length -lt 100000) { return "bundle is only $($j.Body.Length) bytes - that is not a real build" }
    $script:appJs = $j.Body
    Write-Host ("      serving " + $script:appHref + "  (" + $script:appJs.Length + " bytes)") -ForegroundColor DarkGray
    $true
}
# The maintenance-free check. Content-hashed names mean equality is identity.
Check "the deployed bundle is the one in this repo" {
    if (-not $script:appHref) { return "no bundle was read" }
    if (-not (Test-Path $script:LocalIndex)) {
        return "no local web_bundle/index.html at $($script:LocalIndex) - cannot tell whether the deploy matches this repo"
    }
    $localHtml = Get-Content $script:LocalIndex -Raw
    $lm = [regex]::Match($localHtml, 'src="[^"]*?(assets/[^"]+\.js)"')
    if (-not $lm.Success) { return "local web_bundle/index.html loads no script" }
    $local = $lm.Groups[1].Value
    if ($local -eq $script:appHref) { $true }
    elseif ($AllowStaleDeploy) {
        Write-Host "      differs, allowed by -AllowStaleDeploy" -ForegroundColor DarkGray
        $true
    }
    else {
        "deployed $($script:appHref) but this repo has $local. Vite hashes by content, " +
        "so these differ only if the deployed bundle is not this one: either the deploy " +
        "has not finished, or web_bundle/ was not rebuilt and committed " +
        "(.\scripts\build_web.ps1), or you are smoking a different build."
    }
}
# A PWA caches itself. If VERSION did not move, a returning user keeps serving
# the OLD bundle out of their service worker cache and sees none of the change
# - with the server perfectly up to date. That failure is invisible from here
# unless it is checked explicitly.
Check "the service worker version moved with the bundle" {
    $r = Get-Status "$BaseUrl/app/sw.js"
    if ($r.Code -ne 200) { return "sw.js status $($r.Code)" }
    $dm = [regex]::Match($r.Body, 'VERSION\s*=\s*"([^"]+)"')
    if (-not $dm.Success) { return "deployed sw.js declares no VERSION" }
    $deployed = $dm.Groups[1].Value
    if (-not (Test-Path $script:LocalSw)) {
        return "no local web_bundle/sw.js at $($script:LocalSw) - cannot tell whether the cached bundle would update"
    }
    $lm = [regex]::Match((Get-Content $script:LocalSw -Raw), 'VERSION\s*=\s*"([^"]+)"')
    if (-not $lm.Success) { return "local web_bundle/sw.js declares no VERSION" }
    $local = $lm.Groups[1].Value
    Write-Host ("      deployed=" + $deployed + "  local=" + $local) -ForegroundColor DarkGray
    if ($deployed -eq $local) { $true }
    elseif ($AllowStaleDeploy) { Write-Host "      differs, allowed by -AllowStaleDeploy" -ForegroundColor DarkGray; $true }
    else { "deployed sw.js is $deployed, this repo says $local - returning users would be served the cached old bundle" }
}
# The readable gate. Same JSON the local suite reads, so there is one list.
if (-not (Test-Path $script:ContractPath)) {
    Check "the UI contract file exists" { "missing $($script:ContractPath) - the per-feature checks cannot run" }
} else {
    $contract = (Get-Content $script:ContractPath -Raw | ConvertFrom-Json).controls
    Write-Host ("      checking " + $contract.Count + " contracted controls from scripts/ui_contract.json") -ForegroundColor DarkGray
    foreach ($c in $contract) {
        Check "deployed bundle: $($c.what)" {
            if (-not $script:appJs) { return "no bundle was read" }
            if ($script:appJs.Contains($c.needle)) { $true }
            else { "'$($c.needle)' (shipped $($c.since)) is not in the deployed bundle" }
        }
    }
}
Check "deployed bundle ships no literal backslash-u to users" {
    if (-not $script:appJs) { return "no bundle was read" }
    # Regex literals in React's own code legitimately contain these; only
    # QUOTED strings reach a user's eyes. Mirrors tests/test_web_bundle.py.
    $bad = @()
    foreach ($q in [regex]::Matches($script:appJs, '"(?:[^"\\]|\\.)*"')) {
        if ($q.Value -match '\\u[0-9a-fA-F]{4}') { $bad += $q.Value.Substring(0, [Math]::Min(60, $q.Value.Length)) }
    }
    if ($bad.Count -eq 0) { $true } else { "$($bad.Count) string(s) would render literally, e.g. $($bad[0])" }
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
    if ($w.recovering) {
        # Past the threshold but inside the window the sweeper is allowed to
        # take. The app is already fixing this; saying FAIL here trains everyone
        # to ignore the smoke. Reported, not failed.
        Write-Host "      a claim is past the threshold and the sweeper has not reached it yet - recovering, not stuck" -ForegroundColor Yellow
    }
    if ($w.stuck) { "a claimed job is past the stuck threshold AND past the sweep window - the sweeper is not reaching it" }
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
Check "health carries the 14-day spend record" {
    # The ceiling has sat at its placeholder since the ledger shipped, because
    # usage() reports TODAY and a single day is not a peak - so the open item
    # could only ever be restated, never closed. llm_history is the evidence.
    if ($null -eq $script:health) { return "no health payload" }
    $h = $script:health.llm_history
    if ($null -eq $h) {
        return "no 'llm_history' in /api/health - this box predates the ceiling evidence (gemini.set_history_source not wired)"
    }
    if ($h.Count -ge 1 -and $null -ne $h[0].error) {
        return ("the ledger read failed: " + $h[0].error)
    }
    if ($h.Count -eq 0) {
        Write-Host "      no Gemini calls recorded in the last 14 days" -ForegroundColor DarkGray
        return $true
    }
    $peak = ($h | Measure-Object calls -Maximum).Maximum
    $peakDay = ($h | Sort-Object calls -Descending | Select-Object -First 1).day
    foreach ($d in ($h | Select-Object -First 5)) {
        Write-Host ("      " + $d.day + "  " + $d.calls + " calls, " + $d.tokens + " tokens, " + $d.users + " user(s)") -ForegroundColor DarkGray
    }
    Write-Host ("      peak:   " + $peak + " calls on " + $peakDay) -ForegroundColor DarkGray
    $ceiling = [int]$script:health.llm.limits.global_calls
    if ($peak -gt 0 -and $ceiling -gt 0) {
        $ratio = [math]::Round($ceiling / $peak)
        Write-Host ("      the global ceiling is " + $ratio + "x the busiest day - a useful ceiling is nearer 10x") -ForegroundColor DarkGray
    }
    $true
}
Write-Host ""
Write-Host "      NOTE: the ceilings shipped generous on purpose - no Gemini call in" -ForegroundColor DarkGray
Write-Host "      this app had ever been counted before the llm_usage ledger. The peak" -ForegroundColor DarkGray
Write-Host "      printed above is the number to set JH_LLM_GLOBAL_CALLS from (about" -ForegroundColor DarkGray
Write-Host "      10x it). It is a Railway variable - no deploy needed." -ForegroundColor DarkGray
Write-Host "      RAILWAY_RUNBOOK.md, Part 1." -ForegroundColor DarkGray
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
