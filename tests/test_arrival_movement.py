"""Compound arrival labels use explicit synthetic POIs, never fabricated bookings."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap
from backend.locality import explicit_arrival_station, parse_arrival_movement
from backend.bookings import apply_booked_transports
from backend.models import BookedTransport, CityTransfer, DayPlan, Expense, Itinerary, Stop, TripRequest
from backend.transfer_timeline import prepare_transfer_anchors, split_arrival_movements


def movement(**fields):
    data = dict(id='original-arrival', city='上海', kind='break', start='13:21', end='14:20',
        name='抵达上海虹桥站，地铁前往人民广场一带', location_behavior='unlocated',
        description='出站后乘地铁2号线前往人民广场方向，先到酒店寄存行李。')
    data.update(fields)
    return Stop(**data)


def plan(queried=False):
    request = TripRequest(origin='长沙', destination='上海', include_origin_transport=True,
        start_date='2026-10-28', start_time='08:00', days=1)
    transfer = CityTransfer(from_city='长沙', to_city='上海', departure_date=request.start_date,
        departure_time='09:20', arrival_date=request.start_date, arrival_time='13:21', mode='train', unit_cost=500)
    if queried:
        transfer.schedule_status = 'queried'
        transfer.departure_station, transfer.arrival_station = '长沙南', '上海虹桥'
        transfer.train_code = 'SYNTHETIC'
        transfer.queried_at = datetime.now(timezone.utc)
    return request, Itinerary(title='合成抵达接驳', summary='', transfers=[transfer], days=[
        DayPlan(date=request.start_date, title='到达', stops=[movement()],
            expenses=[Expense(label='原活动费用', category='other', unit_cost=10, stop_id='original-arrival')])])


def test_parser_exposes_explicit_endpoints_and_luggage_without_a_hotel():
    assert parse_arrival_movement(movement()) == {'station': '上海虹桥站',
        'destination': '人民广场一带', 'has_luggage_instruction': True}
    result = parse_arrival_movement(movement(name='抵达上海虹桥机场，乘地铁2号线前往人民广场', description=''))
    assert result == {'station': '上海虹桥机场', 'destination': '人民广场', 'has_luggage_instruction': False}


@pytest.mark.parametrize('fields', [
    {'name': '抵达火车站，地铁前往人民广场'},
    {'name': '抵达当地高铁站，地铁前往人民广场'},
    {'name': '抵达上海虹桥站，地铁前往酒店寄存行李'},
    {'name': '抵达上海虹桥站，地铁前往住处'},
    {'name': '抵达上海虹桥站，地铁前往人民广场并参观博物馆'},
    {'name': '抵达上海虹桥站，地铁前往人民广场/南京东路'},
    {'kind': 'meal'}, {'kind': 'sight'}, {'location_status': 'verified'},
    {'booking_anchor': 'transport_arrival'}, {'place_name': '已明确的其他实际地点'},
    {'location_behavior': 'same_as_previous'},
])
def test_parser_does_not_absorb_real_activities_or_guess_multiple_endpoints(fields):
    assert parse_arrival_movement(movement(**fields)) is None


def test_estimated_timetable_gets_search_candidates_without_fake_timetable_or_hotel():
    request, itinerary = plan()
    expenses = [e.model_dump() for e in itinerary.days[0].expenses]
    transfer = itinerary.transfers[0].model_dump()
    prepare_transfer_anchors(itinerary, request)
    station, destination = itinerary.days[0].stops
    assert (station.name, station.start, station.end) == ('上海虹桥站', '13:21', '13:26')
    assert not station.booking_anchor and station.location is None
    assert '估算' in station.description and '不代表车票' in station.description
    assert destination.id == 'original-arrival' and destination.name == '人民广场一带'
    assert destination.place_name == destination.area_name == '人民广场'
    assert (destination.start, destination.end) == ('14:15', '14:20')
    assert '酒店未确认' in destination.description and '先到酒店寄存行李' in destination.description
    assert not any(s.kind == 'hotel' for s in itinerary.days[0].stops)
    assert itinerary.transfers[0].model_dump() == transfer
    assert [e.model_dump() for e in itinerary.days[0].expenses] == expenses
    snapshot = itinerary.model_dump_json()
    prepare_transfer_anchors(itinerary, request)
    assert itinerary.model_dump_json() == snapshot


def test_known_arrival_reuses_one_real_station_anchor_and_keeps_actual_other_activities():
    request, itinerary = plan(queried=True)
    meal = Stop(id='meal', name='车站附近真实餐馆午餐', place_name='具体餐馆', kind='meal', city='上海',
        start='14:30', end='15:00', location_behavior='poi')
    sight = Stop(id='sight', name='人民广场参观', kind='sight', city='上海', start='15:30', end='16:00')
    itinerary.days[0].stops += [meal, sight]
    prepare_transfer_anchors(itinerary, request)
    points = [s for s in itinerary.days[0].stops if s.name == '上海虹桥站']
    assert len(points) == 1 and points[0].booking_anchor == 'transport_arrival'
    assert points[0].start == '13:21' and points[0].end == '13:26'
    assert meal in itinerary.days[0].stops and sight in itinerary.days[0].stops
    snapshot = itinerary.model_dump_json()
    prepare_transfer_anchors(itinerary, request)
    assert itinerary.model_dump_json() == snapshot


def test_conflicting_actual_arrival_station_never_silently_rewrites_a_real_station():
    request, itinerary = plan(queried=True)
    itinerary.days[0].stops[0].name = '抵达上海站，地铁前往人民广场一带'
    with pytest.raises(ValueError, match='到达站不一致'):
        prepare_transfer_anchors(itinerary, request)


def _new_user_station(request):
    return request.model_copy(update={'booked_transports': [BookedTransport(
        transfer_index=0, from_city='长沙', to_city='上海', departure_date=request.start_date,
        departure_time='09:20', arrival_date=request.start_date, arrival_time='13:21',
        departure_place='长沙南站', arrival_place='上海站', number='USER-TRAIN', total_cost=800)]}, deep=True)


def test_booking_supplement_rebuilds_stale_compound_station_from_user_without_mutating_snapshot():
    request, original = plan(queried=True)
    before = original.model_dump_json()
    expenses = [e.model_dump() for e in original.days[0].expenses]
    candidate = original.model_copy(deep=True)
    trip = _new_user_station(request)
    apply_booked_transports(candidate, trip)

    prepare_transfer_anchors(candidate, trip, prefer_user_bookings=True)

    arrivals = [s for s in candidate.days[0].stops if s.booking_anchor == 'transport_arrival']
    assert len(arrivals) == 1 and arrivals[0].place_name == '上海站'
    assert arrivals[0].start == '13:21'
    assert arrivals[0].location is None and arrivals[0].location_status == 'unknown' and not arrivals[0].poi_id
    assert not any(s.place_name == '上海虹桥站' for s in candidate.days[0].stops)
    destination = next(s for s in candidate.days[0].stops if s.id == 'original-arrival')
    assert destination.name == '人民广场一带' and destination.area_name == '人民广场'
    assert destination.location is None and destination.location_status == 'unknown'
    assert '按本次用户填写更新为「上海站」' in destination.description
    assert '原片区不代表酒店' in destination.description
    assert [e.model_dump() for e in candidate.days[0].expenses] == expenses
    assert candidate.transfers[0].booking_status == 'user_confirmed'
    assert candidate.transfers[0].schedule_status == 'estimated'
    assert candidate.transfers[0].unit_cost == 800
    assert original.model_dump_json() == before
    preview = candidate.model_dump_json()
    prepare_transfer_anchors(candidate, trip, prefer_user_bookings=True)
    assert candidate.model_dump_json() == preview


def test_user_station_override_requires_booking_supplement_opt_in():
    request, itinerary = plan(queried=True)
    trip = _new_user_station(request)
    apply_booked_transports(itinerary, trip)
    with pytest.raises(ValueError, match='到达站不一致'):
        prepare_transfer_anchors(itinerary, trip)


def test_booking_opt_in_does_not_allow_a_queried_station_to_rewrite_a_model_station():
    request, itinerary = plan(queried=True)
    itinerary.days[0].stops[0].name = '抵达上海站，地铁前往人民广场一带'
    with pytest.raises(ValueError, match='到达站不一致'):
        prepare_transfer_anchors(itinerary, request, prefer_user_bookings=True)


def test_declaration_must_already_match_the_applied_transfer_before_it_can_override():
    request, itinerary = plan(queried=True)
    trip = _new_user_station(request)
    itinerary.transfers[0].booking_status = 'user_confirmed'
    itinerary.transfers[0].arrival_station = '上海站'
    # An unapplied/mismatched actual clock cannot be treated as this declaration.
    itinerary.transfers[0].arrival_time = '14:21'
    with pytest.raises(ValueError, match='到达站不一致'):
        prepare_transfer_anchors(itinerary, trip, prefer_user_bookings=True)


def test_user_booking_override_never_reinterprets_real_verified_meals_or_sights():
    request, itinerary = plan(queried=True)
    trip = _new_user_station(request)
    real = [movement(id='verified', location_status='verified', location=(121.3, 31.2), poi_id='known'),
        movement(id='meal', kind='meal'), movement(id='sight', kind='sight')]
    originals = {s.id: s.model_dump() for s in real}
    itinerary.days[0].stops += real
    apply_booked_transports(itinerary, trip)
    prepare_transfer_anchors(itinerary, trip, prefer_user_bookings=True)
    assert {s.id: s.model_dump() for s in itinerary.days[0].stops if s.id in originals} == originals


def test_split_respects_day_scope_and_does_not_rewrite_verified_points():
    _, itinerary = plan()
    first = itinerary.days[0].model_copy(deep=True)
    itinerary.days.append(first.model_copy(deep=True))
    split_arrival_movements(itinerary, {1})
    assert itinerary.days[0] == first
    assert len(itinerary.days[1].stops) == 2
    verified = movement(location_status='verified', poi_id='known', location=(121.3, 31.2))
    itinerary.days[0].stops = [verified]
    snapshot = verified.model_dump()
    split_arrival_movements(itinerary, {0})
    assert itinerary.days[0].stops[0].model_dump() == snapshot


@pytest.mark.asyncio
async def test_map_resolves_separate_station_and_real_area_entrance_then_queries_connector():
    _, itinerary = plan()
    service = Amap('上海')
    station = {'id': 'rail-poi', 'name': '上海虹桥站', 'address': '合成车站地址',
        'location': [121.32, 31.19], 'city': '上海', 'type': '交通设施服务;火车站'}
    parking = {**station, 'id': 'parking-poi', 'type': '停车场'}
    region = {'id': 'square-poi', 'name': '人民广场', 'address': '合成区域地址',
        'location': [121.47, 31.23], 'city': '上海', 'type': '风景名胜;广场'}
    service.search = AsyncMock(side_effect=lambda query: [parking, station] if query == '上海虹桥站' else [region])
    def detail(path, params):
        if path == '/v3/place/around':
            return {'pois': []}  # no metro station inside this synthetic station
        item = station if params['id'] == 'rail-poi' else region
        return {'pois': [{**item, 'cityname': '上海市', 'navi': {'entr_location': '121.471,31.231'}, 'business': {}}]}
    service.request = AsyncMock(side_effect=detail)
    service.route = AsyncMock(return_value={'distance_m': 17000, 'duration_s': 1800,
        'polyline': [(121.32, 31.19), (121.471, 31.231)], 'fare_yuan': 6})
    try:
        routes = await service.enrich(itinerary, 'transit', enrich_details=True)
        before, after = itinerary.days[0].stops
        assert before.poi_id == 'rail-poi' and before.location == (121.32, 31.19)
        assert after.poi_id == 'square-poi' and after.location == (121.471, 31.231)
        assert all(s.location_status == 'verified' for s in (before, after))
        assert [call.args[0] for call in service.search.call_args_list] == ['上海虹桥站', '人民广场']
        assert len(routes) == 1 and routes[0].available and routes[0].duration_s == 1800
        service.route.assert_awaited_once_with(before.location, after.location, 'transit')
        assert itinerary.transfers[0].schedule_status == 'estimated'
        assert not before.booking_anchor and not itinerary.transfers[0].arrival_station
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_explicit_pure_station_can_be_queried_but_failed_lookup_stays_unknown():
    service = Amap('上海')
    stop = movement(name='抵达上海虹桥站', end='13:30', description='')
    service.search = AsyncMock(return_value=[])
    try:
        await service.resolve(stop)
        service.search.assert_awaited_once_with('上海虹桥站')
        assert explicit_arrival_station(stop) == '上海虹桥站'
        assert stop.location is None and stop.location_status == 'unknown'
        assert not stop.booking_anchor and not stop.poi_id
    finally:
        await service.close()
