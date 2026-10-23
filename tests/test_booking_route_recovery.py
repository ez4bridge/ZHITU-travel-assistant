"""Booking regeneration heals mutable route endpoints, using offline roads only.

All coordinates, POIs, fare amounts and providers in this file are synthetic.
These regressions do not establish availability of any real Hangzhou route.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from backend import booking_routes
from backend.booking_routes import preview_bookings
from backend.models import (BookedTransport, CityTransfer, ConfirmedHotel, DayPlan, DestinationStay,
                            Expense, Itinerary, Issue, RouteLeg, Stop)
from plan_samples import sample_plan
from plan_samples import mixed_plan
from test_merged_bookings import booking_body, setup_booking


def point(sid, name=None, *, city='北京', start='12:00', end='13:00', meal=False):
    return Stop(id=sid, name=name or f'合成景点-{sid}', city=city,
                kind='meal' if meal else 'sight', meal_period='lunch' if meal else '',
                start=start, end=end, location_behavior='unlocated')


def model_answer(request, first, *, following=None, transfers=()):
    days = [DayPlan(date=request.start_date, title='合成测试第一天', stops=first,
        expenses=[Expense(label=stop.name + '费用', category='food' if stop.kind == 'meal' else 'ticket',
                          unit_cost=20, basis='group', stop_id=stop.id) for stop in first])]
    for index in range(1, request.days):
        stops = following if index == 1 and following is not None else [
            point(f'new-{index}', start='10:00', end='11:00')]
        days.append(DayPlan(date=request.start_date + timedelta(days=index), title='合成次日', stops=stops))
    return Itinerary(title='合成新方案', summary='隔离模型输出', days=days,
                     transfers=[transfer.model_copy(deep=True) for transfer in transfers])


def map_with_failures(denied):
    class OfflineMap:
        """A directed fixture graph; deleting a vertex triggers another real fixture lookup."""
        calls = []
        instances = []

        def __init__(self, cities):
            self.closed = False
            self.__class__.instances.append(self)

        async def enrich(self, itinerary, mode, *, compare_modes, day_indices, optimize_meals, people):
            routes = []
            for index in sorted(day_indices):
                day = itinerary.days[index]
                for number, stop in enumerate(day.stops):
                    stop.location = (116.2 + number * .001, 39.9 + index * .001)
                    stop.location_status = 'verified'
                    stop.poi_id = 'synthetic-' + stop.id
                    stop.location_note = '合成离线地图点，不代表真实地点定位'
                for before, after in zip(day.stops, day.stops[1:]):
                    local = before.city.rstrip('市') == after.city.rstrip('市')
                    available = local and not denied(index, before, after)
                    self.calls.append((index, before.id, after.id, available))
                    routes.append(RouteLeg(day_index=index, from_id=before.id, to_id=after.id,
                        mode='walking', scope='local' if local else 'intercity', available=available,
                        distance_m=400 if available else None, duration_s=300 if available else None,
                        polyline=[before.location, after.location] if available else [],
                        queried_at=datetime.now(timezone.utc), note='合成离线道路，不代表真实地图'))
            return routes

        async def close(self):
            self.closed = True
    return OfflineMap


def regenerate(tmp_path, monkeypatch, stops, denied, *, real_flow=False, following=None, plan=None, hotels=None, transports=(), transfers=(), map_cls=None):
    if not real_flow:
        # This fixture isolates graph repair; a separate case retains real meal preparation.
        monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    plan = plan or sample_plan(2)
    store, plan, storage, state = setup_booking(tmp_path, plan)
    original = plan.model_dump_json()
    if hotels is None:
        hotels = [ConfirmedHotel(city='北京', name='合成已填酒店', check_in='2026-11-01',
                                 check_out='2026-11-04', total_cost=100)]
    amap = map_cls or map_with_failures(denied)

    async def generator(request, *_args, **_kwargs):
        return model_answer(request, stops, following=following, transfers=transfers)

    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, hotels, transports), storage,
        amap_factory=amap, regenerate=True, generator=generator))
    assert len(store.list()) == 1 and plan.model_dump_json() == original
    return result, storage, plan, amap


def apply_ready(result, storage, plan):
    assert result['status'] == 'ready', result.get('conflicts')
    return storage.apply(plan.id, result['preview_id'])


@pytest.mark.parametrize('failed_side', ['arrival', 'departure'])
def test_regeneration_drops_mutable_meal_on_either_side_of_fixed_hotel(tmp_path, monkeypatch, failed_side):
    meal = point('unreachable-meal', '楼外楼（孤山路店）午餐', meal=True)
    good = point('reachable-sight', start='15:00' if failed_side == 'arrival' else '09:00',
                 end='16:00' if failed_side == 'arrival' else '10:00')
    stops = [meal, good] if failed_side == 'arrival' else [good, meal]
    def denied(_index, before, after):
        return ((before.booking_anchor == 'hotel_departure' and after.id == meal.id)
                if failed_side == 'arrival' else (before.id == meal.id and after.booking_anchor == 'hotel_return'))
    result, storage, plan, amap = regenerate(tmp_path, monkeypatch, stops, denied)
    child = apply_ready(result, storage, plan)
    first = child.itinerary.days[0]
    assert meal.id not in {stop.id for stop in first.stops}
    assert good.id in {stop.id for stop in first.stops}
    assert {stop.booking_anchor for stop in first.stops} >= {'hotel_departure', 'hotel_return'}
    assert not any(expense.stop_id == meal.id for expense in first.expenses)
    assert any(meal.name in note for note in [*result['notices'], *child.adjustments])
    assert all(route.available for route in child.routes if route.scope == 'local')
    assert all(instance.closed for instance in amap.instances)
    # Recovery queried the newly adjacent edge rather than assuming it is reachable.
    assert any(index == 0 and available and (before == good.id or after == good.id)
               for index, before, after, available in amap.calls)


def test_missing_route_named_after_fixed_station_removes_its_mutable_predecessor(tmp_path, monkeypatch):
    plan = sample_plan(2)
    plan.request.origin, plan.request.destination = '', '杭州→长沙'
    plan.request.destinations = [DestinationStay(city='杭州'), DestinationStay(city='长沙')]
    plan.request.include_origin_transport = False
    plan.request.return_to_origin = False
    for index, day in enumerate(plan.itinerary.days):
        for stop in day.stops:
            stop.city = '杭州' if index == 0 else '长沙'
    transfer = CityTransfer(from_city='杭州', to_city='长沙', mode='train', departure_date='2026-11-02',
        departure_time='18:00', arrival_date='2026-11-02', arrival_time='20:00', unit_cost=100, basis='group')
    plan.itinerary.transfers = [transfer]
    meal = point('station-bad-meal', '楼外楼（孤山路店）午餐', city='杭州', meal=True)
    good = point('station-good-sight', city='杭州', start='09:00', end='10:00')
    booking = BookedTransport(from_city='杭州', to_city='长沙', mode='train', departure_date='2026-11-02',
        departure_time='18:00', arrival_date='2026-11-02', arrival_time='20:00',
        departure_place='杭州东站', arrival_place='合成长沙站', number='SYN-1800', total_cost=100)
    def denied(_index, before, after):
        return before.id == meal.id and after.booking_anchor == 'transport_departure'
    result, storage, original, amap = regenerate(tmp_path, monkeypatch, [good, meal], denied, plan=plan,
        hotels=[], transports=[booking], transfers=[transfer], following=[
            point('destination-sight', city='长沙', start='10:00', end='11:00')])
    child = apply_ready(result, storage, original)
    assert not any(stop.id == meal.id for day in child.itinerary.days for stop in day.stops)
    fixed = next(stop for stop in child.itinerary.days[0].stops if stop.booking_anchor == 'transport_departure')
    assert fixed.place_name == '杭州东站'
    assert child.request.booked_transports[0] == booking
    assert any(before == good.id and after == fixed.id and available for _, before, after, available in amap.calls)


def test_recovery_rechecks_new_adjacency_until_five_optional_tail_stops_are_removed(tmp_path, monkeypatch):
    good = point('keep-sight', start='09:00', end='10:00')
    optional = [point(f'bad-tail-{index}', start=f'{10 + index}:30', end=f'{11 + index}:00') for index in range(5)]
    def denied(_index, before, after):
        return before.id.startswith('bad-tail-') and after.booking_anchor == 'hotel_return'
    result, storage, plan, amap = regenerate(tmp_path, monkeypatch, [good, *optional], denied)
    child = apply_ready(result, storage, plan)
    first = child.itinerary.days[0]
    assert good.id in {stop.id for stop in first.stops}
    assert not any(stop.id.startswith('bad-tail-') for stop in first.stops)
    assert all(route.available for route in child.routes if route.day_index == 0)
    assert sum(not available for _, _, _, available in amap.calls) >= 5


def test_unreachable_fixed_to_fixed_leg_is_saved_as_unknown_without_fabricating_a_route(tmp_path, monkeypatch):
    original = mixed_plan()
    transfer = original.itinerary.transfers[0].model_copy(deep=True)
    booking = BookedTransport(from_city='北京', to_city='天津', mode='train', departure_date='2026-11-02',
        departure_time='12:00', arrival_date='2026-11-02', arrival_time='13:00',
        departure_place='合成北京站', arrival_place='合成天津站', number='SYN-1200', total_cost=100)
    hotel = ConfirmedHotel(city='天津', name='合成天津酒店', check_in='2026-11-02',
                           check_out='2026-11-04', total_cost=100)
    result, storage, plan, _ = regenerate(tmp_path, monkeypatch, [], lambda _i, a, b: bool(a.booking_anchor and b.booking_anchor),
        following=[], plan=original, hotels=[hotel], transports=[booking], transfers=[transfer])
    child = apply_ready(result, storage, plan)
    assert any(stop.booking_anchor for day in child.itinerary.days for stop in day.stops)
    assert any(not route.available for route in child.routes)
    assert any(error.code == 'missing_route' for error in child.validation.errors)
    assert any(('路线' in note or '接驳' in note) and ('未核' in note or '待核' in note or '未知' in note)
               for note in result['notices'])


def test_route_recovery_does_not_delete_same_named_stop_on_an_unaffected_day(tmp_path, monkeypatch):
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    plan = sample_plan(3)
    label = '楼外楼（孤山路店）午餐'
    for index, day in enumerate(plan.itinerary.days):
        day.stops[-1].name, day.stops[-1].kind = label, 'meal'
        day.stops[-1].start, day.stops[-1].end = '12:00', '13:00'
        day.stops[-1].meal_period = 'lunch'
    preserved = plan.itinerary.days[2].model_copy(deep=True)
    store, plan, storage, state = setup_booking(tmp_path, plan)
    state['activities'][preserved.stops[-1].id] = {'locked': True, 'status': 'planned'}
    amap = map_with_failures(lambda i, before, after: i == 0 and before.id == 's0-1' and after.booking_anchor == 'hotel_return')
    hotel = ConfirmedHotel(city='北京', name='合成已填酒店', check_in='2026-11-01', check_out='2026-11-03', total_cost=100)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel]), storage, amap_factory=amap))
    child = apply_ready(result, storage, plan)
    assert child.itinerary.days[2] == preserved
    assert any(stop.id == 's1-1' for stop in child.itinerary.days[1].stops)
    assert not any(stop.id == 's0-1' for stop in child.itinerary.days[0].stops)
    assert store.get(plan.id).itinerary.days[2] == preserved


@pytest.mark.parametrize('fact', ['locked', 'completed', 'paid'])
def test_route_healing_never_silently_removes_protected_activity_or_its_expense(tmp_path, monkeypatch, fact):
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    plan = sample_plan(2)
    protected = plan.itinerary.days[0].stops[-1]
    cost = plan.itinerary.days[0].expenses[-1]
    if fact == 'paid':
        cost.commitment = 'paid'
    store, plan, storage, state = setup_booking(tmp_path, plan)
    if fact != 'paid':
        state['activities'][protected.id] = {'locked': fact == 'locked',
                                            'status': 'completed' if fact == 'completed' else 'planned'}
    amap = map_with_failures(lambda _i, before, after: before.id == protected.id and after.booking_anchor == 'hotel_return')
    hotel = ConfirmedHotel(city='北京', name='合成已填酒店', check_in='2026-11-02', check_out='2026-11-04', total_cost=100)
    original = plan.model_dump_json()
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel]), storage, amap_factory=amap))
    assert plan.model_dump_json() == original and len(store.list()) == 1
    if result['status'] == 'conflict':
        assert 'preview_id' not in result
        candidate = result.get('candidate')
        if candidate is None:
            return
        assert any(stop['id'] == protected.id for day in candidate['itinerary']['days'] for stop in day['stops'])
    else:
        child = apply_ready(result, storage, plan)
        assert any(stop.id == protected.id for day in child.itinerary.days for stop in day.stops)
        assert any(expense.label == cost.label and expense.unit_cost == cost.unit_cost
                   and expense.commitment == cost.commitment for day in child.itinerary.days for expense in day.expenses)


def test_full_regeneration_keeps_old_paid_ledger_even_when_model_replaces_all_activities(tmp_path, monkeypatch):
    plan = sample_plan(2)
    paid = plan.itinerary.days[0].expenses[0]
    paid.label, paid.commitment, paid.unit_cost, paid.quantity = '合成已付且不可自动退款门票', 'paid', Decimal('37.50'), 2
    result, storage, original, _ = regenerate(tmp_path, monkeypatch,
        [point('entirely-new', start='10:00', end='11:00')], lambda *_: False, plan=plan)
    child = apply_ready(result, storage, original)
    carried = [expense for day in child.itinerary.days for expense in day.expenses if expense.label == paid.label]
    assert len(carried) == 1 and carried[0].commitment == 'paid'
    assert carried[0].unit_cost * carried[0].quantity == Decimal('75.00')


def test_real_arrival_flow_and_route_recovery_do_not_reinsert_the_unreachable_restaurant(tmp_path, monkeypatch):
    meal = point('flow-bad-meal', '合成不可达午餐', meal=True)
    good = point('flow-good-sight', start='15:00', end='16:00')
    result, storage, plan, _ = regenerate(tmp_path, monkeypatch, [meal, good],
        lambda _i, before, after: after.id == meal.id, real_flow=True)
    child = apply_ready(result, storage, plan)
    assert not any(stop.id == meal.id for day in child.itinerary.days for stop in day.stops)
    assert any(stop.id == good.id for day in child.itinerary.days for stop in day.stops)
    assert any('餐' in note for note in result['notices'])


def test_changsha_shanghai_hangzhou_return_regeneration_keeps_all_user_bookings(tmp_path, monkeypatch):
    """The reported multi-city journey, replayed against a deterministic fixture graph."""
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    plan = sample_plan(4)
    plan.request.origin, plan.request.destination = '长沙', '上海→杭州'
    plan.request.destinations = [DestinationStay(city='上海'), DestinationStay(city='杭州')]
    plan.request.include_origin_transport = plan.request.return_to_origin = True
    plan.request.budget = Decimal('10000')
    definitions = [
        ('长沙', '上海', 0, '09:00', '11:00', 'flight', '合成长沙机场', '合成上海机场'),
        ('上海', '杭州', 1, '15:00', '16:00', 'train', '上海虹桥站', '杭州东站'),
        ('杭州', '长沙', 3, '18:00', '20:00', 'train', '杭州东站', '合成长沙站'),
    ]
    tickets, transfers = [], []
    for index, (source, target, offset, start, end, mode, station_a, station_b) in enumerate(definitions):
        travel_date = plan.request.start_date + timedelta(days=offset)
        tickets.append(BookedTransport(transfer_index=index, from_city=source, to_city=target, mode=mode,
            departure_date=travel_date, departure_time=start, arrival_date=travel_date, arrival_time=end,
            departure_place=station_a, arrival_place=station_b, number=f'SYN-{index}', total_cost=100,
            arrival_buffer_min=0))
        transfers.append(CityTransfer(from_city=source, to_city=target, mode=mode,
            departure_date=travel_date, departure_time=start, arrival_date=travel_date, arrival_time=end,
            unit_cost=100, basis='group'))
    hotels = [ConfirmedHotel(city='上海', name='合成上海已填酒店', check_in='2026-11-02', check_out='2026-11-03', total_cost=100),
              ConfirmedHotel(city='杭州', name='合成杭州已填酒店', check_in='2026-11-03', check_out='2026-11-05', total_cost=200)]
    # The first version already knows only the first flight and Shanghai hotel.
    plan.request.booked_transports = [tickets[0].model_copy(deep=True)]
    plan.request.confirmed_hotels = [hotels[0].model_copy(deep=True)]
    plan.itinerary.transfers = [transfer.model_copy(deep=True) for transfer in transfers]
    for index, day in enumerate(plan.itinerary.days):
        for stop in day.stops:
            stop.city = '上海' if index == 0 else '杭州'
    store, plan, storage, state = setup_booking(tmp_path, plan)
    original = plan.model_dump_json()
    meal_id = 'hangzhou-final-lunch'
    amap = map_with_failures(lambda index, before, after:
        index == 3 and before.id == meal_id and after.booking_anchor == 'transport_departure')

    async def generator(request, *_args, **_kwargs):
        stops_by_day = [
            [point('shanghai-sight', city='上海', start='14:00', end='15:00')],
            [point('shanghai-morning', city='上海', start='09:00', end='10:00'),
             point('hangzhou-arrival-sight', city='杭州', start='17:00', end='18:00')],
            [point('hangzhou-full-day', city='杭州', start='10:00', end='11:00')],
            [point('hangzhou-final-sight', city='杭州', start='09:00', end='10:00'),
             point(meal_id, '楼外楼（孤山路店）午餐', city='杭州', meal=True)],
        ]
        return Itinerary(title='合成长沙上海杭州往返', summary='仅离线回归',
            transfers=[transfer.model_copy(deep=True) for transfer in transfers],
            days=[DayPlan(date=request.start_date + timedelta(days=index), title='合成测试日', stops=stops,
                expenses=[Expense(label=stop.name+'费用', category='food' if stop.kind == 'meal' else 'ticket',
                                  unit_cost=20, basis='group', stop_id=stop.id) for stop in stops])
                  for index, stops in enumerate(stops_by_day)])

    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, hotels, tickets), storage,
        amap_factory=amap, regenerate=True, generator=generator))
    assert plan.model_dump_json() == original and len(store.list()) == 1
    child = apply_ready(result, storage, plan)
    assert not any(stop.id == meal_id for day in child.itinerary.days for stop in day.stops)
    assert len(child.request.booked_transports) == 3 and len(child.request.confirmed_hotels) == 2
    assert [(transfer.departure_time, transfer.arrival_time, transfer.booking_status)
            for transfer in child.itinerary.transfers] == [
                ('09:00', '11:00', 'user_confirmed'), ('15:00', '16:00', 'user_confirmed'),
                ('18:00', '20:00', 'user_confirmed')]
    station = next(stop for stop in child.itinerary.days[3].stops if stop.booking_anchor == 'transport_departure')
    assert station.place_name == '杭州东站'
    assert any(index == 3 and before == 'hangzhou-final-sight' and after == station.id and available
               for index, before, after, available in amap.calls)


def test_recovery_reprices_new_adjacency_and_discards_old_unspent_transport_estimate(tmp_path, monkeypatch):
    """A removed restaurant's detour fare must not survive on the new direct edge."""
    base = map_with_failures(lambda _i, before, after:
        before.id == 'fare-meal' and after.booking_anchor == 'hotel_return')

    class FareMap(base):
        async def enrich(self, *args, **kwargs):
            routes = await super().enrich(*args, **kwargs)
            for route in routes:
                route.mode = 'transit'
                if route.available:
                    route.fare_yuan = Decimal('80') if route.to_id == 'fare-meal' else (
                        Decimal('7') if route.from_id == 'fare-good' else Decimal('2'))
            return routes

    plan = sample_plan(2)
    store, plan, storage, state = setup_booking(tmp_path, plan)
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    amap = FareMap
    hotel = ConfirmedHotel(city='北京', name='合成已填酒店', check_in='2026-11-01', check_out='2026-11-04', total_cost=100)

    async def generator(request, *_args, **_kwargs):
        answer = model_answer(request, [point('fare-good', start='09:00', end='10:00'),
            point('fare-meal', meal=True)])
        answer.days[0].expenses.append(Expense(label='旧模型未发生的交通估算', category='transport',
            unit_cost=999, basis='group', commitment='planned'))
        return answer

    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel]), storage,
        amap_factory=amap, regenerate=True, generator=generator))
    child = apply_ready(result, storage, plan)
    costs = [expense for expense in child.itinerary.days[0].expenses if expense.category == 'transport']
    assert sum(expense.unit_cost * expense.quantity * (child.request.people if expense.basis == 'person' else 1)
               for expense in costs) == Decimal('18')  # 2 + 7 per person, two people.
    assert not any(expense.label == '旧模型未发生的交通估算' for expense in costs)
    assert not any(route.from_id == 'fare-meal' or route.to_id == 'fare-meal' for route in child.routes)
    assert any(before == 'fare-good' and available for _i, before, _after, available in amap.calls)


