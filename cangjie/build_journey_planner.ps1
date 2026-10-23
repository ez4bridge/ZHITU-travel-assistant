param([string]$SdkRoot = (Join-Path $PSScriptRoot '.sdk\cangjie'))
$ErrorActionPreference = 'Stop'
# A local SDK is sufficient; no downloads, global PATH, or older project writes.
$sdk = (Resolve-Path -LiteralPath $SdkRoot).Path
$compiler = Join-Path $sdk 'bin\cjc.exe'
if (-not (Test-Path -LiteralPath $compiler)) {
    throw 'Cangjie 1.0.5 SDK missing. Specify -SdkRoot with an existing local SDK.'
}
$savedPath = $env:Path
$savedHome = $env:CANGJIE_HOME
try {
    . (Join-Path $sdk 'envsetup.ps1')
    $destination = Join-Path $PSScriptRoot 'bin'
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $source = Join-Path $PSScriptRoot 'journey_planner\src\main.cj'
    $binary = Join-Path $destination 'journey_planner.exe'
    & $compiler $source -o $binary
    if ($LASTEXITCODE -ne 0) {
        throw 'Constraint planner compilation failed; no Python optimizer fallback is provided.'
    }
    Copy-Item -Path (Join-Path $sdk 'runtime\lib\windows_x86_64_cjnative\*.dll') -Destination $destination -Force
    Copy-Item -LiteralPath (Join-Path $sdk 'LICENSE') -Destination (Join-Path $destination 'SDK-LICENSE') -Force
    Copy-Item -LiteralPath (Join-Path $sdk 'Open_Source_Software_Notice.docx') -Destination $destination -Force
    $manifest = [ordered]@{
        schema_version = '2.2'
        source_sha256 = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant()
        executable_sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $manifest | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $destination 'journey_planner.build.json') -Encoding utf8
    Write-Host 'Engine2.2 compiled locally. Run .venv\Scripts\python.exe tools\check_constraint_planner.py.'
} finally {
    $env:Path = $savedPath
    $env:CANGJIE_HOME = $savedHome
}
