"""Full regeneration with user bookings as fixed facts; offline model double."""
import asyncio
from datetime import timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from backend.agent import booking_facts_instruction
from backend.booking_routes import booking_trip, preview_bookings
from backend.models import CityTransfer, ConfirmedHotel, DayPlan, Expense, Itinerary, Stop, TripRequest
from plan_samples import mixed_plan
from test_merged_bookings import SyntheticBookingMap, booking_body, hotel, setup_booking, train


def generated(request):
    """A synthetic model answer that ignores the hotel it was told about."""
    return Itinerary(title='合成重新生成', summary='合成模型输出，仅离线测试', transfers=[
        CityTransfer(from_city='北京', to_city='天津', departure_date=request.start_date,
                     departure_time='11:00', arrival_date=request.start_date, arrival_time='12:00',
                     mode='train', unit_cost=100, basis='group')], days=[
        DayPlan(date=request.start_date, title='第一天', stops=[
            Stop(id='gen-hotel', name='模型编造的酒店', city='天津', kind='hotel', start='13:30', end='14:00'),
            Stop(id='gen-sight', name='合成新景点', city='天津', start='15:00', end='16:30',
                 location_behavior='unlocated')],
            expenses=[Expense(label='模型编造住宿', category='hotel', unit_cost='400', stop_id='gen-hotel')]),
        DayPlan(date=request.start_date + timedelta(days=1), title='第二天', stops=[
            Stop(id='gen-next', name='合成次日景点', city='天津', start='09:30', end='11:00',
                 location_behavior='unlocated')])])


class RegenerateMap(SyntheticBookingMap):
    """Offline map that also gives the model's new stops synthetic coordinates."""

    skip = frozenset()

    async def enrich(self, itinerary, mode, **kwargs):
        for day in itinerary.days:
            for number, stop in enumerate(day.stops):
                if stop.location is None and stop.name not in self.skip:
                    stop.location = (116.3 + number * .001, 39.9)
                    stop.location_status = 'verified'
                    stop.poi_id = stop.id
        return await super().enrich(itinerary, mode, **kwargs)


def test_regeneration_asks_the_model_with_fixed_facts_and_keeps_them(tmp_path):
    plan = mixed_plan()
    store, plan, storage, state = setup_booking(tmp_path, plan)
    before = plan.model_dump_json()
    calls = []
    lodging = ConfirmedHotel(city='天津', name='合成酒店甲', address='合成分店地址',
                             check_in='2026-11-02', check_out='2026-11-03', total_cost='300')

    async def generate(request, amap, emit, previous=None, instruction='', rail=None):
        calls.append({'request': request, 'instruction': instruction, 'previous': previous})
        return generated(request)

    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [lodging], [train()]),
        storage, amap_factory=RegenerateMap, regenerate=True, generator=generate))

    assert result['status'] == 'ready', result['conflicts']
    assert len(calls) == 1 and calls[0]['previous'] is None
    sent = calls[0]['request']
    assert sent.booked_transports[0].number == 'G-TEST'
    assert sent.confirmed_hotels[0].name == '合成酒店甲'
    assert '固定事实' in calls[0]['instruction']
    assert len(store.list()) == 1 and plan.model_dump_json() == before

    child = storage.apply(plan.id, result['preview_id'])
    day = child.itinerary.days[0]
    assert any(stop.id == 'gen-sight' for stop in day.stops)
    assert not any(stop.id == 'gen-hotel' for stop in day.stops)
    assert not any(expense.label == '模型编造住宿' for d in child.itinerary.days for expense in d.expenses)
    hotel_anchor = next(stop for stop in day.stops if stop.booking_anchor == 'hotel_departure')
    assert hotel_anchor.name == '合成酒店甲' and hotel_anchor.booking_address == '合成分店地址'
    arrival = next(stop for stop in day.stops if stop.booking_anchor == 'transport_arrival')
    assert arrival.place_name == '合成天津站'
    assert child.itinerary.transfers[0].departure_time == '12:00'
    assert child.itinerary.transfers[0].booking_status == 'user_confirmed'
    assert '整份重新生成' in child.itinerary.summary


