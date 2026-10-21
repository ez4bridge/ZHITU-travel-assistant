from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend import map_region


@pytest.mark.asyncio
@pytest.mark.parametrize('center', ['108.4,30.8', 'nan,30', '181,30', ''])
async def test_region_center_is_display_only_and_invalid_coordinates_are_not_guessed(monkeypatch, center):
    client = SimpleNamespace(request=AsyncMock(return_value={'count': '1', 'districts': [
        {'name': '测试区', 'adcode': '500101', 'level': 'district', 'center': center}]}), close=AsyncMock())
    monkeypatch.setattr(map_region, 'Amap', lambda city: client)
    result = await map_region.resolve_map_region('测试')
    assert result['approximate'] is True
    if center == '108.4,30.8':
        assert result['status'] == 'available' and result['center'] == [108.4, 30.8]
        assert result['city'] == '测试区' and '高德' in result['source']
    else:
        assert result['status'] == 'unavailable' and result['center'] is None
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_region_endpoint_handles_ambiguity_safely_and_bounds_input(monkeypatch):
    import backend.main as main
    client = SimpleNamespace(request=AsyncMock(return_value={'count': '2', 'districts': [
        {'name': '朝阳区', 'adcode': code, 'level': 'district', 'center': '110,30'} for code in ('110105', '220104')]}), close=AsyncMock())
    monkeypatch.setattr(map_region, 'Amap', lambda city: client)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as api:
        response = await api.get('/api/map-region', params={'city': '朝阳'})
        assert response.status_code == 200 and response.json()['center'] is None
        assert response.json()['status'] == 'unavailable'
        assert (await api.get('/api/map-region', params={'city': ' '})).status_code == 422
        assert (await api.get('/api/map-region', params={'city': 'a' * 41})).status_code == 422
    assert client.request.await_count == 1