@pytest.mark.parametrize('code', ['map_quota', 'map_rate_limit', 'map_auth'])
def test_provider_failure_does_not_shrink_or_save_itinerary(tmp_path, monkeypatch, code):
    """Quota/rate/auth failures are not evidence that optional activities are unreachable."""
    base = map_with_failures(lambda *_: False)

    class ProviderFailure(base):
        async def enrich(self, *args, **kwargs):
            routes = await super().enrich(*args, **kwargs)
            for route in routes:
                route.available, route.failure_code = False, code
                route.duration_s = route.distance_m = None
                route.polyline = []
            return routes

    plan = sample_plan(2)
    store, plan, storage, state = setup_booking(tmp_path, plan)
    original = plan.model_dump_json()
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    hotel = ConfirmedHotel(city='北京', name='合成已填酒店', check_in='2026-11-01', check_out='2026-11-04', total_cost=100)
    answer = model_answer(plan.request, [point('provider-sight', start='09:00', end='10:00'),
                                        point('provider-meal', meal=True)])

    async def generator(*_args, **_kwargs):
        return answer

    with pytest.raises(RuntimeError, match='地图服务暂不可用'):
        asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel]), storage,
            amap_factory=ProviderFailure, regenerate=True, generator=generator))
    assert {stop.id for stop in answer.days[0].stops} >= {'provider-sight', 'provider-meal'}
    assert plan.model_dump_json() == original and len(store.list()) == 1
    assert all(instance.closed for instance in ProviderFailure.instances)


