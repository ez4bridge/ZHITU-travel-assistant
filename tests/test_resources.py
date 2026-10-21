import hashlib
import json
from pathlib import Path

import pytest

from tools.restore_resources import restore


def bundle(tmp_path):
    source = tmp_path / 'resources'
    path = source / 'study/example/documents.jsonl'
    path.parent.mkdir(parents=True)
    path.write_bytes(b'example')
    manifest = {'version': 1, 'files': [{'path': 'study/example/documents.jsonl', 'bytes': 7,
        'sha256': hashlib.sha256(b'example').hexdigest()}]}
    (source / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
    return source, manifest


def test_restore_checks_files_before_copy_and_requires_explicit_replace(tmp_path):
    source, _ = bundle(tmp_path)
    target = tmp_path / 'runtime'
    assert restore(source, target) == 1
    assert (target / 'study/example/documents.jsonl').read_bytes() == b'example'
    with pytest.raises(ValueError, match='已存在'):
        restore(source, target)
    assert restore(source, target, replace=True) == 1


@pytest.mark.parametrize('change', ['path', 'checksum'])
def test_invalid_bundle_does_not_write_destination(tmp_path, change):
    source, manifest = bundle(tmp_path)
    manifest['files'][0]['path' if change == 'path' else 'sha256'] = '../outside' if change == 'path' else '0' * 64
    (source / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
    target = tmp_path / 'runtime'
    with pytest.raises(ValueError):
        restore(source, target)
    assert not target.exists()
