"""Generic meal labels become real, changeable restaurant suggestions only."""
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap, MapServiceError
from backend.locality import meal_area_query, resolve_unlocated_meals
from backend.models import DayPlan, Stop


def meal(name='附近午餐', **fields):
    return Stop(id='meal', name=name, kind='meal', city='延边朝鲜族自治州',
                start='12:00', end='13:00', location_behavior='unlocated', **fields)


def anchor(**fields):
    return Stop(id='sight', name='已核实景点', city='延边朝鲜族自治州', start='10:00', end='11:00',
                poi_id='sight-poi', location=(128.1, 42.4), location_status='verified', **fields)


def restaurant(id='real-food', city='延边朝鲜族自治州', **fields):
    return {'id': id, 'name': '真实餐厅(分店)', 'typecode': '050100', 'cityname': city,
            'address': '接口返回的分店地址', 'location': '128.101,42.401', **fields}


def day(stops):
    return DayPlan(date='2026-10-30', title='当天', stops=stops)


@pytest.mark.parametrize(('name', 'expected'), [
    ('二道白河镇朝鲜族风味午餐', '二道白河镇'), ('返回延吉市区晚餐', '延吉市'),
    ('中央大街附近东北菜晚餐', '中央大街'), ('市区东北菜晚餐', ''), ('附近午餐', ''),
])
def test_area_title_is_not_used_as_a_restaurant_name(name, expected):
    assert meal_area_query(meal(name)) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize('first', [False, True])
