import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

import backend.main as main
from backend.storage import Store


def payload():
    return {'question': '这是什么？', 'stop_name': '某纪念馆',
            'image_base64': base64.b64encode(b'\x89PNG\r\n\x1a\nimage').decode()}


def test_photo_qa_validates_before_provider_and_does_not_write_history(monkeypatch, tmp_path):
    work = AsyncMock(return_value={'answer': {'status': 'partial'}, 'sources': [],
                                  'usage': {'attempted_calls': 2, 'total_tokens': 123}})
    monkeypatch.setattr(main, 'answer_photo', work)
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'history.sqlite3'))
    with TestClient(main.app) as client:
        for bad in ('not base64!', base64.b64encode(b'<html>bad</html>').decode()):
            assert client.post('/api/study/photo-ask', json={**payload(), 'image_base64': bad}).status_code == 422
        work.assert_not_called()
        assert client.post('/api/study/photo-ask', json={**payload(), 'place_id': 'FAKE'}).status_code == 422
        response = client.post('/api/study/photo-ask', json=payload())
        assert response.status_code == 200 and response.json()['usage']['total_tokens'] == 123
        assert work.call_args.args[1] == '某纪念馆'
        assert main.store.list() == []
        assert client.post('/api/study/photo-ask', content=b'x' * 4_100_001).status_code == 413


@pytest.mark.asyncio
async def test_photo_disconnect_cancels_work(monkeypatch):
    cancelled = asyncio.Event()

    async def pending(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def stream():
        import json
        yield json.dumps(payload()).encode()

    monkeypatch.setattr(main, 'answer_photo', pending)
    request = SimpleNamespace(stream=stream, is_disconnected=AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as error:
        await main.study_photo_ask(request)
    assert error.value.status_code == 499 and cancelled.is_set()
