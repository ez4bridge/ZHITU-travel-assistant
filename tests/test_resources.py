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


def test_replace_removes_superseded_corpus_and_index_but_preserves_other_data(tmp_path):
    source, _ = bundle(tmp_path)
    target = tmp_path / 'runtime'
    old_files = ['study/example/extracted/old.md', 'study/example/raw/old.html',
                 'rag/index-123456789abc/chunks.json']
    kept_files = ['study/another/documents.jsonl', 'plans.sqlite3', 'rag/models/embedding/model.onnx']
    for relative in old_files + kept_files:
        path = target / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'previous')
    assert restore(source, target, replace=True) == 1
    assert all(not (target / relative).exists() for relative in old_files)
    assert all((target / relative).read_bytes() == b'previous' for relative in kept_files)


def test_invalid_replacement_preserves_existing_corpus(tmp_path):
    source, manifest = bundle(tmp_path)
    target = tmp_path / 'runtime'
    old = target / 'study/example/extracted/old.md'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'previous')
    manifest['files'][0]['sha256'] = '0' * 64
    (source / 'MANIFEST.json').write_text(json.dumps(manifest), encoding='utf-8')
    with pytest.raises(ValueError):
        restore(source, target, replace=True)
    assert old.read_bytes() == b'previous'
