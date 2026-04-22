param()

$ErrorActionPreference = "Stop"

$helpersPath = Join-Path $PSScriptRoot "dev_env.ps1"
. $helpersPath

$repoRoot = Get-RepoRoot
$venvPython = Get-VenvPythonPath

if (-not (Test-Path $venvPython)) {
    throw "No repo virtualenv was found (.venv or venv). Run .\scripts\bootstrap_dev.ps1 first."
}

Get-UsableRipgrepPath | Out-Null
Enable-RepoDevPath

Push-Location $repoRoot
try {
    Invoke-Checked { & $venvPython "scripts\validate_repo.py" }
    Invoke-Checked { & $venvPython -m pytest }
    Invoke-Checked { & $venvPython -m ruff check scripts tests CHECK_SETUP.py }
    Invoke-Checked { & $venvPython -m pre_commit run --all-files }
    Invoke-Checked { git diff --check }
}
finally {
    Pop-Location
}
