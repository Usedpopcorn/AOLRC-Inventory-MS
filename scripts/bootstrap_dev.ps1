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

function Test-PythonSelector {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Selector
    )

    $selectorArgs = @()
    if ($Selector.Length -gt 1) {
        $selectorArgs = $Selector[1..($Selector.Length - 1)]
    }

    try {
        & $Selector[0] $selectorArgs --version 1> $null 2> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Get-PythonSelector {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        if (Test-PythonSelector @("py", "-3.12")) {
            return @("py", "-3.12")
        }
        if (Test-PythonSelector @("py", "-3.13")) {
            return @("py", "-3.13")
        }
    }

    if (Get-Command python -ErrorAction SilentlyContinue) {
        return @("python")
    }

    throw "Python launcher not found. Install Python 3.12 or 3.13 first."
}

$repoRoot = Split-Path -Parent $PSScriptRoot
$venvPath = Join-Path $repoRoot ".venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$pythonSelector = Get-PythonSelector

if (-not (Test-Path $venvPython)) {
    $selectorArgs = @()
    if ($pythonSelector.Length -gt 1) {
        $selectorArgs = $pythonSelector[1..($pythonSelector.Length - 1)]
    }

    Invoke-Checked { & $pythonSelector[0] $selectorArgs -m venv $venvPath }
}

Invoke-Checked { & $venvPython -m pip install --upgrade pip }
Invoke-Checked { & $venvPython -m pip install -r (Join-Path $repoRoot "requirements-dev.txt") }
Invoke-Checked { & $venvPython -m pre_commit install --install-hooks }
Invoke-Checked { & $venvPython -m pre_commit install --hook-type pre-push }

Write-Host ""
Write-Host "Bootstrap complete."
Write-Host "Git hooks installed."
Write-Host "Interpreter: $(& $venvPython --version)"
Write-Host "Use .\.venv\Scripts\python.exe scripts\validate_repo.py or .\scripts\check.ps1"
