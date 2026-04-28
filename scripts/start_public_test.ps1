param(
    [int]$TunnelReadyTimeoutSeconds = 45
)

Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "public_test_env.ps1")

$repoRoot = Split-Path -Parent $PSScriptRoot
$deployDir = Join-Path $repoRoot "instance\deploy"
New-Item -ItemType Directory -Force -Path $deployDir | Out-Null

$waitressPidFile = Join-Path $deployDir "public_test_waitress.pid"
$cloudflaredPidFile = Join-Path $deployDir "public_test_cloudflared.pid"
$waitressRunnerFile = Join-Path $deployDir "public_test_waitress_runner.ps1"
$waitressOutLog = Join-Path $deployDir "public_test_waitress.stdout.log"
$waitressErrLog = Join-Path $deployDir "public_test_waitress.stderr.log"
$cloudflaredOutLog = Join-Path $deployDir "public_test_cloudflared.stdout.log"
$cloudflaredErrLog = Join-Path $deployDir "public_test_cloudflared.stderr.log"
$publicUrlFile = Join-Path $deployDir "public_test_url.txt"

if ((Test-Path $waitressPidFile) -or (Test-Path $cloudflaredPidFile)) {
    & (Join-Path $PSScriptRoot "stop_public_test.ps1")
}

Remove-Item -LiteralPath $waitressOutLog, $waitressErrLog, $cloudflaredOutLog, $cloudflaredErrLog, $publicUrlFile, $waitressRunnerFile -Force -ErrorAction SilentlyContinue

$initialConfig = Set-PublicTestEnvironment
if (-not $initialConfig.WaitressServe -or -not (Test-Path $initialConfig.WaitressServe)) {
    throw "waitress-serve.exe not found in the repo virtualenv."
}

$cloudflaredProcess = Start-Process `
    -FilePath $initialConfig.Cloudflared `
    -ArgumentList @("tunnel", "--url", $initialConfig.LocalUrl) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $cloudflaredOutLog `
    -RedirectStandardError $cloudflaredErrLog `
    -PassThru
Set-Content -LiteralPath $cloudflaredPidFile -Value $cloudflaredProcess.Id -Encoding ASCII

$publicUrl = $null
$deadline = (Get-Date).AddSeconds([Math]::Max($TunnelReadyTimeoutSeconds, 10))
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 500
    $logText = ""
    foreach ($candidateLog in @($cloudflaredOutLog, $cloudflaredErrLog)) {
        if (Test-Path $candidateLog) {
            $logText += [Environment]::NewLine + (Get-Content -LiteralPath $candidateLog -Raw)
        }
    }

    $match = [regex]::Match($logText, 'https://[-a-z0-9]+\.trycloudflare\.com')
    if ($match.Success) {
        $publicUrl = $match.Value
        break
    }

    if ($cloudflaredProcess.HasExited) {
        throw "cloudflared exited before publishing a public URL."
    }
}

if (-not $publicUrl) {
    throw "Timed out waiting for cloudflared to publish a public URL."
}

Set-Content -LiteralPath $publicUrlFile -Value $publicUrl -Encoding ASCII
$credentialsPath = Update-PublicTestCredentialsFile -PublicUrl $publicUrl

$waitressBootstrap = @'
$ErrorActionPreference = "Stop"
. '__SCRIPT_PATH__'
$config = Set-PublicTestEnvironment -PublicBaseUrl "__PUBLIC_URL__"
Push-Location $config.RepoRoot
try {
    & $config.WaitressServe --listen "$($config.ListenHost):$($config.Port)" run:app
}
finally {
    Pop-Location
}
'@
$waitressBootstrap = $waitressBootstrap.Replace("__SCRIPT_PATH__", (Join-Path $PSScriptRoot "public_test_env.ps1")).Replace("__PUBLIC_URL__", $publicUrl)
Set-Content -LiteralPath $waitressRunnerFile -Value $waitressBootstrap -Encoding ASCII
$waitressCommand = "& '$waitressRunnerFile'"

$waitressProcess = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $waitressCommand) `
    -WorkingDirectory $repoRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $waitressOutLog `
    -RedirectStandardError $waitressErrLog `
    -PassThru
Set-Content -LiteralPath $waitressPidFile -Value $waitressProcess.Id -Encoding ASCII

Start-Sleep -Seconds 5
if ($waitressProcess.HasExited) {
    throw "The public test Waitress process exited immediately."
}

Write-Host ""
Write-Host "Public test environment loaded."
Write-Host "Origin:     $($initialConfig.LocalUrl)"
Write-Host "Public URL: $publicUrl"
if ($credentialsPath) {
    Write-Host "Credentials: $credentialsPath"
}
Write-Host ""
