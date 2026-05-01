param(
    [switch]$NoNewWindows,
    [int]$StartupTimeoutSeconds = 20
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$stopStackScript = Join-Path $PSScriptRoot "stop_live_test_stack.ps1"
$startServerBat = Join-Path $repoRoot "start_server.bat"
$startTunnelBat = Join-Path $repoRoot "start_tunnel.bat"
. (Join-Path $PSScriptRoot "public_test_env.ps1")
$config = Set-PublicTestEnvironment
$listenPort = [int]$config.Port
$normalizedRepoRoot = [string]$repoRoot

if (-not (Test-Path $startServerBat)) {
    throw "Missing start script: $startServerBat"
}
if (-not (Test-Path $startTunnelBat)) {
    throw "Missing start script: $startTunnelBat"
}

function Get-LiveListener {
    return Get-NetTCPConnection -State Listen -LocalPort $listenPort -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-LiveListenerPid {
    $listener = Get-LiveListener
    if (-not $listener) {
        return $null
    }
    return [int]$listener.OwningProcess
}

function Get-LiveServerProcessIds {
    $processes = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object {
            if (-not $_) {
                return $false
            }
            $name = ([string]$_.Name).ToLowerInvariant()
            $commandLine = [string]$_.CommandLine
            if (-not $commandLine) {
                return $false
            }
            if ($name -notin @("python.exe", "waitress-serve.exe")) {
                return $false
            }
            if ($commandLine -notlike "*run:app*") {
                return $false
            }
            return ($commandLine -like "*$normalizedRepoRoot*")
        }
    return $processes | Select-Object -ExpandProperty ProcessId -Unique
}

function Stop-LingeringLiveServerProcesses {
    $liveProcessIds = Get-LiveServerProcessIds
    foreach ($processId in $liveProcessIds) {
        if (-not $processId) {
            continue
        }
        Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue
        Write-Host "Stopped lingering live app process PID $processId."
    }
}

function Wait-UntilPortFreed {
    param(
        [int]$TimeoutSeconds = 10
    )
    $deadline = (Get-Date).AddSeconds([Math]::Max($TimeoutSeconds, 1))
    while ((Get-Date) -lt $deadline) {
        $listener = Get-LiveListener
        if (-not $listener) {
            return $true
        }
        Start-Sleep -Milliseconds 300
    }
    return $false
}

function Wait-ForFreshListener {
    param(
        [int[]]$OldProcessIds,
        [int]$TimeoutSeconds = 20
    )
    $previous = @{}
    foreach ($oldProcessId in ($OldProcessIds | Where-Object { $_ })) {
        $previous[[int]$oldProcessId] = $true
    }
    $deadline = (Get-Date).AddSeconds([Math]::Max($TimeoutSeconds, 5))
    while ((Get-Date) -lt $deadline) {
        $listener = Get-LiveListener
        if ($listener) {
            $currentPid = [int]$listener.OwningProcess
            if (-not $previous.ContainsKey($currentPid)) {
                return $currentPid
            }
        }
        Start-Sleep -Milliseconds 400
    }
    return $null
}

$existingPids = New-Object System.Collections.Generic.List[int]
$currentListenerPid = Get-LiveListenerPid
if ($currentListenerPid) {
    $existingPids.Add([int]$currentListenerPid) | Out-Null
}
foreach ($liveProcessId in Get-LiveServerProcessIds) {
    if ($liveProcessId -and -not $existingPids.Contains([int]$liveProcessId)) {
        $existingPids.Add([int]$liveProcessId) | Out-Null
    }
}

Write-Host "Stopping any existing live processes..."
& $stopStackScript

Start-Sleep -Seconds 1
Stop-LingeringLiveServerProcesses
if (-not (Wait-UntilPortFreed -TimeoutSeconds 12)) {
    throw "Port $listenPort is still in use after stop/restart cleanup. Aborting restart."
}

if ($NoNewWindows) {
    Write-Host ""
    Write-Host "Starting server in current terminal..."
    & $startServerBat
    exit $LASTEXITCODE
}

Write-Host ""
Write-Host "Starting new terminals for live server and tunnel..."
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    "cd /d `"$repoRoot`"; .\start_server.bat"
)
Start-Sleep -Milliseconds 700
Start-Process powershell.exe -ArgumentList @(
    "-NoExit",
    "-ExecutionPolicy",
    "Bypass",
    "-Command",
    "cd /d `"$repoRoot`"; .\start_tunnel.bat"
)

$freshPid = Wait-ForFreshListener -OldProcessIds $existingPids.ToArray() -TimeoutSeconds $StartupTimeoutSeconds
if (-not $freshPid) {
    throw "Restart timed out waiting for a fresh listener on port $listenPort."
}

$freshProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $freshPid" -ErrorAction SilentlyContinue
if (-not $freshProcess) {
    throw "Fresh listener PID $freshPid was detected but process details are unavailable."
}
$freshCommandLine = [string]$freshProcess.CommandLine
if ($freshCommandLine -notlike "*$normalizedRepoRoot*") {
    throw (
        "Listener PID $freshPid is active on port $listenPort, but command line does not match repo root $normalizedRepoRoot."
    )
}

Write-Host "Restart completed. Fresh live app listener confirmed on port $listenPort (PID $freshPid)."
Write-Host "Two new PowerShell windows should now be open."
