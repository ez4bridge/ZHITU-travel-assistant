import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.budget import optimize_budget, report_for
from backend.models import RouteOption, Stop
from backend.planning import recost_all
from backend.storage import Store
from backend.railway import train_options
from test_core import sample
from test_railway import fixture as rail_fixture


def expensive_trip():
    trip, itinerary, routes = sample()
    trip.budget = Decimal('350')
    route = routes[0]
    route.mode = 'taxi'
    route.taxi_cost_yuan = Decimal('100')
    route.alternatives = [RouteOption(mode='transit', available=True, distance_m=1800,
        duration_s=1800, fare_yuan=3, polyline=route.polyline)]
    recost_all(trip, itinerary, routes)
    return trip, itinerary, routes


def test_verified_cheaper_route_meets_budget_without_changing_food_prices():
    trip, itinerary, routes = expensive_trip()
    original = itinerary.model_dump()
    assert report_for(trip, itinerary, routes)['total_cents'] == 40150
    result, selected, changes = optimize_budget(trip, itinerary, routes, SimpleNamespace(query_cache={}))
    assert selected[0].mode == 'transit'
    report = report_for(trip, result, selected)
    assert report['total_cents'] == 31050 and not report['errors']
    assert 'over_budget' not in {issue['code'] for issue in report['warnings']}
    assert changes and result.days[0].expenses[0].unit_cost == Decimal('50.25')
    assert itinerary.model_dump() == original  # Accepted results are returned, not silently mutated.


def test_manual_transport_choice_is_not_overridden_to_reduce_cost():
    trip, itinerary, routes = expensive_trip()
    routes[0].user_selected = True
    result, selected, changes = optimize_budget(trip, itinerary, routes, SimpleNamespace(query_cache={}))
    assert selected[0].mode == 'taxi' and not changes
    assert report_for(trip, result, selected)['total_cents'] == 40150


def test_cheaper_but_impossible_route_is_rejected():
    trip, itinerary, routes = expensive_trip()
    routes[0].alternatives[0].duration_s = 86400
    result, selected, changes = optimize_budget(trip, itinerary, routes, SimpleNamespace(query_cache={}))
    assert selected[0].mode == 'taxi' and not changes
    assert result.days[0].stops[1].start == '10:30'


def test_cheaper_route_cannot_shift_normal_lunch_into_the_evening():
    trip, itinerary, routes = expensive_trip()
    meal = itinerary.days[0].stops[-1]
    meal.kind, meal.meal_period, meal.name = 'meal', 'lunch', '午餐'
    meal.start, meal.end = '12:00', '13:00'
    routes[0].alternatives[0].duration_s = 6 * 3600
    result, selected, changes = optimize_budget(trip, itinerary, routes, SimpleNamespace(query_cache={}))
    assert selected[0].mode == 'taxi' and not changes
    assert result.days[0].stops[-1].start == '12:00'


def test_cheap_early_return_train_cannot_shift_lunch_into_early_morning():
    from datetime import timedelta
    from test_railway import options
    trip, itinerary, *_ = rail_fixture()
    trip.origin, trip.return_to_origin, trip.start_time, trip.budget = '北京', True, '07:00', Decimal('50')
    itinerary.days[0].stops = [Stop(id='bj', name='北京景点', city='北京', start='07:15', end='08:00',
        location=(116.4, 39.9), location_status='verified')]
    itinerary.days[1].stops = [
        Stop(id='tj', name='天津景点', city='天津', start='09:00', end='10:00', location=(117.2, 39.1), location_status='verified'),
        Stop(id='lunch', name='天津午餐', city='天津', kind='meal', meal_period='lunch', start='12:00', end='12:45',
             location=(117.2, 39.1), location_status='verified')]
    day = trip.start_date + timedelta(days=1)
    itinerary.transfers.append(itinerary.transfers[0].model_copy(update={
        'from_city': '天津', 'to_city': '北京', 'departure_date': day, 'arrival_date': day,
        'departure_time': '17:00', 'arrival_time': '19:00', 'unit_cost': Decimal('1000')}))
    option = {**options()[0], 'departure_date': str(day), 'arrival_date': str(day),
        'departure_time': '09:00', 'arrival_time': '11:00', 'departure_station': '天津', 'arrival_station': '北京南'}
    rail = SimpleNamespace(query_cache={('天津', '北京', str(day)): {'options': [option]}})
    # One colocated leg keeps the test focused on rail and dining constraints.
    from backend.models import RouteLeg
    from datetime import datetime, timezone
    routes = [RouteLeg(day_index=1, from_id='tj', to_id='lunch', mode='walking', available=True,
        duration_s=0, distance_m=0, stationary=True, queried_at=datetime.now(timezone.utc))]
    result, _, changes = optimize_budget(trip, itinerary, routes, rail)
    assert not changes
    assert result.transfers[-1].departure_time == '17:00'
    assert result.days[1].stops[-1].start == '12:00'


def test_single_day_budget_changes_never_touch_other_days():
    trip, itinerary, routes = expensive_trip()
    _, selected, changes = optimize_budget(trip, itinerary, routes, SimpleNamespace(query_cache={}), only_day=1)
    assert selected[0].mode == 'taxi' and not changes


def test_cached_cheaper_train_keeps_actual_schedule_and_ticket_price():
    trip, itinerary, origin, destination, ticket, price = rail_fixture()
    trip.budget = Decimal('50')
    itinerary.transfers[0].departure_time = '10:00'
    itinerary.days[0].stops = [Stop(id='bj', name='北京景点', city='北京', start='09:00', end='09:15',
        location=(116.4, 39.9), location_status='verified')]
    itinerary.days[1].stops = [Stop(id='tj', name='天津景点', city='天津', start='09:00', end='10:00',
        location=(117.2, 39.1), location_status='verified')]
    options = train_options([ticket], [price], origin, destination, trip.start_date, trip.people, '北京', '天津')
    rail = SimpleNamespace(query_cache={('北京', '天津', str(trip.start_date)): {'options': options}})
    result, routes, changes = optimize_budget(trip, itinerary, [], rail)
    assert result.transfers[0].unit_cost == Decimal('19.51')
    assert result.transfers[0].train_code == 'C1234'
    assert result.transfers[0].arrival_time == '11:43'
    assert result.transfers[0].schedule_status == 'queried'
    assert changes and report_for(trip, result, routes)['total_cents'] == 3902


@pytest.mark.asyncio
async def test_over_budget_still_delivers_saves_and_reports_exact_difference(monkeypatch, tmp_path, offline_domestic_scope):
    import backend.main as main
    from backend.amap import Amap
    trip, itinerary, routes = sample()
    trip.budget = Decimal('100')
    storage = Store(tmp_path / 'plans.sqlite3')
    monkeypatch.setattr(main, 'store', storage)
    generate = AsyncMock(side_effect=lambda *a, **kw: itinerary.model_copy(deep=True))
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=routes))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/generate', json=trip.model_dump(mode='json'))
        assert 'event: error' not in response.text, response.text
        result = json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
        saved = (await client.get('/api/plans/' + result['id'])).json()
        recheck = (await client.post('/api/plans/' + result['id'] + '/validate')).json()
    assert saved == result and not result['validation']['errors']
    assert result['validation']['total_cents'] == 30150
    assert result['validation']['budget_cents'] == 10000
    assert 'over_budget' in {issue['code'] for issue in result['validation']['warnings']}
    assert '201.50' in result['itinerary']['tips'][-1]
    assert 'over_budget' in {issue['code'] for issue in recheck['warnings']}
    assert generate.await_count == 1  # No full-model retry just to fail again on budget.
