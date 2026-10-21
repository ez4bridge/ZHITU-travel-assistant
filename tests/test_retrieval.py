"""Retrieval contracts, native-failure isolation and evidence metrics; no model downloads."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.knowledge import KnowledgeBase
from backend.rag_common import digest, diverse, fuse, index_identity, token_windows, write_json
from backend.retrieval import RetrievalService
from retrieval_metrics import choose, metrics
from tools.import_study_materials import import_documents


@pytest.fixture
def knowledge(tmp_path):
    source = tmp_path / 'texts'
    source.mkdir()
    (source / 'a.txt').write_text('陈望道在义乌翻译宣言。' * 230, encoding='utf-8')
    (source / 'b.txt').write_text('青年在学社学习外语。' * 230, encoding='utf-8')
    target = tmp_path / 'corpus'
    import_documents(source, target)
    return KnowledgeBase(target)


def test_token_windows_preserve_offsets_tail_and_non_ascii():
    class Characters:
        def encode(self, text, **_):
            return SimpleNamespace(offsets=[(i, i + 1) for i in range(len(text))])
    text = '甲乙丙。🙂abc' * 150
    windows = token_windows(text, Characters())
    assert windows[0]['start_char'] == 0 and windows[-1]['end_char'] == len(text)
    covered = set()
    for window in windows:
        assert window['text'] == text[window['start_char']:window['end_char']]
        assert len(window['text']) <= 384
        covered.update(range(window['start_char'], window['end_char']))
    assert covered == set(range(len(text)))


def test_rrf_uses_ranks_not_incomparable_raw_scores():
    assert fuse(['a', 'b', 'c'], ['c', 'b', 'a'])[0] in ('a', 'c')
    assert fuse(['a', 'a', 'b'], ['b'])[0] == 'b'


def test_diversity_and_original_citations(knowledge):
    hits = diverse(list(knowledge.chunks), knowledge.chunks)
    assert len(hits) <= 5
    for sid in knowledge.sources:
        assert sum(sid in h['source_ids'] for h in hits) <= 2
    context = knowledge.context([h['id'] for h in hits])
    assert context.retrieval_mode == 'raw_text'
    for fact in context.facts:
        assert fact.text == knowledge.texts[fact.source_ids[0]][fact.start_char:fact.end_char]


def test_index_and_selection_are_bound_to_corpus(knowledge, tmp_path):
    value = {'identity': index_identity(knowledge.version), 'generation': 'test'}
    value['fingerprint'] = digest(value)
    write_json(tmp_path / 'index.json', value)
    service = RetrievalService(knowledge, tmp_path)
    assert service.selected_mode() == 'bm25'
    write_json(tmp_path / 'selection.json', {'mode': 'hybrid', 'fingerprint': value['fingerprint']})
    assert service.selected_mode() == 'hybrid'
    knowledge.version = 'different_corpus'
    assert not service.diagnostic()['index_ready']
    assert service.selected_mode() == 'bm25'


@pytest.mark.asyncio
async def test_missing_native_packages_or_weights_falls_back(knowledge, tmp_path, monkeypatch):
    service = RetrievalService(knowledge, tmp_path, 'hybrid')
    monkeypatch.setattr(service, 'warm', AsyncMock(side_effect=ImportError('native unavailable')))
    assert await service.search('陈望道') == knowledge.search('陈望道')
    assert service.actual_mode == 'bm25' and service.reason


@pytest.mark.asyncio
async def test_reranker_exception_returns_completed_fusion(knowledge, tmp_path, monkeypatch):
    service = RetrievalService(knowledge, tmp_path, 'hybrid_rerank')
    ids = list(reversed(knowledge.chunks))[:10]
    monkeypatch.setattr(service, 'warm', AsyncMock())
    monkeypatch.setattr(service, '_kill', AsyncMock())
    monkeypatch.setattr(service, '_rpc', AsyncMock(side_effect=[{'ids': ids}, RuntimeError('native crash')]))
    hits = await service.search('陈望道')
    expected = diverse(fuse(knowledge.rank_bm25('陈望道')[:10], ids)[:10], knowledge.chunks)
    assert hits == expected and service.actual_mode == 'hybrid'
    service._kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_total_timeout_kills_only_owned_worker(knowledge, tmp_path, monkeypatch):
    service = RetrievalService(knowledge, tmp_path, 'dense', timeout=.03)
    monkeypatch.setattr(service, 'warm', AsyncMock())
    monkeypatch.setattr(service, '_kill', AsyncMock())
    async def slow(*_, **__):
        await asyncio.sleep(2)
    monkeypatch.setattr(service, '_rpc', slow)
    assert await service.search('陈望道') == knowledge.search('陈望道')
    service._kill.assert_awaited_once()
    service._kill.reset_mock()
    async with service._lock:
        assert await service.search('青年') == knowledge.search('青年')
    service._kill.assert_not_awaited()


@pytest.mark.asyncio
async def test_cancellation_does_not_leave_reply_for_next_request(knowledge, tmp_path, monkeypatch):
    service = RetrievalService(knowledge, tmp_path, 'dense')
    monkeypatch.setattr(service, 'warm', AsyncMock())
    monkeypatch.setattr(service, '_kill', AsyncMock())
    entered = asyncio.Event()
    async def slow(*_, **__):
        entered.set()
        await asyncio.sleep(2)
    monkeypatch.setattr(service, '_rpc', slow)
    task = asyncio.create_task(service.search('青年'))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    service._kill.assert_awaited_once()


@pytest.mark.asyncio
async def test_rpc_rejects_cross_request_replies(knowledge, tmp_path):
    service = RetrievalService(knowledge, tmp_path, 'dense')
    service.process = SimpleNamespace(returncode=None,
        stdin=SimpleNamespace(write=lambda _: None, drain=AsyncMock()),
        stdout=SimpleNamespace(readline=AsyncMock(return_value=b'{"id":99,"ids":[]}\n')))
    with pytest.raises(RuntimeError):
        await service._rpc('dense', query='青年')


def test_metrics_counts_independent_evidence_groups_once():
    question = {'evidence_groups': [[{'source_id': 'a', 'start_char': 2, 'end_char': 3}],
                                     [{'source_id': 'b', 'start_char': 2, 'end_char': 3}]]}
    hit = {'source_ids': ['a'], 'start_char': 0, 'end_char': 5}
    assert metrics(question, [hit, hit])['recall'] == .5
    assert metrics(question, [hit])['mrr'] == 1
    assert metrics({'evidence_groups': []}, [hit])['recall'] is None


def test_auto_gate_requires_gain_and_rejects_slow_reranker():
    def item(recall, mrr, seconds, row_mrr):
        return {'test': {'recall_at_5': recall, 'mrr_at_5': mrr, 'p95_seconds': seconds},
                'rows': [{'id': 'x', 'split': 'test', 'kind': 'paraphrase', 'recall': recall, 'mrr': row_mrr}]}
    result = {'fingerprint': 'x', 'question_set_sha256': 'y', 'measured_at': 'today', 'methods': {
        'bm25': item(.8, .7, .01, .5), 'hybrid': item(.8, .8, .1, 1),
        'hybrid_rerank': item(.8, .9, 10, 1)}}
    assert choose(result)['mode'] == 'hybrid'
    result['methods']['hybrid']['test']['recall_at_5'] = .79
    assert choose(result)['mode'] == 'bm25'


@pytest.mark.asyncio
async def test_photo_local_evidence_uses_async_retrieval(knowledge, monkeypatch):
    from backend.photo_study import evidence_sources
    monkeypatch.setattr(knowledge, 'asearch', AsyncMock(return_value=knowledge.search('青年')))
    sources = await evidence_sources(knowledge, '青年')
    assert len(sources) == 1 and sources[0]['kind'] == 'local'
    knowledge.asearch.assert_awaited_once_with('青年')
