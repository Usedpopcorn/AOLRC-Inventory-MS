Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
$deployDir = Join-Path $repoRoot "instance\deploy"
$pidFiles = @(
    (Join-Path $deployDir "public_test_waitress.pid"),
    (Join-Path $deployDir "public_test_cloudflared.pid")
)

foreach ($pidFile in $pidFiles) {
    if (-not (Test-Path $pidFile)) {
        continue
    }

    $pidText = (Get-Content -LiteralPath $pidFile | Select-Object -First 1).Trim()
    if (-not $pidText) {
        Remove-Item -LiteralPath $pidFile -Force
        continue
    }

    $processId = [int]$pidText
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
    if (-not $processInfo) {
        Remove-Item -LiteralPath $pidFile -Force
        continue
    }

    $commandLine = [string]$processInfo.CommandLine
    $looksExpected =
        ($commandLine -like "*LIVE REAL TEST AOLRC INVENTORY*") -or
        ($processInfo.Name -eq "cloudflared.exe")

    if (-not $looksExpected) {
        throw "PID file '$pidFile' points to an unexpected process."
    }

    $null = & taskkill.exe /PID $processId /T /F
    Remove-Item -LiteralPath $pidFile -Force
}

Write-Host "Stopped public test processes."
