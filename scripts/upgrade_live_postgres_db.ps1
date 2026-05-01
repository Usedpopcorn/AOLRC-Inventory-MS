param(
    [switch]$AllowRemoteHost
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

function Get-CurrentBranchName {
    try {
        $branch = (& git -C (Get-RepoRoot) branch --show-current 2>$null | Select-Object -First 1)
        $branchText = ($branch -as [string])
        if ($null -eq $branchText) {
            return ""
        }
        return $branchText.Trim()
    }
    catch {
        return ""
    }
}

function Get-DatabaseUrlFromPublicEnv {
    $envFile = Get-PublicTestEnvironmentFile
    if (-not (Test-Path $envFile)) {
        throw "Live env file not found: $envFile"
    }

    $values = Import-KeyValueEnvFile -Path $envFile
    if (-not $values.ContainsKey("DATABASE_URL") -or -not $values["DATABASE_URL"]) {
        throw "DATABASE_URL not found in $envFile"
    }

    return [pscustomobject]@{
        EnvFile = $envFile
        DatabaseUrl = $values["DATABASE_URL"]
    }
}

function Get-DbHostFromUrl {
    param([Parameter(Mandatory = $true)][string]$DatabaseUrl)
    try {
        $uri = [Uri]$DatabaseUrl
        $hostValue = $uri.Host
        if ($null -eq $hostValue) {
            return ""
        }
        return $hostValue.Trim().ToLowerInvariant()
    }
    catch {
        return ""
    }
}

$dbConfig = Get-DatabaseUrlFromPublicEnv
$databaseUrl = $dbConfig.DatabaseUrl
$dbHost = Get-DbHostFromUrl -DatabaseUrl $databaseUrl
$branchName = Get-CurrentBranchName

if (-not $databaseUrl.ToLowerInvariant().StartsWith("postgresql:") -and -not $databaseUrl.ToLowerInvariant().StartsWith("postgres:")) {
    throw "This script is for PostgreSQL only. Current DATABASE_URL is not PostgreSQL."
}

$isLocalHost = $dbHost -in @("127.0.0.1", "localhost")
if (-not $isLocalHost -and -not $AllowRemoteHost) {
    throw (
        "Refusing to run because DATABASE_URL host '$dbHost' is not local. " +
        "This command is intended for your local live-test Postgres only. " +
        "If you intentionally need remote DB upgrade, rerun with -AllowRemoteHost."
    )
}

if (-not $isLocalHost -and $branchName -and $branchName.ToLowerInvariant() -ne "main") {
    throw (
        "Refusing remote-host DB upgrade from non-main branch '$branchName'. " +
        "Use main for shared/remote DB upgrades."
    )
}

$null = Set-PublicTestEnvironment

Write-Host ""
Write-Host "Running live PostgreSQL migration upgrade."
Write-Host "Env file:       $($dbConfig.EnvFile)"
Write-Host "Git branch:     $(if ($branchName) { $branchName } else { '<unknown>' })"
Write-Host "DATABASE_URL host: $dbHost"
Write-Host "Upgrade target: heads"
Write-Host ""

Write-Host "Before upgrade (flask db current):"
& .\.venv\Scripts\python.exe -m flask db current
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Applying migrations (flask db upgrade heads)..."
& .\.venv\Scripts\python.exe -m flask db upgrade heads
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "After upgrade (flask db current):"
& .\.venv\Scripts\python.exe -m flask db current
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Available heads (flask db heads):"
& .\.venv\Scripts\python.exe -m flask db heads
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Live PostgreSQL upgrade command completed."
