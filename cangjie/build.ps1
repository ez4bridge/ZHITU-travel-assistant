param([string]$SdkRoot = (Join-Path $PSScriptRoot '.sdk\cangjie'))
$ErrorActionPreference = 'Stop'
if (-not (Test-Path -LiteralPath (Join-Path $SdkRoot 'bin\cjc.exe'))) {
    throw 'Cangjie SDK missing. Run tools/download_cangjie.py, or specify -SdkRoot.'
}
. (Join-Path $SdkRoot 'envsetup.ps1')
New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot 'bin') | Out-Null
& (Join-Path $SdkRoot 'bin\cjc.exe') (Join-Path $PSScriptRoot 'src\validator.cj') -o (Join-Path $PSScriptRoot 'bin\validator.exe')
if ($LASTEXITCODE -ne 0) { throw 'Cangjie compilation failed. Python remains available.' }
# Runtime dependencies are local to this module; Python also sets the local DLL path.
Copy-Item -Path (Join-Path $SdkRoot 'runtime\lib\windows_x86_64_cjnative\*.dll') -Destination (Join-Path $PSScriptRoot 'bin') -Force
Copy-Item -LiteralPath (Join-Path $SdkRoot 'LICENSE') -Destination (Join-Path $PSScriptRoot 'bin\SDK-LICENSE') -Force
Copy-Item -LiteralPath (Join-Path $SdkRoot 'Open_Source_Software_Notice.docx') -Destination (Join-Path $PSScriptRoot 'bin') -Force
Write-Host 'Cangjie compiled. Run tools/check_cangjie.py for parity and randomized checks.'
