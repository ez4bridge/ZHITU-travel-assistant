"""Conversation isolation, cancellation and evidence reuse; no live providers."""
import asyncio
import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

import backend.main as main
import backend.photo_study as photo_module
from backend.conversation import ConversationStore, ConversationError, context_for, encoded, turn_memory
from backend.models import StudyAnswer, StudyParagraph, StudyQuestion
from backend.qa_usage import UsageCollector


def question(cid, revision=0, text='他在哪里翻译的？', **kwargs):
    return StudyQuestion(question=text, conversation_id=cid, request_id=str(uuid4()), expected_revision=revision, **kwargs)


def supported(text='陈望道在义乌完成翻译。'):
    return StudyAnswer(status='supported', paragraphs=[StudyParagraph(text=text, fact_ids=['fresh'])])


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    value = ConversationStore(tmp_path / 'chat.sqlite3')
    monkeypatch.setattr(main, 'conversations', value)
    monkeypatch.setattr(main, 'knowledge', SimpleNamespace(ready=True,
        retriever=lambda: SimpleNamespace(start_background=lambda: None), aclose=AsyncMock()))
    return value


def test_storage_restart_budget_retention_and_isolation(local_store):
    first, other = local_store.create(None), local_store.create(None)
    for index in range(23):
        body = question(first['id'], index, '陈望道' + '问题。' * 100)
        _, fingerprint, duplicate = local_store.prepare(body)
        assert duplicate is None
        result = supported().model_dump(mode='json')
        local_store.commit(body, fingerprint, 'text', '第一处地点', result, turn_memory(result, {}))
        local_store.release(first['id'])
    restarted = ConversationStore(local_store.path)
    restored = restarted.get(first['id'], internal=True)
    assert len(restored['turns']) == 20 and restored['revision'] == 23
    memory = context_for(restored['turns'])
    assert len(memory['recent_turns']) <= 3 and len(encoded(memory)) <= 2400
    assert restarted.get(other['id'])['turns'] == []
    assert 'memory' not in restarted.get(first['id'])['turns'][0]


def test_versions_dedup_delete_and_late_commit(local_store):
    cid = local_store.create(None)['id']
    body = question(cid)
    _, digest, _ = local_store.prepare(body)
    with pytest.raises(ConversationError) as busy:
        local_store.prepare(question(cid))
    assert busy.value.status == 409
    result = supported().model_dump(mode='json')
    saved = local_store.commit(body, digest, 'text', '', result, turn_memory(result, {}))
    local_store.release(cid)
    assert local_store.prepare(body)[2] == saved
    changed = body.model_copy(update={'question': '改了内容'})
    with pytest.raises(ConversationError):
        local_store.prepare(changed)
    with pytest.raises(ConversationError):
        local_store.prepare(question(cid, 0))
    next_body = question(cid, 1)
    _, digest, _ = local_store.prepare(next_body)
    local_store.delete(cid)
    with pytest.raises(ConversationError):
        local_store.commit(next_body, digest, 'text', '', result, {})
    local_store.release(cid)
    with pytest.raises(ConversationError):
        local_store.get(cid)


def test_binding_expiration_and_cache(local_store):
    session = local_store.create('a-plan')
    with pytest.raises(ConversationError) as error:
        local_store.prepare(question(session['id']))
    assert error.value.status == 422
    local_store.cache_web(session['id'], [{'kind': 'web', 'text': '临时正文'}])
    assert local_store.web_cache(session['id'])
    assert b'\xe4\xb8\xb4\xe6\x97\xb6\xe6\xad\xa3\xe6\x96\x87' not in local_store.path.read_bytes()
    with local_store.connect() as db:
        db.execute("UPDATE conversations SET updated_at='2000-01-01' WHERE id=?", (session['id'],))
    with pytest.raises(ConversationError):
        local_store.get(session['id'])
    local_store.prune()
    local_store.delete(session['id'])
    assert not local_store.web_cache(session['id'])


