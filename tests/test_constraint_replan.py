"""Synthetic offline orchestration and transaction regressions, without search mocks
being described as native acceptance. The native boundary is tested separately.
"""
import asyncio
from datetime import date, datetime, timezone, timedelta
from decimal import Decimal

import pytest

from backend.constraint_models import ConstraintPreviewRequest
from backend.constraint_replan import context, preview, prepare, cost_amount
from backend.constraint_reference import evaluate
from backend.constraint_storage import ConstraintStore, ConstraintStale, assert_legacy_editable, fingerprint
from backend.models import TripRequest, Itinerary, DayPlan, Stop, Expense, RouteLeg, PlanRecord, ValidationReport
from backend.storage import Store


def sample_plan(days=2):
    trip = TripRequest(destination='北京', start_date='2026-11-02', days=days, budget='1000', people=2, mode='walking')
    plan_days, routes = [], []
    for index in range(days):
        stops = [Stop(id=f's{index}-{n}', name=f'合成地点{index}-{n}', city='北京', start=f'{9+n*2:02d}:00', end=f'{10+n*2:02d}:00',
                      location=(116 + n * .01, 39 + index * .01), poi_id=f'poi{index}-{n}', location_status='verified') for n in range(2)]
        plan_days.append(DayPlan(date=date(2026, 11, 2)+timedelta(days=index), title='合成测试日', stops=stops,
                          expenses=[Expense(label=s.name+'门票', category='ticket', unit_cost='10', basis='group', stop_id=s.id) for s in stops]))
        routes.append(RouteLeg(day_index=index, from_id=stops[0].id, to_id=stops[1].id, mode='walking', available=True,
                               distance_m=1000, duration_s=300, polyline=[stops[0].location, stops[1].location], queried_at=datetime.now(timezone.utc)))
    return PlanRecord(id='base', created_at=datetime.now(timezone.utc), request=trip,
                      itinerary=Itinerary(title='合成行程', summary='仅离线测试', days=plan_days), routes=routes,
                      validation=ValidationReport(status='passed_with_unknowns', errors=[], warnings=[], unknowns=[], total_cents=days*2000, budget_cents=100000))


class FixedBridge:
    diagnostic = 'synthetic unit double, not native acceptance'
    def __init__(self, assignments=None, bad_cost=False):
        self.assignments, self.bad_cost, self.called = assignments, bad_cost, False
    async def ready(self):
        return True
    async def call(self, payload):
        self.called = True
        assignments = self.assignments(payload) if self.assignments else list(payload['baseline'])
        report = evaluate(payload, assignments)
        return {'assignments': assignments, 'outcome': 'provisional', **report,
                'total_cents': report['total_cents'] + int(self.bad_cost), 'unplaced': [],
                'search': {'expanded_states': 1, 'evaluated_candidates': 1, 'termination': 'completed'},
                '_engine': {'name': 'synthetic-unit-double', 'version': 'test'}}


@pytest.fixture
def setup(tmp_path):
    store = Store(tmp_path / 'plans.sqlite3')
    plan = sample_plan()
    store.save(plan)
    storage = ConstraintStore(store.path)
    return store, plan, storage, storage.state(plan)


def command(plan, state, **changes):
    return ConstraintPreviewRequest.model_validate({'base_fingerprint': fingerprint(plan), 'expected_routing_revision': plan.routing_revision,
        'expected_state_revision': state['state_revision'], 'day_indexes': [0], **changes})


def test_preview_does_not_commit_and_apply_is_idempotent(setup):
    store, plan, storage, state = setup
    result = asyncio.run(preview(plan, state, command(plan, state), storage, bridge=FixedBridge()))
    assert result['outcome'] == 'provisional'
    assert result['summary']['after_cents'] == 4000
    assert storage.state(plan)['state_revision'] == 0
    assert len(store.list()) == 1
    assert_legacy_editable(store, plan)
    saved = storage.apply(plan.id, result['preview_id'])
    assert saved.parent_id == plan.id and saved.id != plan.id
    assert saved.itinerary.days[1] == plan.itinerary.days[1]
    assert storage.apply(plan.id, result['preview_id']).id == saved.id
    assert len(store.list()) == 2
    assert storage.state(saved)['state_revision'] == 1
    with pytest.raises(ConstraintStale):
        assert_legacy_editable(store, saved)


