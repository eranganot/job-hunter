# encryption_status.ps1 - where the credential encryption actually stands.
#
# Run this BEFORE you start and AFTER each step. It reads /api/health and
# tells you the next action rather than making you interpret JSON.
#
# It only reads. It sets nothing, writes nothing, and needs no credentials.
#
#   .\scripts\encryption_status.ps1
#   .\scripts\encryption_status.ps1 -Url https://<staging-domain>

param(
  [string]$Url = "https://web-production-192b7.up.railway.app"
)

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "=== reading $Url/api/health ===" -ForegroundColor Cyan

try {
  $h = Invoke-RestMethod -Uri "$Url/api/health" -TimeoutSec 30
} catch {
  Write-Host "[FAIL] could not reach the health endpoint: $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "       If the app is up but this 404s, the domain is wrong - check Railway > web > Settings > Domains."
  exit 1
}

$backend   = $h.db_backend
$encrypted = $h.credentials_encrypted
$keyprint  = $h.credentials_key
$refused   = $h.db_backend_refused

Write-Host ""
Write-Host ("  commit ............... {0}" -f $h.commit)
Write-Host ("  db_backend ........... {0}" -f $backend)
if ($refused) {
  Write-Host ("  db_backend_refused ... {0}" -f $refused) -ForegroundColor Red
}
Write-Host ("  schema_version ....... {0}" -f $h.schema_version)
Write-Host ("  active_users ......... {0}" -f $h.active_users)
Write-Host ("  total_jobs ........... {0}" -f $h.total_jobs)
Write-Host ("  credentials_encrypted  {0}" -f $encrypted)
Write-Host ("  credentials_key ...... {0}" -f $(if ($keyprint) { $keyprint } else { "(none)" }))
Write-Host ""

# ---------------------------------------------------------------- which DB
if ($backend -eq "postgres") {
  $dbArg  = ""   # the container's own DATABASE_URL already points at it
  $dbNote = "no target flag needed - the container's DATABASE_URL already points at it"
  $dbName = "Postgres"
} else {
  $dbArg  = " --sqlite /data/jobs.db"
  $dbNote = "the --sqlite flag is required - the live data is on the /data volume"
  $dbName = "SQLite on the /data volume"
}
Write-Host "The live database is: $dbName" -ForegroundColor Yellow
Write-Host "  ($dbNote)"
Write-Host ""

# ---------------------------------------------------------------- verdict
Write-Host "=== what to do next ===" -ForegroundColor Cyan
Write-Host ""

if (-not $encrypted) {
  Write-Host "STEP 1 is not done: no JH_ENCRYPTION_KEY on this environment." -ForegroundColor Red
  Write-Host ""
  Write-Host "  a) Generate a key on THIS machine (never reuse a value from a chat):"
  Write-Host ""
  Write-Host '     python -c "import secrets;print(secrets.token_urlsafe(48))"' -ForegroundColor Green
  Write-Host ""
  Write-Host "  b) Railway dashboard > this project > the *web* service > Variables"
  Write-Host "     > New Variable.  Name: JH_ENCRYPTION_KEY   Value: <what step (a) printed>"
  Write-Host "     Save. The service redeploys itself."
  Write-Host ""
  Write-Host "     It must go on the *web* service, not Postgres. The app reads it;"
  Write-Host "     the database does not."
  Write-Host ""
  Write-Host "  c) Re-run this script. credentials_encrypted should flip to True"
  Write-Host "     and credentials_key should show a fingerprint."
  Write-Host ""
  exit 0
}

Write-Host "STEP 1 is done - a key is set (fingerprint $keyprint)." -ForegroundColor Green
Write-Host ""
Write-Host "STEP 2 - convert the rows that are already stored." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Running encrypt_credentials.py directly from your laptop CANNOT work:"
Write-Host "  the key is on the 'web' service and the reachable DB URL is on 'Postgres',"
Write-Host "  so no single 'railway run --service X' supplies both. It fails with"
Write-Host "  'JH_ENCRYPTION_KEY is not set', which is misleading - it IS set, on web."
Write-Host ""
Write-Host "  Use the wrapper. It reads both and runs the encryptor in one process:"
Write-Host ""
Write-Host "     .\scripts\run_encrypt_credentials.ps1" -ForegroundColor Green
Write-Host "     .\scripts\run_encrypt_credentials.ps1 -Apply" -ForegroundColor Green
Write-Host ""
Write-Host "  (Equivalent, if you prefer the dashboard: web service > Console, then)"
Write-Host ""
Write-Host "     python scripts/encrypt_credentials.py --dry-run$dbArg" -ForegroundColor Green
Write-Host ""
Write-Host "  Read the output. It lists which users would change and writes nothing."
Write-Host "  Confirm the line 'key fingerprint' matches $keyprint above."
Write-Host "  Then, for real:"
Write-Host ""
Write-Host "     python scripts/encrypt_credentials.py$dbArg" -ForegroundColor Green
Write-Host ""
Write-Host "  PROOF IT WORKED - run the same command a second time. It must say:"
Write-Host "     [DONE] 0 value(s) encrypted and verified; N already encrypted."
Write-Host ""
Write-Host "  Do NOT use /api/health to check step 2. credentials_encrypted is"
Write-Host "  crypto.available() - it reports that a KEY EXISTS, not that the rows"
Write-Host "  were converted. It reads True right now with step 2 still undone."
Write-Host ""
Write-Host "  Then repeat the whole thing against staging."
Write-Host ""
