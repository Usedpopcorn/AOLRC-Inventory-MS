Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "dev_env.ps1")

function Import-KeyValueEnvFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    $values = @{}
    foreach ($line in Get-Content -LiteralPath $Path) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) {
            continue
        }

        $name, $value = $trimmed -split "=", 2
        if (-not $name) {
            continue
        }

        $values[$name] = if ($null -ne $value) { $value } else { "" }
    }

    return $values
}

function Get-LocalProdEnvironmentFile {
    return Join-Path (Get-RepoRoot) "instance\deploy\local_prod_app.env"
}

function Get-PrimaryLanIPv4 {
    $candidateIps = @()

    try {
        $route = Get-NetRoute -DestinationPrefix "0.0.0.0/0" -AddressFamily IPv4 -ErrorAction Stop |
            Sort-Object RouteMetric, InterfaceMetric |
            Select-Object -First 1

        if ($route) {
            $candidateIps += Get-NetIPAddress -AddressFamily IPv4 -InterfaceIndex $route.ifIndex -ErrorAction Stop |
                Where-Object {
                    $_.IPAddress -and
                    $_.IPAddress -notlike "169.254*" -and
                    $_.IPAddress -ne "127.0.0.1"
                } |
                Select-Object -ExpandProperty IPAddress
        }
    }
    catch {
    }

    if (-not $candidateIps) {
        try {
            $candidateIps += Get-NetIPAddress -AddressFamily IPv4 -ErrorAction Stop |
                Where-Object {
                    $_.IPAddress -and
                    $_.IPAddress -notlike "169.254*" -and
                    $_.IPAddress -ne "127.0.0.1"
                } |
                Sort-Object InterfaceMetric |
                Select-Object -ExpandProperty IPAddress
        }
        catch {
        }
    }

    $selectedIp = $candidateIps | Select-Object -First 1
    if ($selectedIp) {
        return $selectedIp
    }

    return "127.0.0.1"
}

function Set-LocalProdEnvironment {
    param(
        [int]$Port = 0
    )

    $repoRoot = Get-RepoRoot
    $envFile = Get-LocalProdEnvironmentFile
    if (-not (Test-Path $envFile)) {
        throw "Local production env file not found: $envFile"
    }

    $values = Import-KeyValueEnvFile -Path $envFile
    $effectivePort = if ($Port -gt 0) {
        $Port
    }
    elseif ($values.ContainsKey("APP_PORT") -and $values["APP_PORT"]) {
        [int]$values["APP_PORT"]
    }
    else {
        8080
    }
    $listenHost = if ($values.ContainsKey("APP_LISTEN_HOST") -and $values["APP_LISTEN_HOST"]) {
        $values["APP_LISTEN_HOST"]
    }
    else {
        "0.0.0.0"
    }
    $lanIp = Get-PrimaryLanIPv4

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

    $env:FLASK_APP = "run.py"
    $env:APP_PORT = [string]$effectivePort
    $env:APP_LISTEN_HOST = $listenHost
    if ($values.ContainsKey("APP_BASE_URL") -and $values["APP_BASE_URL"]) {
        $env:APP_BASE_URL = $values["APP_BASE_URL"]
    }
    else {
        $env:APP_BASE_URL = "http://${lanIp}:$effectivePort"
    }

    $trustedHosts = New-Object System.Collections.Generic.List[string]
    foreach ($trustedHostEntry in @("localhost", "127.0.0.1", "::1", $lanIp, $env:COMPUTERNAME)) {
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
        LanIp         = $lanIp
        LocalUrl      = "http://127.0.0.1:$effectivePort"
        LanUrl        = "http://${lanIp}:$effectivePort"
        HostnameUrl   = "http://$($env:COMPUTERNAME):$effectivePort"
        VenvPython    = $venvPython
        WaitressServe = $waitressServe
    }
}
