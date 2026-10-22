"""Offline user-booking boundaries; synthetic roads are never live-map proof."""
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend.booking_routes import BookingPreviewRequest, preview_bookings
from backend.bookings import apply_hotel_anchors, apply_transport_anchors, validate_bookings
from backend.constraint_storage import ConstraintStore, ConstraintStale, fingerprint
from backend.models import ConfirmedHotel, BookedTransport, RouteLeg, RouteOption, PlanRecord, Expense, Stop, CityTransfer, TripRequest
from backend.models import ValidationReport
from backend.validation import validate_core, normalized_input
from backend.storage import Store
from test_constraint_replan import sample_plan
from test_constraint_city_scope import mixed_plan


class SyntheticBookingMap:
    """Resolve only new booking points and return explicit offline road shapes."""
    instances = []
    available = True
    locate = True

    def __init__(self, cities):
        self.closed = False
        self.__class__.instances.append(self)

    async def enrich(self, itinerary, mode, *, compare_modes, day_indices, optimize_meals, people):
        routes = []
        for index in day_indices:
            day = itinerary.days[index]
            for number, stop in enumerate(day.stops):
                if stop.booking_anchor and self.locate:
                    stop.location = (116.3 + number * .001, 39.9)
                    stop.location_status = 'verified'
                    stop.poi_id = stop.id
            for before, after in zip(day.stops, day.stops[1:]):
                same_city = before.city.rstrip('市') == after.city.rstrip('市')
                available = self.available and same_city and before.location is not None and after.location is not None
                routes.append(RouteLeg(day_index=index, from_id=before.id, to_id=after.id, mode='walking',
                    available=available, scope='local' if same_city else 'intercity', distance_m=500 if available else None,
                    duration_s=600 if available else None, polyline=[before.location, after.location] if available else [],
                    queried_at=datetime.now(timezone.utc), note='合成离线测试道路，不代表真实地图'))
        return routes

    async def close(self):
        self.closed = True


def setup_booking(tmp_path, plan=None):
    plan = plan or sample_plan(3)
    store = Store(tmp_path / 'bookings.sqlite3')
    store.save(plan)
    storage = ConstraintStore(store.path)
    return store, plan, storage, storage.state(plan)


def booking_body(plan, state, hotels=(), transports=()):
    return BookingPreviewRequest(base_fingerprint=fingerprint(plan), expected_routing_revision=plan.routing_revision,
        expected_state_revision=state['state_revision'], confirmed_hotels=list(hotels), booked_transports=list(transports))


def hotel(name='合成酒店甲', start='2026-11-02', end='2026-11-05', total='300'):
    return ConfirmedHotel(city='北京', name=name, check_in=start, check_out=end, total_cost=total)


def train(**changes):
    return BookedTransport(from_city='北京', to_city='天津', departure_date='2026-11-02', departure_time='12:00',
        arrival_date='2026-11-02', arrival_time='13:00', departure_place='合成北京站', arrival_place='合成天津站',
        number='G-TEST', total_cost='100', departure_buffer_min=30, arrival_buffer_min=15, **changes)


def test_hotel_checkin_checkout_nights_and_switch_do_not_invent_first_morning():
    plan = sample_plan(4)
    plan.request.confirmed_hotels = [hotel(end='2026-11-04'), hotel('合成酒店乙', '2026-11-04', '2026-11-06', '500')]
    apply_hotel_anchors(plan.itinerary, plan.request)
    days = plan.itinerary.days
    assert not any(s.booking_anchor == 'hotel_departure' for s in days[0].stops)
    assert next(s for s in days[1].stops if s.booking_anchor == 'hotel_departure').name == '合成酒店甲'
    assert next(s for s in days[2].stops if s.booking_anchor == 'hotel_departure').name == '合成酒店甲'
    assert next(s for s in days[2].stops if s.booking_anchor == 'hotel_return').name == '合成酒店乙'
    assert next(s for s in days[3].stops if s.booking_anchor == 'hotel_departure').name == '合成酒店乙'
    costs = [e for d in days for e in d.expenses if e.category == 'hotel']
    assert len(costs) == 2 and {e.basis for e in costs} == {'group'}
    assert sum(e.unit_cost * e.quantity for e in costs) == Decimal('800')
    apply_hotel_anchors(plan.itinerary, plan.request)
    assert len([e for d in days for e in d.expenses if e.category == 'hotel']) == 2


