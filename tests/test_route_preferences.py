from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap, MapServiceError
from backend.locality import optimize_day_meals, prepare_accommodation
from backend.models import RouteOption, Stop
from backend.planning import recost_all
from backend.transport_cost import estimate_cost
from test_core import sample


@pytest.mark.asyncio
async def test_driving_and_taxi_share_actual_provider_road_and_parse_quote():
    amap = Amap('北京')
    amap.request = AsyncMock(return_value={'route': {'taxi_cost': '35', 'paths': [{
        'distance': '6000', 'duration': '900', 'tolls': '2',
        'steps': [{'polyline': '116.27,40.0;116.30,40.01'}]}]}})
    try:
        driving = await amap.route([116.27, 40], [116.30, 40.01], 'driving')
        taxi = await amap.route([116.27, 40], [116.30, 40.01], 'taxi')
        assert amap.request.await_count == 1
        assert amap.request.call_args.args[1]['extensions'] == 'all'
        assert taxi['taxi_cost_yuan'] == Decimal(35) and driving['tolls_yuan'] == Decimal(2)
        assert taxi['provider_endpoint'] == '/v3/direction/driving'
        assert taxi['polyline'] == driving['polyline']
        option = RouteOption(mode='taxi', available=True, **taxi)
        estimate = estimate_cost(option, '北京')
        assert estimate.low == estimate.high == 35 and estimate.basis == 'vehicle'
    finally:
        await amap.close()


def test_unconfirmed_hotel_is_not_a_route_anchor():
    trip, itinerary, _ = sample()
    trip.days = 2
    itinerary.summary = '皇家园林慢游。全部用公交地铁接驳。住宿选某某酒店，位置居中。'
    itinerary.days[0].stops.insert(0, Stop(name='助手挑的酒店', kind='hotel', start='08:00', end='08:30'))
    confirmed = itinerary.model_copy(deep=True)
    prepare_accommodation(itinerary, trip)
    assert all(s.kind != 'hotel' for s in itinerary.days[0].stops)
    assert '由你选择' in itinerary.days[0].accommodation
    assert '某某酒店' not in itinerary.summary and '全部用公交' not in itinerary.summary
    trip.confirmed_hotel = '用户已订酒店具体分店'
    prepare_accommodation(confirmed, trip)
    assert confirmed.days[0].stops[0].place_name == trip.confirmed_hotel


def test_vehicle_costs_include_group_size_and_never_invent_route_metrics():
    trip, itinerary, routes = sample()
    trip.destination = '北京'
    trip.people = 5
    route = routes[0]
    route.mode = 'taxi'
    route.taxi_cost_yuan = Decimal('30.005')
    original = (route.duration_s, route.distance_m, route.polyline[:])
    recost_all(trip, itinerary, routes)
    expense = next(e for e in itinerary.days[0].expenses if e.category == 'transport')
    assert expense.unit_cost == Decimal('30.01') and expense.quantity == 2 and expense.basis == 'group'
    assert (route.duration_s, route.distance_m, route.polyline) == original
    route.mode = 'driving'
    route.tolls_yuan = Decimal(2)
    cost = estimate_cost(route, '北京', 60)
    assert cost.high > cost.low > 2 and '停车' in cost.assumptions and '非实时油价' in cost.assumptions
    route.available = False
    assert estimate_cost(route, '北京', 60) is None


@pytest.mark.asyncio
async def test_oct28_meal_replaced_only_with_real_nearby_poi_and_road_check():
    _, itinerary, _ = sample()
    day = itinerary.days[0]
    day.stops[0].location = (116.275179, 39.999617)
    day.stops[1].location = (116.300875, 40.006502)
    meal = Stop(id='lunch', name='菜市口餐厅', kind='meal', start='12:00', end='13:00', location=(116.377548, 39.885051))
    day.stops.insert(1, meal)
    amap = Amap('北京')
    near = (116.285, 40.001)
    async def road(a, b, mode):
        assert mode == 'driving'
        distance = 20000 if meal.location in (a, b) else 1500 if near in (a, b) else 3000
        return {'distance_m': distance, 'duration_s': 600, 'polyline': [a, b]}
    amap.route = road
    amap.request = AsyncMock(return_value={'pois': [{'id': 'real-nearby', 'name': '真实就近餐厅',
        'location': '116.285,40.001', 'typecode': '050100', 'cityname': '北京市', 'address': '接口地址'}]})
    try:
        await optimize_day_meals(amap, day)
        assert meal.poi_id == 'real-nearby' and meal.location == near
        assert meal.location_status == 'verified' and '菜市口餐厅' in meal.description
        assert amap.request.call_args.args[0] == '/v3/place/around'
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_failed_locality_check_keeps_original_and_flags_problem():
    _, itinerary, _ = sample()
    day = itinerary.days[0]
    meal = Stop(name='远方午餐', kind='meal', start='12:00', end='13:00', location=(116.4, 39.8))
    day.stops.insert(1, meal)
    amap = Amap('上海')
    amap.route = AsyncMock(side_effect=MapServiceError('map_rate_limit', '限流'))
    try:
        await optimize_day_meals(amap, day)
        assert meal.name == '远方午餐' and '餐饮绕路待调整' in meal.location_note
        amap.route.reset_mock()
        await optimize_day_meals(amap, day, '保留远方午餐')
        amap.route.assert_not_awaited()
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_same_name_scenic_poi_is_distinguished_from_road_not_guessed_branch():
    amap = Amap('北京')
    amap.search = AsyncMock(return_value=[
        {'id': 'scenic', 'name': '前门大街', 'location': [116.4, 39.9], 'address': '', 'type': '风景名胜;旅游景点'},
        {'id': 'road', 'name': '前门大街', 'location': [116.401, 39.9], 'address': '', 'type': '地名地址信息;道路名'}])
    stop = Stop(name='前门大街', kind='sight', start='12:00', end='13:00')
    try:
        await amap.resolve(stop)
        assert stop.poi_id == 'scenic' and stop.location_status == 'verified'
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_short_transit_no_route_recommends_only_provider_walking():
    _, itinerary, routes = sample()
    amap = Amap('上海')
    amap.resolve = AsyncMock()
    async def query(a, b, mode):
        if mode == 'transit':
            raise MapServiceError('no_route', '无公交方案')
        return {'distance_m': 800, 'duration_s': 600, 'polyline': routes[0].polyline,
                'provider_endpoint': '/v3/direction/walking' if mode == 'walking' else '/v3/direction/driving'}
    amap.route = query
    try:
        result = await amap.enrich(itinerary, 'transit', compare_modes=True)
        assert result[0].available and result[0].mode == 'walking' and result[0].duration_s == 600
        assert '公交未返回' in result[0].note
        assert not next(o for o in result[0].alternatives if o.mode == 'transit').available
    finally:
        await amap.close()
