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
import backend.constraint_replan as replan
import backend.planning_conversation as dialogue
from backend.constraint_intent import IntentPatch
from backend.constraint_models import ConstraintIntentRequest, ConstraintPreviewRequest
from backend.constraint_storage import ConstraintStore, fingerprint
from backend.models import (GuideInsight, PlanningGuideSource, StudyAnswer, StudyContext, StudyFact,
                            StudyOptions, StudyParagraph, StudySource, StudyStop,
                            TripRequest, WeatherDay, WeatherSnapshot)
from backend.storage import Store
from test_constraint_replan import FixedBridge, sample_plan


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


def test_constraint_http_preview_apply_preserves_weather_and_user_sources(merged_store, monkeypatch):
    import backend.constraint_routes as routes
    original = ordinary_plan()
    merged_store.save(original)
    raw_before = merged_store.get(original.id).model_dump_json()
    actual_preview = replan.preview

    async def isolated_preview(plan, state, body, storage):
        return await actual_preview(plan, state, body, storage, bridge=FixedBridge())

    monkeypatch.setattr(routes.constraint_replan, 'preview', isolated_preview)
    with TestClient(main.app) as client:
        preview = client.post(f'/api/plans/{original.id}/constraint-preview',
            json=request_body(original, budget_cents=90000))
        assert preview.status_code == 200, preview.text
        assert preview.json()['outcome'] == 'provisional'
        assert len(merged_store.list()) == 1
        applied = client.post(f'/api/plans/{original.id}/constraint-apply',
            json={'preview_id': preview.json()['preview_id']})
        assert applied.status_code == 200, applied.text
        child_id = applied.json()['plan']['id']
    child = merged_store.get(child_id)
    assert child.parent_id == original.id and child.request.budget == 900
    assert_metadata(child, original)
    assert merged_store.get(original.id).model_dump_json() == raw_before
    assert ConstraintStore(merged_store.path).state(child)['state_revision'] == 1


def test_planning_dialogue_http_preview_apply_preserves_weather_and_user_sources(merged_store, monkeypatch):
    import backend.planning_conversation_routes as routes
    original = ordinary_plan()
    merged_store.save(original)
    raw_before = merged_store.get(original.id).model_dump_json()

    async def interpret_budget(plan, state, body):
        assert body.instruction == '全员预算改成900元'
        return dialogue.Proposal(patch=IntentPatch(budget_cents=90000), reply='已按900元全员预算形成预览。')

    async def isolated_dialogue(plan, state, body, storage):
        return await dialogue.conversation_preview(plan, state, body, storage,
            interpreter=interpret_budget, bridge=FixedBridge())

    monkeypatch.setattr(routes, 'conversation_preview', isolated_dialogue)
    with TestClient(main.app) as client:
        response = client.post(f'/api/plans/{original.id}/conversation-preview', json={
            'command': request_body(original), 'instruction': '全员预算改成900元', 'messages': []})
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['unresolved'] == [] and result['preview']['outcome'] == 'provisional'
        assert len(merged_store.list()) == 1
        applied = client.post(f'/api/plans/{original.id}/constraint-apply',
            json={'preview_id': result['preview']['preview_id']})
        assert applied.status_code == 200, applied.text
        child_id = applied.json()['plan']['id']
    child = merged_store.get(child_id)
    assert child.parent_id == original.id and child.request.budget == 900
    assert_metadata(child, original)
    assert merged_store.get(original.id).model_dump_json() == raw_before


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
