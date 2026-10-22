import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.cities import CityCatalog, city_entries
from backend.models import Itinerary, PlanRecord, TripRequest, ValidationReport
from backend.storage import Store
from backend.validation import normalized_input, validate_core


def stay_case(preferred='0.5'):
    request = TripRequest(start_date='2026-10-21', days=1,
                          destinations=[{'city': '北京', 'preferred_days': preferred}])
    itinerary = Itinerary(title='半日游', summary='北京', days=[{
        'date': '2026-10-21', 'title': '天坛', 'stops': [
            {'id': 'a', 'name': '天坛', 'city': '北京', 'start': '09:00', 'end': '12:00'}],
        'expenses': [{'label': '餐饮', 'category': 'food', 'unit_cost': 50}]}])
    return request, itinerary, []


def test_structured_destinations_preserve_order_duration_and_auto():
    request = TripRequest(start_date='2026-10-21', destinations=[
        {'city': ' 成都 ', 'preferred_days': 2}, {'city': '重庆', 'preferred_days': None},
        {'city': '西安', 'preferred_days': '1.5'}])
    assert request.destination == '成都→重庆→西安'
    assert request.city_route == ['成都', '重庆', '西安']
    assert [stay.preferred_days for stay in request.destinations] == [Decimal(2), None, Decimal('1.5')]
    assert TripRequest.model_validate_json(request.model_dump_json()) == request


@pytest.mark.parametrize('days', [0, -1, '0.25', '1.1', 11, 'NaN'])
def test_invalid_duration_rejected(days):
    with pytest.raises(ValidationError):
        TripRequest(start_date='2026-10-21', destinations=[{'city': '北京', 'preferred_days': days}])


@pytest.mark.parametrize('city', [' ', '北京→天津', '北京,天津', '北京→', '→北京'])
def test_each_row_is_one_city(city):
    with pytest.raises(ValidationError):
        TripRequest(start_date='2026-10-21', destinations=[{'city': city}])


def test_five_city_limit_applies_to_both_input_formats():
    names = ['成都', '重庆', '西安', '北京', '天津', '南京']
    assert len(TripRequest(start_date='2026-10-21', destination='→'.join(names[:5])).city_route) == 5
    for fields in [{'destination': '→'.join(names)}, {'destinations': [{'city': name} for name in names]}]:
        with pytest.raises(ValidationError):
            TripRequest(start_date='2026-10-21', **fields)
    with pytest.raises(ValidationError, match='顺序不一致'):
        TripRequest(start_date='2026-10-21', destination='成都→重庆', destinations=[{'city': '重庆'}, {'city': '成都'}])


def test_legacy_six_city_history_readable_but_cannot_be_revised(monkeypatch, tmp_path, fixture_trip_clock):
    import backend.main as main
    request, itinerary, routes = stay_case(None)
    plan = PlanRecord(id='six-old', created_at=datetime.now(timezone.utc), request=request, itinerary=itinerary,
                      routes=routes, validation=ValidationReport(**validate_core(normalized_input(request, itinerary, routes))))
    raw = plan.model_dump(mode='json')
    raw['request'].pop('destinations')
    raw['request']['destination'] = '成都→重庆→西安→北京→天津→南京'
    store = Store(tmp_path / 'legacy.sqlite3')
    with store.connect() as db:
        db.execute('INSERT INTO plans VALUES (?, ?, ?)', ('six-old', plan.created_at.isoformat(), json.dumps(raw)))
    assert len(store.get('six-old').request.city_route) == 6
    assert len(store.list()) == 1
    monkeypatch.setattr(main, 'store', store)
    with TestClient(main.app) as client:
        assert client.get('/api/plans/six-old').status_code == 200
        response = client.post('/api/plans/six-old/revise', json={'instruction': '更轻松'})
        assert response.status_code == 422 and '最多 5' in response.text


