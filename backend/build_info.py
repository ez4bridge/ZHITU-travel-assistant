"""Identify the backend sources loaded by a server without reading local secrets."""
import hashlib
from pathlib import Path


def source_fingerprint() -> str:
    digest = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob('*.py')):
        digest.update(path.name.encode('utf-8'))
        digest.update(b'\0')
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]