def test_overlapping_hotel_nights_reject_but_checkout_switch_is_allowed():
    plan = sample_plan(3)
    plan.request.confirmed_hotels = [hotel(end='2026-11-04'), hotel('重叠酒店', '2026-11-03', '2026-11-05')]
    with pytest.raises(ValueError, match='同一晚'):
        validate_bookings(plan.request)
    plan.request.confirmed_hotels[1].check_in = date(2026, 11, 4)
    validate_bookings(plan.request)


def test_preview_apply_preserves_parent_is_idempotent_and_costs_once(tmp_path):
    store, plan, storage, state = setup_booking(tmp_path)
    old = plan.model_dump(mode='json')
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel()]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'ready', result['conflicts']
    assert len(store.list()) == 1 and plan.model_dump(mode='json') == old
    child = storage.apply(plan.id, result['preview_id'])
    assert child.parent_id == plan.id and child.id != plan.id
    assert store.get(plan.id).model_dump(mode='json') == old
    assert storage.apply(plan.id, result['preview_id']).id == child.id
    costs = [e for d in child.itinerary.days for e in d.expenses if e.category == 'hotel']
    assert len(costs) == 1 and costs[0].unit_cost == Decimal('300') and costs[0].basis == 'group'
    assert child.itinerary.days[1].stops[0].booking_anchor == 'hotel_departure'
    assert SyntheticBookingMap.instances[-1].closed


def test_ticket_buffers_and_user_provenance_without_order_verification(tmp_path):
    store, plan, storage, state = setup_booking(tmp_path, mixed_plan())
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, transports=[train()]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'ready', result['conflicts']
    child = storage.apply(plan.id, result['preview_id'])
    transfer = child.itinerary.transfers[0]
    assert (transfer.departure_time, transfer.arrival_time) == ('12:00', '13:00')
    assert transfer.unit_cost == Decimal('100') and transfer.basis == 'group'
    assert transfer.booking_status == 'user_confirmed' and transfer.price_status == 'user_reported'
    assert '以跨城卡片、每日时间轴和预算明细为准' in child.itinerary.summary
    assert transfer.schedule_status != 'queried' and transfer.queried_at is None
    day = child.itinerary.days[0]
    departure = next(s for s in day.stops if s.booking_anchor == 'transport_departure')
    arrival = next(s for s in day.stops if s.booking_anchor == 'transport_arrival')
    assert departure.start == '11:30' and arrival.start == '13:15'
    assert next(s for s in day.stops if s.id == 's0-1').start >= '13:30'
    assert child.itinerary.days[1] == plan.itinerary.days[1]


def test_bookings_cannot_move_confirmed_completed_activity(tmp_path):
    store, plan, storage, state = setup_booking(tmp_path)
    sid = plan.itinerary.days[1].stops[0].id
    state['activities'][sid] = {'status': 'completed', 'locked': True}
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel()]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'conflict' and 'preview_id' not in result
    assert any('已' in message and ('确认' in message or '完成' in message) for message in result['conflicts'])
    assert len(store.list()) == 1


@pytest.mark.parametrize('failure', ['unknown_place', 'missing_road'])
def test_no_fake_success_when_booking_place_or_road_unknown(tmp_path, failure):
    class UnresolvedMap(SyntheticBookingMap):
        locate = failure != 'unknown_place'
        available = False
    store, plan, storage, state = setup_booking(tmp_path)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel()]), storage, amap_factory=UnresolvedMap))
    assert result['status'] == 'conflict' and 'preview_id' not in result
    assert result['conflicts']
    assert len(store.list()) == 1


def test_preview_stale_routing_revision_never_queries_or_writes(tmp_path):
    store, plan, storage, state = setup_booking(tmp_path)
    body = booking_body(plan, state, [hotel()])
    plan.routing_revision += 1
    before = len(SyntheticBookingMap.instances)
    with pytest.raises(ConstraintStale):
        asyncio.run(preview_bookings(plan, state, body, storage, amap_factory=SyntheticBookingMap))
    assert len(SyntheticBookingMap.instances) == before and len(store.list()) == 1


def test_first_hotel_morning_preserves_existing_late_departure_clock():
    plan = sample_plan(3)
    plan.itinerary.days[1].stops[0].start, plan.itinerary.days[1].stops[0].end = '11:30', '12:30'
    plan.itinerary.days[1].stops[1].start, plan.itinerary.days[1].stops[1].end = '13:30', '14:30'
    plan.request.confirmed_hotels = [hotel()]
    apply_hotel_anchors(plan.itinerary, plan.request)
    assert plan.itinerary.days[1].stops[0].start == '11:30'