def test_stay_preference_is_checked_without_overriding_total_days():
    request, itinerary, routes = stay_case('0.5')
    data = normalized_input(request, itinerary, routes)
    assert data['journey']['stays'] == [{'city': '北京', 'expected_halves': 1, 'actual_halves': 1}]
    assert not validate_core(data)['warnings']
    request.destinations[0].preferred_days = Decimal('2')
    result = validate_core(normalized_input(request, itinerary, routes))
    assert result['status'] == 'issues'
    assert result['warnings'][0]['code'] == 'stay_preference'
    assert '2 天，当前约 0.5 天' in result['warnings'][0]['message']
    assert request.days == 1 and len(itinerary.days) == 1


@pytest.mark.parametrize('start,end,kind,halves', [
    ('09:00', '12:00', 'sight', 1), ('12:00', '15:00', 'sight', 1),
    ('11:00', '13:00', 'sight', 2), ('09:00', '18:00', 'break', 2),
    ('09:00', '18:00', 'hotel', 0), ('11:00', '13:00', 'meal', 0),
])
def test_halfday_boundaries_and_non_sightseeing_excluded(start, end, kind, halves):
    request, itinerary, routes = stay_case()
    stop = itinerary.days[0].stops[0]
    stop.start, stop.end, stop.kind = start, end, kind
    assert normalized_input(request, itinerary, routes)['journey']['stays'][0]['actual_halves'] == halves


def test_revisiting_city_counts_preferences_separately():
    request = TripRequest(start_date='2026-10-21', days=3, destinations=[
        {'city': '北京', 'preferred_days': '.5'}, {'city': '天津'}, {'city': '北京', 'preferred_days': 1}])
    days = []
    for index, city in enumerate(request.city_route):
        days.append({'date': f'2026-10-{21+index}', 'title': city, 'stops': [
            {'id': str(index), 'city': city, 'name': city, 'start': '09:00', 'end': '12:00' if index == 0 else '15:00'}]})
    data = normalized_input(request, Itinerary(title='往返', summary='', days=days), [])
    assert [stay['actual_halves'] for stay in data['journey']['stays']] == [1, 2]


def test_structured_preferences_survive_storage_and_recheck(tmp_path):
    request, itinerary, routes = stay_case('1')
    store = Store(tmp_path / 'preferences.sqlite3')
    result = validate_core(normalized_input(request, itinerary, routes))
    plan = PlanRecord(id='preferred', created_at=datetime.now(timezone.utc), request=request, itinerary=itinerary,
                      routes=routes, validation=ValidationReport(**result))
    store.save(plan)
    restored = store.get('preferred')
    assert restored.request.destinations[0].preferred_days == Decimal('1')
    assert validate_core(normalized_input(restored.request, restored.itinerary, restored.routes)) == result


def test_structured_request_flows_through_api_and_reports_preference(monkeypatch, tmp_path, fixture_trip_clock):
    import backend.main as main
    from backend.amap import Amap
    from backend.validation import Validator
    request, itinerary, _ = stay_case('1')
    async def generate(trip, amap, emit, previous=None, instruction='', rail=None, weather=None, weather_by_city=None):
        assert trip.city_route == ['北京']
        assert trip.destinations[0].preferred_days == Decimal('1')
        return itinerary.model_copy(deep=True)
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'structured.sqlite3'))
    monkeypatch.setattr(main, 'validator', Validator())
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=[]))
    body = request.model_dump(mode='json')
    body.pop('destination')
    with TestClient(main.app) as client:
        response = client.post('/api/plans/generate', json=body)
        assert 'event: error' not in response.text
        result = json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
        assert result['request']['destination'] == '北京'
        assert result['validation']['warnings'][0]['code'] == 'stay_preference'
        saved = client.get(f'/api/plans/{result["id"]}').json()
        assert saved['request']['destinations'] == result['request']['destinations']


