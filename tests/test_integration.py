"""Integration tests for travel and study services."""
import asyncio
import json
import time
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from backend.models import (GuideSource, PlanningGuideSource, PlanRecord, StudyContext,
                            StudyOptions, TripRequest, WeatherDay)
from backend.storage import Store
from backend.weather import SHANGHAI, forecast_trip, unavailable
from test_core import sample


def record(study=False):
    trip, itinerary, routes = sample()
    if study:
        trip = TripRequest.model_validate({**trip.model_dump(), 'mode': 'walking', 'study': StudyOptions().model_dump()})
    return PlanRecord(id='integration-test', created_at=datetime.now(SHANGHAI), request=trip,
        itinerary=itinerary, routes=routes,
        validation={'status': 'passed_with_unknowns', 'errors': [], 'warnings': [], 'unknowns': [],
                    'total_cents': 0, 'budget_cents': 300000},
        study_context=StudyContext(corpus_version='frozen') if study else None)


def test_distinct_guide_inputs_and_web_sources_remain_strict():
    pasted = PlanningGuideSource(title='攻略正文', text='沿江步行再去公园')
    web = GuideSource(id='S1', title='公告', url='https://example.cn', kind='official', retrieved_at=datetime.now(SHANGHAI))
    assert pasted.text and web.id == 'S1'
    with pytest.raises(ValidationError):
        PlanningGuideSource.model_validate(web.model_dump())
    with pytest.raises(ValidationError):
        GuideSource.model_validate(pasted.model_dump())


def test_old_study_and_multicity_records_coexist_without_rewriting_json(tmp_path):
    store = Store(tmp_path / 'plans.sqlite3')
    study = record(True)
    study.weather = unavailable(study.request, datetime.now(SHANGHAI))
    raw = study.model_dump(mode='json')
    for field in ['weather_by_city', 'routing_revision', 'schedule_baseline', 'adjustments']:
        raw.pop(field)
    for field in ['destinations', 'intercity_mode', 'compare_modes', 'guides', 'guide_mode']:
        raw['request'].pop(field)
    original = json.dumps(raw, ensure_ascii=False)
    with store.connect() as db:
        db.execute('INSERT INTO plans VALUES (?, ?, ?)', (study.id, study.created_at.isoformat(), original))
    loaded = store.get(study.id)
    assert loaded.study_context.corpus_version == 'frozen' and loaded.weather == study.weather
    assert loaded.weather_by_city == {} and loaded.routing_revision == 0
    with store.connect() as db:
        assert db.execute('SELECT data FROM plans').fetchone()[0] == original
    travel = record()
    travel.id = 'travel'
    travel.request = TripRequest(start_date=study.request.start_date, destinations=[
        {'city': '成都', 'preferred_days': 1}, {'city': '重庆', 'preferred_days': 1}])
    store.save(travel)
    assert len(store.list()) == 2 and store.get('travel').request.city_route == ['成都', '重庆']


@pytest.mark.asyncio
async def test_multicity_weather_has_one_deadline_and_cancels_slow_city():
    trip = TripRequest(start_date='2026-10-27', destinations=[{'city': '上海'}, {'city': '杭州'}])
    cancelled = []
    async def forecast(request, amap):
        if request.destination == '杭州':
            try:
                await asyncio.sleep(5)
            finally:
                cancelled.append('杭州')
        value = unavailable(request, datetime.now(SHANGHAI))
        value.days[0] = WeatherDay(date=trip.start_date, status='available', day_weather='晴')
        return value
    started = time.monotonic()
    single, cities = await forecast_trip(trip, None, SimpleNamespace(timeout=.04, forecast=forecast))
    assert single is None and set(cities) == {'上海', '杭州'}
    assert cities['上海'].days[0].day_weather == '晴'
    assert cities['杭州'].status == 'unavailable' and cancelled == ['杭州']
    assert time.monotonic() - started < .5


@pytest.mark.asyncio
async def test_web_guide_search_uses_selected_stop_city(monkeypatch):
    import backend.place_guide as module
    plan = record()
    plan.request = TripRequest(destination='上海→杭州', start_date='2026-10-27')
    stop = plan.itinerary.days[0].stops[0]
    stop.city = '杭州'
    searches = []
    async def web(http, calls, method, args, **kwargs):
        searches.append(args['query'])
        return {'results': []}
    monkeypatch.setattr(module, 'web_request', web)
    assert await module.GuideService().sources(None, {}, plan, stop) == []
    assert len(searches) == 2 and all(query.startswith('杭州 ') for query in searches)
    assert not any('上海→杭州' in query for query in searches)


@pytest.mark.asyncio
async def test_route_updates_preserve_weather_and_study_is_not_refreshed(monkeypatch, tmp_path):
    import backend.main as main
    from backend.models import RouteOption
    from backend.validation import Validator
    store = Store(tmp_path / 'plans.sqlite3')
    plan = record()
    plan.weather = unavailable(plan.request, datetime.now(SHANGHAI))
    plan.weather_by_city = {'上海': plan.weather.model_copy(deep=True)}
    route = plan.routes[0]
    route.alternatives = [RouteOption(mode='walking', available=True, distance_m=100,
        duration_s=60, polyline=[(121.49,31.23), (121.49,31.22)])]
    store.save(plan)
    study = record(True)
    study.id = 'study'
    store.save(study)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'validator', Validator())
    forecast = main.weather_service.forecast
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/integration-test/route', json={
            'day_index': 0, 'from_id': route.from_id, 'to_id': route.to_id, 'mode': 'walking', 'expected_routing_revision': 0})
        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated['weather'] == plan.weather.model_dump(mode='json')
        assert updated['weather_by_city']['上海'] == updated['weather']
        assert updated['id'] == plan.id and updated['routing_revision'] == 1
        assert (await client.post('/api/plans/study/refresh')).status_code == 422
        assert (await client.post('/api/plans/study/route', json={
            'day_index': 0, 'from_id': route.from_id, 'to_id': route.to_id, 'mode': 'walking'})).status_code == 422
    assert forecast.await_count == 0


@pytest.mark.asyncio
async def test_deleting_plan_retains_other_versions_and_readable_chat(monkeypatch, tmp_path):
    import backend.main as main
    from backend.conversation import ConversationStore
    from backend.place_guide import GuideStore
    store = Store(tmp_path / 'plans.sqlite3')
    guides = GuideStore(store.path)
    conversations = ConversationStore(tmp_path / 'chats.sqlite3')
    study = record(True)
    store.save(study)
    other = record()
    other.id = 'keep'
    store.save(other)
    chat = conversations.create(study.id)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'guide_store', guides)
    monkeypatch.setattr(main, 'conversations', conversations)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        assert (await client.delete('/api/plans/' + study.id)).status_code == 200
        assert (await client.get('/api/plans/keep')).status_code == 200
        assert (await client.get('/api/study/conversations/' + chat['id'])).status_code == 200
        missing = await client.post('/api/study/ask', json={'question': '这里是什么？', 'plan_id': study.id})
        assert missing.status_code == 404 and '新建会话' in missing.text
        assert (await client.delete('/api/study/conversations/' + chat['id'])).status_code == 204
