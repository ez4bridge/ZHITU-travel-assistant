from unittest.mock import AsyncMock

import pytest

from backend.administrative import AdministrativeMatchError, contains_place, select_division
from backend.amap import Amap, MapServiceError
from backend.models import TripRequest
from backend.weather import SHANGHAI, WeatherService
from datetime import datetime
from test_weather import forecast


def district(name, code, level='district'):
    return {'name': name, 'adcode': code, 'level': level, 'center': '110,30'}


@pytest.mark.parametrize('name,official,code,level', [
    ('万州', '万州区', '500101', 'district'),
    ('阳朔', '阳朔县', '450321', 'district'),
    ('峨边彝族', '峨边彝族自治县', '511132', 'district'),
    ('杭州', '杭州市', '330100', 'city'),
])
def test_generic_official_suffixes(name, official, code, level):
    assert select_division(name, {'count': '1', 'districts': [district(official, code, level)]})['adcode'] == code


def test_ambiguous_short_name_and_explicit_admin_types_do_not_mix():
    rows = [district('长沙市', '430100', 'city'), district('长沙县', '430121')]
    with pytest.raises(AdministrativeMatchError):
        select_division('长沙', {'count': '2', 'districts': rows})
    assert select_division('长沙县', {'count': '2', 'districts': rows})['adcode'] == '430121'
    with pytest.raises(AdministrativeMatchError):
        select_division('长沙市', {'count': '1', 'districts': [rows[1]]})
    with pytest.raises(AdministrativeMatchError):
        select_division('朝阳区', {'count': '2', 'districts': [district('朝阳区', '110105'), district('朝阳区', '220104')]})
    with pytest.raises(AdministrativeMatchError):
        select_division('阳朔', {'count': '30', 'districts': [district('阳朔县', '450321')]})


def test_code_scope_prevents_same_name_elsewhere_and_accepts_city_children():
    assert contains_place(district('万州区', '500101'), {'adcode': '500101'})
    assert not contains_place(district('万州区', '500101'), {'adcode': '500102', 'adname': '万州区'})
    assert contains_place(district('杭州市', '330100', 'city'), {'adcode': '330106'})
    assert not contains_place(district('杭州市', '330100', 'city'), {'adcode': '330200'})


@pytest.mark.asyncio
async def test_weather_and_poi_share_unique_identity_and_unmatched_city_stays_blocked():
    client = Amap('阳朔')
    calls = []
    async def request(path, params):
        calls.append(path)
        if path == '/v3/config/district':
            return {'count': '1', 'districts': [district('阳朔县', '450321')]}
        if 'weatherInfo' in path:
            return forecast('450321')
        assert params['city'] == '450321'
        return {'pois': [{'id': 'a', 'name': '西街', 'cityname': '桂林市', 'adname': '阳朔县',
            'adcode': '450321', 'location': '110.49,24.77'}]}
    client.request = AsyncMock(side_effect=request)
    try:
        result = await WeatherService().forecast(TripRequest(destination='阳朔', start_date=datetime.now(SHANGHAI).date()), client)
        assert result.status == 'available' and result.adcode == '450321'
        assert (await client.search('西街', '阳朔县'))[0]['city'] == '阳朔'
        assert calls.count('/v3/config/district') == 1
        with pytest.raises(ValueError):
            await client.search('西街', '桂林')
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_ambiguous_admin_does_not_issue_poi_query():
    client = Amap('朝阳')
    client.request = AsyncMock(return_value={'count': '2', 'districts': [
        district('朝阳市', '211300', 'city'), district('朝阳区', '110105')]})
    try:
        with pytest.raises(MapServiceError) as found:
            await client.search('公园')
        assert found.value.code == 'map_city_ambiguous'
        assert client.request.await_count == 1
    finally:
        await client.close()
