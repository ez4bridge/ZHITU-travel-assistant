"""Cross-scene HTTP regressions with isolated stores and explicit provider doubles.

The bridge and QA doubles exercise persistence and route wiring, not native or
online acceptance; those are verified separately by the integration owner.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

import backend.main as main
from backend.constraint_models import ConstraintIntentRequest, ConstraintPreviewRequest
from backend.constraint_storage import ConstraintStore, fingerprint
from backend.models import (GuideInsight, PlanningGuideSource, StudyAnswer, StudyContext, StudyFact,
                            StudyOptions, StudyParagraph, StudySource, StudyStop,
                            TripRequest, WeatherDay, WeatherSnapshot)
from plan_samples import sample_plan
from backend.storage import Store


@pytest.fixture
def merged_store(tmp_path, monkeypatch):
    store = Store(tmp_path / 'merged-entrypoints.sqlite3')
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'knowledge', SimpleNamespace(ready=True,
        retriever=lambda: SimpleNamespace(start_background=lambda: None), aclose=AsyncMock()))
    return store


def ordinary_plan():
    plan = sample_plan(days=1)
    plan.request.guides = [PlanningGuideSource(title='用户自己的线索', text='保留两处地点的用户攻略。')]
    plan.itinerary.days[0].tips = ['当天提前核实开放安排']
    plan.itinerary.guide_insights = [GuideInsight(source_index=0, text='保留用户攻略原意')]
    weather = WeatherSnapshot(requested_city='北京', city='北京市', status='available',
        fetched_at=datetime(2026, 10, 22, tzinfo=timezone.utc),
        days=[WeatherDay(date=plan.request.start_date, status='available', day_weather='晴', temp_max=18)])
    plan.weather = weather
    plan.weather_by_city = {'北京': weather.model_copy(deep=True)}
    return plan


def request_body(plan, **changes):
    return ConstraintPreviewRequest(base_fingerprint=fingerprint(plan), expected_routing_revision=0,
        expected_state_revision=0, day_indexes=[0], **changes).model_dump(mode='json')


def assert_metadata(saved, original):
    assert saved.weather == original.weather
    assert saved.weather_by_city == original.weather_by_city
    assert saved.request.guides == original.request.guides
    assert saved.itinerary.guide_insights == original.itinerary.guide_insights
    assert saved.itinerary.days[0].tips == original.itinerary.days[0].tips


def study_plan():
    plan = ordinary_plan()
    plan.id = 'study-merge'
    plan.request = TripRequest(destination='上海', start_date=plan.request.start_date, days=1,
                               study=StudyOptions(), mode='walking')
    for index, stop in enumerate(plan.itinerary.days[0].stops):
        stop.city = '上海'
        stop.study = StudyStop(place_id=f'p{index}', fact_ids=['fact-merge'],
            why_selected='合成研学选择', observation_task='观察合成门牌')
    plan.study_context = StudyContext(corpus_version='synthetic-merge',
        facts=[StudyFact(id='fact-merge', title='合成事实', text='这是测试构造的独立资料，不是历史断言。',
                         source_ids=['source-merge'])],
        sources=[StudySource(id='source-merge', title='合成资料', url='https://example.invalid/synthetic')])
    return plan


@pytest.mark.parametrize(('method', 'suffix'), [
    ('GET', 'constraint-context'), ('GET', 'booking-context'),
    ('POST', 'constraint-intent'), ('POST', 'constraint-preview'),
    ('POST', 'conversation-preview'), ('POST', 'booking-preview'),
    ('POST', 'constraint-apply'), ('POST', 'booking-apply'),
])
def test_study_cannot_enter_ordinary_modification_paths(merged_store, method, suffix):
    plan = study_plan()
    merged_store.save(plan)
    raw_before = merged_store.get(plan.id).model_dump_json()
    command = request_body(plan)
    if suffix == 'conversation-preview':
        body = {'command': command, 'instruction': '取消第一站', 'messages': []}
    elif suffix == 'constraint-intent':
        body = ConstraintIntentRequest(base_fingerprint=fingerprint(plan), expected_routing_revision=0,
            expected_state_revision=0, day_indexes=[0], instruction='取消第一站').model_dump(mode='json')
    elif suffix == 'booking-preview':
        body = {key: value for key, value in command.items() if key in (
            'base_fingerprint', 'expected_routing_revision', 'expected_state_revision')}
    elif suffix.endswith('apply'):
        body = {'preview_id': 'synthetic-preview-never-saved'}
    else:
        body = command
    with TestClient(main.app) as client:
        response = client.request(method, f'/api/plans/{plan.id}/{suffix}',
                                  **({'json': body} if method == 'POST' else {}))
    assert response.status_code == 422, response.text
    assert '历史研学' in response.json()['detail'] and '研学调整' in response.json()['detail']
    assert merged_store.get(plan.id).model_dump_json() == raw_before
    assert len(merged_store.list()) == 1


def test_study_qa_remains_available_with_original_evidence_schema(merged_store, monkeypatch):
    plan = study_plan()
    merged_store.save(plan)
    seen = []

    async def answer(body, knowledge, loaded):
        seen.append((body.question, loaded.id, loaded.study_context.corpus_version))
        return StudyAnswer(status='supported', context=loaded.study_context,
            paragraphs=[StudyParagraph(text='仅引用测试资料中的合成内容。', fact_ids=['fact-merge'])])

    monkeypatch.setattr(main, 'answer_study', answer)
    with TestClient(main.app) as client:
        response = client.post('/api/study/ask', json={
            'plan_id': plan.id, 'place_id': 'p0', 'question': '这份资料支持什么？'})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['status'] == 'supported'
        assert data['paragraphs'][0]['fact_ids'] == ['fact-merge']
        assert data['context']['sources'][0]['id'] == 'source-merge'
        assert client.get(f'/api/plans/{plan.id}').json()['study_context']['corpus_version'] == 'synthetic-merge'
    assert seen == [('这份资料支持什么？', plan.id, 'synthetic-merge')]
    assert len(merged_store.list()) == 1
