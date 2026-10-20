$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$serverPort = & $pythonPath -c 'from backend.config import settings; print(settings.port)'
if ($LASTEXITCODE -ne 0) { throw 'Could not load project configuration.' }
$url = "http://127.0.0.1:$serverPort"
try {
    $ready = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 8
    if ($ready.tasks.active -gt 0) { throw 'A planning task is active. Cancel it or wait before restarting.' }
} catch [System.Net.WebException] { }
& $pythonPath tools/stop_server.py
if ($LASTEXITCODE -ne 0) { throw 'Could not safely stop the recorded project server.' }
$stillRunning = $true
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    try { $null = Invoke-RestMethod -Uri "$url/api/health" -TimeoutSec 1 }
    catch { $stillRunning = $false; break }
    Start-Sleep -Milliseconds 300
}
if ($stillRunning) { throw 'The old service is still listening. No replacement was started.' }
& $pythonPath -m backend.main
exit $LASTEXITCODE
