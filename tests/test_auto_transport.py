from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.amap import Amap, MapServiceError
from backend.storage import Store
from backend.validation import Validator
from test_core import sample
from test_optimization import saved_plan


@pytest.mark.asyncio
async def test_auto_selects_shortest_available_provider_duration_even_when_comparison_flag_off():
    _, itinerary, routes = sample()
    amap = Amap('上海')
    amap.resolve = AsyncMock()
    times = {'walking': 1200, 'transit': 300, 'driving': 500, 'taxi': 500, 'bicycling': 600}
    async def route(a, b, mode):
        return {'distance_m': 2000, 'duration_s': times[mode], 'polyline': routes[0].polyline}
    amap.route = route
    try:
        result = await amap.enrich(itinerary, 'auto', compare_modes=False)
        assert result[0].mode == 'transit' and result[0].duration_s == 300
        assert len(result[0].alternatives) == 5 and '通行时间最短' in result[0].selection_reason
        assert not result[0].user_selected
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_auto_survives_one_endpoint_quota_and_uses_other_real_routes():
    _, itinerary, routes = sample()
    amap = Amap('上海')
    amap.resolve = AsyncMock()
    async def route(a, b, mode):
        if mode == 'walking':
            raise MapServiceError('map_quota', '步行额度不足', True)
        return {'distance_m': 2000, 'duration_s': 400 if mode == 'bicycling' else 600,
                'polyline': routes[0].polyline}
    amap.route = route
    try:
        result = await amap.enrich(itinerary, 'auto')
        assert result[0].mode == 'bicycling' and result[0].available
        assert not next(o for o in result[0].alternatives if o.mode == 'walking').available
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_provider_multiple_paths_selects_fastest_with_valid_geometry():
    amap = Amap('上海')
    amap.request = AsyncMock(return_value={'route': {'taxi_cost': '30', 'paths': [
        {'duration': '900', 'distance': '2000', 'steps': [{'polyline': '121.49,31.23;121.49,31.22'}]},
        {'duration': '100', 'distance': '1000', 'steps': []},
        {'duration': '400', 'distance': '2500', 'tolls': '2', 'steps': [{'polyline': '121.49,31.23;121.50,31.22'}]},
    ]}})
    try:
        result = await amap.route([121.49, 31.23], [121.49, 31.22], 'driving')
        assert result['duration_s'] == 400 and result['distance_m'] == 2500
        assert result['taxi_cost_yuan'] is None  # Do not attach the first path's quote to another path.
        assert result['tolls_yuan'] == 2
    finally:
        await amap.close()


def test_manual_selection_is_saved_without_version_and_preserved_on_auto_refresh(monkeypatch, tmp_path, offline_domestic_scope):
    import backend.main as main
    store = Store(tmp_path / 'plans.sqlite3')
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'validator', Validator())
    original = saved_plan(store)
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=[r.model_copy(deep=True) for r in original.routes]))
    with TestClient(main.app) as client:
        selected = client.post('/api/plans/original/route', json={'day_index': 0, 'from_id': 'a', 'to_id': 'b', 'mode': 'transit'}).json()
        assert selected['routes'][0]['user_selected'] and selected['id'] == original.id
        response = client.post('/api/plans/original/refresh')
        assert 'event: result' in response.text
        refreshed = store.get(original.id)
        assert refreshed.routes[0].mode == 'transit' and refreshed.routes[0].user_selected
        assert len(store.list()) == 1 and refreshed.request.mode == 'auto'