def test_unlocated_user_hotel_is_retained_as_unknown_not_replaced_with_another_hotel(tmp_path, monkeypatch):
    base = map_with_failures(lambda *_: False)

    class UnlocatedHotel(base):
        async def enrich(self, itinerary, mode, **kwargs):
            routes = await super().enrich(itinerary, mode, **kwargs)
            unknown = set()
            for day in itinerary.days:
                for stop in day.stops:
                    if stop.booking_anchor.startswith('hotel_'):
                        unknown.add(stop.id)
                        stop.location, stop.location_status, stop.poi_id = None, 'unknown', ''
            for route in routes:
                if route.from_id in unknown or route.to_id in unknown:
                    route.available, route.failure_code = False, 'missing_location'
                    route.distance_m = route.duration_s = None
                    route.polyline = []
            return routes

    result, storage, plan, _ = regenerate(tmp_path, monkeypatch,
        [point('hotel-unknown-sight', start='10:00', end='11:00')], lambda *_: False, map_cls=UnlocatedHotel)
    child = apply_ready(result, storage, plan)
    anchors = [stop for day in child.itinerary.days for stop in day.stops if stop.booking_anchor.startswith('hotel_')]
    assert anchors and all(stop.name == '合成已填酒店' and stop.location is None for stop in anchors)
    assert any(error.code == 'missing_location' and error.stop_id in {stop.id for stop in anchors}
               for error in child.validation.errors)
    assert any('未唯一定位' in note or '仍未核实' in note for note in result['notices'])
    assert child.request.confirmed_hotels[0].name == '合成已填酒店'


