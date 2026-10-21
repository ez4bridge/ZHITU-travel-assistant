"""Single-owner local Qdrant/ONNX worker. stdout is JSON-lines protocol only."""
import argparse
from contextlib import redirect_stdout
import json
import os
from pathlib import Path
import sys
import time
import traceback
from uuid import uuid4, uuid5, NAMESPACE_URL

if os.name == 'nt':
    import ctypes
    # Native inference failures must not leave an interactive Windows crash dialog.
    ctypes.windll.kernel32.SetErrorMode(0x8003)

from .rag_common import (DIMENSION, EMBED_MODEL, QUERY_PREFIX, RERANK_MODEL, digest,
                         file_hash, index_identity, read_json, token_windows, write_json)


def tokenizer_at(path: Path):
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_file(str(path / 'tokenizer.json'))
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


def short_title(title: str, tokenizer) -> str:
    windows = token_windows(title, tokenizer, budget=32, overlap=0)
    return windows[0]['text'] if windows else ''


class Engine:
    def __init__(self, directory: Path, rerank: bool = False):
        from fastembed import TextEmbedding
        self.directory = directory
        self.models = read_json(directory / 'models.json')
        self.verify_models()
        self.tokenizer = tokenizer_at(directory / 'models' / 'embedding')
        self.embedding = TextEmbedding(model_name=EMBED_MODEL, threads=2,
            providers=['CPUExecutionProvider'], specific_model_path=str(directory / 'models' / 'embedding'),
            cache_dir=str(directory / 'cache'), local_files_only=True)
        self.reranker, self.rerank_error = None, False
        self.client = None
        self.chunks = {}
        if rerank:
            self.load_reranker()

    def verify_models(self):
        for kind, record in self.models.items():
            for name, expected in record['files'].items():
                path = self.directory / 'models' / kind / name
                if not path.is_file() or file_hash(path) != expected['sha256']:
                    raise ValueError('Local model checksum mismatch; run explicit prepare_rag --download')

    def load_reranker(self):
        if self.reranker is None and not self.rerank_error:
            try:
                from fastembed.rerank.cross_encoder import TextCrossEncoder
                self.rerank_tokenizer = tokenizer_at(self.directory / 'models' / 'reranker')
                self.reranker = TextCrossEncoder(model_name=RERANK_MODEL, threads=2,
                    providers=['CPUExecutionProvider'],
                    specific_model_path=str(self.directory / 'models' / 'reranker'),
                    cache_dir=str(self.directory / 'cache'), local_files_only=True)
            except Exception:
                traceback.print_exc(file=sys.stderr)
                self.rerank_error = True

    def build(self, knowledge) -> dict:
        from qdrant_client import QdrantClient, models
        identity = index_identity(knowledge.version)
        generation = 'index-' + uuid4().hex[:12]
        folder = self.directory / generation
        folder.mkdir(parents=True)
        windows = []
        for cid, chunk in knowledge.chunks.items():
            title = short_title(chunk['title'], self.tokenizer)
            for item in token_windows(chunk['text'], self.tokenizer):
                text = title + '\n' + item['text']
                if len(self.tokenizer.encode(text).ids) > 512:
                    raise ValueError('Embedding token window overflow')
                windows.append({**item, 'parent_id': cid, 'input': text})
        client = QdrantClient(path=str(folder / 'qdrant'))
        try:
            client.create_collection('passages', vectors_config=models.VectorParams(
                size=DIMENSION, distance=models.Distance.COSINE))
            for offset in range(0, len(windows), 8):
                batch = windows[offset:offset + 8]
                vectors = list(self.embedding.embed([w['input'] for w in batch], batch_size=8))
                client.upsert('passages', points=[models.PointStruct(
                    id=str(uuid5(NAMESPACE_URL, f'{knowledge.version}:{offset + i}')),
                    vector=vector.tolist(), payload={k: v for k, v in item.items() if k != 'input'})
                    for i, (item, vector) in enumerate(zip(batch, vectors))])
                print(f'Indexed {min(offset + 8, len(windows))}/{len(windows)} windows', file=sys.stderr, flush=True)
        finally:
            client.close()
        write_json(folder / 'chunks.json', knowledge.chunks)
        manifest = {'identity': identity, 'generation': generation, 'models': self.models,
                    'window_count': len(windows), 'parent_count': len(knowledge.chunks),
                    'chunks_sha256': file_hash(folder / 'chunks.json')}
        manifest['fingerprint'] = digest(manifest)
        write_json(self.directory / 'index.json', manifest)
        return manifest

    def open_index(self):
        from qdrant_client import QdrantClient
        self.manifest = read_json(self.directory / 'index.json')
        if self.models != self.manifest['models']:
            raise ValueError('Model revisions changed; rebuild the index')
        folder = (self.directory / self.manifest['generation']).resolve()
        if not folder.is_relative_to(self.directory.resolve()):
            raise ValueError('Unsafe index path')
        if file_hash(folder / 'chunks.json') != self.manifest['chunks_sha256']:
            raise ValueError('Index text checksum mismatch')
        self.chunks = read_json(folder / 'chunks.json')
        self.client = QdrantClient(path=str(folder / 'qdrant'))

    def dense(self, query: str) -> list[str]:
        import numpy as np
        queries = [QUERY_PREFIX + window['text'] for window in token_windows(query, self.tokenizer)]
        if not queries:
            return []
        if any(len(self.tokenizer.encode(q).ids) > 512 for q in queries):
            raise ValueError('Query token window overflow')
        vector = np.mean(list(self.embedding.embed(queries, batch_size=2)), axis=0)
        vector = vector / max(float(np.linalg.norm(vector)), 1e-12)
        limit, found = min(32, self.manifest['window_count']), {}
        while limit:
            result = self.client.query_points('passages', query=vector.tolist(), limit=limit,
                                               with_payload=True).points
            for hit in result:
                cid = hit.payload['parent_id']
                found[cid] = max(found.get(cid, -float('inf')), hit.score)
            if len(found) >= 10 or limit >= self.manifest['window_count']:
                break
            limit = min(limit * 2, self.manifest['window_count'])
        return sorted(found, key=lambda cid: (-found[cid], cid))[:10]

    def rerank(self, query: str, ids: list[str]) -> dict:
        self.load_reranker()
        if self.rerank_error:
            return {'ids': ids, 'rerank_error': True}
        tokenizer, pairs, owners = self.rerank_tokenizer, [], []
        queries = token_windows(query, tokenizer, budget=120, overlap=24)
        for cid in ids[:10]:
            chunk = self.chunks[cid]
            title = short_title(chunk['title'], tokenizer)
            for q in queries:
                budget = min(384, 512 - len(tokenizer.encode(q['text'], title).ids) - 8)
                for window in token_windows(chunk['text'], tokenizer, budget=budget, overlap=64):
                    text = title + '\n' + window['text']
                    if len(tokenizer.encode(q['text'], text).ids) > 512:
                        raise ValueError('Reranker token window overflow')
                    pairs.append((q['text'], text))
                    owners.append(cid)
        scores = {}
        for cid, score in zip(owners, self.reranker.rerank_pairs(pairs, batch_size=1)):
            scores[cid] = max(scores.get(cid, -float('inf')), score)
        return {'ids': sorted(scores, key=lambda cid: (-scores[cid], ids.index(cid)))}

    def stats(self) -> dict:
        import psutil
        info = psutil.Process().memory_info()
        return {'rss_bytes': info.rss, 'peak_rss_bytes': getattr(info, 'peak_wset', info.rss)}


