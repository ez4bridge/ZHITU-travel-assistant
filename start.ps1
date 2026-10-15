$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) { throw 'Run setup.ps1 first.' }
if (-not (Test-Path -LiteralPath 'frontend\dist\index.html')) { throw 'Run setup.ps1 to build the frontend.' }
$env:PYTHONUTF8 = '1'
$serverPort = & $pythonPath -c 'from backend.config import settings; print(settings.port)'
if ($LASTEXITCODE -ne 0) { throw 'Could not load project configuration.' }
$url = "http://127.0.0.1:$serverPort"
try {
    $ready = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 5 -ErrorAction Stop
    if ($ready.app_id -eq 'travel-assistant') {
        Write-Host "Travel Assistant is already running. Open $url"
        exit 0
    }
} catch { }
& $pythonPath -m backend.main
exit $LASTEXITCODE
