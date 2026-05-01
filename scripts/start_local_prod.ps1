param(
    [int]$Port = 0
)

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "local_prod_env.ps1")

$config = Set-LocalProdEnvironment -Port $Port
$waitressServe = $config.WaitressServe
if (-not $waitressServe -or -not (Test-Path $waitressServe)) {
    throw "waitress-serve not found in the repo virtualenv. Install requirements first."
}

Write-Host ""
Write-Host "Local production-style environment loaded."
Write-Host "Env file:  $($config.EnvFile)"
Write-Host "Listen:    $($config.ListenHost):$($config.Port)"
Write-Host "Local URL: $($config.LocalUrl)"
Write-Host "LAN URL:   $($config.LanUrl)"
Write-Host ""

$exitCode = 0
Push-Location $config.RepoRoot
try {
    & $waitressServe --listen "$($config.ListenHost):$($config.Port)" run:app
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
