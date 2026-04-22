param()

$ErrorActionPreference = "Stop"

$helpersPath = Join-Path $PSScriptRoot "dev_env.ps1"
. $helpersPath

$repoRoot = Get-RepoRoot
$venvPath = Get-PreferredVenvPath
$pythonSelector = Get-PythonSelector
$ripgrepPath = Get-UsableRipgrepPath

Enable-RepoDevPath

$venvPython = Join-Path $venvPath (Get-VenvPythonRelativePath)

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
if ($ripgrepPath) {
    Write-Host "ripgrep: $(& $ripgrepPath --version | Select-Object -First 1)"
}
else {
    Write-Host "ripgrep: not provisioned automatically; install ripgrep separately if you want rg in plain shells"
}
Write-Host "Interpreter: $(& $venvPython --version)"
Write-Host "Use .\scripts\dev_shell.ps1 to add repo tools to PATH in this shell."
Write-Host "Then run python scripts\validate_repo.py or .\scripts\check.ps1"