def test_one_unplaceable_suggestion_is_dropped_instead_of_blocking(tmp_path):
    """Reported 2026-10-22: one unresolved stop made the whole preview unsaveable."""
    plan = mixed_plan()
    store, plan, storage, state = setup_booking(tmp_path, plan)

    class PickyMap(RegenerateMap):
        skip = frozenset({'无法定位的地点'})

    async def generate(request, amap, emit, previous=None, instruction='', rail=None):
        itinerary = generated(request)
        itinerary.days[0].stops.append(Stop(id='gen-weird', name='无法定位的地点', city='天津',
            start='16:40', end='17:10', location_behavior='unlocated'))
        return itinerary

    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [], [train()]),
        storage, amap_factory=PickyMap, regenerate=True, generator=generate))
    assert result['status'] == 'ready', result['conflicts']
    assert any('无法定位的地点' in notice for notice in result['notices'])
    child = storage.apply(plan.id, result['preview_id'])
    assert not any(stop.id == 'gen-weird' for day in child.itinerary.days for stop in day.stops)
    assert any(stop.id == 'gen-sight' for day in child.itinerary.days for stop in day.stops)


def test_regeneration_is_refused_when_a_fact_would_be_lost(tmp_path):
    plan = mixed_plan()
    store, plan, storage, state = setup_booking(tmp_path, plan)
    before = plan.model_dump_json()
    state['activities']['s0-0'] = {'status': 'completed', 'locked': True}
    calls = []

    async def generate(*args, **kwargs):
        calls.append(args)
        return generated(args[0])

    with pytest.raises(ValueError, match='完成或锁定'):
        asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel()]), storage,
            amap_factory=SyntheticBookingMap, regenerate=True, generator=generate))
    assert not calls and plan.model_dump_json() == before


def test_booking_facts_instruction_only_appears_with_user_facts():
    empty = TripRequest(destination='北京', start_date='2026-11-02', days=2)
    assert booking_facts_instruction(empty) == ''
    with_train = TripRequest(destination='北京', start_date='2026-11-02', days=2,
        booked_transports=[train()])
    text = booking_facts_instruction(with_train)
    assert '固定事实' in text and '不得改写' in text and '不得声称已查验订单' in text


def test_regenerate_route_passes_conflicts_back_without_saving(tmp_path, monkeypatch):
    """A conflicting regeneration returns 409-style conflicts, never a save."""
    from backend import main, booking_routes
    store, plan, storage, state = setup_booking(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    seen = []

    async def synthetic_preview(*args, **kwargs):
        seen.append(kwargs.get('regenerate'))
        return {'status': 'conflict', 'changes': [], 'notices': [], 'conflicts': ['synthetic conflict']}

    monkeypatch.setattr(booking_routes, 'preview_bookings', synthetic_preview)
    with TestClient(main.app) as client:
        body = booking_body(plan, state, [hotel()]).model_dump(mode='json')
        response = client.post(f'/api/plans/{plan.id}/booking-regenerate', json=body)
        assert response.status_code == 200, response.text
        assert response.json()['status'] == 'conflict'
        assert seen == [True]
        assert len(store.list()) == 1
def test_regeneration_with_over_budget_saves_anyway_and_reports(tmp_path):
    """Reported 2026-10-23: budget overrun blocked the supplement regeneration."""
    plan = mixed_plan()
    plan.request.budget = Decimal('1')
    plan.itinerary.days[0].expenses.append(Expense(label='合成门票', category='ticket',
                                                   unit_cost='500', stop_id=plan.itinerary.days[0].stops[0].id))
    store, plan, storage, state = setup_booking(tmp_path, plan)

    async def generate(request, amap, emit, previous=None, instruction='', rail=None):
        return plan.itinerary.model_copy(deep=True)

    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel()], [train()]),
        storage, amap_factory=RegenerateMap, regenerate=True, generator=generate))
    assert result['status'] == 'ready', result.get('conflicts')
    assert result['preview_id']
    assert any('超过全员预算' in notice for notice in result['notices'])
    child = storage.apply(plan.id, result['preview_id'])
    assert any(error.code == 'over_budget' for error in child.validation.errors)
