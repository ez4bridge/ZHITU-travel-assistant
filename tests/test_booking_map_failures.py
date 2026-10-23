"""Real Amap enrichment with offline provider doubles, never live requests."""
from datetime import date
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap, MapServiceError
from backend.locality import resolve_unlocated_meals
from backend.models import DayPlan, Itinerary, Stop


def itinerary(*, located=False):
    return Itinerary(title='合成离线地点核验', summary='', days=[DayPlan(
        date=date(2026, 10, 28), title='上海', stops=[Stop(id=f'sight-{index}',
            name=name, city='上海', start=start, end=end,
            location=(121.49 + index * .001, 31.23) if located else None,
            poi_id=f'synthetic-{index}' if located else '',
            location_status='verified' if located else 'unknown')
        for index, (name, start, end) in enumerate([
            ('合成景点甲', '09:00', '10:00'), ('合成景点乙', '11:00', '12:00')])])])


def district():
    return {'count': '1', 'districts': [{'name': '上海市', 'adcode': '310000',
        'citycode': '021', 'level': 'province', 'center': '121.49,31.23', 'districts': []}]}


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['map_network', 'map_rate_limit', 'map_response', 'map_permission'])
async def test_failed_poi_service_is_recorded_even_when_route_reports_missing_location(code):
    service = Amap('上海')
    trip = itinerary()

    async def request(path, params):
        if path == '/v3/config/district':
            return district()
        assert path == '/v3/place/text'
        raise MapServiceError(code, '合成接口暂不可用')

    service.request = AsyncMock(side_effect=request)
    try:
        routes = await service.enrich(trip, 'auto', compare_modes=True)
        assert routes and all(not route.available and route.failure_code == 'missing_location' for route in routes)
        assert service.unresolved_provider_errors == [code]
        assert len(trip.days[0].stops) == 2
        assert all(stop.location is None for stop in trip.days[0].stops)
        assert any(call.args[0] == '/v3/place/text' for call in service.request.await_args_list)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_successful_empty_poi_results_are_not_mislabeled_as_provider_failure():
    service = Amap('上海')

    async def request(path, params):
        if path == '/v3/config/district':
            return district()
        assert path == '/v3/place/text'
        return {'pois': []}

    service.request = AsyncMock(side_effect=request)
    try:
        trip = itinerary()
        routes = await service.enrich(trip, 'auto', compare_modes=True)
        assert routes and routes[0].failure_code == 'missing_location'
        assert service.unresolved_provider_errors == []
        assert all(stop.location is None for stop in trip.days[0].stops)
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_failed_optional_route_does_not_flag_when_another_actual_route_is_available():
    service = Amap('上海')
    service.resolve = AsyncMock()

    async def route(before, after, mode):
        if mode == 'walking':
            raise MapServiceError('map_quota', '合成步行接口配额用尽', True)
        return {'distance_m': 2000, 'duration_s': 600,
                'polyline': [(121.49, 31.23), (121.491, 31.23)]}

    service.route = route
    try:
        routes = await service.enrich(itinerary(located=True), 'auto', compare_modes=True)
        assert routes[0].available
        assert not next(option for option in routes[0].alternatives if option.mode == 'walking').available
        assert service.unresolved_provider_errors == []
    finally:
        await service.close()


def meal_day():
    anchor = Stop(id='known-sight', name='合成已核实景点', city='上海', start='10:00', end='11:00',
                  poi_id='synthetic-anchor', location=(121.49, 31.23), location_status='verified')
    meal = Stop(id='meal-need', name='附近午餐', city='上海', kind='meal',
                start='12:00', end='13:00', location_behavior='unlocated')
    return DayPlan(date=date(2026, 10, 28), title='合成餐饮片区', stops=[anchor, meal])


def restaurant(sid='synthetic-restaurant'):
    return {'id': sid, 'name': '合成餐馆', 'typecode': '050100', 'cityname': '上海市',
            'location': '121.491,31.231', 'address': '合成分店地址'}


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['map_network', 'map_rate_limit', 'map_quota'])
async def test_generic_meal_around_service_failure_is_recorded_before_any_route_exists(code):
    service = Amap('上海')
    day = meal_day()
    service.request = AsyncMock(side_effect=MapServiceError(code, '合成周边检索故障'))
    service.route = AsyncMock()
    try:
        await resolve_unlocated_meals(service, day)
        service.request.assert_awaited_once()
        assert service.request.await_args.args[0] == '/v3/place/around'
        service.route.assert_not_awaited()
        assert service.unresolved_provider_errors == [code]
        assert day.stops[-1].location is None and day.stops[-1].name == '附近午餐'
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['map_network', 'map_rate_limit', 'map_quota'])
async def test_generic_meal_road_service_failure_is_recorded_when_no_candidate_can_be_verified(code):
    service = Amap('上海')
    day = meal_day()
    service.request = AsyncMock(return_value={'pois': [restaurant()]})
    service.route = AsyncMock(side_effect=MapServiceError(code, '合成餐馆道路查询故障'))
    try:
        await resolve_unlocated_meals(service, day)
        service.route.assert_awaited_once()
        assert service.unresolved_provider_errors == [code]
        assert day.stops[-1].location is None and day.stops[-1].name == '附近午餐'
    finally:
        await service.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['no_route', 'map_quota'])
async def test_second_real_meal_candidate_can_succeed_without_a_stale_provider_flag(code):
    service = Amap('上海')
    day = meal_day()
    service.request = AsyncMock(return_value={'pois': [restaurant('first'), restaurant('second')]})
    service.route = AsyncMock(side_effect=[MapServiceError(code, '合成第一候选查询失败'),
        {'distance_m': 500, 'polyline': [(121.49, 31.23), (121.491, 31.231)]}])
    try:
        await resolve_unlocated_meals(service, day)
        assert day.stops[-1].poi_id == 'second' and day.stops[-1].location_status == 'verified'
        assert service.unresolved_provider_errors == []
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_meal_ordinary_empty_results_or_no_route_do_not_flag_provider_failure():
    service = Amap('上海')
    service.request = AsyncMock(return_value={'pois': []})
    try:
        day = meal_day()
        await resolve_unlocated_meals(service, day)
        assert service.unresolved_provider_errors == []
        service.request.return_value = {'pois': [restaurant()]}
        service.route = AsyncMock(side_effect=MapServiceError('no_route', '合成道路不存在'))
        await resolve_unlocated_meals(service, day)
        assert service.unresolved_provider_errors == []
        assert day.stops[-1].location is None
    finally:
        await service.close()
