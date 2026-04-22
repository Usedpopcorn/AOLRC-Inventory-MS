param()

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "dev_env.ps1")

$repoRoot = Get-RepoRoot
$ripgrepPath = Get-UsableRipgrepPath
$venvPython = Get-VenvPythonPath

Enable-RepoDevPath

Write-Host ""
Write-Host "Repo dev shell ready."
Write-Host "Repo root: $repoRoot"

if ($ripgrepPath) {
    $ripgrepVersion = & $ripgrepPath --version | Select-Object -First 1
    Write-Host "ripgrep: $ripgrepVersion"
}
else {
    Write-Host "ripgrep: not configured in this shell"
}

if (Test-Path $venvPython) {
    Write-Host "python: $(& $venvPython --version)"
    Write-Host "Commands now resolve through the repo toolchain in this shell."
    if ($ripgrepPath) {
        Write-Host "Suggested next steps: rg --files | Select-Object -First 10 ; python -m pytest ; .\scripts\check.ps1"
    }
    else {
        Write-Host "Suggested next steps: python -m pytest ; .\scripts\check.ps1"
    }
}
else {
    Write-Host "python: repo virtualenv not found"
    Write-Host "Next step: .\scripts\bootstrap_dev.ps1"
}