def test_hotel_edit_with_stable_booking_id_replaces_cost_once():
    plan = sample_plan(4)
    booking = hotel(end='2026-11-05')
    booking.booking_id = 'stable-hotel-1'
    plan.request.confirmed_hotels = [booking]
    apply_hotel_anchors(plan.itinerary, plan.request)
    booking.check_in, booking.check_out, booking.total_cost = date(2026, 11, 3), date(2026, 11, 5), Decimal('450')
    apply_hotel_anchors(plan.itinerary, plan.request)
    costs = [e for d in plan.itinerary.days for e in d.expenses if e.category == 'hotel']
    assert len(costs) == 1 and costs[0].unit_cost == Decimal('450') and costs[0].booking_ref == 'stable-hotel-1'


def test_ticket_removal_cannot_silently_clear_existing_booking_or_refund(tmp_path):
    store, plan, storage, state = setup_booking(tmp_path, mixed_plan())
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, transports=[train()]), storage, amap_factory=SyntheticBookingMap))
    child = storage.apply(plan.id, result['preview_id'])
    updated = storage.state(child)
    with pytest.raises(ValueError, match='删除|撤销'):
        asyncio.run(preview_bookings(child, updated, booking_body(child, updated), storage, amap_factory=SyntheticBookingMap))
    assert len(store.list()) == 2


def test_unaffected_day_with_existing_issue_is_not_reflowed(tmp_path):
    plan = sample_plan(3)
    plan.itinerary.days[2].stops[1].start, plan.itinerary.days[2].stops[1].end = '10:00', '11:00'
    plan.validation = ValidationReport(**validate_core(normalized_input(plan.request, plan.itinerary, plan.routes)), backend='python')
    store, plan, storage, state = setup_booking(tmp_path, plan)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel(end='2026-11-03')]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'ready', result['conflicts']
    candidate = PlanRecord.model_validate(result['candidate'])
    assert candidate.itinerary.days[2] == plan.itinerary.days[2]
    assert [r for r in candidate.routes if r.day_index == 2] == [r for r in plan.routes if r.day_index == 2]
    assert any(e.code == 'transfer_time' and e.day_index == 2 for e in candidate.validation.errors)


def test_all_same_day_city_windows_remain_effective_in_booking_preview(tmp_path):
    store, plan, storage, state = setup_booking(tmp_path, mixed_plan())
    # First window would be exceeded by the new Beijing station check-in at11:30;
    # the Tianjin window must not overwrite it simply because both use day0.
    state['day_windows'] = [
        {'day_index': 0, 'scope_city': '北京', 'start_min': 540, 'end_min': 660},
        {'day_index': 0, 'scope_city': '天津', 'start_min': 720, 'end_min': 1200},
    ]
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, transports=[train()]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'conflict' and any('时段' in message for message in result['conflicts'])


def test_known_hotel_total_does_not_erase_other_city_lodging_on_mixed_day():
    plan = mixed_plan()
    plan.itinerary.days[0].expenses.append(Expense(label='天津原住宿估算', category='hotel', unit_cost=80, basis='group'))
    plan.request.confirmed_hotels = [hotel(end='2026-11-03')]
    apply_hotel_anchors(plan.itinerary, plan.request)
    costs = [e for d in plan.itinerary.days for e in d.expenses if e.category == 'hotel']
    assert any(e.label == '天津原住宿估算' and e.unit_cost == Decimal('80') for e in costs)
    assert sum(e.unit_cost for e in costs) == Decimal('380')


def test_previous_night_checkout_first_day_cost_and_departure_are_included():
    plan = sample_plan(2)
    plan.request.confirmed_hotels = [hotel(start='2026-11-01', end='2026-11-02')]
    validate_bookings(plan.request)
    apply_hotel_anchors(plan.itinerary, plan.request)
    first = plan.itinerary.days[0]
    assert first.stops[0].booking_anchor == 'hotel_departure'
    assert not any(s.booking_anchor == 'hotel_return' for d in plan.itinerary.days for s in d.stops)
    costs = [e for d in plan.itinerary.days for e in d.expenses if e.category == 'hotel']
    assert len(costs) == 1 and costs[0].unit_cost == Decimal('300') and costs[0].basis == 'group'


