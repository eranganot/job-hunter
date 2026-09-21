# run_encrypt_credentials.ps1 - run the credential encryptor FROM YOUR LAPTOP.
#
# WHY THIS EXISTS
#   encrypt_credentials.py needs two things that live in two different places:
#     - JH_ENCRYPTION_KEY   -> on the `web` service
#     - a reachable DB URL  -> DATABASE_PUBLIC_URL, on the `Postgres` service
#                              (web's DATABASE_URL is *.railway.internal, which
#                               does not resolve from a laptop)
#   So no single `railway run --service X` works, which is why every attempt so
#   far has failed with "JH_ENCRYPTION_KEY is not set". This reads both, puts
#   them in the environment of ONE process, and runs the script there.
#
#   Values are never printed and never touch your PowerShell history.
#
# USAGE
#   .\scripts\run_encrypt_credentials.ps1                    # dry run (default, writes nothing)
#   .\scripts\run_encrypt_credentials.ps1 -Apply             # the real run
#   .\scripts\run_encrypt_credentials.ps1 -Apply -Env Staging -Database jobhunter_staging
#
# Requires: railway CLI, logged in and linked to the Job-Hunter project.

param(
  [string]$Env      = "production",
  [string]$Database = "jobhunter_prod",
  [string]$WebService = "web",
  [string]$PgService  = "Postgres",
  [switch]$Apply
)

$ErrorActionPreference = "Stop"

function Info($m) { Write-Host "  $m" }
function Fail($m) { Write-Host "[FAIL] $m" -ForegroundColor Red; exit 1 }

Write-Host ""
Write-Host "=== encrypt stored credentials : $Env / $Database ===" -ForegroundColor Cyan
Write-Host ""

# --- read a service's variables as an object (values stay in memory) ----------
function Get-Vars($service) {
  $raw = & railway variables --json -e $Env --service $service 2>&1
  if ($LASTEXITCODE -ne 0) {
    $raw = & railway variable list --json -e $Env --service $service 2>&1   # older CLI wording
  }
  if ($LASTEXITCODE -ne 0) { Fail "could not read variables for service '$service': $raw" }
  try { return $raw | ConvertFrom-Json } catch { Fail "unparseable variable output for '$service'" }
}

Info "reading $WebService variables ..."
$web = Get-Vars $WebService
Info "reading $PgService variables ..."
$pg  = Get-Vars $PgService

$key = $web.JH_ENCRYPTION_KEY
if (-not $key) {
  Fail @"
JH_ENCRYPTION_KEY is not set on the '$WebService' service in '$Env'.

       Set it first:  Railway dashboard > $WebService > Variables > New Variable
       Generate the value locally:
           python -c "import secrets;print(secrets.token_urlsafe(48))"
"@
}

$url = $pg.DATABASE_PUBLIC_URL
if (-not $url) { $url = $pg.POSTGRES_PUBLIC_URL }
if (-not $url) {
  Fail @"
No DATABASE_PUBLIC_URL on the '$PgService' service in '$Env'.

       That is the TCP-proxy URL (junction.proxy.rlwy.net) - the only host
       reachable from a laptop. Railway > $PgService > Variables, or enable the
       TCP proxy under Settings > Networking.
"@
}

# never print either value - confirm presence and shape only
$hostOnly = ([uri]$url).Host
Info "key     : found on $WebService (value not shown)"
Info "database: $Database on $hostOnly"
Info ("mode    : {0}" -f $(if ($Apply) { "APPLY - writes" } else { "DRY RUN - writes nothing" }))
Write-Host ""

if ($Apply) {
  Write-Host "This will rewrite credential columns in $Database." -ForegroundColor Yellow
  $answer = Read-Host "Type YES to continue"
  if ($answer -ne "YES") { Write-Host "Nothing written."; exit 0 }
  Write-Host ""
}

# --- run the encryptor with both values present ------------------------------
$argv = @("scripts/encrypt_credentials.py", "--url", $url, "--database", $Database)
if (-not $Apply) { $argv += "--dry-run" }

$prevKey = $env:JH_ENCRYPTION_KEY
try {
  $env:JH_ENCRYPTION_KEY = $key
  & python @argv
  $rc = $LASTEXITCODE
} finally {
  $env:JH_ENCRYPTION_KEY = $prevKey   # do not leave it in this shell
}

Write-Host ""
if ($rc -ne 0) {
  Write-Host "[FAIL] the encryptor exited $rc - read its output above. Nothing is half-done:" -ForegroundColor Red
  Write-Host "       it verifies each row round-trips before counting it."
  exit $rc
}

if (-not $Apply) {
  Write-Host "Dry run only. Re-run with -Apply to write." -ForegroundColor Yellow
} else {
  Write-Host "Now prove it: run this script again WITHOUT -Apply." -ForegroundColor Green
  Write-Host "It must report 0 value(s) would be encrypted, N already were."
  Write-Host ""
  Write-Host "Do not use /api/health to check this. credentials_encrypted only"
  Write-Host "reports that a key exists, not that these rows were converted."
}
Write-Host ""
