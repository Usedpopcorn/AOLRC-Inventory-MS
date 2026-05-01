param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$FlaskArgs
)

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "local_prod_env.ps1")

if (-not $FlaskArgs -or $FlaskArgs.Count -eq 0) {
    throw "Usage: .\\scripts\\local_prod_flask.ps1 <flask arguments>"
}

$config = Set-LocalProdEnvironment
$python = $config.VenvPython
if (-not (Test-Path $python)) {
    throw "Repo virtualenv Python not found: $python"
}

$exitCode = 0
Push-Location $config.RepoRoot
try {
    & $python -m flask @FlaskArgs
    $exitCode = $LASTEXITCODE
}
finally {
    Pop-Location
}

exit $exitCode
