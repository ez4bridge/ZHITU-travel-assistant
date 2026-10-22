"""Verify and restore an extracted study/model resource bundle."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[1]


def checksum(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def remove_superseded(destination: Path, targets: list[tuple[Path, Path]]) -> None:
    incoming = {target for _, target in targets}
    corpus_roots = {destination / Path(*target.relative_to(destination).parts[:2])
                    for target in incoming
                    if target.relative_to(destination).parts[0] == 'study'
                    and len(target.relative_to(destination).parts) >= 3}
    rag = destination / 'rag'
    index_roots = {path for path in rag.glob('index-*')
                   if re.fullmatch(r'index-[0-9a-f]{12}', path.name) and path.is_dir()}
    roots = corpus_roots | index_roots
    obsolete = []
    for root in roots:
        if root.is_symlink() or not root.resolve().is_relative_to(destination):
            raise ValueError('已有资源包含不安全的路径')
        if root.exists():
            for path in root.rglob('*'):
                if path.is_symlink() or not path.resolve().is_relative_to(destination):
                    raise ValueError('已有资源包含不安全的路径')
                if path.is_file() and path not in incoming:
                    obsolete.append(path)
    for path in obsolete:
        path.unlink()
    for root in roots:
        if root.is_dir():
            for path in sorted(root.rglob('*'), key=lambda item: len(item.parts), reverse=True):
                if path.is_dir() and not any(path.iterdir()):
                    path.rmdir()
            if not any(root.iterdir()):
                root.rmdir()


def restore(source: Path, destination: Path, replace: bool = False) -> int:
    source, destination = source.resolve(), destination.resolve()
    if (destination / 'app.pid').exists():
        raise ValueError('请先停止应用再恢复资料和索引')
    manifest = json.loads((source / 'MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('version') != 1 or not manifest.get('files'):
        raise ValueError('资源清单格式无效')
    targets = []
    seen = set()
    for item in manifest['files']:
        relative = Path(item['path'])
        incoming = (source / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not incoming.is_relative_to(source) or not incoming.is_file():
            raise ValueError('资源路径无效')
        if item['path'] in seen:
            raise ValueError('资源清单包含重复文件')
        seen.add(item['path'])
        if incoming.stat().st_size != item['bytes'] or checksum(incoming) != item['sha256']:
            raise ValueError('资源文件校验失败：' + item['path'])
        if relative.parts[0] not in ('study', 'rag'):
            continue
        target = (destination / relative).resolve()
        if not target.is_relative_to(destination):
            raise ValueError('目标路径无效')
        if target.exists() and not replace:
            raise ValueError('目标文件已存在；需要替换时显式使用 --replace')
        targets.append((incoming, target))
    if replace:
        remove_superseded(destination, targets)
    for incoming, target in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(incoming, target)
    return len(targets)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='解压后的资源目录')
    parser.add_argument('--replace', action='store_true', help='替换已有资料和索引')
    args = parser.parse_args()
    try:
        count = restore(args.source, ROOT / '.runtime', args.replace)
    except (ValueError, OSError, KeyError, TypeError) as error:
        raise SystemExit(str(error)) from None
    print(f'已校验并恢复 {count} 个运行资源文件。')


if __name__ == '__main__':
    main()
