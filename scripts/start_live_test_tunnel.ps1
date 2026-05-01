param(
    [string]$TunnelName = "aolrc-inventory-live-test",
    [switch]$Quick
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

$config = Set-PublicTestEnvironment
$cloudflared = $config.Cloudflared
if (-not $cloudflared -or -not (Test-Path $cloudflared)) {
    throw "cloudflared.exe was not found. Install it with: winget install --id Cloudflare.cloudflared"
}

if ($Quick) {
    Write-Host "Starting temporary trycloudflare.com tunnel to $($config.LocalUrl)"
    & $cloudflared tunnel --url $config.LocalUrl
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Starting named Cloudflare Tunnel: $TunnelName"
Write-Host "Expected public hostname: $($config.PublicBaseUrl)"
Write-Host "Expected origin service:  $($config.LocalUrl)"
Write-Host ""
Write-Host "If this fails with an authentication or missing tunnel error, run:"
Write-Host "  cloudflared tunnel login"
Write-Host "  cloudflared tunnel create $TunnelName"
Write-Host "  cloudflared tunnel route dns $TunnelName www.aolrcinventory.org"
Write-Host "Then add a Cloudflare tunnel config that maps the hostname to $($config.LocalUrl)."
Write-Host ""

& $cloudflared tunnel run $TunnelName
exit $LASTEXITCODE
