Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "local_prod_env.ps1")

function Get-PublicTestEnvironmentFile {
    return Join-Path (Get-RepoRoot) "instance\deploy\public_test_app.env"
}

function Get-PublicTestCredentialsPath {
    $repoRoot = Get-RepoRoot
    $parentDir = Split-Path -Parent $repoRoot
    return Join-Path $parentDir "AOLRC Inventory Public Test Credentials.txt"
}

function Get-PublicTestSharedCredentialsPath {
    $userProfile = [Environment]::GetFolderPath("UserProfile")
    if (-not $userProfile) {
        return $null
    }

    $oneDriveRoot = Join-Path $userProfile "OneDrive - Appalachian State University"
    if (-not (Test-Path $oneDriveRoot)) {
        return $null
    }

    $sharedDir = Join-Path $oneDriveRoot "AOLRC Inventory Public Test"
    New-Item -ItemType Directory -Force -Path $sharedDir | Out-Null
    return Join-Path $sharedDir "AOLRC Inventory Public Test Credentials.txt"
}

function Update-PublicTestCredentialsFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PublicUrl
    )

    $credentialsPath = Get-PublicTestCredentialsPath
    if (-not (Test-Path $credentialsPath)) {
        return $null
    }

    $content = Get-Content -LiteralPath $credentialsPath -Raw
    $publicLoginUrl = $PublicUrl.TrimEnd("/") + "/login"
    if ($content -match '(?m)^Public URL: .*$') {
        $updatedContent = [regex]::Replace($content, '(?m)^Public URL: .*$', "Public URL: $publicLoginUrl", 1)
    }
    else {
        $updatedContent = "Public URL: $publicLoginUrl`r`n`r`n$content"
    }

    Set-Content -LiteralPath $credentialsPath -Value $updatedContent -Encoding UTF8
    $sharedCredentialsPath = Get-PublicTestSharedCredentialsPath
    if ($sharedCredentialsPath) {
        Copy-Item -LiteralPath $credentialsPath -Destination $sharedCredentialsPath -Force
    }

    return $credentialsPath
}

function Get-PublicTestCloudflaredPath {
    $command = Get-Command cloudflared -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }

    $candidatePaths = @(
        (Join-Path ${env:ProgramFiles(x86)} "cloudflared\cloudflared.exe"),
        (Join-Path $env:ProgramFiles "cloudflared\cloudflared.exe")
    )

    foreach ($candidatePath in $candidatePaths) {
        if ($candidatePath -and (Test-Path $candidatePath)) {
            return $candidatePath
        }
    }

    throw "cloudflared.exe not found. Install it first with 'winget install --id Cloudflare.cloudflared'."
}

function Set-PublicTestEnvironment {
    param(
        [string]$PublicBaseUrl
    )

    $repoRoot = Get-RepoRoot
    $envFile = Get-PublicTestEnvironmentFile
    if (-not (Test-Path $envFile)) {
        throw "Public test env file not found: $envFile"
    }

    $values = Import-KeyValueEnvFile -Path $envFile
    $effectivePort = if ($values.ContainsKey("APP_PORT") -and $values["APP_PORT"]) {
        [int]$values["APP_PORT"]
    }
    else {
        8081
    }
    $listenHost = if ($values.ContainsKey("APP_LISTEN_HOST") -and $values["APP_LISTEN_HOST"]) {
        $values["APP_LISTEN_HOST"]
    }
    else {
        "127.0.0.1"
    }

    foreach ($entry in $values.GetEnumerator()) {
        switch ($entry.Key) {
            "APP_BASE_URL" { continue }
            "APP_PORT" { continue }
            "APP_LISTEN_HOST" { continue }
            "TRUSTED_HOSTS" { continue }
            default {
                Set-Item -Path "Env:$($entry.Key)" -Value $entry.Value
            }
        }
    }

    $publicHost = $null
    if ($PublicBaseUrl) {
        $env:APP_BASE_URL = $PublicBaseUrl
        try {
            $publicHost = ([Uri]$PublicBaseUrl).Host
        }
        catch {
            $publicHost = $null
        }
    }
    elseif (Test-Path Env:APP_BASE_URL) {
        Remove-Item Env:APP_BASE_URL
    }

    $trustedHosts = New-Object System.Collections.Generic.List[string]
    foreach ($trustedHostEntry in @("localhost", "127.0.0.1", "::1", $env:COMPUTERNAME, $publicHost)) {
        if ($trustedHostEntry) {
            $trustedHosts.Add($trustedHostEntry)
        }
    }
    if ($values.ContainsKey("TRUSTED_HOSTS") -and $values["TRUSTED_HOSTS"]) {
        foreach ($configuredTrustedHost in ($values["TRUSTED_HOSTS"] -split ",")) {
            $trimmedHost = $configuredTrustedHost.Trim()
            if ($trimmedHost) {
                $trustedHosts.Add($trimmedHost)
            }
        }
    }

    $env:FLASK_APP = "run.py"
    $env:APP_PORT = [string]$effectivePort
    $env:APP_LISTEN_HOST = $listenHost
    $env:TRUSTED_HOSTS = (($trustedHosts | Select-Object -Unique) -join ",")

    $venvPython = Get-VenvPythonPath
    $venvScripts = Get-VenvScriptsPath
    $waitressServe = $null
    if ($venvScripts) {
        $waitressServe = Join-Path $venvScripts "waitress-serve.exe"
    }

    return [pscustomobject]@{
        RepoRoot      = $repoRoot
        EnvFile       = $envFile
        ListenHost    = $listenHost
        Port          = $effectivePort
        LocalUrl      = "http://${listenHost}:$effectivePort"
        PublicBaseUrl = $PublicBaseUrl
        PublicHost    = $publicHost
        VenvPython    = $venvPython
        WaitressServe = $waitressServe
        Cloudflared   = Get-PublicTestCloudflaredPath
    }
}
