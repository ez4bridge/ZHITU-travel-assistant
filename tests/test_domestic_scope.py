import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.domestic_scope import DomesticScopeError, ensure_domestic_trip
from backend.models import TripRequest
from backend.weather import SHANGHAI


def trip(destination='上海', **kwargs):
    return TripRequest(destination=destination, start_date=datetime.now(SHANGHAI).date() + timedelta(days=1), **kwargs)


def response(name, code, level='city'):
    return {'count': '1', 'districts': [{'name': name, 'adcode': code, 'level': level}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('name,official,code,level', [
    ('上海', '上海市', '310000', 'province'),
    ('杭州', '杭州市', '330100', 'city'),
    ('阳朔', '阳朔县', '450321', 'district'),
    ('香港', '香港特别行政区', '810000', 'province'),
    ('澳门', '澳门特别行政区', '820000', 'province'),
])
async def test_official_domestic_city_passes_and_reuses_identity(name, official, code, level):
    client = SimpleNamespace(request=AsyncMock(return_value=response(official, code, level)))
    request = trip(name, origin=name)
    await ensure_domestic_trip(request, client)
    await ensure_domestic_trip(request, client)
    client.request.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('name,data', [
    ('火星', {'count': '0', 'districts': []}),
    ('火星', response('火星村', '430121', 'district')),
    ('月球', {'districts': []}),
    ('东京', {'districts': []}),
    ('巴黎', response('巴黎市', '990100')),
    ('朝阳区', {'count': '2', 'districts': [
        {'name': '朝阳区', 'adcode': '110105', 'level': 'district'},
        {'name': '朝阳区', 'adcode': '220104', 'level': 'district'}]}),
])
async def test_fictional_foreign_fuzzy_or_ambiguous_city_is_rejected(name, data):
    client = SimpleNamespace(request=AsyncMock(return_value=data))
    with pytest.raises(DomesticScopeError, match='国内|中国境内'):
        await ensure_domestic_trip(trip(name), client)


@pytest.mark.asyncio
async def test_unavailable_provider_does_not_skip_verification_or_leak_urls():
    client = SimpleNamespace(request=AsyncMock(side_effect=httpx.ConnectError('secret-signed-url')))
    with pytest.raises(DomesticScopeError, match='核验暂不可用') as error:
        await ensure_domestic_trip(trip(), client)
    assert 'secret' not in str(error.value)
    assert error.value.code == 'domestic_scope_unavailable'


@pytest.mark.asyncio
@pytest.mark.parametrize('body', [
    {'destination': '火星'},
    {'destination': '上海→火星'},
    {'destination': '东京'},
    {'destination': '上海', 'origin': '火星'},
    {'destination': '上海', 'origin': '巴黎', 'return_to_origin': True},
    {'destinations': [{'city': '上海', 'preferred_days': 1}, {'city': '火星', 'preferred_days': 1}]},
])
async def test_api_rejects_invalid_city_before_weather_model_or_rail(monkeypatch, tmp_path, body):
    import backend.main as main
    from backend.amap import Amap
    from backend.railway import Railway
    from backend.storage import Store
    async def administrative(self, path, params):
        assert path == '/v3/config/district'
        return response('上海市', '310000', 'province') if params['keywords'] == '上海' else {'districts': []}
    monkeypatch.setattr(Amap, 'request', administrative)
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'plans.sqlite3'))
    model, weather, rail = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(main, 'generate_itinerary', model)
    monkeypatch.setattr(main, 'forecast_trip', weather)
    monkeypatch.setattr(Railway, 'enrich', rail)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        result = await client.post('/api/plans/generate', json={
            'start_date': str(datetime.now(SHANGHAI).date() + timedelta(days=1)), **body})
    assert 'event: error' in result.text and 'event: result' not in result.text
    assert '中国境内' in result.text
    detail = json.loads(result.text.split('event: error\ndata: ')[1].split('\n\n')[0])
    assert detail['code'] == 'domestic_scope_unconfirmed'
    assert detail['city'] in ('火星', '东京', '巴黎')
    model.assert_not_awaited()
    weather.assert_not_awaited()
    rail.assert_not_awaited()
    assert main.store.list() == []


@pytest.mark.asyncio
@pytest.mark.parametrize('action', ['revise', 'refresh'])
async def test_invalid_legacy_city_remains_readable_but_cannot_be_regenerated(monkeypatch, tmp_path, action):
    import backend.main as main
    from backend.amap import Amap
    from backend.models import PlanRecord
    from backend.storage import Store
    from test_core import sample
    request, itinerary, routes = sample()
    request.destination = '火星'
    plan = PlanRecord(id='legacy-mars', created_at=datetime.now(SHANGHAI), request=request,
        itinerary=itinerary, routes=routes, validation={
            'status': 'passed_with_unknowns', 'errors': [], 'warnings': [], 'unknowns': [],
            'total_cents': 0, 'budget_cents': 100000})
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'legacy.sqlite3'))
    main.store.save(plan)
    before = main.store.get(plan.id).model_dump(mode='json')
    monkeypatch.setattr(Amap, 'request', AsyncMock(return_value={'districts': []}))
    model, enrich = AsyncMock(), AsyncMock()
    monkeypatch.setattr(main, 'generate_itinerary', model)
    monkeypatch.setattr(Amap, 'enrich', enrich)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        assert (await client.get('/api/plans/legacy-mars')).status_code == 200
        result = await client.post(f'/api/plans/legacy-mars/{action}', json={'instruction': '调整活动'})
    assert 'event: error' in result.text and '中国境内' in result.text
    model.assert_not_awaited()
    enrich.assert_not_awaited()
    assert main.store.get(plan.id).model_dump(mode='json') == before


@pytest.mark.asyncio
async def test_valid_multicity_request_reaches_model_after_every_city_is_verified(monkeypatch):
    import backend.main as main
    from backend.amap import Amap
    seen = []
    async def administrative(self, path, params):
        city = params['keywords']; seen.append(city)
        return {'上海': response('上海市', '310000', 'province'),
                '杭州': response('杭州市', '330100'),
                '北京': response('北京市', '110000', 'province')}[city]
    async def model(*args, **kwargs):
        assert seen == ['北京', '上海', '杭州']
        raise RuntimeError('verified-model-start')
    monkeypatch.setattr(Amap, 'request', administrative)
    generate = AsyncMock(side_effect=model)
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        result = await client.post('/api/plans/generate', json=trip('上海→杭州', origin='北京').model_dump(mode='json'))
    generate.assert_awaited_once()
    assert 'verified-model-start' in result.text
