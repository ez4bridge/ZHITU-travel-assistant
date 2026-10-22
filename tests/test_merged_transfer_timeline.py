"""Intercity endpoint integration with explicit synthetic POI/road evidence."""
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap
from backend.bookings import apply_booked_transports
from backend.journey import normalized_journey, validate_journey
from backend.models import (BookedTransport, CityTransfer, DayPlan, Expense, Itinerary,
                            Stop, TripRequest, itinerary_expenses)
from backend.planning import recost_all
from backend.transfer_timeline import prepare_transfer_anchors, station_query


def trip(**fields):
    return TripRequest(origin='长沙', destination='北京', include_origin_transport=True,
        start_date='2026-11-02', start_time='07:00', days=2, people=2, budget=5000, **fields)


def transfer(**fields):
    data = dict(from_city='长沙', to_city='北京', departure_date='2026-11-02',
        departure_time='09:00', arrival_date='2026-11-02', arrival_time='12:00',
        mode='train', unit_cost='100', departure_station='长沙南', arrival_station='北京西',
        schedule_status='queried', price_status='reference', train_code='G318',
        queried_at=datetime(2026, 10, 21, tzinfo=timezone.utc), source='合成铁路查询')
    data.update(fields)
    return CityTransfer(**data)


def itinerary(connection=None):
    return Itinerary(title='跨城接续测试', summary='', transfers=[connection or transfer()], days=[
        DayPlan(date='2026-11-02', title='到达', stops=[Stop(id='sight', name='合成景点',
            city='北京', start='14:00', end='15:00')], expenses=[]),
        DayPlan(date='2026-11-03', title='次日', stops=[], expenses=[]),
    ])


def test_queried_unbooked_rail_places_both_origin_and_arrival_in_daily_plan():
    request, plan = trip(), itinerary()
    plan.days[0].expenses = [Expense(label='原门票', category='ticket', unit_cost=30)]
    before = [e.model_dump() for e in itinerary_expenses(plan)]
    prepare_transfer_anchors(plan, request)
    departure, arrival, sight = plan.days[0].stops
    assert (departure.name, departure.city, departure.start) == ('长沙南站', '长沙', '08:00')
    assert (arrival.name, arrival.city, arrival.start, arrival.end) == ('北京西站', '北京', '12:00', '12:05')
    assert '不额外统一增加30分钟' in arrival.description
    assert departure.booking_anchor == 'transport_departure' and arrival.booking_anchor == 'transport_arrival'
    assert '尚未预订' in departure.description and plan.transfers[0].booking_status == 'unconfirmed'
    assert [e.model_dump() for e in itinerary_expenses(plan)] == before
    snapshot = plan.model_dump_json()
    prepare_transfer_anchors(plan, request)
    assert plan.model_dump_json() == snapshot


def test_only_pure_duplicate_station_events_are_removed():
    plan = itinerary()
    duplicates = [
        Stop(id='fake-depart', name='长沙南站出发（G318）', city='长沙', kind='break',
             start='09:00', end='09:05', location_behavior='unlocated'),
        Stop(id='fake-arrive', name='抵达北京西站', city='北京', kind='break',
             start='12:00', end='12:05', location_behavior='unlocated'),
        Stop(id='genuine', name='长沙南站候车厅休息并买水', city='长沙', kind='break',
             start='07:30', end='07:45', place_name='长沙南站候车厅'),
        Stop(id='other-day', name='长沙南站出发', city='长沙', kind='break', start='10:00', end='10:05'),
    ]
    plan.days[0].stops = duplicates[:3] + plan.days[0].stops
    plan.days[1].stops = duplicates[3:]
    prepare_transfer_anchors(plan, trip())
    ids = {s.id for d in plan.days for s in d.stops}
    assert 'fake-depart' not in ids and 'fake-arrive' not in ids
    assert {'genuine', 'other-day', 'sight'} <= ids


def test_wrong_city_unlocated_pure_departure_is_replaced_without_dropping_costs():
    plan = itinerary()
    duplicate = Stop(id='wrong-city-depart', name='长沙南站出发（G318）', city='北京市', kind='break',
        start='09:00', end='09:05', location_behavior='unlocated', location_status='unknown')
    plan.days[0].stops.insert(0, duplicate)
    plan.days[0].expenses = [Expense(label='原关联交通预算', category='transport', unit_cost=25, stop_id=duplicate.id)]
    expense_before = [e.model_dump() for e in itinerary_expenses(plan)]
    prepare_transfer_anchors(plan, trip())
    assert not any(s.id == duplicate.id for day in plan.days for s in day.stops)
    departure = next(s for s in plan.days[0].stops if s.booking_anchor == 'transport_departure')
    assert (departure.city, departure.name, departure.start) == ('长沙', '长沙南站', '08:00')
    assert [e.model_dump() for e in itinerary_expenses(plan)] == expense_before
    once = plan.model_dump_json()
    prepare_transfer_anchors(plan, trip())
    assert plan.model_dump_json() == once


