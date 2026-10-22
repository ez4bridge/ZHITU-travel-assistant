"""Merge boundaries: administrative identity survives new booking/meal endpoints.

All POIs and roads below are explicit synthetic fixtures, never live-map proof.
"""
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap
from backend.locality import resolve_unlocated_meals
from backend.models import DayPlan, Stop


def county_client():
    client = Amap('阳朔')
    division = {'name': '阳朔县', 'adcode': '450321', 'level': 'district'}
    client.administrative_divisions = {'阳朔': division, '阳朔县': division}
    return client


@pytest.mark.asyncio
async def test_new_boundary_meal_uses_county_adcode_instead_of_parent_city_name():
    client = county_client()
    anchor = Stop(id='sight', name='合成已核实景点', city='阳朔', start='10:00', end='11:00',
        poi_id='county-sight', location=(110.49, 24.77), location_status='verified')
    meal = Stop(id='food', name='附近午餐', city='阳朔', kind='meal', start='12:00', end='13:00',
        location_behavior='unlocated')
    base = {'name': '合成餐厅', 'typecode': '050100', 'cityname': '桂林市',
        'adname': '阳朔县', 'location': '110.491,24.771', 'address': '合成街道'}
    client.request = AsyncMock(return_value={'pois': [
        {**base, 'id': 'wrong-district', 'adcode': '450322'},
        {**base, 'id': 'county-food', 'adcode': '450321'},
    ]})
    client.route = AsyncMock(return_value={'distance_m': 600,
        'polyline': [(110.49, 24.77), (110.491, 24.771)]})
    try:
        await resolve_unlocated_meals(client, DayPlan(date='2026-11-01', title='合成县区', stops=[anchor, meal]))
        assert meal.poi_id == 'county-food' and meal.location_status == 'verified'
        assert meal.city == '阳朔'
        assert client.route.await_count == 1  # Wrong district never reaches routing.
        assert client.request.await_count == 1
        assert '营业' in meal.location_note and '不表示已预订' in meal.description
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_transport_type_and_hotel_address_disambiguation_keep_requested_county():
    client = county_client()
    client.search = AsyncMock(return_value=[
        {'id': 'rail', 'name': '阳朔站', 'city': '阳朔', 'type': '交通设施服务;火车站',
         'location': [110.49, 24.77], 'address': '合成站址'},
        {'id': 'parking', 'name': '阳朔站', 'city': '阳朔', 'type': '停车场',
         'location': [110.491, 24.77], 'address': '合成停车场'},
    ])
    station = Stop(name='阳朔站', city='阳朔县', kind='break', start='09:00', end='09:05',
        booking_anchor='transport_arrival', location=(1, 2), location_status='verified')
    hotel = Stop(name='合成酒店', city='阳朔县', kind='hotel', start='18:00', end='18:05',
        booking_anchor='hotel_return', booking_address='广西阳朔县西街18号', location=(1, 2))
    try:
        await client.resolve(station)
        assert station.poi_id == 'rail' and station.location == (110.49, 24.77)
        assert station.city == '阳朔'
        client.search.return_value = [
            {'id': 'west', 'name': '合成酒店(西街店)', 'city': '阳朔',
             'location': [110.49, 24.77], 'address': '西街18号'},
            {'id': 'east', 'name': '合成酒店(东街店)', 'city': '阳朔',
             'location': [110.50, 24.77], 'address': '东街18号'},
        ]
        await client.resolve(hotel)
        assert hotel.city == '阳朔' and hotel.poi_id == 'west'
        assert hotel.location_status == 'verified' and hotel.location != (1, 2)
        hotel.booking_address = '合成地址不存在99号'
        await client.resolve(hotel)
        assert hotel.location_status == 'unknown' and hotel.location is None
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_new_hotel_disambiguation_does_not_weaken_existing_scenic_auxiliary_filter():
    client = county_client()
    client.search = AsyncMock(return_value=[
        {'id': 'park', 'name': '合成山水景区', 'type': '风景名胜', 'city': '阳朔',
         'location': [110.49, 24.77], 'address': ''},
        {'id': 'tickets', 'name': '合成山水景区售票处', 'type': '售票处', 'city': '阳朔',
         'location': [110.49, 24.77], 'address': ''},
        {'id': 'parking', 'name': '合成山水景区停车场', 'type': '停车场', 'city': '阳朔',
         'location': [110.49, 24.77], 'address': ''},
    ])
    stop = Stop(name='合成山水', city='阳朔县', kind='sight', start='09:00', end='10:00')
    try:
        await client.resolve(stop)
        assert stop.poi_id == 'park' and stop.location_status == 'verified'
        assert stop.city == '阳朔'
    finally:
        await client.close()