def test_legacy_confirmed_hotel_converts_without_duplicate_anchors(tmp_path):
    plan = sample_plan(2)
    plan.request.confirmed_hotel = '合成酒店甲'
    plan.itinerary.days[0].stops.append(Stop(id='legacy-hotel-back', name='合成酒店甲', place_name='合成酒店甲',
        city='北京', kind='hotel', start='12:00', end='12:05', location=(116.35, 39.9), location_status='verified'))
    plan.itinerary.days[1].stops.insert(0, Stop(id='legacy-hotel-out', name='合成酒店甲', place_name='合成酒店甲',
        city='北京', kind='hotel', start='09:00', end='09:05', location=(116.35, 39.9), location_status='verified'))
    store, plan, storage, state = setup_booking(tmp_path, plan)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel(end='2026-11-04')]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'ready', result['conflicts']
    child = storage.apply(plan.id, result['preview_id'])
    assert len([s for s in child.itinerary.days[0].stops if s.kind == 'hotel']) == 1
    assert len([s for s in child.itinerary.days[1].stops if s.kind == 'hotel']) == 2
    assert not any(s.id.startswith('legacy-hotel-') for d in child.itinerary.days for s in d.stops)


def test_empty_arrival_day_connects_station_to_confirmed_hotel(tmp_path):
    plan = sample_plan(2)
    plan.request = TripRequest(origin='北京', destination='天津', include_origin_transport=True,
        start_date='2026-11-02', start_time='09:00', days=2, people=2, budget=1000, mode='walking')
    plan.itinerary.days[0].stops = []
    plan.itinerary.days[0].expenses = []
    for stop in plan.itinerary.days[1].stops:
        stop.city = '天津'
    plan.itinerary.transfers = [CityTransfer(from_city='北京', to_city='天津', departure_date='2026-11-02',
        arrival_date='2026-11-02', departure_time='12:00', arrival_time='13:00', mode='train', unit_cost=100, basis='group')]
    plan.routes = [r for r in plan.routes if r.day_index != 0]
    store, plan, storage, state = setup_booking(tmp_path, plan)
    lodging = ConfirmedHotel(city='天津', name='合成天津酒店', check_in='2026-11-02', check_out='2026-11-04', total_cost=300)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [lodging], [train()]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'ready', result['conflicts']
    child = storage.apply(plan.id, result['preview_id'])
    day = child.itinerary.days[0]
    assert [s.booking_anchor for s in day.stops] == ['transport_departure', 'transport_arrival', 'hotel_return']
    departure, arrival, destination = day.stops
    assert departure.city == '北京'
    assert arrival.start == '13:15' and destination.start >= '13:30'
    assert any(r.from_id == arrival.id and r.to_id == destination.id and r.available for r in child.routes)


def test_early_booked_flight_derives_departure_start_in_confirmable_preview(tmp_path):
    plan = sample_plan(2)
    plan.request.origin, plan.request.include_origin_transport = '长沙', True
    plan.itinerary.transfers = [CityTransfer(from_city='长沙', to_city='北京', departure_date='2026-11-02',
        arrival_date='2026-11-02', departure_time='12:00', arrival_time='14:00', mode='flight', unit_cost=100, basis='group')]
    booking = BookedTransport(from_city='长沙', to_city='北京', mode='flight', departure_date='2026-11-02',
        departure_time='07:00', arrival_date='2026-11-02', arrival_time='09:00', departure_place='合成长沙机场',
        arrival_place='合成北京机场', total_cost=100, departure_buffer_min=120, arrival_buffer_min=60)
    store, plan, storage, state = setup_booking(tmp_path, plan)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, transports=[booking]), storage, amap_factory=SyntheticBookingMap))
    assert result['status'] == 'ready', result['conflicts']
    child = storage.apply(plan.id, result['preview_id'])
    assert child.request.start_time == '05:00' and plan.request.start_time == '09:00'
    assert child.itinerary.transfers[0].departure_time == '07:00'
    assert next(s for s in child.itinerary.days[0].stops if s.booking_anchor == 'transport_arrival').start == '10:00'
    assert child.itinerary.days[1] == plan.itinerary.days[1]


