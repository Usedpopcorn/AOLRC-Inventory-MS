param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

$config = Set-PublicTestEnvironment
$publicHost = "$($config.PublicHost)"
$localUrl = "$($config.LocalUrl)"

$cloudflaredProcesses = Get-CimInstance Win32_Process -Filter "Name = 'cloudflared.exe'" -ErrorAction SilentlyContinue
if (-not $cloudflaredProcesses) {
    Write-Host "No cloudflared process found."
    return
}

$stopped = New-Object System.Collections.Generic.List[int]
foreach ($processInfo in $cloudflaredProcesses) {
    $processId = [int]$processInfo.ProcessId
    $commandLine = [string]$processInfo.CommandLine
    $looksRelevant =
        ($commandLine -like "*tunnel run*") -or
        ($commandLine -like "*$publicHost*") -or
        ($commandLine -like "*$localUrl*") -or
        ($commandLine -like "*aolrc-inventory-live-test*")

    if (-not $looksRelevant) {
        continue
    }

    Stop-Process -Id $processId -Force
    $stopped.Add($processId) | Out-Null
    Write-Host "Stopped cloudflared process PID $processId."
}

if ($stopped.Count -eq 0) {
    Write-Host "No matching cloudflared tunnel process was stopped."
}
