"""Regression checks for upstream multi-city features with local route preferences."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap
from backend.locality import optimize_day_meals, prepare_accommodation
from backend.models import CityTransfer, Stop, TripRequest
from backend.planning import recost_all
from backend.models import itinerary_expenses, cents
from test_core import sample


def two_cities():
    request, itinerary, routes = sample()
    request = TripRequest(**{**request.model_dump(), 'destination': '北京→上海', 'days': 2,
        'destinations': [{'city': '北京', 'preferred_days': 1}, {'city': '上海', 'preferred_days': 1}]})
    itinerary.days[0].stops[0].city = '北京'
    itinerary.days[0].stops[1].city = '上海'
    return request, itinerary, routes


@pytest.mark.asyncio
async def test_transit_provider_city_and_cache_follow_each_stop_city():
    amap = Amap(['北京', '上海'])
    amap.request = AsyncMock(return_value={'route': {'transits': [{
        'distance': '2000', 'duration': '900', 'cost': '4', 'segments': [{
            'walking': {'steps': [{'polyline': '121.49,31.23;121.49,31.22'}]}}]}]}})
    try:
        for city in ['北京', '上海', '上海']:
            await amap.route_in_city([121.49, 31.23], [121.49, 31.22], 'transit', city)
        assert amap.request.await_count == 2
        assert [call.args[1]['city'] for call in amap.request.call_args_list] == ['北京', '上海']
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_meal_locality_never_routes_across_cities():
    _, itinerary, _ = two_cities()
    itinerary.days[0].stops.insert(1, Stop(name='午餐', kind='meal', city='北京',
        start='10:00', end='10:30', location=(116.4, 39.9)))
    amap = Amap(['北京', '上海'])
    amap.route = AsyncMock()
    amap.request = AsyncMock()
    try:
        await optimize_day_meals(amap, itinerary.days[0])
        amap.route.assert_not_awaited()
        amap.request.assert_not_awaited()
    finally:
        await amap.close()


def test_confirmed_hotel_applies_only_to_first_city_and_travel_days_can_be_empty():
    trip, itinerary, _ = two_cities()
    trip.confirmed_hotel = '用户在北京订的酒店'
    second = itinerary.days[0].model_copy(deep=True)
    second.date += timedelta(days=1)
    itinerary.days[0].stops = [Stop(name='北京酒店', city='北京', kind='hotel', start='09:00', end='10:00')]
    second.stops = [Stop(name='助手挑的上海酒店', city='上海', kind='hotel', start='18:00', end='19:00')]
    itinerary.days.append(second)
    prepare_accommodation(itinerary, trip)
    assert itinerary.days[0].stops[0].place_name == trip.confirmed_hotel
    assert not second.stops and '由你选择' in second.accommodation


def test_local_costs_use_current_city_and_preserve_intercity_budget():
    trip, itinerary, routes = two_cities()
    routes[0].mode = 'taxi'
    routes[0].distance_m = 6000
    itinerary.transfers = [CityTransfer(from_city='北京', to_city='上海', departure_date=trip.start_date,
        arrival_date=trip.start_date, departure_time='12:00', arrival_time='17:00', unit_cost=500)]
    transfer_before = itinerary.transfers[0].model_dump()
    recost_all(trip, itinerary, routes)
    assert routes[0].cost_estimate.low == Decimal('22.10')
    assert '上海' in routes[0].cost_estimate.method
    assert itinerary.transfers[0].model_dump() == transfer_before
    total = sum(cents(e.unit_cost) * e.quantity * (trip.people if e.basis == 'person' else 1)
                for e in itinerary_expenses(itinerary))
    assert total >= 50000 * trip.people
