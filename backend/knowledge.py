"""Text retrieval and immutable citation snapshots."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import unicodedata
from urllib.parse import urlparse

from .models import StudyContext, StudyFact, StudySource

MAX_DOCUMENTS = 100
MAX_CHARACTERS = 1_000_000


def today_shanghai() -> date:
    return datetime.now(timezone(timedelta(hours=8))).date()


def normalize(text: str) -> str:
    return unicodedata.normalize('NFKC', text).lower().strip()


def tokens(text: str) -> list[str]:
    output = []
    for run in re.findall(r'[\u3400-\u9fff]+|[a-z0-9]+', normalize(text)):
        if re.fullmatch(r'[a-z0-9]+', run) or len(run) == 1:
            output.append(run)
        else:
            output.extend(run[i:i + 2] for i in range(len(run) - 1))
    return output


def safe_source_url(value: str) -> str:
    if not value:
        return ''
    parsed = urlparse(value)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('资料来源必须是无账号信息的 HTTP/HTTPS 链接')
    return value


def read_documents(directory: Path) -> list[dict]:
    """Read TXT/Markdown only. Legacy seed/catalog/relationship files are ignored."""
    root = directory.resolve()
    if not root.is_dir():
        raise ValueError('资料目录不存在')
    content_root = root / 'extracted' if (root / 'extracted').is_dir() else root
    paths = sorted(p for p in content_root.iterdir() if p.suffix.lower() in ('.md', '.txt') and p.is_file())
    if not paths or len(paths) > MAX_DOCUMENTS:
        raise ValueError(f'请提供1至{MAX_DOCUMENTS}个UTF-8 TXT/Markdown正文文件')
    documents, total = [], 0
    for path in paths:
        if not path.resolve().is_relative_to(content_root.resolve()) or path.stat().st_size > 4_000_000:
            raise ValueError('资料路径或单个文件大小不符合要求')
        raw = path.read_text(encoding='utf-8-sig').replace('\r\n', '\n')
        header, separator, body = raw.partition('\n---\n')
        body = body.strip() if separator else raw.strip()
        if not body:
            continue
        total += len(body)
        if total > MAX_CHARACTERS:
            raise ValueError('一次导入的正文总量不能超过100万字符')
        metadata = header if separator else ''
        title = re.search(r'^#\s+(.+)$', raw, re.M)

        def field(label: str) -> str:
            found = re.search(r'^' + label + r'[：:]\s*(.+)$', metadata, re.M)
            return found.group(1).strip() if found else ''

        documents.append({
            'id': 'doc_' + hashlib.sha256(path.name.encode()).hexdigest()[:16],
            'title': title.group(1)[:240] if title else path.stem[:240],
            'url': safe_source_url(field('来源')), 'publisher': field('发布者'),
            'publication_date': field('发布日期'), 'retrieved_at': field('获取时间'),
            'text': body, 'text_sha256': hashlib.sha256(body.encode()).hexdigest(),
        })
    if not documents:
        raise ValueError('资料没有可检索的正文')
    return documents


def load_bundle(root: Path) -> dict:
    path = root / 'documents.jsonl'
    if path.stat().st_size > 8_000_000:
        raise ValueError('资料包过大')
    documents = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
    if not 1 <= len(documents) <= MAX_DOCUMENTS or len({d['id'] for d in documents}) != len(documents):
        raise ValueError('资料数量或编号无效')
    if sum(len(d['text']) for d in documents) > MAX_CHARACTERS:
        raise ValueError('资料正文过大')
    for document in documents:
        StudySource.model_validate({k: v for k, v in document.items() if k != 'text'})
        safe_source_url(document['url'])
        if not document['text'].strip() or hashlib.sha256(document['text'].encode()).hexdigest() != document['text_sha256']:
            raise ValueError('资料正文校验失败')
    digest = hashlib.sha256(json.dumps(documents, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
    return {'documents': documents, 'version': 'raw_' + digest}


class KnowledgeBase:
    def __init__(self, root: Path):
        self.root, self.ready, self.version = Path(root), False, ''
        self.message = '研学资料未导入，请导入TXT或Markdown正文后重启服务'
        self.sources, self.chunks, self.texts = {}, {}, {}
        self._frequencies, self._document_frequency = {}, Counter()
        self._retriever = None
        try:
            bundle = load_bundle(self.root)
            self.version = bundle['version']
            for document in bundle['documents']:
                sid, body = document['id'], document['text']
                self.sources[sid] = StudySource.model_validate({k: v for k, v in document.items() if k != 'text'})
                self.texts[sid] = body
                start = 0
                while start < len(body):
                    end = min(start + 1000, len(body))
                    if end < len(body):
                        boundary = body.rfind('\n', start + 600, end)
                        if boundary > start:
                            end = boundary
                    cid = f'{sid}_{start}_{end}'
                    self.chunks[cid] = {'id': cid, 'title': document['title'], 'text': body[start:end],
                        'source_ids': [sid], 'role': 'raw_passage', 'start_char': start, 'end_char': end}
                    frequency = Counter(tokens(document['title'] + '\n' + body[start:end]))
                    self._frequencies[cid] = frequency
                    self._document_frequency.update(frequency.keys())
                    if end == len(body):
                        break
                    start = max(start + 1, end - 200)
            self._average_length = sum(sum(f.values()) for f in self._frequencies.values()) / len(self.chunks)
            self.ready, self.message = True, '研学原文资料已就绪'
        except (OSError, ValueError, KeyError, TypeError, ZeroDivisionError):
            if self.root.exists():
                self.message = '原文资料尚未导入或校验失败，请重新导入TXT/Markdown正文'

    def require(self):
        if not self.ready:
            raise RuntimeError(self.message)

    def rank_bm25(self, query: str) -> list[str]:
        self.require()
        query_terms, scored = set(tokens(query)), []
        for cid, frequencies in self._frequencies.items():
            length, score = sum(frequencies.values()), 0.0
            for term in query_terms:
                count = frequencies[term]
                if count:
                    df = self._document_frequency[term]
                    idf = math.log(1 + (len(self.chunks) - df + .5) / (df + .5))
                    score += idf * count * 2.2 / (count + 1.2 * (.25 + .75 * length / self._average_length))
            if score:
                scored.append((score, cid))
        scored.sort(key=lambda row: (-row[0], row[1]))
        return [cid for _, cid in scored]

    def search(self, query: str, day: date | None = None, place_id: str | None = None) -> list[dict]:
        # Kept as the synchronous, dependency-free baseline for importers and old callers.
        from .rag_common import diverse
        return diverse(self.rank_bm25(query), self.chunks)

    def retriever(self):
        if self._retriever is None:
            from .config import settings
            from .retrieval import RetrievalService
            self._retriever = RetrievalService(self, settings.rag_dir, settings.rag_mode)
        return self._retriever

    async def asearch(self, query: str) -> list[dict]:
        return await self.retriever().search(query)

    async def aclose(self):
        if self._retriever:
            await self._retriever.close()

    def adjacent(self, chunk_id: str) -> list[dict]:
        chunk = self.chunks[chunk_id]
        chunks = [c for c in self.chunks.values() if c['source_ids'] == chunk['source_ids']]
        index = next(i for i, c in enumerate(chunks) if c['id'] == chunk_id)
        return chunks[max(0, index - 1):index + 2]

    def fact(self, cid: str) -> StudyFact:
        # The legacy wire name is retained; contents are now verbatim passages.
        return StudyFact.model_validate(self.chunks[cid])

    def context(self, chunk_ids: list[str], policies=None, places=None) -> StudyContext:
        ids = list(dict.fromkeys(chunk_ids))
        sources = {sid for cid in ids for sid in self.chunks[cid]['source_ids']}
        return StudyContext(corpus_version=self.version, retrieval_mode='raw_text',
            facts=[self.fact(cid) for cid in ids], sources=[self.sources[sid] for sid in sorted(sources)],
            policies=policies or {}, places=places or {})

    def evidence(self, chunks: list[dict]) -> list[dict]:
        return [{**self.fact(c['id']).model_dump(),
                 'source': self.sources[c['source_ids'][0]].model_dump()} for c in chunks]

    def catalog(self) -> dict:
        return {'ready': self.ready, 'message': self.message, 'corpus_version': self.version,
            'retrieval_mode': 'raw_text', 'source_count': len(self.sources), 'chunk_count': len(self.chunks),
            'fact_count': len(self.chunks), 'topics': ['1920年前后的上海：青年与新思想'], 'places': [],
            'retrieval': self.retriever().diagnostic()}