def test_booking_preview_retains_user_selected_taxi_on_unchanged_edge(tmp_path):
    plan = sample_plan(3)
    route = plan.routes[1]
    route.mode, route.user_selected, route.taxi_cost_yuan = 'taxi', True, Decimal('20')
    route.selection_reason = '用户明确选择打车'
    class SyntheticChoiceMap(SyntheticBookingMap):
        async def enrich(self, *args, **kwargs):
            routes = await super().enrich(*args, **kwargs)
            for candidate in routes:
                if (candidate.day_index, candidate.from_id, candidate.to_id) == (route.day_index, route.from_id, route.to_id):
                    candidate.alternatives = [RouteOption(mode='taxi', available=True, distance_m=600, duration_s=300,
                        polyline=candidate.polyline, taxi_cost_yuan=20, note='合成新查询打车候选')]
            return routes
    store, plan, storage, state = setup_booking(tmp_path, plan)
    result = asyncio.run(preview_bookings(plan, state, booking_body(plan, state, [hotel()]), storage, amap_factory=SyntheticChoiceMap))
    assert result['status'] == 'ready', result['conflicts']
    child = storage.apply(plan.id, result['preview_id'])
    chosen = next(r for r in child.routes if (r.day_index, r.from_id, r.to_id) == (route.day_index, route.from_id, route.to_id))
    assert chosen.mode == 'taxi' and chosen.user_selected
    assert chosen.taxi_cost_yuan == Decimal('20')


def test_booking_http_registration_preview_apply_and_invalid_apply_body(tmp_path, monkeypatch):
    from backend import main, booking_routes
    store, plan, storage, state = setup_booking(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    original = booking_routes.preview_bookings
    async def synthetic_preview(*args, **kwargs):
        return await original(*args, **kwargs, amap_factory=SyntheticBookingMap)
    monkeypatch.setattr(booking_routes, 'preview_bookings', synthetic_preview)
    with TestClient(main.app) as client:
        context = client.get(f'/api/plans/{plan.id}/booking-context')
        assert context.status_code == 200 and context.json()['current_plan_id'] == plan.id
        body = booking_body(plan, state, [hotel()]).model_dump(mode='json')
        response = client.post(f'/api/plans/{plan.id}/booking-preview', json=body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['status'] == 'ready' and data['preview_id']
        assert len(store.list()) == 1
        for bad_id in ([], {}, 9, None, ''):
            malformed = client.post(f'/api/plans/{plan.id}/booking-apply', json={'preview_id': bad_id})
            assert malformed.status_code == 422, malformed.text
        saved = client.post(f'/api/plans/{plan.id}/booking-apply', json={'preview_id': data['preview_id']})
        assert saved.status_code == 200 and saved.json()['plan']['parent_id'] == plan.id
        assert len(store.list()) == 2
        assert client.get('/api/health').json()['tasks']['booking_active'] == 0


def test_booking_http_busy_rejects_without_writes_and_health_includes_it(tmp_path, monkeypatch):
    from backend import main, booking_routes
    store, plan, storage, state = setup_booking(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(booking_routes, '_active_previews', 2)
    with TestClient(main.app) as client:
        body = booking_body(plan, state, [hotel()]).model_dump(mode='json')
        response = client.post(f'/api/plans/{plan.id}/booking-preview', json=body)
        assert response.status_code == 429 and len(store.list()) == 1
        health = client.get('/api/health').json()
        assert health['tasks']['booking_active'] == 2 and health['tasks']['active'] >= 2


@pytest.mark.parametrize('failure,status', [(ValueError('补录输入冲突'), 422), (RuntimeError('合成服务不可用'), 503)])
def test_booking_http_failed_preview_releases_health_slot(tmp_path, monkeypatch, failure, status):
    from backend import main, booking_routes
    store, plan, storage, state = setup_booking(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    async def failed_preview(*args, **kwargs):
        raise failure
    monkeypatch.setattr(booking_routes, 'preview_bookings', failed_preview)
    with TestClient(main.app) as client:
        body = booking_body(plan, state, [hotel()]).model_dump(mode='json')
        response = client.post(f'/api/plans/{plan.id}/booking-preview', json=body)
        assert response.status_code == status
        assert client.get('/api/health').json()['tasks']['booking_active'] == 0
        assert len(store.list()) == 1


def test_booking_disconnection_cancels_work_and_releases_slot(tmp_path, monkeypatch):
    from backend import main, booking_routes
    store, plan, storage, state = setup_booking(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    cancelled = []
    async def never_complete(*args, **kwargs):
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.append(True)
    class Disconnected:
        async def is_disconnected(self):
            return True
    monkeypatch.setattr(booking_routes, 'preview_bookings', never_complete)
    route = next(r for r in main.app.routes if getattr(r, 'path', '') == '/api/plans/{plan_id}/booking-preview')
    async def run():
        with pytest.raises(HTTPException) as error:
            await route.endpoint(plan.id, booking_body(plan, state, [hotel()]), Disconnected())
        assert error.value.status_code == 499
    asyncio.run(run())
    assert cancelled == [True] and booking_routes.active_booking_previews() == 0
    assert len(store.list()) == 1
