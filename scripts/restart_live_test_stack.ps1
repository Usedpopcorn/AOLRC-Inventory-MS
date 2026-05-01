param(
    [switch]$NoNewWindows
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$stopStackScript = Join-Path $PSScriptRoot "stop_live_test_stack.ps1"
$startServerBat = Join-Path $repoRoot "start_server.bat"
$startTunnelBat = Join-Path $repoRoot "start_tunnel.bat"

if (-not (Test-Path $startServerBat)) {
    throw "Missing start script: $startServerBat"
}
if (-not (Test-Path $startTunnelBat)) {
    throw "Missing start script: $startTunnelBat"
}

Write-Host "Stopping any existing live processes..."
& $stopStackScript

Start-Sleep -Seconds 1

if ($NoNewWindows) {
    Write-Host ""
    Write-Host "Starting server in current terminal..."
    & $startServerBat
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Starting new terminals for live server and tunnel..."
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    "cd /d `"$repoRoot`"; .\start_server.bat"
)
Start-Sleep -Milliseconds 700
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    "cd /d `"$repoRoot`"; .\start_tunnel.bat"
)

Write-Host "Restart command completed. Two new PowerShell windows should now be open."
