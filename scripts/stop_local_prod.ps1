Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $repoRoot "instance\deploy\waitress.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "No local production PID file found."
    exit 0
}

$pidText = (Get-Content -LiteralPath $pidFile | Select-Object -First 1).Trim()
if (-not $pidText) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "Removed empty local production PID file."
    exit 0
}

$processId = [int]$pidText
$processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
if (-not $processInfo) {
    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "Local production process $processId is not running."
    exit 0
}

$commandLine = [string]$processInfo.CommandLine
if ($commandLine -notlike "*LIVE REAL TEST AOLRC INVENTORY*" -or $commandLine -notlike "*start_local_prod.ps1*") {
    throw "PID file points to a process that does not look like this repo's local production app."
}

$null = & taskkill.exe /PID $processId /T /F
Remove-Item -LiteralPath $pidFile -Force
Write-Host "Stopped local production app process $processId."
