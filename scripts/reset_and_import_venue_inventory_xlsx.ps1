param(
    [string]$WorkbookPath = "c:\Users\Jacob\Downloads\Copy of Venue Inventory .xlsx",
    [switch]$DryRun,
    [switch]$SkipBackup
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

$config = Set-PublicTestEnvironment
if (-not $config.VenvPython -or -not (Test-Path $config.VenvPython)) {
    throw "Python virtual environment not found. Run .\scripts\bootstrap_dev.ps1 first."
}

$repoRoot = $config.RepoRoot
$workbook = [System.IO.Path]::GetFullPath($WorkbookPath)
if (-not (Test-Path $workbook)) {
    throw "Workbook not found: $workbook"
}

if (-not $DryRun -and -not $SkipBackup) {
    Write-Host "Creating safety backup before import..."
    & (Join-Path $PSScriptRoot "backup_live_test_db.ps1")
    if ($LASTEXITCODE -ne 0) {
        throw "Backup failed with exit code $LASTEXITCODE."
    }
}

Push-Location $repoRoot
try {
    $pythonArgs = @(
        (Join-Path $PSScriptRoot "import_venue_inventory_xlsx.py"),
        "--workbook",
        $workbook
    )
    if ($DryRun) {
        $pythonArgs += "--dry-run"
    }

    & $config.VenvPython @pythonArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Import script failed with exit code $LASTEXITCODE."
    }
}
finally {
    Pop-Location
}
