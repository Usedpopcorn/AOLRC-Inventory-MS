param(
    [ValidateSet("sqlite", "postgres")]
    [string]$Target = "sqlite",
    [string]$SqliteDatabase = "local_test.db",
    [string]$PostgresUrl,
    [switch]$SkipDockerRecreate,
    [switch]$SkipMigrationChecks,
    [switch]$DryRun,
    [switch]$CreateBackups
)

$ErrorActionPreference = "Stop"

$helpersPath = Join-Path $PSScriptRoot "dev_env.ps1"
. $helpersPath

$repoRoot = Get-RepoRoot
$envPath = Join-Path $repoRoot ".env"
$venvPython = Get-VenvPythonPath

if (-not (Test-Path $envPath)) {
    throw "Missing .env at $envPath"
}

if (-not (Test-Path $venvPython)) {
    throw "No repo virtualenv was found (.venv or venv). Run .\scripts\bootstrap_dev.ps1 first."
}

Get-UsableRipgrepPath | Out-Null
Enable-RepoDevPath

function Parse-DatabaseUrlLine {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Line
    )

    if ($Line -notmatch '^\s*(?<comment>#\s*)?DATABASE_URL=(?<url>\S.*)\s*$') {
        return $null
    }

    return [pscustomobject]@{
        IsCommented = [bool]$Matches["comment"]
        Url = $Matches["url"].Trim()
    }
}

function Get-DatabaseConfig {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Lines
    )

    $activeUrl = $null
    $commentedSqliteUrl = $null
    $commentedPostgresUrl = $null

    foreach ($line in $Lines) {
        $parsed = Parse-DatabaseUrlLine -Line $line
        if ($null -eq $parsed) {
            continue
        }

        if (-not $parsed.IsCommented -and -not $activeUrl) {
            $activeUrl = $parsed.Url
            continue
        }

        if ($parsed.Url.StartsWith("sqlite:", [System.StringComparison]::OrdinalIgnoreCase)) {
            if (-not $commentedSqliteUrl) {
                $commentedSqliteUrl = $parsed.Url
            }
            continue
        }

        if ($parsed.Url.StartsWith("postgresql:", [System.StringComparison]::OrdinalIgnoreCase)) {
            if (-not $commentedPostgresUrl) {
                $commentedPostgresUrl = $parsed.Url
            }
        }
    }

    return [pscustomobject]@{
        ActiveUrl = $activeUrl
        CommentedSqliteUrl = $commentedSqliteUrl
        CommentedPostgresUrl = $commentedPostgresUrl
    }
}

function Get-GitBranchName {
    try {
        $branchOutput = & git -C $repoRoot branch --show-current 2>$null
        if ($LASTEXITCODE -ne 0) {
            return $null
        }
        return (($branchOutput | Select-Object -First 1) -as [string]).Trim()
    }
    catch {
        return $null
    }
}

function Build-UpdatedEnvLines {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Lines,
        [Parameter(Mandatory = $true)]
        [string]$ActiveUrl,
        [string]$FallbackUrl
    )

    $output = New-Object System.Collections.Generic.List[string]
    $inserted = $false

    foreach ($line in $Lines) {
        if ($line -match '^\s*#?\s*DATABASE_URL=') {
            if (-not $inserted) {
                $output.Add("DATABASE_URL=$ActiveUrl")
                if ($FallbackUrl -and $FallbackUrl -ne $ActiveUrl) {
                    $output.Add("#DATABASE_URL=$FallbackUrl")
                }
                $inserted = $true
            }
            continue
        }

        $output.Add($line)
    }

    if (-not $inserted) {
        $output.Add("DATABASE_URL=$ActiveUrl")
        if ($FallbackUrl -and $FallbackUrl -ne $ActiveUrl) {
            $output.Add("#DATABASE_URL=$FallbackUrl")
        }
    }

    return $output.ToArray()
}

function Invoke-RepoPython {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    & $venvPython @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code $LASTEXITCODE"
    }
}

function Invoke-FlaskCommand {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $originalFlaskApp = $env:FLASK_APP
    $env:FLASK_APP = "run.py"
    try {
        Invoke-RepoPython -Arguments (@("-m", "flask") + $Arguments)
    }
    finally {
        if ($null -eq $originalFlaskApp) {
            Remove-Item Env:FLASK_APP -ErrorAction SilentlyContinue
        }
        else {
            $env:FLASK_APP = $originalFlaskApp
        }
    }
}

function Get-ResolvedDatabasePath {
    $script = @'
from app import create_app, db
app = create_app()
with app.app_context():
    print(db.engine.url.database or '')
'@

    $pathOutput = & $venvPython -c $script
    if ($LASTEXITCODE -ne 0) {
        throw "Could not resolve the active database path."
    }

    return (($pathOutput | Select-Object -Last 1) -as [string]).Trim()
}

