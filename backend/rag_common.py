"""Shared, dependency-free retrieval policy and reproducible index identity."""
from collections import Counter
import hashlib
import json
from pathlib import Path

EMBED_MODEL = 'BAAI/bge-small-zh-v1.5'
RERANK_MODEL = 'BAAI/bge-reranker-v2-m3-int8'
WINDOW_TOKENS, WINDOW_OVERLAP, DIMENSION = 384, 64, 512
QUERY_PREFIX = '为这个句子生成表示以用于检索相关文章：'
MODES = ('auto', 'bm25', 'dense', 'hybrid', 'hybrid_rerank')


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def index_identity(corpus_version: str) -> dict:
    return {'schema': 1, 'corpus_version': corpus_version, 'embedding_model': EMBED_MODEL,
            'rerank_model': RERANK_MODEL, 'window_tokens': WINDOW_TOKENS,
            'window_overlap': WINDOW_OVERLAP, 'dimension': DIMENSION, 'query_prefix': QUERY_PREFIX}


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.pending')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def file_hash(path: Path) -> str:
    checksum = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def fuse(bm25: list[str], dense: list[str]) -> list[str]:
    scores = Counter()
    for ranking in (bm25, dense):
        for rank, cid in enumerate(dict.fromkeys(ranking), 1):
            scores[cid] += 1 / (60 + rank)
    return sorted(scores, key=lambda cid: (-scores[cid], cid))


def diverse(ids: list[str], chunks: dict, limit: int = 5) -> list[dict]:
    counts, result = Counter(), []
    for cid in dict.fromkeys(ids):
        if cid not in chunks:
            continue
        sid = chunks[cid]['source_ids'][0]
        if counts[sid] < 2:
            result.append(chunks[cid])
            counts[sid] += 1
        if len(result) == limit:
            break
    return result


def token_windows(text: str, tokenizer, budget: int = WINDOW_TOKENS,
                  overlap: int = WINDOW_OVERLAP) -> list[dict]:
    """Use tokenizer character offsets, preserving original text including the tail."""
    if budget <= overlap or overlap < 0:
        raise ValueError('Invalid token window')
    encoded = tokenizer.encode(text, add_special_tokens=False)
    offsets = [(a, b) for a, b in encoded.offsets if b > a]
    if not offsets:
        return []
    result, start = [], 0
    while start < len(offsets):
        end = min(start + budget, len(offsets))
        left = 0 if start == 0 else offsets[start][0]
        right = len(text) if end == len(offsets) else offsets[end - 1][1]
        result.append({'text': text[left:right], 'start_char': left, 'end_char': right})
        if end == len(offsets):
            break
        start = end - overlap
    return result
