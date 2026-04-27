Set-StrictMode -Version Latest

function Test-IsWindowsPlatform {
    return [System.IO.Path]::DirectorySeparatorChar -eq "\"
}

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

function Test-CommandWorks {
    param(
        [Parameter(Mandatory = $true)]
        [string]$CommandPath,
        [string[]]$Arguments = @("--version")
    )

    try {
        & $CommandPath @Arguments 1> $null 2> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Get-RepoRoot {
    return Split-Path -Parent $PSScriptRoot
}

function Get-PreferredVenvPath {
    return Join-Path (Get-RepoRoot) ".venv"
}

function Get-VenvPythonRelativePath {
    if (Test-IsWindowsPlatform) {
        return "Scripts\python.exe"
    }

    return "bin/python"
}

function Get-VenvBinRelativePath {
    if (Test-IsWindowsPlatform) {
        return "Scripts"
    }

    return "bin"
}

function Get-ExistingVenvPath {
    $repoRoot = Get-RepoRoot
    $venvPythonRelativePath = Get-VenvPythonRelativePath

    foreach ($name in @(".venv", "venv")) {
        $candidate = Join-Path $repoRoot $name
        $candidatePython = Join-Path $candidate $venvPythonRelativePath
        if (Test-Path $candidatePython) {
            return $candidate
        }
    }

    return $null
}

function Get-VenvPythonPath {
    $venvPath = Get-ExistingVenvPath
    if ($venvPath) {
        return Join-Path $venvPath (Get-VenvPythonRelativePath)
    }

    return Join-Path (Get-PreferredVenvPath) (Get-VenvPythonRelativePath)
}

function Get-VenvScriptsPath {
    $venvPath = Get-ExistingVenvPath
    if ($venvPath) {
        return Join-Path $venvPath (Get-VenvBinRelativePath)
    }

    return $null
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
    if ((Test-IsWindowsPlatform) -and (Get-Command py -ErrorAction SilentlyContinue)) {
        if (Test-PythonSelector @("py", "-3.12")) {
            return @("py", "-3.12")
        }
        if (Test-PythonSelector @("py", "-3.13")) {
            return @("py", "-3.13")
        }
    }

    if ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-PythonSelector @("python"))) {
        return @("python")
    }

    if ((Get-Command python3 -ErrorAction SilentlyContinue) -and (Test-PythonSelector @("python3"))) {
        return @("python3")
    }

    throw "Python launcher not found. Install Python 3.12 or 3.13 first."
}

function Get-RepoToolBinPath {
    if (Test-IsWindowsPlatform) {
        return Join-Path (Get-RepoRoot) ".tools\bin"
    }

    return Join-Path (Get-RepoRoot) ".tools/bin"
}

function Get-RepoRipgrepPath {
    $toolName = "rg"
    if (Test-IsWindowsPlatform) {
        $toolName = "rg.exe"
    }

    return Join-Path (Get-RepoToolBinPath) $toolName
}

function Get-UsableRipgrepPath {
    $toolBin = Get-RepoToolBinPath
    $repoRipgrep = Get-RepoRipgrepPath

    if (Test-CommandWorks $repoRipgrep) {
        return $repoRipgrep
    }

    if (Test-CommandWorks "rg") {
        $workingRipgrep = Get-Command rg -ErrorAction SilentlyContinue
        if ($workingRipgrep -and $workingRipgrep.Source) {
            return $workingRipgrep.Source
        }

        return "rg"
    }

    if (-not (Test-IsWindowsPlatform)) {
        return $null
    }

    $detectedRipgrep = Get-Command rg -ErrorAction SilentlyContinue
    if (-not $detectedRipgrep -or -not $detectedRipgrep.Source) {
        return $null
    }

    New-Item -ItemType Directory -Path $toolBin -Force | Out-Null
    Copy-Item $detectedRipgrep.Source $repoRipgrep -Force

    if (-not (Test-CommandWorks $repoRipgrep)) {
        return $null
    }

    return $repoRipgrep
}

function Enable-RepoDevPath {
    $pathEntries = New-Object System.Collections.Generic.List[string]
    $toolBin = Get-RepoToolBinPath
    if (Test-Path (Get-RepoRipgrepPath)) {
        $pathEntries.Add($toolBin)
    }

    $venvScripts = Get-VenvScriptsPath
    if ($venvScripts -and (Test-Path $venvScripts)) {
        $pathEntries.Add($venvScripts)
    }

    if ($pathEntries.Count -eq 0) {
        return
    }

    $currentEntries = @($env:PATH -split ";" | Where-Object { $_ })
    $normalized = @{}
    foreach ($entry in $currentEntries) {
        $normalized[$entry.TrimEnd("\").ToLowerInvariant()] = $true
    }

    $prepend = New-Object System.Collections.Generic.List[string]
    foreach ($entry in $pathEntries) {
        $trimmedEntry = $entry.TrimEnd("\")
        $normalizedEntry = $trimmedEntry.ToLowerInvariant()
        if (-not $normalized.ContainsKey($normalizedEntry)) {
            $prepend.Add($trimmedEntry)
            $normalized[$normalizedEntry] = $true
        }
    }

    if ($prepend.Count -gt 0) {
        $env:PATH = ((@($prepend.ToArray()) + $currentEntries) -join ";")
    }
}
