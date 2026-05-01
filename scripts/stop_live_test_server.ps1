param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

$config = Set-PublicTestEnvironment
$port = [int]$config.Port
$listenHost = "$($config.ListenHost)"
$repoRoot = "$($config.RepoRoot)"

$listeners = Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue
if (-not $listeners) {
    Write-Host "No listening process found on port $port."
    return
}

$stopped = New-Object System.Collections.Generic.List[int]
foreach ($listener in $listeners) {
    $processId = [int]$listener.OwningProcess
    if ($stopped.Contains($processId)) {
        continue
    }

    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if (-not $processInfo) {
        continue
    }

    $name = [string]$processInfo.Name
    $commandLine = [string]$processInfo.CommandLine
    $looksLikeLiveServer =
        ($name -match "waitress|python") -or
        ($commandLine -like "*run:app*") -or
        ($commandLine -like "*LIVE REAL TEST AOLRC INVENTORY*")

    if (-not $looksLikeLiveServer) {
        Write-Host "Skipping PID $processId on port $port because it does not look like the live app process."
        continue
    }

    Stop-Process -Id $processId -Force
    $stopped.Add($processId) | Out-Null
    Write-Host "Stopped live app process PID $processId (host=$listenHost, port=$port)."
}

if ($stopped.Count -eq 0) {
    Write-Host "No matching live app process was stopped."
}