def test_wrong_city_unknown_arrival_requires_exact_station_event_and_same_date():
    plan = itinerary()
    plan.days[0].stops = [
        Stop(id='wrong-arrival', name='抵达北京西站', city='长沙', kind='break', start='12:00', end='12:05'),
        Stop(id='different-station', name='抵达北京南站', city='长沙', kind='break', start='12:00', end='12:05'),
        Stop(id='non-event', name='长沙南站出发前买早餐', city='北京', kind='break', start='07:00', end='07:30'),
        *plan.days[0].stops,
    ]
    plan.days[1].stops = [Stop(id='wrong-date', name='长沙南站出发（G318）', city='北京', kind='break', start='09:00', end='09:05')]
    prepare_transfer_anchors(plan, trip())
    ids = {s.id for day in plan.days for s in day.stops}
    assert 'wrong-arrival' not in ids
    assert {'different-station', 'non-event', 'wrong-date'} <= ids


def test_verified_rest_and_dining_are_not_changed_by_station_deduplication():
    plan = itinerary()
    rest = Stop(id='verified-rest', name='长沙南站候车厅休息并买水', place_name='长沙南站候车厅',
        city='北京', kind='break', start='07:00', end='07:20', location=(113.0, 28.0), location_status='verified')
    meal = Stop(id='verified-meal', name='北京西站到达后午餐', place_name='已核实站内餐厅', city='北京',
        kind='meal', start='13:00', end='13:30', location=(116.3, 39.8), location_status='verified', location_behavior='unlocated')
    plan.days[0].stops = [rest, *plan.days[0].stops, meal]
    before = {s.id: s.model_dump() for s in (rest, meal)}
    prepare_transfer_anchors(plan, trip())
    retained = {s.id: s.model_dump() for s in plan.days[0].stops if s.id in before}
    assert retained == before


def test_estimated_flight_does_not_invent_airports_or_claim_verified_points():
    plan = itinerary(transfer(mode='flight', schedule_status='estimated',
        departure_station='', arrival_station='', source='', queried_at=None))
    prepare_transfer_anchors(plan, trip())
    assert [s.id for s in plan.days[0].stops] == ['sight']
    assert not any(s.location or s.booking_anchor for d in plan.days for s in d.stops)


def test_user_booked_airports_use_mode_buffers_and_explicit_provenance():
    request = trip()
    request.booked_transports = [BookedTransport(from_city='长沙', to_city='北京', mode='flight',
        departure_date='2026-11-02', departure_time='10:00', arrival_date='2026-11-02', arrival_time='12:00',
        departure_place='长沙黄花国际机场T2航站楼', arrival_place='北京大兴国际机场', total_cost=1000)]
    plan = itinerary()
    apply_booked_transports(plan, request)
    prepare_transfer_anchors(plan, request)
    depart, arrive, _ = plan.days[0].stops
    assert depart.name == '长沙黄花国际机场T2航站楼' and depart.start == '08:00'
    assert arrive.name == '北京大兴国际机场' and arrive.start == '13:00'
    assert depart.booking_ref == arrive.booking_ref == 'transport-0'
    assert '用户填写' in depart.description and '尚未预订' not in depart.description


def test_explicit_zero_arrival_buffer_is_preserved_in_sorting_and_timestamp():
    request = trip()
    request.booked_transports = [BookedTransport(from_city='长沙', to_city='北京',
        departure_date='2026-11-02', departure_time='09:00', arrival_date='2026-11-02', arrival_time='12:00',
        departure_place='长沙南站', arrival_place='北京西站', arrival_buffer_min=0, departure_buffer_min=0)]
    plan = itinerary()
    plan.days[0].stops[0].start, plan.days[0].stops[0].end = '12:01', '13:00'
    apply_booked_transports(plan, request)
    prepare_transfer_anchors(plan, request)
    departure, arrival, sight = plan.days[0].stops
    assert departure.start == '09:00' and arrival.start == '12:00' and sight.start == '12:01'


def test_overnight_ride_and_buffer_crossing_midnight_use_actual_calendar_day():
    plan = itinerary(transfer(departure_time='23:00', arrival_date='2026-11-03', arrival_time='06:00'))
    plan.days[0].stops = []
    plan.days[1].stops = [Stop(id='next', name='次日景点', city='北京', start='06:00', end='07:00')]
    prepare_transfer_anchors(plan, trip())
    assert plan.days[0].stops[0].start == '22:00'
    assert plan.days[1].stops[0].booking_anchor == 'transport_arrival'
    assert plan.days[1].stops[0].start == '06:00'
    plan = itinerary(transfer(mode='flight', departure_time='21:00', arrival_time='23:50'))
    plan.days[0].stops = []
    prepare_transfer_anchors(plan, trip())
    assert [s.booking_anchor for s in plan.days[0].stops] == ['transport_departure']
    assert plan.days[1].stops[0].booking_anchor == 'transport_arrival'
    assert plan.days[1].stops[0].start == '00:50'


