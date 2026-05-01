param(
    [string]$BackupDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

function ConvertTo-PlainText {
    param([string]$Value)
    return [System.Uri]::UnescapeDataString($Value)
}

$config = Set-PublicTestEnvironment
$repoRoot = $config.RepoRoot
$envFile = $config.EnvFile
$values = Import-KeyValueEnvFile -Path $envFile

if (-not $values.ContainsKey("DATABASE_URL") -or -not $values["DATABASE_URL"]) {
    throw "DATABASE_URL is missing from $envFile"
}

$databaseUri = [Uri]$values["DATABASE_URL"]
if ($databaseUri.Scheme -notin @("postgresql", "postgres")) {
    throw "Live-test backup only supports PostgreSQL DATABASE_URL values."
}

$pgDump = Join-Path "C:\Program Files\PostgreSQL\16\bin" "pg_dump.exe"
if (-not (Test-Path $pgDump)) {
    $pgDumpCommand = Get-Command pg_dump -ErrorAction SilentlyContinue
    if (-not $pgDumpCommand -or -not $pgDumpCommand.Source) {
        throw "pg_dump.exe was not found. Install PostgreSQL client tools or add pg_dump to PATH."
    }
    $pgDump = $pgDumpCommand.Source
}

$targetDir = if ($BackupDir) {
    $BackupDir
}
else {
    Join-Path $repoRoot "backups\postgres"
}
New-Item -ItemType Directory -Force -Path $targetDir | Out-Null

$databaseName = $databaseUri.AbsolutePath.TrimStart("/")
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupPath = Join-Path $targetDir "aolrc_public_test_${databaseName}_${timestamp}.dump"

$userInfo = $databaseUri.UserInfo -split ":", 2
$dbUser = ConvertTo-PlainText $userInfo[0]
$dbPassword = if ($userInfo.Count -gt 1) { ConvertTo-PlainText $userInfo[1] } else { "" }
$dbPort = if ($databaseUri.Port -gt 0) { [string]$databaseUri.Port } else { "5432" }

$previousPgPassword = $env:PGPASSWORD
try {
    $env:PGPASSWORD = $dbPassword
    & $pgDump `
        --host $databaseUri.Host `
        --port $dbPort `
        --username $dbUser `
        --dbname $databaseName `
        --format custom `
        --file $backupPath `
        --no-password
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed with exit code $LASTEXITCODE"
    }
}
finally {
    if ($null -eq $previousPgPassword) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    else {
        $env:PGPASSWORD = $previousPgPassword
    }
}

$backupItem = Get-Item -LiteralPath $backupPath
if ($backupItem.Length -le 0) {
    throw "Backup file was created but is empty: $backupPath"
}

Write-Host "Backup created: $backupPath"
Write-Host "Backup bytes: $($backupItem.Length)"
Write-Host ""
Write-Host "Restore note:"
Write-Host "  Use pg_restore against a verified local target database only."
Write-Host "  Do not restore over the live-test database until you intentionally accept replacing local data."