def emit(value):
    print(json.dumps(value, ensure_ascii=False), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--rag-dir', type=Path, required=True)
    parser.add_argument('--build', action='store_true')
    parser.add_argument('--rerank', action='store_true')
    args = parser.parse_args()
    started = time.perf_counter()
    with redirect_stdout(sys.stderr):
        engine = Engine(args.rag_dir, args.rerank)
        if args.build:
            from .config import settings
            from .knowledge import KnowledgeBase
            knowledge = KnowledgeBase(settings.study_corpus_dir)
            knowledge.require()
            manifest = engine.build(knowledge)
        else:
            engine.open_index()
            manifest = engine.manifest
    emit({'status': 'built' if args.build else 'ready', 'fingerprint': manifest['fingerprint'],
          'cold_seconds': time.perf_counter() - started, **engine.stats()})
    if args.build:
        return
    try:
        for line in sys.stdin:
            request = json.loads(line)
            started = time.perf_counter()
            try:
                with redirect_stdout(sys.stderr):
                    if request['op'] == 'dense':
                        result = {'ids': engine.dense(request['query'])}
                    elif request['op'] == 'rerank':
                        result = engine.rerank(request['query'], request['ids'])
                    else:
                        raise ValueError('Unknown operation')
                emit({'id': request['id'], **result, 'stats': engine.stats(),
                      'seconds': time.perf_counter() - started})
            except Exception:
                traceback.print_exc(file=sys.stderr)
                emit({'id': request.get('id'), 'error': 'worker_request_failed'})
    finally:
        engine.client.close()


if __name__ == '__main__':
    main()
