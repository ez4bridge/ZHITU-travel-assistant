from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.amap import MapServiceError
from backend.models import PlanRecord, WeatherDay, WeatherSnapshot
from backend.storage import Store
from backend.weather import SHANGHAI, WeatherService
from test_weather import trip, amap


@pytest.mark.asyncio
@pytest.mark.parametrize('error,code,retryable', [
    (httpx.ConnectError('private signed-url'), 'network', True),
    (MapServiceError('map_auth', 'private api-key'), 'configuration', False),
    (TimeoutError('private signed-url'), 'timeout', True),
])
async def test_safe_weather_failure_explains_the_right_next_step(error, code, retryable):
    result = await WeatherService().forecast(trip(), SimpleNamespace(request=AsyncMock(side_effect=error)))
    assert result.failure_code == code and result.retryable == retryable
    assert result.failure_reason and 'private' not in result.model_dump_json()


@pytest.mark.asyncio
async def test_manual_weather_query_is_fresh_but_does_not_rewrite_plan_or_call_model(monkeypatch, tmp_path):
    import backend.main as main
    from test_core import sample
    request, itinerary, routes = sample()
    plan = PlanRecord(id='weather-retry', created_at=datetime.now(SHANGHAI), request=request,
        itinerary=itinerary, routes=routes, validation={
            'status': 'passed_with_unknowns', 'errors': [], 'warnings': [], 'unknowns': [],
            'total_cents': 12300, 'budget_cents': 100000})
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'weather-retry.sqlite3'))
    main.store.save(plan)
    original = main.store.get(plan.id).model_dump(mode='json')
    fresh = WeatherSnapshot(requested_city='上海', city='上海市', status='available',
        fetched_at=datetime.now(SHANGHAI), days=[WeatherDay(date=request.start_date, status='available', day_weather='晴')])
    query = AsyncMock(return_value=fresh)
    monkeypatch.setattr(main, 'weather_service', SimpleNamespace(forecast=query))
    model = AsyncMock(side_effect=AssertionError('Weather retry must not call the model'))
    monkeypatch.setattr(main, 'generate_itinerary', model)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        result = await client.post('/api/plans/weather-retry/weather', params={'city': '上海市'})
        outside = await client.post('/api/plans/weather-retry/weather', params={'city': '北京'})
    assert result.status_code == 200 and result.json()['days'][0]['day_weather'] == '晴'
    assert outside.status_code == 422 and query.await_count == 1
    assert query.await_args.kwargs == {'force': True}
    assert main.store.get(plan.id).model_dump(mode='json') == original
    model.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_retry_bypasses_only_the_selected_public_cache():
    service = WeatherService()
    await service.forecast(trip(), amap())
    fresh_client = amap()
    result = await service.forecast(trip(), fresh_client, force=True)
    assert not result.from_cache and fresh_client.request.await_count == 2
