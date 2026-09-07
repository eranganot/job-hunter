<#
    archive_old_clones.ps1 - retire the two stale Job Hunter copies.

    C:\dev\job-hunter is now the single source of truth (see CLAUDE.md). This
    script renames the other two copies so nothing - and nobody - edits them by
    accident. It RENAMES, it does not delete: reverse it by renaming back.

    Run it from anywhere:
        .\scripts\archive_old_clones.ps1            # dry run, shows what it would do
        .\scripts\archive_old_clones.ps1 -Apply     # actually rename
#>
[CmdletBinding()]
param(
    [switch]$Apply,
    [string]$OneDrivePath = ""
)

# 'Continue', not 'Stop': git writes progress to stderr and PowerShell 5.1 turns
# that into a terminating error under 'Stop'. Renames below are checked explicitly.
$ErrorActionPreference = "Continue"
$suffix = "_ARCHIVED_2026-09"

function Info($m) { Write-Host "      $m" -ForegroundColor DarkGray }
function Ok($m)   { Write-Host "OK:   $m" -ForegroundColor Green }
function Warn($m) { Write-Host "WARN: $m" -ForegroundColor Yellow }

$targets = @("C:\Users\erang\job-hunter")
if ($OneDrivePath -ne "") { $targets += $OneDrivePath }

Write-Host ""
Write-Host "=== archive stale Job Hunter clones ===" -ForegroundColor Cyan
Info "keeping: C:\dev\job-hunter"

if (-not (Test-Path "C:\dev\job-hunter\app.py")) {
    Write-Host "FAIL: C:\dev\job-hunter does not look like the repo - stopping." -ForegroundColor Red
    exit 1
}

foreach ($t in $targets) {
    if (-not (Test-Path $t)) { Info "not found, skipping: $t"; continue }

    # Refuse to archive a clone that still holds work nobody carried over.
    Push-Location $t
    $dirty = ""
    try { $dirty = (git status --porcelain 2>$null) -join "`n" } catch { }
    Pop-Location

    if ($dirty -ne "") {
        Warn "$t has uncommitted changes:"
        Write-Host $dirty -ForegroundColor DarkGray
        Info "Phase 0 already copied app.py, STATUS.md, .gitignore and PAID_SOURCES_BENCHMARK.md"
        Info "into C:\dev\job-hunter. Anything else listed above is NOT carried over."
        $answer = Read-Host "Archive it anyway? (y/N)"
        if ($answer -ne "y") { Info "left alone: $t"; continue }
    }

    $dest = "$t$suffix"
    if (Test-Path $dest) { Warn "$dest already exists - skipping $t"; continue }

    if ($Apply) {
        try {
            Rename-Item -LiteralPath $t -NewName (Split-Path $dest -Leaf) -ErrorAction Stop
            Ok "renamed -> $dest"
        } catch {
            Warn "could not rename $t : $($_.Exception.Message)"
            Info "Close any editor/terminal sitting in that folder and re-run."
        }
    } else {
        Info "DRY RUN would rename: $t  ->  $dest"
    }
}

if (-not $Apply) {
    Write-Host ""
    Write-Host "Dry run only. Re-run with -Apply to perform the renames." -ForegroundColor Yellow
    Write-Host "For the OneDrive copy, pass its path:" -ForegroundColor DarkGray
    Write-Host "    .\scripts\archive_old_clones.ps1 -Apply -OneDrivePath 'C:\Users\erang\OneDrive\...\Job Hunter'" -ForegroundColor DarkGray
}
Write-Host ""
