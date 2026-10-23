"""Mixed-city dates use a same-city slice; other cities remain frozen."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from backend.constraint_replan import context, prepare, preview, build_projection
from backend.constraint_bridge import ConstraintBridge
from backend.constraint_models import ConstraintIntentRequest
from backend import constraint_intent
from backend.constraint_storage import fingerprint
from backend.constraint_storage import ConstraintStore
from backend.models import CityTransfer, DestinationStay, Expense, RouteLeg, Stop
from backend.planning import recost_all
from backend.storage import Store
from test_constraint_replan import FixedBridge, sample_plan, command


def mixed_plan():
    plan = sample_plan()
    plan.request.destinations = [DestinationStay(city='北京', preferred_days=1), DestinationStay(city='天津', preferred_days=1)]
    plan.request.destination = '北京→天津'
    first = plan.itinerary.days[0]
    first.stops[1].city = '天津'
    first.stops[1].start, first.stops[1].end = '14:00', '15:00'
    extra = first.stops[1].model_copy(deep=True)
    extra.id, extra.name = 'tj-extra', '合成天津地点'
    extra.start, extra.end = '16:00', '17:00'
    extra.location = (116.02, 39)
    first.stops.append(extra)
    first.expenses.append(Expense(label='合成天津门票', category='ticket', unit_cost=10, basis='group', stop_id=extra.id))
    for stop in plan.itinerary.days[1].stops:
        stop.city = '天津'
    plan.itinerary.transfers = [CityTransfer(from_city='北京', to_city='天津', departure_date=first.date,
        arrival_date=first.date, departure_time='11:00', arrival_time='12:00', mode='train', unit_cost=20, basis='group')]
    plan.routes[0] = RouteLeg(day_index=0, from_id=first.stops[1].id, to_id=extra.id, mode='taxi', available=True,
        distance_m=600, duration_s=180, taxi_cost_yuan=15, polyline=[first.stops[1].location, extra.location], queried_at=datetime.now(timezone.utc))
    recost_all(plan.request, plan.itinerary, plan.routes)
    return plan


def setup_mixed(tmp_path):
    plan = mixed_plan()
    store = Store(tmp_path / 'mixed.sqlite3')
    store.save(plan)
    storage = ConstraintStore(store.path)
    return plan, storage, storage.state(plan)


def test_context_exposes_two_selectable_city_segments(tmp_path):
    plan, storage, state = setup_mixed(tmp_path)
    data = asyncio.run(context(plan, state, FixedBridge()))
    day = data['days'][0]
    assert day['supported']
    scopes = {value['city']: value for value in day['city_scopes']}
    assert scopes['北京']['supported'] and scopes['天津']['supported']
    assert scopes['北京']['end_min'] == 11 * 60
    assert scopes['天津']['start_min'] == 12 * 60


@pytest.mark.parametrize('city', ['北京', '天津'])
def test_mixed_day_city_segment_preserves_other_city_and_full_cost(tmp_path, city):
    plan, storage, state = setup_mixed(tmp_path)
    body = command(plan, state, scope_city=city)
    result = asyncio.run(preview(plan, state, body, storage, bridge=FixedBridge()))
    assert result.get('preview_id'), result
    child = storage.apply(plan.id, result['preview_id'])
    assert child.itinerary.days[0].stops == plan.itinerary.days[0].stops
    assert child.itinerary.days[1] == plan.itinerary.days[1]
    assert child.itinerary.transfers == plan.itinerary.transfers
    assert result['summary']['after_cents'] == result['summary']['before_cents'] == 8500
    assert any(r.mode == 'taxi' and r.from_id == 's0-1' and r.to_id == 'tj-extra' for r in child.routes)


def test_city_scope_rejects_other_city_activity_edits_and_roads(tmp_path):
    plan, storage, state = setup_mixed(tmp_path)
    body = command(plan, state, scope_city='北京', activities=[{'stop_id': 'tj-extra', 'priority': 'optional'}])
    with pytest.raises(ValueError, match='窗口外'):
        prepare(plan, state, body)
    body = command(plan, state, scope_city='北京', route_choices=[{'day_index': 0, 'from_id': 's0-1', 'to_id': 'tj-extra', 'mode': 'taxi'}])
    with pytest.raises(ValueError, match='所选城市'):
        prepare(plan, state, body)


def test_city_segment_time_limits_cannot_cross_frozen_transfer(tmp_path):
    plan, storage, state = setup_mixed(tmp_path)
    body = command(plan, state, scope_city='天津', day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 1200}])
    catalog, states, city, ids, _ = prepare(plan, state, body)
    payload, _, _, _ = build_projection(plan, body, catalog, states, city, ids)
    assert payload['days'][0]['start_min'] == 720
    assert {task['task_id'] for task in payload['source_catalog']} == {'s0-1', 'tj-extra'}
    body = command(plan, state, scope_city='北京', day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 1200}])
    catalog, states, city, ids, _ = prepare(plan, state, body)
    payload, _, _, _ = build_projection(plan, body, catalog, states, city, ids)
    assert payload['days'][0]['end_min'] == 660


def test_city_must_have_existing_segment_on_every_selected_day(tmp_path):
    plan, storage, state = setup_mixed(tmp_path)
    with pytest.raises(ValueError, match='没有该城市'):
        prepare(plan, state, command(plan, state, day_indexes=[0, 1], scope_city='北京'))


def test_scope_omitted_still_rejects_ambiguous_city(tmp_path):
    plan, storage, state = setup_mixed(tmp_path)
    with pytest.raises(ValueError, match='先明确'):
        prepare(plan, state, command(plan, state))


def test_natural_language_cannot_silently_ignore_other_city_request(tmp_path, monkeypatch):
    plan, storage, state = setup_mixed(tmp_path)
    monkeypatch.setattr(constraint_intent.settings, 'llm_key', 'offline-placeholder')
    async def fake_model(*args):
        return {'patch': {'budget_cents': 90000}, 'unresolved': []}
    monkeypatch.setattr(constraint_intent, 'model_interpret', fake_model)
    body = ConstraintIntentRequest(base_fingerprint=fingerprint(plan), expected_routing_revision=0,
        expected_state_revision=0, day_indexes=[0], scope_city='北京', instruction='预算900元，合成天津地点必去')
    result = asyncio.run(constraint_intent.parse_intent(plan, state, body))
    assert any('切换城市' in message for message in result['unresolved'])


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / 'cangjie/bin/journey_planner.exe').is_file(), reason='Engine2.2 native binary not built')
def test_real_native_moves_only_beijing_segment_before_frozen_departure(tmp_path):
    plan, storage, state = setup_mixed(tmp_path)
    body = command(plan, state, scope_city='北京', day_windows=[{'day_index': 0, 'start_min': 570, 'end_min': 900}])
    result = asyncio.run(preview(plan, state, body, storage, bridge=ConstraintBridge()))
    assert result.get('preview_id'), result
    assert result['engine']['name'] == 'cangjie-journey-planner'
    child = storage.apply(plan.id, result['preview_id'])
    assert child.itinerary.days[0].stops[0].start == '09:30'
    assert child.itinerary.days[0].stops[0].end == '10:30'
    assert child.itinerary.days[0].stops[1:] == plan.itinerary.days[0].stops[1:]
    assert child.itinerary.transfers == plan.itinerary.transfers
    assert result['summary']['after_cents'] == 8500


@pytest.mark.skipif(not (Path(__file__).resolve().parents[1] / 'cangjie/bin/journey_planner.exe').is_file(), reason='Engine2.2 native binary not built')
def test_native_departure_station_is_fixed_city_endpoint_not_midday_activity(tmp_path):
    plan = mixed_plan()
    day = plan.itinerary.days[0]
    plan.itinerary.transfers[0].departure_time = '12:00'
    plan.itinerary.transfers[0].arrival_time = '13:00'
    station = Stop(id='booked-departure', name='合成已订出发站', city='北京', kind='break', start='11:00', end='11:05',
        booking_anchor='transport_departure', booking_ref='transport-test', location=(116.005, 39), location_status='verified')
    day.stops.insert(1, station)
    plan.routes.append(RouteLeg(day_index=0, from_id='s0-0', to_id=station.id, mode='walking', available=True,
        distance_m=500, duration_s=600, polyline=[day.stops[0].location, station.location], queried_at=datetime.now(timezone.utc)))
    store = Store(tmp_path / 'station-native.sqlite3')
    store.save(plan)
    storage = ConstraintStore(store.path)
    state = storage.state(plan)
    body = command(plan, state, scope_city='北京', day_windows=[{'day_index': 0, 'start_min': 570, 'end_min': 1000}],
        activities=[{'stop_id': 's0-0', 'priority': 'must'}])
    catalog, states, city, ids, _ = prepare(plan, state, body)
    payload, _, anchors, _ = build_projection(plan, body, catalog, states, city, ids)
    assert payload['days'][0]['end_min'] == 665
    assert anchors[payload['days'][0]['end_anchor_id']].id == station.id
    result = asyncio.run(preview(plan, state, body, storage, bridge=ConstraintBridge()))
    assert result.get('preview_id'), result
    child = storage.apply(plan.id, result['preview_id'])
    assert child.itinerary.days[0].stops[0].start == '09:30'
    assert next(s for s in child.itinerary.days[0].stops if s.id == station.id) == station
    assert child.itinerary.days[0].stops[2:] == plan.itinerary.days[0].stops[2:]


def test_arrival_station_clips_window_and_is_start_anchor(tmp_path):
    plan = mixed_plan()
    day = plan.itinerary.days[0]
    station = Stop(id='booked-arrival', name='合成已订到达站', city='天津', kind='break', start='13:15', end='13:20',
        booking_anchor='transport_arrival', booking_ref='transport-test', location=(116.008, 39), location_status='verified')
    day.stops.insert(1, station)
    store = Store(tmp_path / 'arrival-projection.sqlite3')
    store.save(plan)
    storage = ConstraintStore(store.path)
    state = storage.state(plan)
    body = command(plan, state, scope_city='天津', day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 1200}])
    catalog, states, city, ids, _ = prepare(plan, state, body)
    payload, _, anchors, _ = build_projection(plan, body, catalog, states, city, ids)
    assert payload['days'][0]['start_min'] == 795
    assert anchors[payload['days'][0]['start_anchor_id']].id == station.id
    assert next(task for task in payload['source_catalog'] if task['task_id'] == station.id)['protected']