async def test_boundary_meals_use_real_nearby_restaurants_and_real_roads(first):
    service = Amap('延边朝鲜族自治州')
    stop = meal()
    service.search = AsyncMock()
    service.request = AsyncMock(return_value={'pois': [restaurant()]})
    service.route = AsyncMock(return_value={'distance_m': 600, 'duration_s': 100,
                                          'polyline': [(128.1, 42.4), (128.101, 42.401)]})
    try:
        await resolve_unlocated_meals(service, day([stop, anchor()] if first else [anchor(), stop]))
        assert stop.poi_id == 'real-food' and stop.location_status == 'verified'
        assert stop.location == (128.101, 42.401) and stop.location_behavior == 'poi'
        assert '可更换' in stop.description and '不表示已预订' in stop.description
        service.search.assert_not_awaited()
        assert service.route.await_count == 1
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_explicit_return_city_never_uses_previous_mountain_as_dining_area():
    service = Amap('延边朝鲜族自治州')
    stop = meal('返回延吉市区晚餐')
    service.search = AsyncMock(return_value=[{'id': 'yanji', 'name': '延吉市',
        'location': [129.5, 42.9], 'address': '', 'type': '地名地址信息'}])
    service.request = AsyncMock(return_value={'pois': [restaurant(location='129.501,42.901')]})
    service.route = AsyncMock(return_value={'distance_m': 300, 'polyline': [(129.5, 42.9), (129.501, 42.901)]})
    try:
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        service.search.assert_awaited_once_with('延吉市')
        assert service.request.call_args.args[1]['location'] == '129.5,42.9'
        assert stop.location == (129.501, 42.901) and '延吉市' in stop.description
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_town_government_is_search_anchor_only_never_meal_location():
    service = Amap('延边朝鲜族自治州')
    stop = meal('二道白河镇朝鲜族风味午餐')
    service.search = AsyncMock(return_value=[{'id': 'town-gov', 'name': '二道白河镇人民政府',
        'location': [128.14, 42.45], 'address': '', 'type': '政府机关'}])
    service.request = AsyncMock(return_value={'pois': [restaurant()]})
    service.route = AsyncMock(return_value={'distance_m': 700, 'polyline': [(128.14, 42.45), (128.101, 42.401)]})
    try:
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        assert stop.poi_id == 'real-food' and stop.poi_id != 'town-gov'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_duplicate_street_labels_can_use_unique_real_scenic_area_not_arbitrary_road():
    service = Amap('延边朝鲜族自治州')
    stop = meal('中央大街晚餐')
    service.search = AsyncMock(return_value=[
        {'id': 'road1', 'name': '中央大街', 'location': [128.1, 42.4], 'type': '道路名'},
        {'id': 'road2', 'name': '中央大街', 'location': [128.11, 42.41], 'type': '热点地名'},
        {'id': 'scenic', 'name': '中央大街步行街', 'location': [128.12, 42.42], 'type': '特色商业街;风景名胜'},
    ])
    service.request = AsyncMock(return_value={'pois': [restaurant()]})
    service.route = AsyncMock(return_value={'distance_m': 600, 'polyline': [(128.12, 42.42), (128.101, 42.401)]})
    try:
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        assert service.request.call_args.args[1]['location'] == '128.12,42.42'
        assert stop.poi_id == 'real-food'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_wrong_city_invalid_geometry_and_ambiguous_named_branch_stay_unknown():
    service = Amap('延边朝鲜族自治州')
    stop = meal()
    service.request = AsyncMock(return_value={'pois': [restaurant(city='哈尔滨市')]})
    service.route = AsyncMock(return_value={'distance_m': 10, 'polyline': []})
    try:
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        assert stop.location is None
        service.route.assert_not_awaited()
        service.request.return_value = {'pois': [restaurant()]}
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        assert stop.location is None and '未取得道路' in stop.location_note
        stop.location_behavior = 'poi'
        stop.name = '某连锁餐厅'
        service.request.reset_mock()
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        service.request.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_cross_city_neighbors_and_failed_explicit_area_do_not_fall_back_elsewhere():
    service = Amap(['延边朝鲜族自治州', '哈尔滨市'])
    stop = meal()
    other = anchor()
    other.city = '哈尔滨市'
    service.search = AsyncMock(return_value=[])
    service.request = AsyncMock()
    try:
        await resolve_unlocated_meals(service, day([other, stop]))
        service.request.assert_not_awaited()
        stop.name = '二道白河镇朝鲜族风味午餐'
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        assert stop.location is None and '未用附近其他城区替代' in stop.location_note
        service.request.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_candidate_query_failure_is_bounded_and_next_real_candidate_can_succeed():
    service = Amap('延边朝鲜族自治州')
    stop = meal()
    service.request = AsyncMock(return_value={'pois': [restaurant(id='bad'), restaurant(id='good')]})
    service.route = AsyncMock(side_effect=[MapServiceError('no_route', '无道路'),
        {'distance_m': 500, 'polyline': [(128.1, 42.4), (128.101, 42.401)]}])
    try:
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        assert stop.poi_id == 'good' and service.route.await_count == 2
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_refresh_enrichment_also_resolves_generic_meals(monkeypatch):
    from backend.models import Itinerary
    service = Amap('延边朝鲜族自治州')
    stop = meal()
    itinerary = Itinerary(title='路线刷新', summary='', days=[day([anchor(), stop])])
    service.resolve = AsyncMock()
    service.request = AsyncMock(return_value={'pois': [restaurant()]})
    service.route = AsyncMock(return_value={'distance_m': 500, 'duration_s': 600,
        'polyline': [(128.1, 42.4), (128.101, 42.401)]})
    try:
        routes = await service.enrich(itinerary, 'auto', compare_modes=True, optimize_meals=False)
        assert stop.location_status == 'verified'
        assert len(routes) == 1 and routes[0].available
        assert {o.mode for o in routes[0].alternatives} == {'walking', 'transit', 'taxi', 'driving', 'bicycling'}
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_same_name_place_indexed_as_shop_and_road_is_not_ambiguous():
    service = Amap('上海')
    service.search = AsyncMock(return_value=[
        {'id': 'shop', 'name': '南京路步行街', 'location': [121.48, 31.24],
         'address': '黄浦区', 'type': '购物服务;购物相关场所;购物相关场所'},
        {'id': 'road', 'name': '南京路步行街', 'location': [121.47, 31.23],
         'address': '黄浦区', 'type': '地名地址信息;交通地名;道路名'}])
    stop = Stop(id='sight', name='南京路步行街', place_name='南京路步行街', city='上海',
                kind='sight', start='10:00', end='11:00')
    try:
        await service.resolve(stop)
        assert stop.location_status == 'verified' and stop.poi_id == 'shop'
        assert stop.location == (121.48, 31.24)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_area_split_into_sub_areas_still_seeds_a_real_restaurant():
    service = Amap('延边朝鲜族自治州')
    stop = meal('田子坊周边午餐')
    service.search = AsyncMock(return_value=[
        {'id': 'tzf', 'name': '上海田子坊', 'location': [121.46, 31.21], 'address': '',
         'type': '购物服务;特色商业街;特色商业街'},
        {'id': 'tzf-e', 'name': '田子坊东部旅游区', 'location': [121.47, 31.21], 'address': '',
         'type': '风景名胜;风景名胜相关;旅游景点'},
        {'id': 'tzf-m', 'name': '田子坊中部旅游区', 'location': [121.465, 31.21], 'address': '',
         'type': '风景名胜;风景名胜相关;旅游景点'}])
    service.request = AsyncMock(return_value={'pois': [restaurant()]})
    service.route = AsyncMock(return_value={'distance_m': 500, 'polyline': [(121.46, 31.21), (128.101, 42.401)]})
    try:
        await resolve_unlocated_meals(service, day([anchor(), stop]))
        assert service.request.call_args.args[1]['location'] == '121.46,31.21'
        assert stop.poi_id == 'real-food' and stop.location_status == 'verified'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_booked_hotel_address_filters_real_branches_without_trusting_user_coordinates():
    service = Amap('延边朝鲜族自治州')
    service.search = AsyncMock(return_value=[
        {'id': 'branch-a', 'name': '某某酒店(西街店)', 'address': '西街18号', 'location': [129.5, 42.9]},
        {'id': 'branch-b', 'name': '某某酒店(东街店)', 'address': '东街88号', 'location': [129.6, 42.9]},
    ])
    stop = Stop(name='某某酒店', kind='hotel', city='延边朝鲜族自治州', start='09:00', end='09:00',
        booking_anchor='hotel_departure', booking_address='吉林省延吉市东街88号', location=(1, 2))
    try:
        await service.resolve(stop)
        assert stop.poi_id == 'branch-b' and stop.location == (129.6, 42.9)
        assert stop.name == '某某酒店' and stop.start == stop.end == '09:00'
        stop.booking_address = '并不存在的新街道99号'
        await service.resolve(stop)
        assert stop.location is None and not stop.poi_id and '地址尚未匹配' in stop.location_note
    finally:
        await service.close()