def test_stale_preview_after_route_revision_is_rejected(setup):
    store, plan, storage, state = setup
    result = asyncio.run(preview(plan, state, command(plan, state), storage, bridge=FixedBridge()))
    changed = plan.model_copy(deep=True)
    changed.routing_revision += 1
    store.update_routes(changed, plan.routing_revision)
    with pytest.raises(ConstraintStale):
        storage.apply(plan.id, result['preview_id'])
    assert len(store.list()) == 1


def test_old_parent_cannot_fork_confirmed_chain(setup):
    store, plan, storage, state = setup
    result = asyncio.run(preview(plan, state, command(plan, state), storage, bridge=FixedBridge()))
    storage.apply(plan.id, result['preview_id'])
    with pytest.raises(ConstraintStale):
        prepare(plan, storage.state(plan), command(plan, state))


def test_unknown_past_activity_is_not_automatically_completed(setup):
    store, plan, storage, state = setup
    bridge = FixedBridge()
    request = command(plan, state, mode='during', as_of='2026-11-02T10:30:00+08:00')
    result = asyncio.run(preview(plan, state, request, storage, bridge=bridge))
    assert result['outcome'] == 'conflict' and 'preview_id' not in result
    assert result['errors'][0]['code'] == 'past_unconfirmed'
    assert not bridge.called and storage.state(plan)['activities'] == {}


