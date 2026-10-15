"""Official Cangjie 1.0.5 Windows ZIP; digest from official download metadata.
Uses Python stdlib only. Installs into cangjie/.sdk without global settings.
"""
import hashlib
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sdk = ROOT / 'cangjie' / '.sdk'
sdk.mkdir(parents=True, exist_ok=True)
archive = sdk / 'cangjie-sdk-windows-x64-1.0.5.zip'
url = 'https://cangjie-lang.cn/v1/files/auth/downLoad?nsId=142267&fileName=cangjie-sdk-windows-x64-1.0.5.zip&objectKey=698acebfbd66ae22b62330af'
expected = '08c6c1dd0de73396f63ac85085d635e79ce84fef98d330cb00869b4fc8f1cb2e'
if not archive.exists():
    print('Downloading official Cangjie SDK (283.50 MB)...', flush=True)
    with urllib.request.urlopen(url, timeout=30) as response, archive.open('wb') as stream:
        if 'json' in response.headers.get('Content-Type', ''):
            raise SystemExit('Official SDK endpoint requires login; no automatic login attempted.')
        while block := response.read(1024 * 1024):
            stream.write(block)
digest = hashlib.sha256(archive.read_bytes()).hexdigest()
if digest != expected:
    raise SystemExit('Cangjie ZIP checksum failed; existing file retained for inspection.')
print('SHA256 verified.', flush=True)
with zipfile.ZipFile(archive) as package:
    for item in package.infolist():
        target = (sdk / item.filename).resolve()
        if not target.is_relative_to(sdk.resolve()):
            raise SystemExit('Unsafe SDK archive entry.')
    package.extractall(sdk)
print('Project-local Cangjie SDK extracted.', flush=True)
