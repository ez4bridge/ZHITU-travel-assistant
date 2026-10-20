param([string]$Python = 'python', [switch]$Dev, [switch]$SkipRailMcp, [string]$NodeVersion = '22.23.3')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8 = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 or later is required.' }
}
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$requirements = if (Test-Path -LiteralPath 'requirements-lock-windows-py311.txt') { 'requirements-lock-windows-py311.txt' } else { 'requirements.txt' }
& $pythonPath -m pip install -r $requirements
if ($LASTEXITCODE -ne 0) { throw 'Python dependency installation failed.' }
if ($Dev) {
    & $pythonPath -m pip install -r requirements-dev.txt
    if ($LASTEXITCODE -ne 0) { throw 'Test dependency installation failed.' }
}
$toolsDir = Join-Path $PSScriptRoot '.tools'
New-Item -ItemType Directory -Force -Path $toolsDir | Out-Null
$nodeHome = Join-Path $toolsDir 'node'
if (-not (Test-Path -LiteralPath (Join-Path $nodeHome 'node.exe'))) {
    if ($NodeVersion -notmatch '^22\.[0-9]+\.[0-9]+$') { throw 'NodeVersion must be a Node 22 release.' }
    $nodeBase = "https://nodejs.org/dist/v$NodeVersion"
    $sums = (Invoke-WebRequest -Uri "$nodeBase/SHASUMS256.txt" -UseBasicParsing).Content
    if ($sums -is [byte[]]) { $sums = [Text.Encoding]::UTF8.GetString($sums) }
    $line = ($sums -split "`n" | Where-Object { $_ -match '^[a-fA-F0-9]{64}\s+node-v22\.[0-9]+\.[0-9]+-win-x64\.zip$' } | Select-Object -First 1).Trim()
    if (-not $line) { throw 'Official Node 22 Windows x64 release was not found.' }
    $hash, $archiveName = $line -split '\s+', 2
    $zipPath = Join-Path $toolsDir $archiveName
    Invoke-WebRequest -Uri "$nodeBase/$archiveName" -OutFile $zipPath -UseBasicParsing
    if ((Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash -ne $hash) { throw 'Node archive checksum failed.' }
    Expand-Archive -LiteralPath $zipPath -DestinationPath $toolsDir -Force
    $expanded = Join-Path $toolsDir ([IO.Path]::GetFileNameWithoutExtension($archiveName))
    $resolved = [IO.Path]::GetFullPath($expanded)
    if (-not $resolved.StartsWith([IO.Path]::GetFullPath($toolsDir) + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Unsafe Node path.' }
    Move-Item -LiteralPath $resolved -Destination $nodeHome
}
$env:PATH = $nodeHome + ';' + $env:PATH
$env:npm_config_cache = Join-Path $toolsDir 'npm-cache'
Push-Location -LiteralPath 'frontend'
try {
    if (Test-Path -LiteralPath 'package-lock.json') { & (Join-Path $nodeHome 'npm.cmd') ci } else { & (Join-Path $nodeHome 'npm.cmd') install }
    if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    & (Join-Path $nodeHome 'npm.cmd') run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
if (-not $SkipRailMcp) {
    & $pythonPath tools/setup_rail_mcp.py
    if ($LASTEXITCODE -ne 0) {
        Write-Warning 'Railway dependencies could not be installed. The app can still use transport estimates. Retry: .venv\Scripts\python.exe tools\setup_rail_mcp.py'
    }
}
if (-not (Test-Path -LiteralPath '.env.local')) { Copy-Item -LiteralPath '.env.example' -Destination '.env.local' }
Write-Host 'Setup complete. Configure .env.local, then run start.bat.'
