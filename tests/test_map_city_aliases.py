from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap
from backend.models import Stop


@pytest.mark.asyncio
@pytest.mark.parametrize('requested,returned,tool_city,code,level', [
    ('香港', '香港特别行政区', '香港', '810000', 'province'),
    ('香港特别行政区', '香港特别行政区', '香港', '810000', 'province'),
    ('澳门', '澳门特别行政区', '澳门', '820000', 'province'),
    ('西安', '西安市', '西安市', '610100', 'city'),
    ('万州', '万州区', '万州', '500101', 'district'),
    ('阳朔', '阳朔县', '阳朔', '450321', 'district'),
])
async def test_search_accepts_official_city_name_without_changing_stored_city(requested, returned, tool_city, code, level):
    amap = Amap([requested, '上海'])
    places = {'pois': [
        {'id': 'matched', 'name': '测试景点', 'cityname': returned, 'adname': returned, 'adcode': code, 'location': '114.047326,22.309841'},
        {'id': 'elsewhere', 'name': '测试景点', 'cityname': '广州市', 'adname': '越秀区', 'location': '113.26,23.12'},
    ]}
    async def request(path, params):
        if path == '/v3/config/district':
            return {'count': '1', 'districts': [{'name': returned, 'adcode': code, 'level': level}]}
        assert params['city'] == code
        return places
    amap.request = AsyncMock(side_effect=request)
    try:
        places = await amap.search('测试景点', tool_city)
        assert [place['id'] for place in places] == ['matched']
        assert places[0]['city'] == requested
        assert amap.request.call_args.args[1]['city'] == code
        stop = Stop(id='a', city=tool_city, name='测试景点', kind='sight', start='09:00', end='10:00')
        await amap.resolve(stop)
        assert stop.location_status == 'verified'
        assert stop.city == requested
        assert stop.poi_id == 'matched'
        assert amap.canonical_city(returned) == requested
        assert amap.request.await_count == 2
        with pytest.raises(ValueError, match='本次目的地'):
            amap.canonical_city('广州')
    finally:
        await amap.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('another_attraction', [False, True])
async def test_attraction_and_auxiliary_facilities_are_not_treated_as_branches(another_attraction):
    amap = Amap('测试市')
    candidates = [
        {'id': 'scenic', 'name': '青山瀑布群景区', 'type': '风景名胜', 'city': '测试市', 'location': [110, 30], 'address': ''},
        {'id': 'parking', 'name': '青山瀑布群景区停车场', 'type': '交通设施服务;停车场', 'city': '测试市', 'location': [110, 30], 'address': ''},
        {'id': 'tickets', 'name': '青山瀑布群景区售票处', 'type': '生活服务;售票处', 'city': '测试市', 'location': [110, 30], 'address': ''},
    ]
    if another_attraction:
        candidates.append({**candidates[0], 'id': 'other', 'name': '青山瀑布国家公园'})
    amap.search = AsyncMock(return_value=candidates)
    stop = Stop(id='a', city='测试市', name='青山瀑布', kind='sight', start='09:00', end='10:00')
    try:
        await amap.resolve(stop)
        assert stop.location_status == ('unknown' if another_attraction else 'verified')
        assert stop.poi_id == ('' if another_attraction else 'scenic')
    finally:
        await amap.close()
