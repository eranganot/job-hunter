<#
    build_web.ps1 - build the SPA, verify it, and publish it into web_bundle/.

    Railway does NOT build the frontend: web_bundle/ is committed and served
    as-is. So a broken build is not a red deploy, it is a live unstyled app.
    That has happened - web_bundle carried a 393-byte stylesheet containing the
    literal "@tailwind base;@tailwind components;@tailwind utilities;" because
    PostCSS never ran. Vite exited 0. Nothing anywhere said a word.

    This script is the answer to that: build, assert, publish, assert again.
    scripts/verify_web_bundle.py holds the assertions and is also run by the
    test suite, so a broken bundle cannot be committed either.

    Usage:
        .\scripts\build_web.ps1                 # build + publish + verify
        .\scripts\build_web.ps1 -NoBump         # do not bump the SW version
        .\scripts\build_web.ps1 -VerifyOnly     # just check what is committed
#>
[CmdletBinding()]
param(
    [switch]$NoBump,
    [switch]$VerifyOnly,
    [switch]$CleanInstall
)

$ErrorActionPreference = "Continue"
$repo = Split-Path -Parent $PSScriptRoot

function Fail($m) { Write-Host "FAIL: $m" -ForegroundColor Red; exit 1 }
function Ok($m)   { Write-Host "OK:   $m" -ForegroundColor Green }
function Info($m) { Write-Host "      $m" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "=== build web ===" -ForegroundColor Cyan
Set-Location $repo
if (-not (Test-Path "web/package.json")) { Fail "web/package.json not found - wrong directory?" }

if ($VerifyOnly) {
    & python scripts/verify_web_bundle.py --bundle web_bundle
    if ($LASTEXITCODE -ne 0) { Fail "the committed bundle does not pass verification." }
    Ok "committed bundle verified"
    exit 0
}

# --- Service worker cache version ---------------------------------------------
# Returning users hold the old bundle until this changes. Bumped BEFORE the
# build so web/public/sw.js and the published copy can never disagree.
$swPath = Join-Path $repo "web/public/sw.js"
if (-not $NoBump) {
    $sw = Get-Content $swPath -Raw
    # Anchor on the capture group, not on end-of-line: the digits sit before a
    # closing quote, so a '[0-9]*$' pattern matches the empty string and the
    # bump silently writes "jh-v" (learned the hard way, 2026-09-15).
    if ($sw -match 'VERSION\s*=\s*"jh-v(\d+)"') {
        $next = [int]$Matches[1] + 1
        $sw = $sw -replace 'VERSION\s*=\s*"jh-v\d+"', "VERSION = `"jh-v$next`""
        Set-Content -Path $swPath -Value $sw -NoNewline
        Ok "service worker cache: jh-v$($Matches[1]) -> jh-v$next"
    } else {
        Fail "could not find a VERSION = `"jh-vN`" line in web/public/sw.js"
    }
} else { Info "service worker version left alone (-NoBump)" }

# --- Install ------------------------------------------------------------------
Set-Location (Join-Path $repo "web")
if ($CleanInstall -or -not (Test-Path "node_modules")) {
    Write-Host "Installing dependencies..." -ForegroundColor Cyan
    & npm ci --no-audit --no-fund 2>&1 | Select-Object -Last 3
    if ($LASTEXITCODE -ne 0) { Fail "npm ci failed." }
    Ok "dependencies installed"
} else { Info "node_modules present (use -CleanInstall to reinstall)" }

# --- Build --------------------------------------------------------------------
Write-Host "Building..." -ForegroundColor Cyan
& npm run build 2>&1 | Select-Object -Last 6
if ($LASTEXITCODE -ne 0) { Fail "the build failed." }
if (-not (Test-Path "dist/index.html")) { Fail "the build produced no dist/index.html." }
Ok "built"

# --- Publish ------------------------------------------------------------------
Set-Location $repo
$bundle = Join-Path $repo "web_bundle"
$bundleAssets = Join-Path $bundle "assets"
# Clearing assets/ is the whole point of this step. Vite's emptyOutDir cleans
# dist/, not web_bundle/, so every previous build was left behind: 22 files
# where index.html referenced 2, and 5MB of dead weight in the image.
if (Test-Path $bundleAssets) {
    $before = @(Get-ChildItem $bundleAssets -File).Count
    Remove-Item $bundleAssets -Recurse -Force
    Info "cleared $before old asset file(s)"
}
New-Item -ItemType Directory -Force -Path $bundle | Out-Null
Copy-Item -Path (Join-Path $repo "web/dist/*") -Destination $bundle -Recurse -Force
Ok "published to web_bundle/"

# --- Verify -------------------------------------------------------------------
& python scripts/verify_web_bundle.py --bundle web_bundle
if ($LASTEXITCODE -ne 0) { Fail "the published bundle did not pass verification - do NOT commit it." }
Ok "verified"

Write-Host ""
Write-Host "Next:" -ForegroundColor Cyan
Write-Host "    .\scripts\ship.ps1 -Message `"...`"" -ForegroundColor White
Write-Host ""