def test_late_anchor_cannot_be_clamped_to_false_earlier_time():
    plan = itinerary(transfer(departure_time='20:00', arrival_time='23:55'))
    plan.days[0].stops = []
    with pytest.raises(ValueError, match='不足5分钟'):
        prepare_transfer_anchors(plan, trip())


def test_out_of_horizon_buffer_is_not_silently_wrapped_to_travel_day():
    plan = itinerary(transfer(mode='flight', departure_date='2026-11-03', departure_time='21:00',
        arrival_date='2026-11-03', arrival_time='23:50'))
    prepare_transfer_anchors(plan, trip())
    assert not any(s.booking_anchor == 'transport_arrival' for d in plan.days for s in d.stops)
    assert '范围外接驳未核实' in plan.transfers[0].query_note


@pytest.mark.asyncio
async def test_local_connectors_use_verified_station_pois_and_never_route_between_cities():
    request = trip()
    plan = itinerary()
    plan.days[0].stops.insert(0, Stop(id='origin', name='合成长沙景点', city='长沙', start='07:00', end='07:15'))
    prepare_transfer_anchors(plan, request)
    service = Amap(request.journey_cities)
    calls = []
    async def search(name, city=''):
        calls.append((name, city))
        return [{'id': f'{city}:{name}', 'name': name, 'city': city,
            'address': '合成地图测试地址', 'type': '交通设施服务;火车站' if '站' in name else '风景名胜',
            'location': [113.0, 28.0] if city == '长沙' else [116.0 + len(name) * .001, 39.9]}]
    service.search = search
    service.route = AsyncMock(return_value={'distance_m': 1000, 'duration_s': 600,
        'polyline': [(116.0, 39.9), (116.01, 39.91)], 'taxi_cost_yuan': Decimal('20')})
    try:
        routes = await service.enrich(plan, 'taxi', compare_modes=False)
        assert all(s.location_status == 'verified' for s in plan.days[0].stops)
        assert ('长沙南站', '长沙') in calls and ('北京西站', '北京') in calls
        assert len([r for r in routes if r.scope == 'intercity']) == 1
        assert service.route.await_count == 2  # sight→origin station, arrival station→sight only.
        assert all(r.available for r in routes if r.scope == 'local')
        recost_all(request, plan, routes)
        total = sum(e.unit_cost * e.quantity * (request.people if e.basis == 'person' else 1)
                    for e in itinerary_expenses(plan))
        assert total == Decimal('240')  # 2×100 intercity + 2×20 local, each once.
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_station_name_ambiguity_never_creates_fake_coordinates():
    service = Amap('北京')
    service.search = AsyncMock(return_value=[])
    stop = Stop(name='不存在的北京车站', city='北京', kind='break', start='10:00', end='10:05',
        booking_anchor='transport_arrival', location=(1, 2), location_status='verified')
    try:
        await service.resolve(stop)
        assert stop.location is None and stop.location_status == 'unknown'
        service.search.return_value = [
            {'id': 'train', 'name': '北京西站', 'location': [116.3, 39.8], 'address': '', 'type': '交通设施服务;火车站'},
            {'id': 'metro', 'name': '北京西站', 'location': [116.31, 39.81], 'address': '', 'type': '交通设施服务;地铁站'},
        ]
        stop.name = '北京西站'
        await service.resolve(stop)
        assert stop.poi_id == 'train' and stop.location == (116.3, 39.8)
    finally:
        await service.close()


def test_official_rail_short_names_and_explicit_entrances_have_bounded_normalization():
    assert station_query('北京西', 'train') == '北京西站'
    assert station_query('北京西站北广场', 'train') == '北京西站北广场'
    assert station_query('北京大兴国际机场', 'flight') == '北京大兴国际机场'


def test_origin_station_is_not_a_tourism_destination_but_remains_time_validated():
    request, plan = trip(), itinerary()
    prepare_transfer_anchors(plan, request)
    data = normalized_journey(request, plan)
    assert any(s['city'] == '长沙' for s in data['stops'])
    errors, _, _ = validate_journey(data)
    assert not any(e['code'] in ('city_sequence', 'city_arrival', 'intercity_activity_overlap') for e in errors)
    plan.days[0].stops[0].start, plan.days[0].stops[0].end = '10:00', '10:05'
    errors, _, _ = validate_journey(normalized_journey(request, plan))
    assert any(e['code'] == 'intercity_activity_overlap' for e in errors)
