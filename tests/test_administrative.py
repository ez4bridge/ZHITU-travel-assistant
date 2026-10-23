from unittest.mock import AsyncMock

import pytest

from backend.administrative import AdministrativeMatchError, contains_place, select_division
from backend.amap import Amap, MapServiceError
from backend.models import Stop, TripRequest
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
    # The destination list is city-level, so a same-named county does not make
    # the city ambiguous (reported 2026-10-22: 长沙 resolved nothing at all).
    assert select_division('长沙', {'count': '2', 'districts': rows})['adcode'] == '430100'
    assert select_division('长沙县', {'count': '2', 'districts': rows})['adcode'] == '430121'
    with pytest.raises(AdministrativeMatchError):
        select_division('长沙市', {'count': '1', 'districts': [rows[1]]})
    with pytest.raises(AdministrativeMatchError):
        select_division('朝阳区', {'count': '2', 'districts': [district('朝阳区', '110105'), district('朝阳区', '220104')]})
    with pytest.raises(AdministrativeMatchError):
        select_division('阳朔', {'count': '30', 'districts': [district('阳朔县', '450321')]})


@pytest.mark.asyncio
async def test_station_alias_picks_the_airport_itself_not_its_terminals():
    client = Amap('上海')
    items = [
        {'id': 'air', 'name': '上海浦东国际机场', 'adcode': '310115', 'adname': '浦东新区',
         'type': '交通设施服务;机场相关;飞机场', 'location': '121.8,31.1', 'address': '迎宾大道6000号'},
        {'id': 't2', 'name': '上海浦东国际机场2号航站楼', 'adcode': '310115', 'adname': '浦东新区',
         'type': '交通设施服务;机场相关;飞机场', 'location': '121.81,31.15', 'address': '纬一路100号'},
        {'id': 't2d', 'name': '上海浦东国际机场2号航站楼(国内出发)', 'adcode': '310115', 'adname': '浦东新区',
         'type': '交通设施服务;机场相关;机场出发/到达', 'location': '121.82,31.16', 'address': '纬一路100号'}]

    async def request(path, params):
        if path == '/v3/config/district':
            return {'count': '1', 'districts': [district('上海市', '310000', 'province')]}
        assert params['keywords'] == '浦东机场'
        return {'pois': items}

    client.request = AsyncMock(side_effect=request)
    stop = Stop(id='anchor', name='浦东机场', place_name='浦东机场', city='上海', kind='break',
                start='09:00', end='09:05', booking_anchor='transport_arrival')
    try:
        await client.resolve(stop)
        assert stop.location_status == 'verified' and stop.matched_name == '上海浦东国际机场'
        assert stop.poi_id == 'air' and stop.location == (121.8, 31.1)
        assert '航站楼' in stop.location_note and '未核实' in stop.location_note
    finally:
        await client.close()


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
