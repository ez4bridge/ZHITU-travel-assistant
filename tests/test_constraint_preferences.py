"""Per-day defaults, confirmed route choices and their native/full ledger gates."""
import asyncio
from decimal import Decimal

import pytest

from backend.constraint_models import ConstraintPreviewRequest
from backend.constraint_replan import context, preview, prepare, effective_day_windows
from backend.constraint_storage import ConstraintStore
from backend.models import RouteOption
from backend.storage import Store
from test_constraint_replan import sample_plan, FixedBridge, command


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'preferences.sqlite3')
    plan = sample_plan()
    store.save(plan)
    storage = ConstraintStore(store.path)
    return store, plan, storage, storage.state(plan)


def add_taxi(plan):
    route = plan.routes[0]
    route.alternatives = [RouteOption(mode='taxi', available=True, distance_m=1000, duration_s=180,
        polyline=route.polyline, taxi_cost_yuan=Decimal('12.50'), note='synthetic taxi reference')]


def write_fixture(store, plan):
    with store.connect() as db:
        db.execute('UPDATE plans SET data=? WHERE id=?', (plan.model_dump_json(), plan.id))


def test_each_day_context_uses_its_original_bounds_not_a_uniform_default(setup):
    store, plan, storage, state = setup
    plan.itinerary.days[1].stops[0].start, plan.itinerary.days[1].stops[0].end = '12:00', '13:00'
    plan.itinerary.days[1].stops[1].start, plan.itinerary.days[1].stops[1].end = '15:00', '16:00'
    result = asyncio.run(context(plan, state, FixedBridge()))
    assert [(d['start_min'], d['end_min']) for d in result['days']] == [(540, 720), (720, 960)]
    request = command(plan, state, day_indexes=[0, 1], day_windows=[{'day_index': 0, 'start_min': 600, 'end_min': 1260}])
    windows = effective_day_windows(plan, state, request)
    assert windows[0] == {'day_index': 0, 'start_min': 600, 'end_min': 1260}
    assert windows[1] == {'day_index': 1, 'start_min': 720, 'end_min': 960}


def test_preview_does_not_save_preferences_apply_saves_only_explicit_days(setup):
    store, plan, storage, state = setup
    request = command(plan, state, day_indexes=[0, 1], day_windows=[{'day_index': 0, 'start_min': 600, 'end_min': 1260}])
    def shifted(payload):
        return [{**a, 'start_min': a['start_min'] + (60 if a['day_index'] == 0 else 0),
                 'end_min': a['end_min'] + (60 if a['day_index'] == 0 else 0)} for a in payload['baseline']]
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge(shifted)))
    assert result['outcome'] == 'provisional', result
    assert storage.state(plan)['day_windows'] == []
    saved = storage.apply(plan.id, result['preview_id'])
    current = storage.state(saved)
    assert current['day_windows'] == [{'day_index': 0, 'start_min': 600, 'end_min': 1260}]
    assert saved.itinerary.days[1] == plan.itinerary.days[1]
    result = asyncio.run(context(saved, current, FixedBridge()))
    assert result['days'][0]['end_min'] == 1260
    assert result['days'][1]['end_min'] == 720
    assert all('day_windows' not in activity for activity in current['activities'].values())


def test_explicit_legacy_uniform_fields_remain_compatible(setup):
    store, plan, storage, state = setup
    request = command(plan, state, day_indexes=[0, 1], daily_start_min=480, daily_end_min=1380)
    windows = effective_day_windows(plan, state, request)
    assert all(windows[i]['start_min'] == 480 and windows[i]['end_min'] == 1380 for i in (0, 1))


def test_context_recommends_available_faster_mode_with_group_cost(setup):
    store, plan, storage, state = setup
    add_taxi(plan)
    plan.routes[0].distance_m, plan.routes[0].duration_s = 3000, 1800
    result = asyncio.run(context(plan, state, FixedBridge()))
    route = next(r for r in result['routes'] if r['day_index'] == 0)
    assert route['mode'] == 'walking' and route['recommended_mode'] == 'taxi'
    taxi = next(o for o in route['options'] if o['mode'] == 'taxi')
    assert taxi['cost_cents'] == 1250 and taxi['cost_state'] == 'estimated' and taxi['recommended']


def test_context_short_walk_recommendation_matches_shared_transport_policy(setup):
    store, plan, storage, state = setup
    add_taxi(plan)
    result = asyncio.run(context(plan, state, FixedBridge()))
    route = next(r for r in result['routes'] if r['day_index'] == 0)
    assert route['recommended_mode'] == 'walking'
    assert '推荐步行' in route['recommendation_reason']


