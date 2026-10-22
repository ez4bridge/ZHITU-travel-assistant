"""Atomic clock repair regressions; all places and route durations are synthetic."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from backend.bookings import apply_transport_anchors, repair_booking_schedule
from backend.models import BookedTransport, CityTransfer, ConfirmedHotel, RouteLeg, Stop, TripRequest
from backend.storage import Store
from test_constraint_replan import sample_plan


def road(before, after, minutes, day_index=0):
    return RouteLeg(day_index=day_index, from_id=before.id, to_id=after.id, mode='walking',
        available=True, distance_m=1000, duration_s=minutes * 60,
        polyline=[before.location, after.location], queried_at=datetime.now(timezone.utc),
        note='合成离线路线，不代表真实地图')


def late_plan():
    plan = sample_plan(days=1)
    plan.request.destination = '上海'
    plan.request.start_time = '09:00'
    points = [Stop(id=sid, name=name, city='上海', start=start, end=end,
                   kind='break' if sid == 'arrival' else 'sight',
                   location=(121.4 + index * .01, 31.2), poi_id=sid, location_status='verified')
        for index, (sid, name, start, end) in enumerate([
            ('arrival', '合成上海站', '20:50', '20:55'),
            ('bund', '合成外滩', '21:00', '22:10'),
            ('nanjing', '合成南京路', '21:40', '22:30')])]
    points[0].booking_anchor = 'transport_arrival'
    plan.itinerary.days[0].stops = points
    plan.routes = [road(points[0], points[1], 56), road(points[1], points[2], 30)]
    return plan


def test_overflow_does_not_publish_earlier_shift_and_later_old_clock():
    plan = late_plan()
    before = plan.model_dump_json()
    conflicts, changes = repair_booking_schedule(plan)
    assert any('无法当天完成' in conflict for conflict in conflicts)
    # The former implementation changed the Bund to21:51-23:01, then left
    # Nanjing Road at21:40-22:30 when its calculated end overflowed midnight.
    assert changes == []
    assert plan.model_dump_json() == before


def test_fixed_station_collision_rolls_back_earlier_shifts():
    plan = late_plan()
    day = plan.itinerary.days[0]
    day.stops[2].start, day.stops[2].end = '22:40', '22:45'
    day.stops[2].booking_anchor = 'transport_departure'
    before = plan.model_dump_json()
    conflicts, changes = repair_booking_schedule(plan)
    assert any('固定安排' in conflict for conflict in conflicts)
    assert changes == [] and plan.model_dump_json() == before


def test_invalid_duration_rolls_back_other_selected_dates():
    plan = sample_plan(days=2)
    plan.itinerary.days[0].stops[1].start = '09:30'
    plan.itinerary.days[0].stops[1].end = '10:30'
    plan.itinerary.days[1].stops[1].end = plan.itinerary.days[1].stops[1].start
    before = plan.model_dump_json()
    conflicts, changes = repair_booking_schedule(plan)
    assert any('时间无效' in conflict for conflict in conflicts)
    assert changes == [] and plan.model_dump_json() == before


def test_feasible_repair_commits_all_new_times_with_actual_route_gaps():
    plan = late_plan()
    plan.itinerary.days[0].stops[2].start, plan.itinerary.days[0].stops[2].end = '21:40', '21:50'
    plan.routes[1].duration_s = 5 * 60
    conflicts, changes = repair_booking_schedule(plan)
    assert conflicts == [] and len(changes) == 2
    points = plan.itinerary.days[0].stops
    assert (points[1].start, points[1].end) == ('21:51', '23:01')
    assert (points[2].start, points[2].end) == ('23:06', '23:16')
    assert (points[0].start, points[0].end) == ('20:50', '20:55')


def test_only_days_preserves_outside_invalid_schedule():
    plan = sample_plan(days=2)
    plan.itinerary.days[0].stops[1].start, plan.itinerary.days[0].stops[1].end = '09:30', '10:30'
    plan.itinerary.days[1].stops[1].end = plan.itinerary.days[1].stops[1].start
    outside = plan.itinerary.days[1].model_dump_json()
    conflicts, changes = repair_booking_schedule(plan, only_days={0})
    assert not conflicts and changes
    assert plan.itinerary.days[0].stops[1].start == '10:05'
    assert plan.itinerary.days[1].model_dump_json() == outside


def test_locked_completed_stop_is_not_moved_or_partially_preceded():
    plan = late_plan()
    plan.itinerary.days[0].stops[2].start, plan.itinerary.days[0].stops[2].end = '22:40', '22:45'
    before = plan.model_dump_json()
    state = {'activities': {'nanjing': {'status': 'completed', 'locked': True}}}
    conflicts, changes = repair_booking_schedule(plan, state)
    assert conflicts and changes == [] and plan.model_dump_json() == before


def test_departure_deadline_conflict_rolls_back_even_without_midnight_overflow():
    plan = late_plan()
    plan.itinerary.days[0].stops[2].start, plan.itinerary.days[0].stops[2].end = '21:40', '21:50'
    plan.routes[1].duration_s = 60
    plan.itinerary.transfers = [CityTransfer(from_city='上海', to_city='北京',
        departure_date=plan.request.start_date, departure_time='23:10',
        arrival_date=plan.request.start_date + timedelta(days=1), arrival_time='06:00', unit_cost=100)]
    before = plan.model_dump_json()
    conflicts, changes = repair_booking_schedule(plan)
    assert any('赶不上23:10' in conflict for conflict in conflicts)
    assert changes == [] and plan.model_dump_json() == before


@pytest.mark.parametrize(('mode', 'buffer', 'anchor_start', 'sight_start'), [
    ('train', None, '12:00', '12:10'),
    ('flight', 0, '12:00', '12:10'),
    ('flight', None, '13:00', '13:10'),
])
def test_arrival_buffer_is_not_added_again_after_visible_station_point(mode, buffer, anchor_start, sight_start):
    plan = sample_plan(days=1)
    plan.request = TripRequest(origin='北京', destination='上海', include_origin_transport=True,
        start_date=plan.request.start_date, start_time='09:00', days=1,
        booked_transports=[BookedTransport(from_city='北京', to_city='上海', mode=mode,
            departure_date=plan.request.start_date, departure_time='10:00',
            arrival_date=plan.request.start_date, arrival_time='12:00',
            departure_place='合成北京站', arrival_place='合成上海站', arrival_buffer_min=buffer)])
    plan.itinerary.transfers = [CityTransfer(from_city='北京', to_city='上海', mode=mode,
        departure_date=plan.request.start_date, departure_time='10:00',
        arrival_date=plan.request.start_date, arrival_time='12:00', unit_cost=100)]
    sight = Stop(id='sight', name='合成景点', city='上海', start='12:05', end='12:15',
                 location=(121.4, 31.2), location_status='verified')
    plan.itinerary.days[0].stops = [sight]
    apply_transport_anchors(plan.itinerary, plan.request)
    anchor = plan.itinerary.days[0].stops[0]
    anchor.location, anchor.location_status = (121.41, 31.21), 'verified'
    plan.routes = [road(anchor, sight, 5)]
    conflicts, _ = repair_booking_schedule(plan)
    assert conflicts == []
    assert anchor.booking_anchor == 'transport_arrival' and anchor.start == anchor_start
    assert sight.start == sight_start


@pytest.mark.parametrize('fixed_anchor_path', [True, False])
def test_ordinary_revise_failure_never_saves_mixed_schedule(tmp_path, monkeypatch, fixed_anchor_path):
    import backend.main as main
    from backend.amap import Amap

    previous = late_plan()
    if fixed_anchor_path:
        previous.request.confirmed_hotels = [ConfirmedHotel(city='上海', name='合成酒店',
            check_in=previous.request.start_date, check_out=previous.request.start_date + timedelta(days=1))]
    else:
        previous.itinerary.days[0].stops[0].booking_anchor = ''
    store = Store(tmp_path / 'atomic-stream.sqlite3')
    store.save(previous)
    before = store.get(previous.id).model_dump_json()
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'knowledge', SimpleNamespace(
        retriever=lambda: SimpleNamespace(start_background=lambda: None), aclose=AsyncMock()))

    async def generate(*args, **kwargs):
        return previous.itinerary.model_copy(deep=True)

    monkeypatch.setattr(main, 'generate_itinerary', generate)
    # Anchor construction and provider lookup have their own regression suite;
    # retain this exact late-arrival catalog to exercise the ordinary SSE caller.
    monkeypatch.setattr(main, 'prepare_transfer_anchors', lambda *args, **kwargs: None)
    monkeypatch.setattr(main, 'prepare_accommodation', lambda *args, **kwargs: None)
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=previous.routes))
    with TestClient(main.app) as client:
        response = client.post(f'/api/plans/{previous.id}/revise', json={'instruction': '合成测试：按晚到后的实际路线排时'})
    if 'event: error' in response.text:
        # Nothing can be trimmed (or a fixed anchor is genuinely impossible).
        assert '无法当天完成' in response.text
        assert 'event: result' not in response.text
        assert len(store.list()) == 1
        assert store.get(previous.id).model_dump_json() == before
    else:
        # Otherwise the day is trimmed to a consistent schedule instead of a
        # half-applied one; the removal is recorded for the user.
        assert 'event: result' in response.text
        assert len(store.list()) == 2
        latest = max(store.list(), key=lambda item: item['revision'])
        saved_plan = store.get(latest['id'])
        assert any('已移除模型建议的活动' in item for item in saved_plan.adjustments)
