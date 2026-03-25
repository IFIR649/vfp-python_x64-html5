param(
    [string]$BindHost = "127.0.0.1",
    [int]$Port = 8766,
    [switch]$OpenHost
)

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$rootDir = Split-Path -Parent $scriptDir
$hostProjectPath = Join-Path $rootDir "dotnet\host\VfpWebViewHost.csproj"
$healthUrl = "http://$BindHost`:$Port/health"

function Test-Backend {
    param([string]$Url)

    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 2
        return $response.StatusCode -eq 200
    }
    catch {
        return $false
    }
}

Set-Location $rootDir

if (-not (Test-Backend -Url $healthUrl)) {
    $pythonCommand = Get-Command python -ErrorAction Stop

    Start-Process `
        -FilePath $pythonCommand.Source `
        -ArgumentList @("-m", "uvicorn", "backend.main:app", "--host", $BindHost, "--port", $Port.ToString()) `
        -WorkingDirectory $rootDir

    $backendReady = $false
    for ($i = 0; $i -lt 12; $i++) {
        Start-Sleep -Seconds 1
        if (Test-Backend -Url $healthUrl) {
            $backendReady = $true
            break
        }
    }

    if (-not $backendReady) {
        throw "Backend no respondio en $healthUrl."
    }
}

if ($OpenHost) {
    dotnet run --project $hostProjectPath
}
else {
    Write-Host "Backend listo en $healthUrl"
}
