"""On-demand selected modes use bounded real-provider boundaries, never fake paths."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.constraint_bridge import ConstraintBridge
from backend.constraint_replan import (AmapConstraintProvider, build_projection, context, freeze_routes,
    prepare, preview, select_route_mode)
from backend.constraint_storage import ConstraintStore
from backend.models import Expense, RouteLeg, Stop
from backend.storage import Store
from test_constraint_replan import FixedBridge, command, sample_plan


class QueriedRoads:
    """Explicit synthetic provider injection; mode is recorded at the boundary."""
    instances = []
    fail_mode = None
    missing_geometry = False

    def __init__(self, city):
        self.calls, self.closed = [], False
        self.__class__.instances.append(self)

    async def route(self, before, after, mode, city):
        self.calls.append((before.id, after.id, mode, city))
        if mode == self.fail_mode:
            raise RuntimeError('合成不可用路线，不调用外部地图')
        return {'available': True, 'distance_m': 1000, 'duration_s': 300,
                'polyline': [] if self.missing_geometry else [before.location, after.location],
                'fare_yuan': Decimal('2') if mode == 'transit' else None,
                'taxi_cost_yuan': Decimal('12.50') if mode == 'taxi' else None,
                'provider_endpoint': 'synthetic-on-demand-route', 'note': '合成查询边界，仅测试程序流程'}

    async def close(self):
        self.closed = True


def setup(tmp_path, plan=None):
    store = Store(tmp_path / 'on-demand.sqlite3')
    plan = plan or sample_plan()
    store.save(plan)
    storage = ConstraintStore(store.path)
    return store, plan, storage, storage.state(plan)


def choice(mode='taxi', **fields):
    return {'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': mode, **fields}


def test_context_offers_five_modes_without_claiming_unqueried_options_available(tmp_path):
    _, plan, _, state = setup(tmp_path)
    result = asyncio.run(context(plan, state, FixedBridge()))
    route = result['routes'][0]
    assert route['can_query']
    assert {o['mode'] for o in route['options']} == {'walking', 'transit', 'taxi', 'driving', 'bicycling'}
    taxi = next(o for o in route['options'] if o['mode'] == 'taxi')
    assert not taxi['available'] and taxi['duration_s'] is None and taxi['cost_cents'] is None
    assert '尚未查询' in taxi['note']


def test_select_route_mode_remains_strict_except_explicit_deferred_query_path(tmp_path):
    _, plan, _, state = setup(tmp_path)
    route = plan.routes[0]
    with pytest.raises(ValueError, match='可用路线'):
        select_route_mode(route, 'taxi')
    pending = select_route_mode(route, 'taxi', allow_unqueried=True)
    assert pending.mode == 'taxi' and not pending.available and pending.duration_s is None
    assert pending.polyline == [] and pending.cost_estimate is None
    assert any(o.mode == 'walking' and o.available for o in pending.alternatives)
    assert route.mode == 'walking' and route.available
    with pytest.raises(ValueError, match='仅支持'):
        select_route_mode(route, 'helicopter', allow_unqueried=True)
    prepare(plan, state, command(plan, state, route_choices=[choice()]))


@pytest.mark.parametrize('mode', ['transit', 'taxi', 'driving', 'bicycling'])
def test_unqueried_selected_mode_is_queried_then_native_verified_before_apply(tmp_path, mode):
    store, plan, storage, state = setup(tmp_path)
    before = plan.model_dump_json()
    result = asyncio.run(preview(plan, state, command(plan, state, route_choices=[choice(mode)]), storage,
        bridge=ConstraintBridge(), provider_factory=QueriedRoads))
    assert result.get('preview_id'), result
    assert result['engine']['name'] == 'cangjie-journey-planner'
    assert any(call[:3] == ('s0-0', 's0-1', mode) for call in QueriedRoads.instances[-1].calls)
    assert QueriedRoads.instances[-1].closed and len(store.list()) == 1
    assert plan.model_dump_json() == before
    child = storage.apply(plan.id, result['preview_id'])
    selected = next(r for r in child.routes if r.day_index == 0)
    assert selected.mode == mode and selected.available and selected.user_selected
    assert any(o.mode == 'walking' and o.available for o in selected.alternatives)
    assert child.itinerary.days[1] == plan.itinerary.days[1]
    assert child.validation.total_cents == result['summary']['after_cents']


def test_confirmed_child_can_query_another_mode_and_keep_both_earlier_choices(tmp_path):
    _, plan, storage, state = setup(tmp_path)
    first = asyncio.run(preview(plan, state, command(plan, state, route_choices=[choice('taxi')]), storage,
        bridge=ConstraintBridge(), provider_factory=QueriedRoads))
    child = storage.apply(plan.id, first['preview_id'])
    current = storage.state(child)
    second = asyncio.run(preview(child, current, command(child, current, route_choices=[choice('transit')]), storage,
        bridge=ConstraintBridge(), provider_factory=QueriedRoads))
    assert second.get('preview_id'), second
    saved = storage.apply(child.id, second['preview_id'])
    route = next(r for r in saved.routes if r.day_index == 0)
    assert route.mode == 'transit' and {o.mode for o in route.alternatives} >= {'walking', 'taxi'}
    assert saved.validation.total_cents == 4400


@pytest.mark.parametrize('failure', ['network', 'geometry', 'no_provider'])
def test_query_failure_never_uses_the_previous_walking_route_or_changes_facts(tmp_path, failure):
    store, plan, storage, state = setup(tmp_path)
    class FailedRoads(QueriedRoads):
        fail_mode = 'taxi' if failure == 'network' else None
        missing_geometry = failure == 'geometry'
    raw = plan.model_dump_json()
    result = asyncio.run(preview(plan, state, command(plan, state, route_choices=[choice('taxi')]), storage,
        bridge=FixedBridge(), provider_factory=None if failure == 'no_provider' else FailedRoads))
    assert 'preview_id' not in result and result['errors'], result
    assert plan.model_dump_json() == raw and len(store.list()) == 1
    assert storage.state(plan)['activities'] == {}


def test_existing_selected_snapshot_is_reused_without_querying_that_mode_again(tmp_path):
    from backend.models import RouteOption
    _, plan, storage, state = setup(tmp_path)
    plan.routes[0].alternatives = [RouteOption(mode='taxi', available=True, distance_m=800,
        duration_s=180, polyline=plan.routes[0].polyline, taxi_cost_yuan=10)]
    class CountRoads(QueriedRoads):
        pass
    result = asyncio.run(preview(plan, state, command(plan, state, route_choices=[choice()]), storage,
        bridge=FixedBridge(), provider_factory=CountRoads))
    assert result.get('preview_id'), result
    assert not any(call[:3] == ('s0-0', 's0-1', 'taxi') for call in CountRoads.instances[-1].calls)


@pytest.mark.parametrize('condition', ['unknown_endpoint', 'nonadjacent', 'completed'])
def test_query_permission_does_not_relax_location_adjacency_or_completed_protection(tmp_path, condition):
    _, plan, _, state = setup(tmp_path)
    activities = []
    if condition == 'unknown_endpoint':
        plan.itinerary.days[0].stops[0].location_status = 'unknown'
    elif condition == 'nonadjacent':
        plan.itinerary.days[0].stops.insert(1, Stop(id='between', name='其它活动', city='北京', start='10:15', end='10:30',
            poi_id='between-poi', location=(116.005, 39), location_status='verified'))
    else:
        activities = [{'stop_id': 's0-0', 'status': 'completed'}, {'stop_id': 's0-1', 'status': 'completed'}]
    with pytest.raises(ValueError, match='核实|相邻|已完成'):
        prepare(plan, state, command(plan, state, route_choices=[choice()], activities=activities))


def test_requested_edge_is_first_within_the_original_twenty_query_limit(tmp_path):
    plan = sample_plan(3)
    plan.routes = []
    for index, day in enumerate(plan.itinerary.days):
        day.stops = [Stop(id=f'd{index}-{n}', name=f'合成点{index}-{n}', city='北京', start=f'{9+n:02d}:00',
            end=f'{9+n:02d}:20', poi_id=f'p{index}-{n}', location=(116+n*.001, 39+index*.01),
            location_status='verified') for n in range(8)]
        day.expenses = []
    a, b = plan.itinerary.days[2].stops[:2]
    plan.routes = [RouteLeg(day_index=2, from_id=a.id, to_id=b.id, mode='walking', available=True,
        distance_m=100, duration_s=60, polyline=[a.location, b.location], queried_at=datetime.now(timezone.utc))]
    _, plan, _, state = setup(tmp_path, plan)
    body = command(plan, state, day_indexes=[0, 1, 2], day_windows=[{'day_index':i,'start_min':540,'end_min':1300} for i in range(3)],
        route_choices=[choice('taxi', day_index=2, from_id=a.id, to_id=b.id)])
    catalog, states, city, active, _ = prepare(plan, state, body)
    payload, reverse, anchors, _ = build_projection(plan, body, catalog, states, city, active)
    class LimitedRoads(QueriedRoads):
        pass
    frozen, roads, count = asyncio.run(freeze_routes(plan, payload, reverse, anchors, catalog, city,
        LimitedRoads, states=states, route_choices=body.route_choices))
    assert count == len(LimitedRoads.instances[-1].calls) == 20
    assert LimitedRoads.instances[-1].calls[0][:3] == (a.id, b.id, 'taxi')
    assert roads[(2, a.id, b.id)].mode == 'taxi'
    assert any(r['time_state'] == 'unknown' for r in frozen)


def test_real_amap_adapter_marks_success_only_after_provider_returns_validated_metrics():
    from types import SimpleNamespace
    service = AmapConstraintProvider('北京')
    original = service.client
    service.client = SimpleNamespace(route_in_city=AsyncMock(return_value={
        'distance_m': 500, 'duration_s': 300, 'polyline': [(116, 39), (116.01, 39)]}), close=AsyncMock())
    try:
        data = asyncio.run(service.route(SimpleNamespace(location=(116,39)),
            SimpleNamespace(location=(116.01,39)), 'transit', '北京'))
        assert data['available'] and data['duration_s'] == 300
    finally:
        asyncio.run(original.close())
