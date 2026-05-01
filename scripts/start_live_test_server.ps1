param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

$config = Set-PublicTestEnvironment
if (-not $config.WaitressServe -or -not (Test-Path $config.WaitressServe)) {
    throw "waitress-serve.exe was not found in the repo virtualenv. Run .\scripts\bootstrap_dev.ps1 first."
}

Write-Host ""
Write-Host "Starting AOLRC live-test app with Waitress."
Write-Host "Env file:  $($config.EnvFile)"
Write-Host "Listen:    $($config.ListenHost):$($config.Port)"
Write-Host "Local URL: $($config.LocalUrl)"
Write-Host "Public:    $($config.PublicBaseUrl)"
Write-Host ""

Push-Location $config.RepoRoot
try {
    & $config.WaitressServe --listen "$($config.ListenHost):$($config.Port)" run:app
}
finally {
    Pop-Location
}