def test_recovery_cannot_save_past_a_confirmed_window_after_removing_an_unreachable_meal(tmp_path, monkeypatch):
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    plan = sample_plan(2)
    # The existing 11:00 sight is outside the confirmed 09:00–10:00 window.
    # It must not become saveable merely because an unrelated bad meal is healed.
    bad = plan.itinerary.days[0].stops[0]
    bad.name, bad.kind, bad.meal_period = '合成不可达早餐', 'meal', 'breakfast'
    store, plan, storage, state = setup_booking(tmp_path, plan)
    state['day_windows'] = [{'day_index': 0, 'scope_city': '北京', 'start_min': 540, 'end_min': 600}]
    hotel = ConfirmedHotel(city='北京', name='合成已填酒店', check_in='2026-11-02', check_out='2026-11-04', total_cost=100)
    amap = map_with_failures(lambda _i, before, after: before.id == bad.id)
    original = plan.model_dump_json()
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel]), storage,
        amap_factory=amap))
    assert result['status'] == 'conflict' and 'preview_id' not in result
    assert any('每日活动时段' in error for error in result['conflicts'])
    assert plan.model_dump_json() == original and len(store.list()) == 1


def test_preexisting_error_for_a_stop_does_not_hide_a_new_hotel_to_stop_failure(tmp_path, monkeypatch):
    """The same destination id can acquire a different incoming edge after hotel entry."""
    monkeypatch.setattr(booking_routes, 'prepare_arrival_flow', lambda *_args, **_kwargs: [])
    plan = sample_plan(2)
    meal = plan.itinerary.days[0].stops[0]
    meal.name, meal.kind, meal.meal_period = '合成旧版未知早餐', 'meal', 'breakfast'
    plan.validation.errors = [Issue(code='missing_route', message='旧版邻接尚未核实', day_index=0, stop_id=meal.id)]
    store, plan, storage, state = setup_booking(tmp_path, plan)
    hotel = ConfirmedHotel(city='北京', name='合成新填酒店', check_in='2026-11-01', check_out='2026-11-04', total_cost=100)
    amap = map_with_failures(lambda index, before, after:
        index == 0 and before.booking_anchor == 'hotel_departure' and after.id == meal.id)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel]), storage, amap_factory=amap))
    child = apply_ready(result, storage, plan)
    assert not any(stop.id == meal.id for day in child.itinerary.days for stop in day.stops)
    assert all(route.available for route in child.routes if route.day_index == 0)
