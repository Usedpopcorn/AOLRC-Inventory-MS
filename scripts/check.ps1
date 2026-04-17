param()

$ErrorActionPreference = "Stop"

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    & $Command
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed with exit code $LASTEXITCODE"
    }
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) {
    throw ".venv is missing. Run .\scripts\bootstrap_dev.ps1 first."
}

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
