param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$stopServerScript = Join-Path $PSScriptRoot "stop_live_test_server.ps1"
$stopTunnelScript = Join-Path $PSScriptRoot "stop_live_test_tunnel.ps1"

Write-Host "Stopping live app server..."
& $stopServerScript

Write-Host ""
Write-Host "Stopping live tunnel..."
& $stopTunnelScript

Write-Host ""
Write-Host "Live stack stop attempt complete."
