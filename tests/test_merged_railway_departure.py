"""Synthetic cached timetable rows: no live railway service or invented proof."""
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.models import BookedTransport, CityTransfer, DayPlan, Itinerary, Stop, TripRequest
from backend.railway import CHINA_TIMEZONE, Railway


def setup(first_origin=True):
    day = datetime.now(CHINA_TIMEZONE).date() + timedelta(days=3)
    request = TripRequest(origin='长沙' if first_origin else '', destination='上海' if first_origin else '长沙→上海',
        include_origin_transport=first_origin, start_date=day, start_time='09:00', days=2)
    transfer = CityTransfer(from_city='长沙', to_city='上海', departure_date=day, departure_time='16:18',
        arrival_date=day, arrival_time='20:20', mode='train', unit_cost=500)
    itinerary = Itinerary(title='离线车次测试', summary='', transfers=[transfer], days=[
        DayPlan(date=day, title='第一天', stops=[]), DayPlan(date=day + timedelta(days=1), title='第二天', stops=[]),
    ])
    railway = Railway(request)
    railway.enabled = True
    return railway, itinerary


def option(railway, token, departure, arrival, price='500'):
    day = railway.request.start_date.isoformat()
    start = datetime.fromisoformat(day + 'T' + departure)
    end = datetime.fromisoformat(day + 'T' + arrival)
    return {'option_id': token, 'from_city': '长沙', 'to_city': '上海', 'train_code': 'SYN-' + token,
        'departure_station': '长沙南', 'arrival_station': '上海虹桥', 'departure_date': day,
        'arrival_date': day, 'departure_time': departure, 'arrival_time': arrival,
        'duration_minutes': int((end - start).total_seconds() / 60), 'reference_price': price,
        'seat_type': '合成席位', 'seat_availability': '有',
        'queried_at': datetime.now(timezone.utc).isoformat(), 'source': '明确合成时刻表，非真实班次'}


def cached(railway, rows):
    result = {'status': 'queried', 'message': '合成缓存', 'options': rows}
    railway.query = AsyncMock(return_value=result)
    railway.options = {row['option_id']: row for row in rows}
    railway.query_cache[('长沙', '上海', railway.request.start_date.isoformat())] = result


@pytest.mark.asyncio
async def test_first_origin_reconsiders_model_afternoon_and_prefers_earlier_arrival():
    railway, itinerary = setup()
    rows = [option(railway, 'early', '10:05', '14:05'), option(railway, 'fast', '10:30', '13:30'),
            option(railway, 'late', '16:18', '20:20')]
    cached(railway, rows)
    itinerary.transfers[0].rail_option_id = 'late'  # A valid model-selected row is still only a suggestion.
    await railway.enrich(itinerary)
    selected = itinerary.transfers[0]
    assert selected.train_code == 'SYN-fast' and selected.departure_time == '10:30'
    assert selected.arrival_time == '13:30' and selected.schedule_status == 'queried'
    assert railway.query.call_args.args == ('长沙', '上海', railway.request.start_date.isoformat(), '09:30')
    assert '不受模型初稿出发时间限制' in selected.query_note


@pytest.mark.asyncio
async def test_first_origin_keeps_station_buffer_and_uses_reference_price_only_as_tiebreak():
    railway, itinerary = setup()
    cached(railway, [option(railway, 'too-early', '09:15', '11:30', '10'),
                     option(railway, 'costly', '10:00', '14:00', '600'),
                     option(railway, 'cheaper', '10:00', '14:00', '400')])
    await railway.enrich(itinerary)
    assert itinerary.transfers[0].train_code == 'SYN-cheaper'
    assert itinerary.transfers[0].unit_cost == Decimal('400')


@pytest.mark.asyncio
async def test_same_day_does_not_offer_departed_morning_train(monkeypatch):
    import backend.railway as module
    railway, itinerary = setup()
    # This emulates a plan requested at 14:25 while its form still says 09:00.
    selected_day = railway.request.start_date
    class FixedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            value = datetime.combine(selected_day, time(14, 25), CHINA_TIMEZONE)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)
    monkeypatch.setattr(module, 'datetime', FixedClock)
    cached(railway, [option(railway, 'morning', '10:00', '14:00'), option(railway, 'future', '16:18', '20:20')])
    await railway.enrich(itinerary)
    assert railway.query.call_args.args[-1] == '14:55'
    assert itinerary.transfers[0].train_code == 'SYN-future'


@pytest.mark.asyncio
async def test_booked_user_transport_is_not_requeried_or_replaced():
    railway, itinerary = setup()
    original = itinerary.transfers[0]
    original.booking_status = 'user_confirmed'
    original.train_code = 'USER-ORDER'
    railway.request.booked_transports = [BookedTransport(from_city='长沙', to_city='上海',
        departure_date=original.departure_date, departure_time=original.departure_time,
        arrival_date=original.arrival_date, arrival_time=original.arrival_time,
        number='USER-ORDER', departure_place='长沙南站', arrival_place='上海虹桥站')]
    snapshot = original.model_dump()
    cached(railway, [option(railway, 'morning', '10:00', '14:00')])
    await railway.enrich(itinerary)
    assert original.model_dump() == snapshot
    railway.query.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_option_id_never_bypasses_activity_fit_on_other_legs():
    railway, itinerary = setup(first_origin=False)
    itinerary.transfers[0].departure_time = '09:00'
    row = option(railway, 'overlap', '10:00', '11:00')
    cached(railway, [row])
    itinerary.transfers[0].rail_option_id = row['option_id']
    itinerary.days[0].stops = [Stop(name='原定上午景点', city='长沙', start='09:30', end='12:00')]
    await railway.enrich(itinerary)
    # A conflicting activity no longer hides the real train: use the queried row
    # and let scheduling move the day around it.
    assert itinerary.transfers[0].schedule_status == 'queried'
    assert itinerary.transfers[0].train_code == 'SYN-overlap'
    assert '行程已按班次重排' in itinerary.transfers[0].query_note


@pytest.mark.asyncio
async def test_other_city_leg_retains_model_not_before_floor_and_fixed_activity_windows():
    railway, itinerary = setup(first_origin=False)
    rows = [option(railway, 'morning', '10:00', '14:00'), option(railway, 'late', '16:18', '20:20')]
    cached(railway, rows)
    itinerary.days[0].stops = [Stop(name='原定上午景点', city='长沙', start='09:30', end='12:00')]
    await railway.enrich(itinerary)
    assert itinerary.transfers[0].train_code == 'SYN-late'
    assert railway.query.call_args.args[-1] == '16:18'
