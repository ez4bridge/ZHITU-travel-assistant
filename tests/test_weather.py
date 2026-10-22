"""Weather parsing, request isolation and planning integration; no network quota."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.models import PlanRecord, StudyOptions, TripRequest, WeatherDay, WeatherSnapshot
from backend.storage import Store
from backend.weather import (SHANGHAI, WeatherService, normalize_city, parse_forecast,
                             resolve_district, snapshot, unavailable, weather_context)


def today():
    return datetime.now(SHANGHAI).date()


def trip(**values):
    return TripRequest(**{'destination': '上海', 'start_date': today() + timedelta(days=1), 'days': 1, **values})


def district(city='上海市', code='310000'):
    return {'count': '1', 'districts': [{'name': city, 'adcode': code,
        'level': 'province' if city == '上海市' else 'city'}]}


def forecast(code='310000', rain='小雨'):
    return {'forecasts': [{'adcode': code, 'reporttime': datetime.now(SHANGHAI).strftime('%Y-%m-%d %H:%M:%S'),
        'casts': [{'date': str(today() + timedelta(days=i)), 'dayweather': rain, 'nightweather': '阴',
                   'daytemp': '22', 'nighttemp': '18', 'daywind': '东', 'nightwind': '东',
                   'daypower': '1-3', 'nightpower': '1-3'} for i in range(4)]}]}


def amap(city='上海市', code='310000'):
    return SimpleNamespace(request=AsyncMock(side_effect=[district(city, code), forecast(code)]))


@pytest.mark.parametrize('change', ['ambiguous', 'no_match', 'province', 'truncated'])
def test_city_resolution_never_guesses(change):
    data = district('朝阳市', '211300')
    if change == 'ambiguous':
        data['districts'].append({'name': '朝阳市', 'adcode': '999999', 'level': 'city'})
    elif change == 'no_match':
        data['districts'][0]['name'] = '北京市'
    elif change == 'province':
        data['districts'][0]['level'] = 'province'
    else:
        data['count'] = '30'
    with pytest.raises(ValueError):
        resolve_district('朝阳', data)
    assert normalize_city(' 上海市 ') == normalize_city('上海')


@pytest.mark.asyncio
async def test_cache_reuses_public_forecast_but_reselects_dates():
    service, client = WeatherService(), amap()
    first = await service.forecast(trip(days=2), client)
    second = await service.forecast(trip(destination='上海市', start_date=today()+timedelta(days=3), days=3), client)
    assert client.request.await_count == 2 and second.from_cache
    assert first.status == 'available' and second.status == 'partial'
    assert [d.status for d in second.days] == ['available', 'not_covered', 'not_covered']
    second.days[0].day_weather = 'mutated'
    third = await service.forecast(trip(), client)
    assert first.days[0].day_weather == third.days[0].day_weather == '小雨'
    assert second.fetched_at == first.fetched_at


@pytest.mark.asyncio
async def test_city_cache_isolation_expiry_and_limit():
    service = WeatherService()
    one = await service.forecast(trip(), amap())
    other = await service.forecast(trip(destination='杭州'), amap('杭州市', '330100'))
    assert one.adcode == '310000' and other.adcode == '330100'
    service.ttl = -1
    client = amap()
    assert not (await service.forecast(trip(), client)).from_cache
    assert client.request.await_count == 2
    service.ttl = 1800
    for i in range(65):
        name = f'测试{i}市'
        await service.forecast(trip(destination=name), amap(name, f'{100000+i:06}'))
    assert len(service.cache) == 64 and 'amap:上海' not in service.cache


@pytest.mark.asyncio
@pytest.mark.parametrize('offset', [-2, 30])
async def test_outside_forecast_never_uses_today(offset):
    result = await WeatherService().forecast(trip(start_date=today()+timedelta(days=offset)), amap())
    assert result.status == 'not_covered' and result.days[0].day_temp is None


@pytest.mark.asyncio
async def test_bad_fields_are_not_fabricated_and_wrong_city_rejected():
    data = forecast()
    del data['forecasts'][0]['casts'][1]['daytemp']
    data['forecasts'][0]['casts'][1]['nighttemp'] = 'nan'
    value = parse_forecast({'name':'上海市','adcode':'310000'}, data, datetime.now(SHANGHAI))
    result = snapshot(trip(), value, False)
    assert result.days[0].day_temp is None and result.days[0].night_temp is None
    data['forecasts'][0]['adcode'] = '330100'
    with pytest.raises(ValueError, match='目的地'):
        parse_forecast({'name':'上海市','adcode':'310000'}, data, datetime.now(SHANGHAI))


@pytest.mark.parametrize('change', ['old', 'future', 'missing_time', 'duplicate'])
def test_invalid_forecasts_fail_closed(change):
    data = forecast()
    row = data['forecasts'][0]
    if change == 'old':
        row['reporttime'] = (datetime.now(SHANGHAI)-timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
    elif change == 'future':
        row['reporttime'] = (datetime.now(SHANGHAI)+timedelta(days=3)).strftime('%Y-%m-%d %H:%M:%S')
    elif change == 'missing_time':
        row.pop('reporttime')
    else:
        row['casts'].append(deepcopy(row['casts'][1]))
    with pytest.raises(ValueError):
        parse_forecast({'name':'上海市','adcode':'310000'}, data, datetime.now(SHANGHAI))


@pytest.mark.asyncio
async def test_timeout_auth_failure_and_cancel():
    async def slow(*_):
        await asyncio.sleep(5)
    service = WeatherService(timeout=.02)
    result = await service.forecast(trip(), SimpleNamespace(request=slow))
    assert result.status == 'unavailable' and not service.cache
    client = SimpleNamespace(request=AsyncMock(side_effect=RuntimeError('private-key signed-url')))
    result = await service.forecast(trip(), client)
    assert 'private-key' not in result.model_dump_json()
    task = asyncio.create_task(WeatherService().forecast(trip(), SimpleNamespace(request=slow)))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_weather_calls_use_existing_signed_amap_transport(monkeypatch):
    from backend.amap import Amap, signed_params
    from backend.config import settings
    monkeypatch.setattr(settings, 'amap_key', 'test-web-key')
    monkeypatch.setattr(settings, 'amap_sign_secret', 'test-signing-secret')
    seen = []
    def handle(request):
        params = dict(request.url.params)
        expected = signed_params({k:v for k,v in params.items() if k not in ('key','sig')}, 'test-web-key', 'test-signing-secret')
        assert params == expected
        seen.append(request.url.path)
        return httpx.Response(200, json={'status':'1', **(district() if 'district' in request.url.path else forecast())})
    client = Amap('上海')
    await client.client.aclose()
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        result = await WeatherService().forecast(trip(), client)
        assert result.status == 'available' and len(seen) == 2
        assert 'test-web-key' not in result.model_dump_json()
    finally:
        await client.close()


def test_snapshot_store_backward_compatibility_and_independence(tmp_path):
    from test_core import sample
    request, itinerary, routes = sample()
    old = PlanRecord(id='old', created_at=datetime.now(SHANGHAI), request=request,
        itinerary=itinerary, routes=routes, validation={
            'status':'passed_with_unknowns','errors':[],'warnings':[],'unknowns':[], 'total_cents':0,'budget_cents':100000})
    raw = old.model_dump(mode='json')
    raw.pop('weather')
    assert PlanRecord.model_validate(raw).weather is None
    store = Store(tmp_path/'plans.sqlite3')
    store.save(old)
    new = old.model_copy(deep=True, update={'id':'new','parent_id':'old','revision':2,
        'weather':unavailable(request,datetime.now(SHANGHAI))})
    store.save(new)
    assert store.get('old').weather is None and store.get('new').weather.status == 'unavailable'


@pytest.mark.asyncio
async def test_regular_agent_receives_weather_without_extra_model_round(monkeypatch):
    from backend import agent
    from backend.config import settings
    from test_core import sample
    calls = []
    async def create(**kwargs):
        calls.append(deepcopy(kwargs))
        message = SimpleNamespace(tool_calls=None, model_dump=lambda **_: {'role':'assistant','content':'资料已齐'},
            content=sample()[1].model_dump_json())
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
    monkeypatch.setattr(agent, 'AsyncOpenAI', lambda **_: Client())
    monkeypatch.setattr(settings, 'llm_key', 'test')
    monkeypatch.setattr(settings, 'amap_key', 'test')
    weather = await WeatherService().forecast(trip(), amap())
    await agent.generate_itinerary(sample()[0], SimpleNamespace(), AsyncMock(), weather=weather)
    assert len(calls) == 2
    assert json.loads(calls[0]['messages'][1]['content'])['weather']['days'][0]['day_weather'] == '小雨'
    assert '必去地点' in calls[0]['messages'][0]['content']


@pytest.mark.asyncio
@pytest.mark.parametrize('study', [False, True])
async def test_generate_revise_history_and_recheck_share_server_snapshot(study, monkeypatch, tmp_path, offline_domestic_scope):
    import backend.main as main
    from backend.amap import Amap
    from backend.models import StudyContext
    from backend.validation import Validator
    from test_core import sample
    store = Store(tmp_path/'http.sqlite3')
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'validator', Validator())
    monkeypatch.setattr(main, 'knowledge', SimpleNamespace(ready=True))
    seen = []
    async def get_weather(request, _):
        value = unavailable(request, datetime.now(SHANGHAI))
        if not seen:
            value.status = 'available'
            value.days[0] = WeatherDay(date=request.start_date, status='available', day_weather='雨', day_temp=22)
        return value
    main.weather_service.forecast.side_effect = get_weather
    async def regular(request, amap, emit, previous=None, instruction='', weather=None, rail=None, weather_by_city=None):
        seen.append(weather)
        return sample()[1]
    async def study_plan(request, knowledge, amap, emit, validator, previous=None, instruction='', weather=None, rail=None, weather_by_city=None):
        itinerary = await regular(request, amap, emit, previous, instruction, weather)
        from backend.models import StudyFact, StudySource, StudyStop
        context = StudyContext(corpus_version='test',
            facts=[StudyFact(id='fact', title='资料', text='测试资料', source_ids=['source'])],
            sources=[StudySource(id='source', title='来源', url='https://example.cn/source')])
        for stop in itinerary.days[0].stops:
            stop.study = StudyStop(place_id=stop.id, fact_ids=['fact'])
        return itinerary, sample()[2], context, await validator.validate(request,itinerary,sample()[2], context)
    monkeypatch.setattr(main, 'generate_itinerary', regular)
    monkeypatch.setattr(main, 'generate_study_plan', study_plan)
    monkeypatch.setattr(main, 'update_study_request', AsyncMock(side_effect=lambda request,*_: request))
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=sample()[2]))
    body = sample()[0].model_dump(mode='json')
    if study:
        body['mode'] = 'walking'
        body['study'] = StudyOptions().model_dump(mode='json')
    def result(response):
        assert 'event: error' not in response.text, response.text
        return json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        first = result(await client.post('/api/plans/generate', json=body))
        second = result(await client.post(f"/api/plans/{first['id']}/revise", json={'instruction':'保留主题，缩短露天停留'}))
        assert first['weather'] == seen[0].model_dump(mode='json')
        assert second['weather'] == seen[1].model_dump(mode='json')
        assert second['weather']['status'] == 'unavailable' and second['parent_id'] == first['id']
        assert (await client.get(f"/api/plans/{first['id']}")).json() == first
        assert (await client.post(f"/api/plans/{first['id']}/validate")).status_code == 200
        assert main.weather_service.forecast.await_count == 2