def test_completed_record_and_anchor_are_preserved(setup):
    store, plan, storage, state = setup
    request = command(plan, state, mode='during', as_of='2026-11-02T10:30:00+08:00', current_stop_id='s0-0',
        activities=[{'stop_id': 's0-0', 'status': 'completed', 'priority': 'want', 'locked': False, 'removable_cost': False}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    assert result['outcome'] == 'provisional', result
    saved = storage.apply(plan.id, result['preview_id'])
    assert saved.itinerary.days[0].stops[0] == plan.itinerary.days[0].stops[0]
    current = storage.state(saved)
    revert = command(saved, current, activities=[{'stop_id': 's0-0', 'status': 'planned'}])
    with pytest.raises(ValueError, match='不能'):
        prepare(saved, current, revert)


def test_native_cost_disagreement_never_saves_preview(setup):
    store, plan, storage, state = setup
    result = asyncio.run(preview(plan, state, command(plan, state), storage, bridge=FixedBridge(bad_cost=True)))
    assert result['errors'][0]['code'] == 'independent_disagreement' and 'preview_id' not in result
    assert len(store.list()) == 1


def test_full_trip_budget_conflict_blocks_apply(setup):
    store, plan, storage, state = setup
    result = asyncio.run(preview(plan, state, command(plan, state, budget_cents=1000), storage, bridge=FixedBridge()))
    assert result['outcome'] == 'conflict' and 'preview_id' not in result
    assert any(e['code'] == 'remaining_budget' for e in result['errors'])


def test_explicit_cancel_does_not_remove_default_cost(setup):
    store, plan, storage, state = setup
    request = command(plan, state, activities=[{'stop_id': 's0-1', 'status': 'cancelled'}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    assert result['summary']['after_cents'] == 4000
    saved = storage.apply(plan.id, result['preview_id'])
    assert all(s.id != 's0-1' for s in saved.itinerary.days[0].stops)
    assert sum(1 for d in saved.itinerary.days for e in d.expenses if e.stop_id == 's0-1') == 1


def test_explicit_unpaid_free_cancel_can_remove_only_linked_ticket(setup):
    store, plan, storage, state = setup
    request = command(plan, state, activities=[{'stop_id': 's0-1', 'status': 'cancelled', 'removable_cost': True}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    assert result['summary']['after_cents'] == 3000, result
    saved = storage.apply(plan.id, result['preview_id'])
    assert not any(e.stop_id == 's0-1' for d in saved.itinerary.days for e in d.expenses)


def test_engine_failure_has_no_search_fallback(setup):
    store, plan, storage, state = setup
    class Unavailable(FixedBridge):
        async def ready(self):
            return False
    with pytest.raises(RuntimeError):
        asyncio.run(preview(plan, state, command(plan, state), storage, bridge=Unavailable()))
    assert len(store.list()) == 1


def test_context_exposes_real_limits_and_no_internal_source_snapshots(setup):
    store, plan, storage, state = setup
    result = asyncio.run(context(plan, state, FixedBridge()))
    assert result['days'][0]['supported'] and result['stops'][0]['status'] == 'planned'
    assert all(not key.startswith('_') for s in result['stops'] for key in s)


def test_scope_does_not_silently_truncate_large_or_mixed_days(setup):
    store, plan, storage, state = setup
    plan.itinerary.days[0].stops[1].city = '天津'
    with pytest.raises(ValueError, match='同一城市'):
        prepare(plan, state, command(plan, state))


def test_during_requires_explicit_beijing_timezone(setup):
    store, plan, storage, state = setup
    with pytest.raises(ValueError):
        command(plan, state, mode='during', as_of='2026-11-02T10:30:00')
    with pytest.raises(ValueError):
        command(plan, state, mode='during')


class SyntheticRoads:
    instances = []
    def __init__(self, city):
        self.calls, self.closed = 0, False
        self.instances.append(self)
    async def route(self, before, after, mode, city):
        self.calls += 1
        return {'available': True, 'distance_m': 1000, 'duration_s': 300,
                'polyline': [before.location, after.location], 'note': 'synthetic offline route'}
    async def close(self):
        self.closed = True


def test_authorized_missed_task_moves_once_with_expense_and_same_city(setup):
    store, plan, storage, state = setup
    def candidate(payload):
        return [*payload['baseline'], {'task_id': 's0-0', 'day_index': 0, 'start_min': 780, 'end_min': 840}]
    request = command(plan, state, day_indexes=[1], day_windows=[{'day_index': 1, 'start_min': 540, 'end_min': 840}], activities=[{'stop_id': 's0-0', 'status': 'missed'}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge(candidate), provider_factory=SyntheticRoads))
    assert result['outcome'] == 'provisional', result
    saved = storage.apply(plan.id, result['preview_id'])
    assert [s.id for s in saved.itinerary.days[0].stops] == ['s0-1']
    assert sum(s.id == 's0-0' for day in saved.itinerary.days for s in day.stops) == 1
    assert [s.id for s in saved.itinerary.days[1].stops] == ['s1-0', 's1-1', 's0-0']
    assert sum(cost_amount(e, 2) for day in saved.itinerary.days for e in day.expenses) == 4000
    assert sum(e.stop_id == 's0-0' for day in saved.itinerary.days for e in day.expenses) == 1
    assert SyntheticRoads.instances[-1].calls <= 20 and SyntheticRoads.instances[-1].closed


def test_missing_real_new_edge_blocks_application_without_external_calls(setup):
    store, plan, storage, state = setup
    def candidate(payload):
        return [*payload['baseline'], {'task_id': 's0-0', 'day_index': 0, 'start_min': 780, 'end_min': 840}]
    request = command(plan, state, day_indexes=[1], activities=[{'stop_id': 's0-0', 'status': 'missed'}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge(candidate)))
    assert result['outcome'] == 'conflict' and 'preview_id' not in result
    assert any(e['code'] == 'critical_route_unknown' for e in result['errors'])


def test_omitted_missed_tasks_still_appear_in_next_context(setup):
    store, plan, storage, state = setup
    request = command(plan, state, activities=[{'stop_id': 's0-1', 'status': 'missed'}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    saved = storage.apply(plan.id, result['preview_id'])
    assert all(s.id != 's0-1' for day in saved.itinerary.days for s in day.stops)
    updated = asyncio.run(context(saved, storage.state(saved), FixedBridge()))
    pool = next(s for s in updated['stops'] if s['stop_id'] == 's0-1')
    assert pool['status'] == 'missed' and pool['in_task_pool'] is True
    assert pool['cost_cents'] == 1000


def test_scope_rejects_over_eight_without_truncation(setup):
    store, plan, storage, state = setup
    for n in range(7):
        extra = plan.itinerary.days[0].stops[0].model_copy(deep=True)
        extra.id = f'extra-{n}'
        plan.itinerary.days[0].stops.append(extra)
    with pytest.raises(ValueError, match='最多8'):
        prepare(plan, state, command(plan, state))
    assert len(plan.itinerary.days[0].stops) == 9


def test_unknown_city_cannot_be_added_as_missed(setup):
    store, plan, storage, state = setup
    plan.itinerary.days[1].stops[0].city = '天津'
    request = command(plan, state, activities=[{'stop_id': 's1-0', 'status': 'missed'}])
    with pytest.raises(ValueError, match='同城'):
        prepare(plan, state, request)


def test_task_pool_restores_ticket_when_another_nonremovable_cost_remains(setup):
    store, plan, storage, state = setup
    plan.itinerary.days[0].expenses.append(Expense(label='独立不可退餐费', category='food', unit_cost='5', stop_id='s0-1', commitment='paid'))
    # Update the synthetic database row to match this explicit test setup.
    with store.connect() as db:
        db.execute('UPDATE plans SET data=? WHERE id=?', (plan.model_dump_json(), plan.id))
    first = command(plan, state, activities=[{'stop_id': 's0-1', 'status': 'missed', 'removable_cost': True}])
    omitted = asyncio.run(preview(plan, state, first, storage, bridge=FixedBridge()))
    assert omitted['summary']['after_cents'] == 3500, omitted
    saved = storage.apply(plan.id, omitted['preview_id'])
    current = storage.state(saved)
    second = command(saved, current, day_indexes=[1], day_windows=[{'day_index': 1, 'start_min': 540, 'end_min': 840}], activities=[{'stop_id': 's0-1', 'status': 'missed', 'removable_cost': True}])
    def assignments(payload):
        return [*payload['baseline'], {'task_id': 's0-1', 'day_index': 0, 'start_min': 780, 'end_min': 840}]
    restored = asyncio.run(preview(saved, current, second, storage, bridge=FixedBridge(assignments), provider_factory=SyntheticRoads))
    assert restored['outcome'] == 'provisional', restored
    assert restored['summary']['after_cents'] == 4500
    final = storage.apply(saved.id, restored['preview_id'])
    rows = [e for d in final.itinerary.days for e in d.expenses if e.stop_id == 's0-1']
    assert len(rows) == 2 and {e.category for e in rows} == {'ticket', 'food'}


@pytest.mark.parametrize('mode', ['walking', 'taxi', 'driving', 'transit', 'bicycling'])
def test_all_existing_transport_modes_keep_truthful_type_and_exact_cost(setup, mode):
    store, plan, storage, state = setup
    plan.request.mode = mode
    plan.routes[0].mode = mode
    if mode == 'transit':
        plan.routes[0].fare_yuan = Decimal('3')
    if mode == 'taxi':
        plan.routes[0].taxi_cost_yuan = Decimal('12.50')
    with store.connect() as db:
        db.execute('UPDATE plans SET data=? WHERE id=?', (plan.model_dump_json(), plan.id))
    result = asyncio.run(preview(plan, state, command(plan, state), storage, bridge=FixedBridge()))
    assert result['outcome'] == 'provisional', result
    saved = storage.apply(plan.id, result['preview_id'])
    assert next(r for r in saved.routes if r.day_index == 0).mode == mode
    assert saved.validation.total_cents == result['summary']['after_cents']


def test_middle_missed_task_reconnects_source_neighbours_without_retiming(setup):
    store, plan, storage, state = setup
    third = plan.itinerary.days[0].stops[0].model_copy(deep=True)
    third.id, third.name, third.start, third.end, third.location = 'third', '合成源日第三点', '13:00', '14:00', (116.03, 39.0)
    plan.itinerary.days[0].stops.append(third)
    plan.itinerary.days[0].expenses.append(Expense(label='第三点票', category='ticket', stop_id='third', unit_cost=10))
    with store.connect() as db:
        db.execute('UPDATE plans SET data=? WHERE id=?', (plan.model_dump_json(), plan.id))
    def assignments(payload):
        return [*payload['baseline'], {'task_id': 's0-1', 'day_index': 0, 'start_min': 780, 'end_min': 840}]
    request = command(plan, state, day_indexes=[1], day_windows=[{'day_index': 1, 'start_min': 540, 'end_min': 840}], activities=[{'stop_id': 's0-1', 'status': 'missed'}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge(assignments), provider_factory=SyntheticRoads))
    assert result['outcome'] == 'provisional', result
    saved = storage.apply(plan.id, result['preview_id'])
    assert saved.itinerary.days[0].stops == [plan.itinerary.days[0].stops[0], third]
    assert any(r.day_index == 0 and r.from_id == 's0-0' and r.to_id == 'third' for r in saved.routes)
    assert saved.validation.total_cents == 5000


def test_ancestor_chain_prevents_old_grandchild_fork(setup):
    store, plan, storage, state = setup
    ordinary = plan.model_copy(deep=True)
    ordinary.id, ordinary.parent_id = 'ordinary-b', plan.id
    grandchild = ordinary.model_copy(deep=True)
    grandchild.id, grandchild.parent_id = 'ordinary-c', ordinary.id
    store.save(ordinary)
    store.save(grandchild)
    accepted = asyncio.run(preview(plan, state, command(plan, state), storage, bridge=FixedBridge()))
    saved = storage.apply(plan.id, accepted['preview_id'])
    descendant_state = storage.state(grandchild)
    assert descendant_state['state_revision'] == 1
    assert descendant_state['current_plan_id'] == saved.id
    with pytest.raises(ConstraintStale):
        assert_legacy_editable(store, grandchild)
    with pytest.raises(ConstraintStale):
        prepare(grandchild, descendant_state, command(grandchild, descendant_state))


def test_history_cycle_cannot_start_new_confirmation_chain(setup):
    store, plan, storage, state = setup
    child = plan.model_copy(deep=True)
    child.id, child.parent_id = 'cycle', plan.id
    plan.parent_id = child.id
    with store.connect() as db:
        db.execute('UPDATE plans SET data=? WHERE id=?', (plan.model_dump_json(), plan.id))
    store.save(child)
    with pytest.raises(ConstraintStale, match='循环'):
        storage.state(plan)


def test_completely_omitted_day_can_be_selected_again_from_task_pool(setup):
    store, plan, storage, state = setup
    request = command(plan, state, activities=[{'stop_id': s.id, 'status': 'missed', 'removable_cost': True} for s in plan.itinerary.days[0].stops])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    assert result['outcome'] == 'provisional', result
    saved = storage.apply(plan.id, result['preview_id'])
    assert saved.itinerary.days[0].stops == []
    current = storage.state(saved)
    view = asyncio.run(context(saved, current, FixedBridge()))
    assert view['days'][0]['supported'] and view['days'][0]['cities'] == ['北京']
    new_request = command(saved, current)
    catalog, states, city, active, errors = prepare(saved, current, new_request)
    assert set(active) == {'s0-0', 's0-1'} and not errors


def test_context_free_cancel_eligibility_matches_server_cost_rule(setup):
    store, plan, storage, state = setup
    plan.itinerary.days[0].expenses[1].category = 'food'
    result = asyncio.run(context(plan, state, FixedBridge()))
    eligibility = {s['stop_id']: s['removable_cost_allowed'] for s in result['stops']}
    assert eligibility['s0-0'] is True
    assert eligibility['s0-1'] is True
    request = command(plan, state, activities=[{'stop_id': 's0-1', 'removable_cost': True}])
    prepare(plan, state, request)
    # Planned food is recalculable; explicitly paid food remains in the ledger.
    plan.itinerary.days[0].expenses[1].commitment = 'paid'
    result = asyncio.run(context(plan, state, FixedBridge()))
    assert next(s for s in result['stops'] if s['stop_id'] == 's0-1')['removable_cost_allowed'] is False
    request = command(plan, state, activities=[{'stop_id': 's0-1', 'removable_cost': True}])
    with pytest.raises(ValueError, match='已付款'):
        prepare(plan, state, request)


def test_task_pool_eligibility_keeps_explicit_ticket_snapshot(setup):
    store, plan, storage, state = setup
    request = command(plan, state, activities=[{'stop_id': 's0-1', 'status': 'missed', 'removable_cost': True}])
    result = asyncio.run(preview(plan, state, request, storage, bridge=FixedBridge()))
    saved = storage.apply(plan.id, result['preview_id'])
    view = asyncio.run(context(saved, storage.state(saved), FixedBridge()))
    row = next(s for s in view['stops'] if s['stop_id'] == 's0-1')
    assert row['in_task_pool'] and row['removable_cost_allowed']


def test_no_solution_budget_message_names_amount_and_gap(setup):
    store, plan, storage, state = setup
    class NoSolution(FixedBridge):
        async def call(self, payload):
            result = await super().call(payload)
            return {**result, 'assignments': None, 'outcome': 'no_solution'}
    result = asyncio.run(preview(plan, state, command(plan, state, budget_cents=3000), storage, bridge=NoSolution()))
    assert result['outcome'] == 'no_solution_found' and 'preview_id' not in result
    message = next(e['message'] for e in result['errors'] if e['code'] == 'remaining_budget')
    assert '40.00' in message and '30.00' in message and '10.00' in message
    assert '内核' not in message and '快照' not in message
    assert result['summary']['after_cents'] == result['summary']['before_cents']


def test_real_native_current_lunch_after_completed_prefix_has_reference_road(setup):
    """Preserve completed activities while replanning synthetic lunch and roads."""
    from backend.constraint_bridge import constraint_planner
    from constraint_fixtures import seed, SyntheticRoads as DemoRoads
    store, ignored, storage, ignored_state = setup
    if not constraint_planner.executable.is_file():
        pytest.skip('native executable not built; not native acceptance')
    seed(store)
    plan = store.get('constraint-demo-progress')
    state = storage.state(plan)
    request = command(plan, state, day_indexes=[0, 1], mode='during', as_of='2026-10-23T11:30:00+08:00',
        day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 1260}], current_stop_id='d1s3', activities=[{'stop_id': 'd1s1', 'status': 'completed'},
                                          {'stop_id': 'd1s2', 'status': 'missed', 'priority': 'must'}])
    result = asyncio.run(preview(plan, state, request, storage, provider_factory=DemoRoads))
    assert result['outcome'] == 'provisional', result
    assert result['engine']['name'] == 'cangjie-journey-planner'
    assert result['summary']['after_cents'] == 110000
    assert not result['errors']
    saved = storage.apply(plan.id, result['preview_id'])
    completed = next(s for s in saved.itinerary.days[0].stops if s.id == 'd1s1')
    assert completed == plan.itinerary.days[0].stops[0]
    connection = next(r for r in saved.routes if r.day_index == 0 and r.from_id == 'd1s1' and r.to_id == 'd1s3')
    assert connection.available and connection.duration_s == 600
    assert '不证明实际走过' in connection.note
    assert any('不代表实际已走或已付款' in n for n in result['notices'])
    assert any(s.id == 'd1s2' for day in saved.itinerary.days for s in day.stops)