@pytest.mark.asyncio
async def test_agent_receives_selected_stay_preferences(monkeypatch):
    import backend.agent as agent
    from backend.config import settings
    from types import SimpleNamespace
    request, itinerary, _ = stay_case('0.5')
    class Message:
        tool_calls = []
        def model_dump(self, **kwargs):
            return {'role': 'assistant', 'content': '资料已收集'}
    complete = AsyncMock(side_effect=[
        SimpleNamespace(choices=[SimpleNamespace(message=Message())]),
        SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=itinerary.model_dump_json()))])])
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=complete))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(agent, 'AsyncOpenAI', lambda **kwargs: Client())
    monkeypatch.setattr(settings, 'llm_key', 'test-key')
    monkeypatch.setattr(settings, 'amap_key', 'test-map-key')
    await agent.generate_itinerary(request, SimpleNamespace(), AsyncMock())
    system = complete.await_args_list[0].kwargs['messages'][0]['content']
    assert '北京：0.5 天' in system and '总行程 1 天是硬约束' in system


def administrative_data():
    return {'districts': [{'districts': [
        {'name': '北京市', 'adcode': '110000', 'districts': [{'name': '朝阳区', 'adcode': '110105'}]},
        {'name': '四川省', 'adcode': '510000', 'districts': [
            {'name': '成都市', 'adcode': '510100'}, {'name': '绵阳市', 'adcode': '510700'}]}]}]}


def test_city_catalog_separates_city_and_province():
    entries = city_entries(administrative_data())
    assert {'name': '成都市', 'province': '四川省', 'adcode': '510100'} in entries
    assert {entry['name'] for entry in entries} == {'北京市', '成都市', '绵阳市'}


@pytest.mark.asyncio
async def test_city_suggestions_cache_only_public_metadata(monkeypatch):
    from backend.amap import Amap
    call = AsyncMock(return_value=administrative_data())
    monkeypatch.setattr(Amap, 'request', call)
    catalog = CityCatalog()
    assert await catalog.search('') == []
    assert (await catalog.search('成都'))[0]['province'] == '四川省'
    assert (await catalog.search('北京'))[0]['adcode'] == '110000'
    assert call.await_count == 1


def test_city_suggestion_endpoint_never_exposes_provider_credentials(monkeypatch):
    from backend.main import app, city_catalog
    monkeypatch.setattr(city_catalog, 'search', AsyncMock(return_value=[{'name': '成都市', 'province': '四川省', 'adcode': '510100'}]))
    with TestClient(app) as client:
        assert client.get('/api/cities', params={'query': '成都'}).json()[0]['name'] == '成都市'
        assert client.get('/api/cities', params={'query': 'x' * 41}).status_code == 422
    monkeypatch.setattr(city_catalog, 'search', AsyncMock(side_effect=RuntimeError('private-provider-response')))
    with TestClient(app) as client:
        response = client.get('/api/cities', params={'query': '成都'})
        assert response.status_code == 503 and 'private-provider-response' not in response.text


def test_total_days_derived_from_city_durations_and_roundtrip():
    request = TripRequest(start_date='2026-10-21', origin='北京', return_to_origin=True,
                          destinations=[{'city': '成都', 'preferred_days': '1.5'},
                                        {'city': '重庆', 'preferred_days': '1'}])
    assert request.days == 3
    assert request.journey_cities == ['北京', '成都', '重庆', '北京']
    assert TripRequest.model_validate_json(request.model_dump_json()) == request
    halves = TripRequest(start_date='2026-10-21', destinations=[
        {'city': '成都', 'preferred_days': '.5'}, {'city': '重庆', 'preferred_days': '.5'}])
    assert halves.days == 1


def test_derived_duration_limit_and_legacy_total_preserved():
    with pytest.raises(ValidationError, match='合计最多 10 天'):
        TripRequest(start_date='2026-10-21', destinations=[
            {'city': '成都', 'preferred_days': 6}, {'city': '重庆', 'preferred_days': 5}])
    request = TripRequest(start_date='2026-10-21', days=5, destinations=[
        {'city': '成都', 'preferred_days': 1}, {'city': '重庆', 'preferred_days': 1}])
    assert request.days == 5
    assert TripRequest(destination='成都', start_date='2026-10-21').days == 3


def test_single_city_duration_does_not_add_intercity_unknown():
    request, itinerary, routes = stay_case('0.5')
    result = validate_core(normalized_input(request, itinerary, routes))
    assert 'intercity_estimates' not in {item['code'] for item in result['unknowns']}