function Backup-FileIfPresent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not (Test-Path $Path)) {
        return $null
    }

    $directory = Split-Path -Parent $Path
    $filename = Split-Path -Leaf $Path
    $timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupName = "{0}.pre_{1}_{2}" -f $filename, $Label, $timestamp
    $backupPath = Join-Path $directory $backupName
    Copy-Item $Path $backupPath
    return $backupPath
}

function Test-DockerComposeAvailable {
    try {
        & docker compose version 1>$null 2>$null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

$envLines = Get-Content $envPath
$config = Get-DatabaseConfig -Lines $envLines
$currentBranch = Get-GitBranchName

$sqliteUrl = "sqlite:///$SqliteDatabase"

$resolvedPostgresUrl = $PostgresUrl
if (-not $resolvedPostgresUrl) {
    if ($config.ActiveUrl -and $config.ActiveUrl.StartsWith("postgresql:", [System.StringComparison]::OrdinalIgnoreCase)) {
        $resolvedPostgresUrl = $config.ActiveUrl
    }
    elseif ($config.CommentedPostgresUrl) {
        $resolvedPostgresUrl = $config.CommentedPostgresUrl
    }
}

if ($Target -eq "postgres") {
    if (-not $resolvedPostgresUrl) {
        throw "No Postgres DATABASE_URL is available. Add a commented Postgres line in .env or pass -PostgresUrl."
    }
    if ($currentBranch -and $currentBranch.ToLowerInvariant() -ne "main") {
        throw (
            "Current branch '{0}' cannot activate Postgres because the app enforces SQLite on feature branches. " +
            "Switch to main first or keep the Postgres URL commented."
        ) -f $currentBranch
    }
    $activeUrl = $resolvedPostgresUrl
    $fallbackUrl = $sqliteUrl
}
else {
    $activeUrl = $sqliteUrl
    $fallbackUrl = $resolvedPostgresUrl
}

$updatedEnvLines = Build-UpdatedEnvLines -Lines $envLines -ActiveUrl $activeUrl -FallbackUrl $fallbackUrl
$updatedEnvText = (($updatedEnvLines -join [Environment]::NewLine) + [Environment]::NewLine)
$currentEnvText = [System.IO.File]::ReadAllText($envPath)

Write-Host ""
Write-Host "Target database: $Target"
if ($currentBranch) {
    Write-Host "Git branch: $currentBranch"
}
else {
    Write-Host "Git branch: <unknown>"
}
Write-Host "Active DATABASE_URL: $activeUrl"
if ($fallbackUrl) {
    Write-Host "Commented fallback DATABASE_URL: $fallbackUrl"
}

if ($DryRun) {
    Write-Host ""
    Write-Host "Dry run only. .env was not changed."
    return
}

if ($currentEnvText -ne $updatedEnvText) {
    $envBackup = $null
    if ($CreateBackups) {
        $envBackup = Backup-FileIfPresent -Path $envPath -Label "db_switch"
    }
    [System.IO.File]::WriteAllText($envPath, $updatedEnvText)
    if ($envBackup) {
        Write-Host "Backed up .env to $envBackup"
    }
    Write-Host "Updated .env"
}
else {
    Write-Host ".env already matches the requested database target."
}

if (-not $SkipMigrationChecks) {
    Write-Host ""
    Write-Host "Running migration health checks..."
    $resolvedDbPath = Get-ResolvedDatabasePath
    if ($Target -eq "sqlite") {
        if ($CreateBackups) {
            $dbBackup = Backup-FileIfPresent -Path $resolvedDbPath -Label "migration"
            if ($dbBackup) {
                Write-Host "Backed up SQLite DB to $dbBackup"
            }
        }
    }
    Write-Host "Resolved database path: $resolvedDbPath"
    Invoke-FlaskCommand -Arguments @("db", "current")
    Invoke-FlaskCommand -Arguments @("db", "upgrade")
    Invoke-FlaskCommand -Arguments @("db", "current")
    Invoke-FlaskCommand -Arguments @("db", "check")
}

if (-not $SkipDockerRecreate) {
    Write-Host ""
    if (Test-DockerComposeAvailable) {
        Write-Host "Recreating Docker web service..."
        & docker compose up -d --force-recreate web
        if ($LASTEXITCODE -ne 0) {
            throw "docker compose up failed with exit code $LASTEXITCODE"
        }
        if (-not $SkipMigrationChecks) {
            & docker compose exec web flask db current
            if ($LASTEXITCODE -ne 0) {
                throw "docker compose exec web flask db current failed with exit code $LASTEXITCODE"
            }
        }
    }
    else {
        Write-Host "Docker Compose is not available. Skipping container recreate."
    }
}

Write-Host ""
Write-Host "Database switch complete."