def test_http_text_history_and_legacy_compatibility(local_store, monkeypatch):
    seen = []

    async def answer(body, knowledge, plan, memory=None, usage=None):
        seen.append(memory)
        if usage:
            usage.calls.append({'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}})
        return supported()

    monkeypatch.setattr(main, 'answer_study', answer)
    with TestClient(main.app) as client:
        cid = client.post('/api/study/conversations', json={}).json()['id']
        first = question(cid, text='陈望道在哪里翻译？')
        response = client.post('/api/study/ask', json=first.model_dump())
        assert response.status_code == 200 and response.json()['usage']['total_tokens'] == 30
        followup = client.post('/api/study/ask', json=question(cid, 1).model_dump())
        assert followup.status_code == 200 and '陈望道' in encoded(seen[-1])
        assert client.post('/api/study/ask', json=first.model_dump()).json() == response.json()
        assert len(seen) == 2
        assert client.get('/api/study/conversations/' + cid).json()['revision'] == 2
        legacy = client.post('/api/study/ask', json={'question': '陈望道在哪里翻译？'})
        assert legacy.status_code == 200 and 'conversation_id' not in legacy.json()
        assert client.post('/api/study/ask', json={'question': '问题', 'conversation_id': cid}).status_code == 422
        assert client.delete('/api/study/conversations/' + cid).status_code == 204
        assert client.get('/api/study/conversations/' + cid).status_code == 404


def test_photo_memory_no_image_persistence_and_no_local_corpus_gate(local_store, monkeypatch):
    seen = []

    async def photo(data, stop, query, knowledge, memory, state, cached_sources):
        seen.append((data, stop, memory, cached_sources))
        state.update(observation={'kind': 'scene', 'objects': ['印刷机'], 'uncertainties': ['年代未知']},
                     sources=[{'kind': 'web', 'url': 'https://www.example.gov.cn/page', 'text': 'ONLY_IN_MEMORY'}])
        return {'answer': {'status': 'partial', 'visible_summary': '印刷机', 'history': [], 'unknowns': ['年代未知']},
                'sources': [], 'usage': {'attempted_calls': 1, 'total_tokens': 90}}

    monkeypatch.setattr(main, 'answer_photo', photo)
    monkeypatch.setattr(main, 'knowledge', SimpleNamespace(ready=False,
        retriever=lambda: SimpleNamespace(start_background=lambda: None), aclose=AsyncMock()))
    with TestClient(main.app) as client:
        cid = client.post('/api/study/conversations', json={}).json()['id']
        image = base64.b64encode(b'\x89PNG\r\n\x1a\nnot-a-user-image').decode()
        body = {**question(cid).model_dump(), 'image_base64': image, 'stop_name': '又新印刷所'}
        assert client.post('/api/study/photo-ask', json=body).status_code == 200
        followup = client.post('/api/study/ask', json=question(cid, 1, '它有什么作用？').model_dump())
        assert followup.status_code == 200 and followup.json()['kind'] == 'photo_followup'
        assert seen[1][0] is None and seen[1][1] == '又新印刷所'
        assert '年代未知' in encoded(seen[1][2]) and seen[1][3][0]['text'] == 'ONLY_IN_MEMORY'
        stored = local_store.path.read_bytes()
        assert image.encode() not in stored and b'ONLY_IN_MEMORY' not in stored and b'image_base64' not in stored
        assert client.post('/api/study/ask', json=question(cid, 2, use_photo_context=False).model_dump()).status_code == 503
        assert cid not in local_store.active


@pytest.mark.asyncio
async def test_cancelled_question_never_commits(local_store, monkeypatch):
    cancelled = asyncio.Event()

    async def pending(*args, **kwargs):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, 'answer_study', pending)
    cid = local_store.create(None)['id']
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as error:
        await main.study_ask(question(cid), request)
    assert error.value.status_code == 499 and cancelled.is_set()
    assert local_store.get(cid)['turns'] == [] and cid not in local_store.active


@pytest.mark.asyncio
async def test_photo_followup_uses_cached_evidence_without_vision(monkeypatch):
    requests = []

    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            await self.http.aclose()

        async def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(id='fake', model='fake', usage=None, choices=[SimpleNamespace(finish_reason='stop',
                message=SimpleNamespace(content=json.dumps({'status': 'partial', 'visible_summary': '沿用印刷机线索',
                    'history': [{'text': '印刷机参与书刊印制。', 'source_ids': ['S1']}], 'unknowns': ['年代未知']})))])

    def factory(**kwargs):
        client = Client()
        client.http = kwargs['http_client']
        return client

    search = AsyncMock(side_effect=AssertionError('缓存命中不应联网'))
    monkeypatch.setattr(photo_module, 'AsyncOpenAI', factory)
    monkeypatch.setattr(photo_module, 'online_sources', search)
    monkeypatch.setattr(photo_module.settings, 'llm_key', 'fake')
    monkeypatch.setattr(photo_module.settings, 'llm_url', 'https://api.deepseek.com')
    result = await photo_module.answer_photo(None, '印刷所', '这台机器有什么用途？', SimpleNamespace(ready=False),
        memory={'photo_observation': {'kind': 'scene', 'objects': ['印刷机'], 'search_terms': ['印刷机'], 'uncertainties': ['年代未知']}},
        cached_sources=[{'kind': 'web', 'title': '印刷机', 'url': 'https://www.example.gov.cn/p', 'text': '印刷所印刷机印制书刊。' * 20}])
    assert len(requests) == 1 and result['usage']['vision_calls'] == 0 and result['usage']['web_cache_hits'] == 1
    assert 'image_url' not in json.dumps(requests)


@pytest.mark.asyncio
async def test_usage_accounts_attempts_without_guessing_missing_tokens():
    collector = UsageCollector()
    response = SimpleNamespace(usage=None)
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=response))))
    await collector.track(client).chat.completions.create(messages=[])
    assert collector.result()['total_tokens'] is None and collector.result()['usage_missing_calls'] == 1
