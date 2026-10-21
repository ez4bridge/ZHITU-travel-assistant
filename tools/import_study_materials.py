"""Import local UTF-8 TXT/Markdown; no model calls or semantic extraction."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.knowledge import KnowledgeBase, load_bundle, read_documents


def import_documents(source: Path, target: Path) -> dict:
    source, target = source.resolve(), target.resolve()
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('资料源与目标目录必须分开')
    documents = read_documents(source)
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / 'documents.pending'
    try:
        temporary.write_text(''.join(json.dumps(d, ensure_ascii=False) + '\n' for d in documents), encoding='utf-8')
        temporary.replace(target / 'documents.jsonl')
    finally:
        temporary.unlink(missing_ok=True)
    load_bundle(target)
    knowledge = KnowledgeBase(target)
    knowledge.require()
    return knowledge.catalog()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path, help='TXT/Markdown directory or existing research materials directory')
    args = parser.parse_args()
    print(json.dumps(import_documents(args.source, settings.study_corpus_dir), ensure_ascii=True, indent=2))
    print('Import complete. Restart the local service to load these raw documents.')


if __name__ == '__main__':
    main()