def test_selected_alternative_is_native_priced_applied_and_keeps_old_option(setup):
    store, plan, storage, state = setup
    add_taxi(plan)
    write_fixture(store, plan)
    request = command(plan, state, route_choices=[{'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'taxi'}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    assert result['outcome'] == 'provisional', result
    assert result['summary']['after_cents'] == 5250
    assert result['summary']['route_changed_count'] == 1
    change = result['route_changes'][0]
    assert change['before_mode'] == 'walking' and change['after_mode'] == 'taxi'
    assert change['before_cost_cents'] == 0 and change['after_cost_cents'] == 1250
    saved = storage.apply(plan.id, result['preview_id'])
    route = next(r for r in saved.routes if r.day_index == 0)
    assert route.mode == 'taxi' and route.user_selected
    assert any(o.mode == 'walking' for o in route.alternatives)
    assert saved.itinerary.days[1] == plan.itinerary.days[1]


def test_unqueried_mode_is_pending_and_not_invented_without_provider(setup):
    store, plan, storage, state = setup
    request = command(plan, state, route_choices=[{'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'transit'}])
    prepare(plan, state, request)
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    assert result['outcome'] == 'conflict' and 'preview_id' not in result
    assert len(store.list()) == 1 and plan.routes[0].mode == 'walking'


def test_completed_travel_cannot_be_overwritten_by_route_choice(setup):
    store, plan, storage, state = setup
    add_taxi(plan)
    request = command(plan, state, activities=[{'stop_id': 's0-0', 'status': 'completed'}, {'stop_id': 's0-1', 'status': 'completed'}],
        route_choices=[{'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'taxi'}])
    with pytest.raises(ValueError, match='已完成记录'):
        prepare(plan, state, request)


def test_user_selected_edge_cannot_be_silently_dropped(setup):
    store, plan, storage, state = setup
    add_taxi(plan)
    request = command(plan, state, route_choices=[{'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'taxi'}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge(lambda p: p['baseline'][:1])))
    assert result['outcome'] == 'conflict' and 'preview_id' not in result
    assert any(e['code'] == 'route_choice_not_used' for e in result['errors'])


def test_missed_task_can_use_confirmed_future_route_without_changing_completion(setup):
    store, plan, storage, state = setup
    add_taxi(plan)
    write_fixture(store, plan)
    request = command(plan, state, mode='during', as_of='2026-11-02T10:30:00+08:00', current_stop_id='s0-0',
        activities=[{'stop_id': 's0-0', 'status': 'completed'}, {'stop_id': 's0-1', 'status': 'missed', 'priority': 'must'}],
        route_choices=[{'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'taxi'}])
    assigned = lambda p: [{'task_id': 's0-1', 'day_index': 0, 'start_min': 660, 'end_min': 720}]
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge(assigned)))
    assert result['outcome'] == 'provisional', result
    assert result['summary']['after_cents'] == 5250
    saved = storage.apply(plan.id, result['preview_id'])
    assert saved.itinerary.days[0].stops[0] == plan.itinerary.days[0].stops[0]
    assert next(r for r in saved.routes if r.day_index == 0).mode == 'taxi'


def test_day_and_route_changes_reject_nonselected_or_duplicate_references(setup):
    store, plan, storage, state = setup
    with pytest.raises(ValueError):
        command(plan, state, day_windows=[{'day_index': 1, 'start_min': 600, 'end_min': 1260}])
    choice = {'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'walking'}
    with pytest.raises(ValueError):
        command(plan, state, route_choices=[choice, choice])


def test_real_native_per_day_and_taxi_choice_stay_in_same_independent_ledger(setup):
    from backend.constraint_bridge import constraint_planner
    store, plan, storage, state = setup
    if not constraint_planner.executable.is_file():
        pytest.skip('native executable not built; not native acceptance')
    add_taxi(plan)
    write_fixture(store, plan)
    request = command(plan, state, day_windows=[{'day_index': 0, 'start_min': 570, 'end_min': 1260}],
        route_choices=[{'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'taxi'}])
    result = asyncio.run(preview(plan, state, request, storage))
    assert result['outcome'] == 'provisional', result['errors']
    assert result['engine']['name'] == 'cangjie-journey-planner'
    assert result['summary']['after_cents'] == 5250
    saved = storage.apply(plan.id, result['preview_id'])
    assert saved.validation.total_cents == 5250
    assert saved.itinerary.days[1] == plan.itinerary.days[1]
    assert next(r for r in saved.routes if r.day_index == 0).mode == 'taxi'
