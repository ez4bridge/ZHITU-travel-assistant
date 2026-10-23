"""Provider plan order and hub→metro snapping; all fixtures are synthetic."""
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap
from backend.models import Stop


def plan(duration, cost, polyline='121.5,31.2;121.51,31.21'):
    return {'duration': str(duration), 'distance': '1200', 'cost': str(cost), 'segments': [
        {'walking': {'steps': [{'polyline': polyline}]},
         'bus': {'buslines': [{'name': f'合成线路{cost}'}]}}]}


@pytest.mark.asyncio
async def test_transit_keeps_the_providers_top_plan_not_our_fastest():
    service = Amap('上海')
    service.request = AsyncMock(return_value={'route': {'transits': [plan(600, 7), plan(300, 24)]}})
    try:
        result = await service._route((121.5, 31.2), (121.51, 31.21), 'transit', '上海')
        # The user sees the first plan in Amap; re-ranking would show 300s instead.
        assert result['duration_s'] == 600 and result['fare_yuan'] == 7
        assert result['taxi_cost_yuan'] is None
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_airport_stop_queries_the_metro_station_inside_it():
    service = Amap('上海')
    station = {'id': 'metro', 'name': '浦东1号2号航站楼地铁站', 'location': '121.8055,31.1509',
               'type': '交通设施服务;地铁站;地铁站'}

    async def request(path, params):
        if path == '/v3/place/around':
            return {'pois': [station]}
        return {'route': {'transits': [plan(6300, 7)]}}

    service.request = AsyncMock(side_effect=request)
    airport = Stop(id='air', name='上海浦东国际机场', city='上海', kind='break', start='13:00', end='13:05',
                   booking_anchor='transport_arrival', location=(121.8087, 31.1423), location_status='verified')
    try:
        snap = await service.transit_anchor(airport, airport.location)
        assert snap and snap['name'] == '浦东1号2号航站楼地铁站'
        points, extra_s, extra_m, notes = await service.transit_points(airport, airport)
        assert points[0] == (121.8055, 31.1509)
        assert extra_s >= 600 and extra_m > 0          # ~1 km indoor walk, not 65 minutes
        assert extra_s < 3600
        assert '地铁站' in notes[0] and '站内步行' in notes[0]
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_a_hotel_name_ending_in_a_shop_is_not_treated_as_a_station():
    service = Amap('上海')
    service.request = AsyncMock()
    hotel = Stop(id='hotel', name='如家商旅酒店(西藏北路地铁站店)', city='上海', kind='hotel',
                 start='14:00', end='14:15', booking_anchor='hotel_departure',
                 location=(121.46, 31.26), location_status='verified')
    try:
        assert await service.transit_anchor(hotel, hotel.location) is None
        service.request.assert_not_awaited()
    finally:
        await service.close()
