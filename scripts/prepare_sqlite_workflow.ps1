param(
    [switch]$CreateBackups,
    [switch]$SkipDummyUserSeed
)

$ErrorActionPreference = "Stop"

$helpersPath = Join-Path $PSScriptRoot "dev_env.ps1"
. $helpersPath

$repoRoot = Get-RepoRoot
$switchScript = Join-Path $repoRoot "scripts\switch_database.ps1"

if (-not (Test-Path $switchScript)) {
    throw "Missing switch script at $switchScript"
}

Push-Location $repoRoot
try {
    Invoke-Checked { & $switchScript -Target "sqlite" -CreateBackups:$CreateBackups }

    if (-not $SkipDummyUserSeed) {
        Write-Host ""
        Write-Host "Verifying SQLite dummy users for local auth..."
        & docker compose exec web python seed_dummy_users.py --check
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Dummy-user check failed; seeding local auth users..."
            Invoke-Checked { & docker compose exec web python seed_dummy_users.py }
            Invoke-Checked { & docker compose exec web python seed_dummy_users.py --check }
        }
        else {
            Write-Host "Dummy-user check passed."
        }
    }

    Write-Host ""
    Write-Host "SQLite workflow prep complete."
    Write-Host "Open: http://127.0.0.1:5000/dashboard"
    Write-Host "Before commit/push: .\scripts\check.ps1"
}
finally {
    Pop-Location
}
