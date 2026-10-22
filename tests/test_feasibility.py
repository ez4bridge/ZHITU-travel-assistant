from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.feasibility import check_departure, schedule_journey, price_meals, require_feasible
from backend.models import Stop
from test_roundtrip_generation import roundtrip
from test_core import sample


def test_past_dates_and_same_day_times_are_rejected_in_beijing_time():
    trip, _, _ = sample()
    now = datetime(2026, 10, 22, 19, 30, tzinfo=timezone(timedelta(hours=8)))
    trip.start_date = now.date()
    for value in ['09:00', '19:30']:
        trip.start_time = value
        with pytest.raises(ValueError, match='北京时间'):
            check_departure(trip, now)
    trip.start_time = '19:31'
    check_departure(trip, now)
    trip.start_date -= timedelta(days=1)
    with pytest.raises(ValueError):
        check_departure(trip, now)


def test_arrival_and_return_boundaries_reserve_station_allowance():
    trip, itinerary = roundtrip()
    stop = itinerary.days[1].stops[0]
    itinerary.days[0].stops = [stop.model_copy(update={'start': '13:00', 'end': '14:00'})]
    itinerary.days[1].stops = []
    changes = schedule_journey(trip, itinerary)
    assert itinerary.days[0].stops[0].start == '14:45'
    assert changes
    itinerary.days[2].stops = [stop.model_copy(update={'start': '17:00', 'end': '18:00'})]
    schedule_journey(trip, itinerary)
    assert itinerary.days[2].stops[0].end == '16:15'


def test_cannot_fit_activities_does_not_shorten_or_delete_them():
    trip, itinerary = roundtrip()
    stop = itinerary.days[1].stops[0].model_copy(update={'start': '09:00', 'end': '20:00'})
    itinerary.days[0].stops = [stop]
    original = itinerary.model_dump()
    with pytest.raises(ValueError, match='无法'):
        schedule_journey(trip, itinerary)
    assert itinerary.model_dump() == original


def test_real_local_routes_are_included_in_time_repair():
    trip, itinerary, routes = sample()
    routes[0].duration_s = 5400
    schedule_journey(trip, itinerary, routes)
    assert itinerary.days[0].stops[1].start == '11:30'


def test_meal_budget_replaces_model_price_without_double_counting_people():
    trip, itinerary, _ = sample()
    meal = Stop(id='meal', name='真实餐馆', kind='meal', start='12:00', end='13:00',
        location_status='verified', average_cost_yuan=Decimal('167.25'),
        information_source='https://www.amap.com/place/restaurant')
    itinerary.days[0].stops.append(meal)
    price_meals(itinerary)
    food = [e for e in itinerary.days[0].expenses if e.category == 'food']
    assert len(food) == 1
    assert food[0].unit_cost == Decimal('167.25')
    assert food[0].quantity == 1 and food[0].basis == 'person'
    assert food[0].stop_id == meal.id and '高德' in food[0].note
    price_meals(itinerary)
    assert len([e for e in itinerary.days[0].expenses if e.category == 'food']) == 1


@pytest.mark.parametrize('cost', [None, Decimal('0')])
def test_no_amap_cost_does_not_fall_back_to_invented_meal_price(cost):
    _, itinerary, _ = sample()
    itinerary.days[0].stops.append(Stop(id='meal', name='餐馆', kind='meal', start='12:00', end='13:00',
        location_status='verified', average_cost_yuan=cost, information_source='https://www.amap.com/place/x'))
    with pytest.raises(ValueError, match='人均消费'):
        price_meals(itinerary)


def test_blocking_issues_cannot_be_published():
    with pytest.raises(ValueError, match='未发布或保存'):
        require_feasible(SimpleNamespace(errors=[SimpleNamespace(message='交通冲突')]))
    require_feasible(SimpleNamespace(errors=[]))


@pytest.mark.asyncio
async def test_invalid_sse_plan_is_never_saved_and_correction_is_bounded(monkeypatch, tmp_path, offline_domestic_scope):
    import backend.main as main
    from backend.storage import Store
    from backend.amap import Amap
    from backend.weather import unavailable
    trip, itinerary, routes = sample()
    itinerary.days[0].stops[0].location_status = 'unknown'
    storage = Store(tmp_path / 'plans.sqlite3')
    monkeypatch.setattr(main, 'store', storage)
    generate = AsyncMock(side_effect=lambda *a, **kw: itinerary.model_copy(deep=True))
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    monkeypatch.setattr(main, 'forecast_trip', AsyncMock(return_value=(unavailable(trip, datetime.now(timezone.utc)), {})))
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=routes))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/generate', json=trip.model_dump(mode='json'))
    assert 'event: error' in response.text and 'event: result' not in response.text
    assert not storage.list()
    assert generate.await_count == 2
    assert 'failure' in generate.await_args.args[4]


@pytest.mark.asyncio
async def test_generate_endpoint_rejects_past_time_before_model_call(monkeypatch, offline_domestic_scope):
    import backend.main as main
    trip, _, _ = sample()
    trip.start_date = datetime.now(timezone(timedelta(hours=8))).date() - timedelta(days=1)
    generate = AsyncMock()
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/generate', json=trip.model_dump(mode='json'))
    assert response.status_code == 422
    generate.assert_not_awaited()
