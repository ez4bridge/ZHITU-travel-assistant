"""Real-native dialogue regression with explicit offline geocoding and roads."""
import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from backend import planning_conversation as conversation
from backend.planning_conversation import ConversationRequest, Proposal, NewPlace, Modification, Message
from backend.constraint_bridge import ConstraintBridge
from backend.constraint_storage import ConstraintStore, ConstraintStale
from backend.models import Expense, Stop, RouteLeg
from backend.storage import Store
from test_constraint_replan import sample_plan, command, SyntheticRoads


class SyntheticPlaceMap:
    instances = []
    ambiguous = False

    def __init__(self, cities):
        self.closed = False
        self.__class__.instances.append(self)

    async def resolve(self, stop):
        if self.ambiguous:
            stop.location_status, stop.location_note = 'unknown', '同名地点不唯一，请明确分店'
        else:
            stop.location, stop.location_status, stop.poi_id = (116.02, 39), 'verified', 'offline-new-poi'
            stop.matched_name = stop.name

    async def close(self):
        self.closed = True


def setup_conversation(tmp_path, plan=None):
    plan = plan or sample_plan()
    store = Store(tmp_path / 'conversation.sqlite3')
    store.save(plan)
    storage = ConstraintStore(store.path)
    return store, plan, storage, storage.state(plan)


def new_place(**changes):
    return NewPlace.model_validate({'name': '合成新景点', 'city': '北京', 'day_index': 0,
        'duration_min': 30, 'estimated_total_yuan': '25', 'priority': 'must', **changes})


def run_preview(plan, storage, state, proposal, *, amap_factory=SyntheticPlaceMap, instruction='按本次要求调整', messages=(), **changes):
    body = ConversationRequest(command=command(plan, state, **changes), instruction=instruction, messages=list(messages))
    async def interpreter(*args):
        return proposal
    return asyncio.run(conversation.conversation_preview(plan, state, body, storage, interpreter=interpreter,
        amap_factory=amap_factory, bridge=ConstraintBridge(), provider_factory=SyntheticRoads))


def test_real_native_add_cancel_reprices_then_apply_preserves_parent(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    raw = plan.model_dump(mode='json')
    proposal = Proposal(patch={'activities': [{'stop_id': 's0-0', 'status': 'cancelled'}]}, additions=[new_place()])
    result = run_preview(plan, storage, state, proposal, day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 840}])
    assert not result['unresolved'] and result['preview'].get('preview_id'), result
    preview = result['preview']
    assert preview['engine']['name'] == 'cangjie-journey-planner'
    assert preview['summary']['before_cents'] == 4000 and preview['summary']['after_cents'] == 5500
    assert any('added' in row['fields'] and row['before_day'] is None for row in preview['changes'])
    assert any('removed' in row['fields'] and row['stop_id'] == 's0-0' for row in preview['changes'])
    assert len(store.list()) == 1 and plan.model_dump(mode='json') == raw
    child = storage.apply(plan.id, preview['preview_id'])
    assert child.parent_id == plan.id and child.revision == plan.revision + 1
    assert child.itinerary.days[1] == plan.itinerary.days[1]
    assert not any(s.id == 's0-0' for d in child.itinerary.days for s in d.stops)
    assert len([s for s in child.itinerary.days[0].stops if s.name == '合成新景点']) == 1
    assert storage.apply(plan.id, preview['preview_id']).id == child.id
    assert store.get(plan.id).model_dump(mode='json') == raw
    assert SyntheticPlaceMap.instances[-1].closed


@pytest.mark.parametrize('commitment', ['paid', 'committed'])
def test_cancel_retains_paid_or_committed_cost_even_when_activity_removed(tmp_path, commitment):
    store, plan, storage, state = setup_conversation(tmp_path)
    plan.itinerary.days[0].expenses[0].commitment = commitment
    # Parent evidence saved with actual commitment before computing its CAS.
    with store.connect() as db:
        db.execute('UPDATE plans SET data=? WHERE id=?', (plan.model_dump_json(), plan.id))
    result = run_preview(plan, storage, state, Proposal(patch={'activities': [{'stop_id': 's0-0', 'status': 'cancelled'}]}))
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    assert any(e.stop_id == 's0-0' and e.commitment == commitment for d in child.itinerary.days for e in d.expenses)
    assert result['preview']['summary']['after_cents'] == 4000


def test_structured_cancellation_removes_linked_unspent_food_without_checkbox(tmp_path):
    plan = sample_plan()
    plan.itinerary.days[0].stops[0].kind = 'meal'
    plan.itinerary.days[0].expenses[0].category = 'food'
    store, plan, storage, state = setup_conversation(tmp_path, plan)
    result = run_preview(plan, storage, state, Proposal(), instruction='',
        activities=[{'stop_id': 's0-0', 'status': 'cancelled', 'removable_cost': False}])
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    assert not any(e.stop_id == 's0-0' for d in child.itinerary.days for e in d.expenses)
    assert result['preview']['summary']['after_cents'] == 3000


def test_structured_retain_cost_preserves_forecast_without_claiming_paid(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    result = run_preview(plan, storage, state, Proposal(), instruction='',
        activities=[{'stop_id': 's0-0', 'status': 'cancelled', 'retain_cost': True}])
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    fee = next(e for d in child.itinerary.days for e in d.expenses if e.stop_id == 's0-0')
    assert fee.commitment == 'planned' and result['preview']['summary']['after_cents'] == 4000


def test_booking_reference_cost_is_never_released_by_dialogue_cancel(tmp_path):
    plan = sample_plan()
    plan.itinerary.days[0].expenses[0].booking_ref = 'user-confirmed-order'
    store, plan, storage, state = setup_conversation(tmp_path, plan)
    result = run_preview(plan, storage, state, Proposal(patch={'activities': [{'stop_id': 's0-0', 'status': 'cancelled'}]}))
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    assert any(e.booking_ref == 'user-confirmed-order' for d in child.itinerary.days for e in d.expenses)
    assert result['preview']['summary']['after_cents'] == 4000


def test_natural_cancel_but_paid_keeps_cost_as_user_declaration_without_receipt(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    body = ConversationRequest(command=command(plan, state), instruction='取消合成地点0-0但已付款')
    result = asyncio.run(conversation.conversation_preview(plan, state, body, storage, amap_factory=SyntheticPlaceMap,
        bridge=ConstraintBridge(), provider_factory=SyntheticRoads))
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    fee = next(e for d in child.itinerary.days for e in d.expenses if e.stop_id == 's0-0')
    assert fee.commitment == 'planned' and result['preview']['summary']['after_cents'] == 4000
    assert storage.state(child)['activities']['s0-0']['retain_cost'] is True


@pytest.mark.parametrize('kind,category', [('meal', 'food'), ('hotel', 'hotel')])
@pytest.mark.parametrize('cancel_all', [False, True])
def test_shared_unbound_forecast_only_released_when_all_related_items_cancelled(tmp_path, kind, category, cancel_all):
    plan = sample_plan()
    day = plan.itinerary.days[0]
    for stop in day.stops:
        stop.kind = kind
    for fee in day.expenses:
        fee.category = category
    day.expenses.append(Expense(label='整日共享预算预留', category=category, unit_cost=100, basis='group'))
    store, plan, storage, state = setup_conversation(tmp_path, plan)
    choices = [{'stop_id': 's0-0', 'status': 'cancelled'}]
    if cancel_all:
        choices.append({'stop_id': 's0-1', 'status': 'cancelled'})
    result = run_preview(plan, storage, state, Proposal(patch={'activities': choices}))
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    assert result['preview']['summary']['after_cents'] == (2000 if cancel_all else 13000)
    assert any(e.label == '整日共享预算预留' for d in child.itinerary.days for e in d.expenses) is not cancel_all
    if not cancel_all:
        assert any('保留共享预算' in notice and '不按比例扣减' in notice for notice in result['preview']['notices'])


def test_geocoder_ambiguity_requires_followup_without_native_or_writes(tmp_path):
    class AmbiguousMap(SyntheticPlaceMap):
        ambiguous = True
    class NeverCalledNative:
        async def ready(self):
            raise AssertionError('Ambiguous place must not reach native execution')
    store, plan, storage, state = setup_conversation(tmp_path)
    body = ConversationRequest(command=command(plan, state), instruction='加一个同名餐馆')
    async def interpreter(*args):
        return Proposal(additions=[new_place()])
    result = asyncio.run(conversation.conversation_preview(plan, state, body, storage,
        interpreter=interpreter, amap_factory=AmbiguousMap, bridge=NeverCalledNative()))
    assert result['preview'] is None and any('同名' in u for u in result['unresolved'])
    assert len(store.list()) == 1 and AmbiguousMap.instances[-1].closed


@pytest.mark.parametrize('existing', [False, True])
def test_duplicate_same_city_poi_needs_clarification_without_double_charge(tmp_path, existing):
    class SamePlaceMap(SyntheticPlaceMap):
        async def resolve(self, stop):
            await super().resolve(stop)
            if existing:
                stop.poi_id = 'poi0-0'
    class NeverCalledNative:
        async def ready(self):
            raise AssertionError('Duplicate POI must not reach native execution')
    store, plan, storage, state = setup_conversation(tmp_path)
    async def interpreter(*args):
        return Proposal(additions=[new_place()] if existing else [new_place(), new_place(name='同一景点另一名称')])
    body = ConversationRequest(command=command(plan, state), instruction='增加这些地点')
    result = asyncio.run(conversation.conversation_preview(plan, state, body, storage, interpreter=interpreter,
        amap_factory=SamePlaceMap, bridge=NeverCalledNative()))
    assert result['preview'] is None and any('同一POI' in message for message in result['unresolved'])
    assert len(store.list()) == 1 and SamePlaceMap.instances[-1].closed


def test_followup_messages_reach_interpreter_without_mutating_parent(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    seen = []
    async def interpreter(plan, state, body):
        seen.append([(m.role, m.content) for m in body.messages])
        if not body.messages:
            return Proposal(reply='想加哪一家？', unresolved=['请明确地点全称'])
        return Proposal(additions=[new_place()], reply='已按完整地点加入预览')
    first = ConversationRequest(command=command(plan, state), instruction='加个景点')
    response = asyncio.run(conversation.conversation_preview(plan, state, first, storage, interpreter=interpreter,
        bridge=ConstraintBridge(), amap_factory=SyntheticPlaceMap, provider_factory=SyntheticRoads))
    assert response['preview'] is None and response['unresolved']
    messages = [Message(role='user', content='加个景点'), Message(role='assistant', content=response['assistant_message'])]
    second = ConversationRequest(command=command(plan, state, day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 840}]),
        instruction='合成新景点，半小时，全员预留25元', messages=messages)
    response = asyncio.run(conversation.conversation_preview(plan, state, second, storage, interpreter=interpreter,
        bridge=ConstraintBridge(), amap_factory=SyntheticPlaceMap, provider_factory=SyntheticRoads))
    assert response['preview'].get('preview_id'), response
    assert seen == [[], [('user', '加个景点'), ('assistant', '想加哪一家？')]] and len(store.list()) == 1


def test_all_cancelled_window_can_be_empty_without_resurrecting_sentinel(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    result = run_preview(plan, storage, state, Proposal(patch={'activities': [
        {'stop_id': 's0-0', 'status': 'cancelled'}, {'stop_id': 's0-1', 'status': 'cancelled'}]}))
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    assert child.itinerary.days[0].stops == [] and child.itinerary.days[1] == plan.itinerary.days[1]
    assert result['preview']['summary']['after_cents'] == 2000


def test_duration_modification_is_visible_and_completed_protection_rejects(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    result = run_preview(plan, storage, state, Proposal(modifications=[Modification(stop_id='s0-0', duration_min=30)]))
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    changed = next(s for s in child.itinerary.days[0].stops if s.id == 's0-0')
    assert int(changed.end[:2]) * 60 + int(changed.end[3:]) - int(changed.start[:2]) * 60 - int(changed.start[3:]) == 30
    latest = storage.state(child)
    latest['activities']['s0-0']['status'] = 'completed'
    with pytest.raises(ValueError, match='已完成'):
        run_preview(child, storage, latest, Proposal(modifications=[Modification(stop_id='s0-0', duration_min=45)]))


def test_preview_apply_rechecks_original_parent_cas(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    result = run_preview(plan, storage, state, Proposal(patch={'activities': [{'stop_id': 's0-0', 'status': 'cancelled'}]}))
    assert result['preview'].get('preview_id'), result
    plan.routing_revision += 1
    with store.connect() as db:
        db.execute('UPDATE plans SET data=? WHERE id=?', (plan.model_dump_json(), plan.id))
    with pytest.raises(ConstraintStale):
        storage.apply(plan.id, result['preview']['preview_id'])
    assert len(store.list()) == 1


def test_conversation_fee_notice_and_budget_conflict_use_recalculation_semantics(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    result = run_preview(plan, storage, state, Proposal(patch={'budget_cents': 1}))
    preview = result['preview']
    assert not preview.get('preview_id') and preview['errors']
    assert all(not notice.startswith('费用默认保留；') for notice in preview['notices'])
    assert any('未发生预算' in notice and '已承诺' in notice for notice in preview['notices'])
    assert any('已按本次选择重算未发生预算' in row['message'] for row in preview['errors'])
    assert all('取消活动不会自动免除费用' not in row['message'] for row in [*preview['errors'], *preview['checks']])


def test_conversation_cancel_cannot_bypass_existing_activity_time_lock(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    state['activities']['s0-0'] = {'stop_id': 's0-0', 'status': 'planned', 'priority': 'must',
        'locked': True, 'removable_cost': False, 'retain_cost': False}
    with pytest.raises(ValueError, match='锁定|解除'):
        run_preview(plan, storage, state, Proposal(patch={'activities': [{'stop_id': 's0-0', 'status': 'cancelled'}]}))


def test_omitted_structured_lock_field_does_not_imply_unlock(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    state['activities']['s0-0'] = {'stop_id': 's0-0', 'status': 'planned', 'priority': 'must',
        'locked': True, 'removable_cost': False, 'retain_cost': False}
    with pytest.raises(ValueError, match='锁定|解除'):
        run_preview(plan, storage, state, Proposal(), instruction='', activities=[{'stop_id': 's0-0', 'status': 'cancelled'}])


def test_following_conversation_keeps_prior_cancelled_facts_and_source_snapshots(tmp_path):
    import copy
    store, plan, storage, state = setup_conversation(tmp_path)
    first = run_preview(plan, storage, state, Proposal(patch={'activities': [{'stop_id': 's0-0', 'status': 'cancelled'}]}))
    child = storage.apply(plan.id, first['preview']['preview_id'])
    current = storage.state(child)
    previous = copy.deepcopy(current['activities']['s0-0'])
    unchanged_state = copy.deepcopy(current)
    assert previous['status'] == 'cancelled' and previous['_stop']['id'] == 's0-0' and previous['_expenses']
    second = run_preview(child, storage, current, Proposal(additions=[new_place()]))
    assert second['preview'].get('preview_id'), second
    assert current == unchanged_state
    final = storage.apply(child.id, second['preview']['preview_id'])
    latest = storage.state(final)
    assert latest['activities']['s0-0'] == previous
    added = next(s for s in final.itinerary.days[0].stops if s.name == '合成新景点')
    assert '_addition' not in latest['activities'][added.id]


def test_explicit_unlock_allows_cancelling_ordinary_activity_only(tmp_path):
    store, plan, storage, state = setup_conversation(tmp_path)
    state['activities']['s0-0'] = {'stop_id': 's0-0', 'status': 'planned', 'priority': 'must',
        'locked': True, 'removable_cost': False, 'retain_cost': False}
    result = run_preview(plan, storage, state, Proposal(patch={'activities': [
        {'stop_id': 's0-0', 'status': 'cancelled', 'locked': False}]}))
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    assert not any(s.id == 's0-0' for d in child.itinerary.days for s in d.stops)


@pytest.mark.parametrize('status', ['completed', 'missed'])
def test_requirements_cannot_smuggle_unconfirmed_progress_in_structured_choices(tmp_path, status):
    store, plan, storage, state = setup_conversation(tmp_path)
    with pytest.raises(ValueError, match='途中|进度'):
        run_preview(plan, storage, state, Proposal(), instruction='', activities=[{'stop_id': 's0-0', 'status': status}])


def test_conversation_http_uses_original_parent_and_rejects_unknown_activity(tmp_path, monkeypatch):
    from backend import main
    from backend import planning_conversation_routes as conversation_routes
    store, plan, storage, state = setup_conversation(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    original = conversation_routes.conversation_preview
    async def interpreter(*args):
        return Proposal(patch={'activities': [{'stop_id': 's0-0', 'status': 'cancelled'}]}, additions=[new_place()])
    async def offline_preview(*args, **kwargs):
        return await original(*args, **kwargs, interpreter=interpreter, amap_factory=SyntheticPlaceMap,
            provider_factory=SyntheticRoads, bridge=ConstraintBridge())
    monkeypatch.setattr(conversation_routes, 'conversation_preview', offline_preview)
    with TestClient(main.app) as client:
        body = ConversationRequest(command=command(plan, state, day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 840}]),
            instruction='把第一个地点换成合成新景点').model_dump(mode='json')
        response = client.post(f'/api/plans/{plan.id}/conversation-preview', json=body)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data['preview']['preview_id'] and data['preview']['engine']['name'] == 'cangjie-journey-planner'
        assert len(store.list()) == 1
        # An unknown id stays rejected even if an interpreter yields another valid patch.
        malicious = ConversationRequest(command=command(plan, state, activities=[{'stop_id': 'injected-id', 'status': 'cancelled'}]),
            instruction='取消未知地点').model_dump(mode='json')
        before_map = len(SyntheticPlaceMap.instances)
        assert client.post(f'/api/plans/{plan.id}/conversation-preview', json=malicious).status_code == 422
        assert len(SyntheticPlaceMap.instances) == before_map
        assert client.post(f'/api/plans/{plan.id}/conversation-preview', json={**body, 'verified_location': [116, 39]}).status_code == 422
        assert client.get('/api/health').json()['tasks']['conversation_active'] == 0
        child = client.post(f'/api/plans/{plan.id}/constraint-apply', json={'preview_id': data['preview']['preview_id']})
        assert child.status_code == 200 and child.json()['plan']['parent_id'] == plan.id
        assert len(store.list()) == 2


def test_conversation_http_busy_guard_includes_health_and_preserves_history(tmp_path, monkeypatch):
    from backend import main
    from backend import planning_conversation_routes as conversation_routes
    store, plan, storage, state = setup_conversation(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(conversation_routes, '_active', 2)
    with TestClient(main.app) as client:
        body = ConversationRequest(command=command(plan, state), instruction='调整行程').model_dump(mode='json')
        response = client.post(f'/api/plans/{plan.id}/conversation-preview', json=body)
        assert response.status_code == 429 and len(store.list()) == 1
        health = client.get('/api/health').json()['tasks']
        assert health['conversation_active'] == 2 and health['active'] >= 2


def test_conversation_disconnection_cancels_work_and_releases_health_slot(tmp_path, monkeypatch):
    from backend import main
    from backend import planning_conversation_routes as conversation_routes
    store, plan, storage, state = setup_conversation(tmp_path)
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
    monkeypatch.setattr(conversation_routes, 'conversation_preview', never_complete)
    route = next(r for r in main.app.routes if getattr(r, 'path', '') == '/api/plans/{plan_id}/conversation-preview')
    async def run():
        with pytest.raises(HTTPException) as error:
            await route.endpoint(plan.id, ConversationRequest(command=command(plan, state), instruction='调整行程'), Disconnected())
        assert error.value.status_code == 499
    asyncio.run(run())
    assert cancelled == [True] and conversation_routes.active_conversations() == 0
    assert len(store.list()) == 1


def test_replace_one_activity_at_eight_capacity_uses_real_native(tmp_path):
    plan = sample_plan()
    first = plan.itinerary.days[0]
    first.stops = [Stop(id=f'cap-{n}', name=f'合成八项{n}', city='北京', kind='sight',
        start=f'{9 + n // 2:02d}:{"00" if n % 2 == 0 else "30"}',
        end=f'{9 + n // 2:02d}:{"20" if n % 2 == 0 else "50"}',
        location=(116 + n * .001, 39), location_status='verified', poi_id=f'cap-poi-{n}') for n in range(8)]
    first.expenses = [Expense(label=s.name + '预算', category='ticket', unit_cost=10, basis='group', stop_id=s.id) for s in first.stops]
    plan.routes = [r for r in plan.routes if r.day_index != 0]
    plan.routes += [RouteLeg(day_index=0, from_id=a.id, to_id=b.id, mode='walking', available=True,
        distance_m=500, duration_s=300, polyline=[a.location, b.location], queried_at=datetime.now(timezone.utc))
        for a, b in zip(first.stops, first.stops[1:])]
    store, plan, storage, state = setup_conversation(tmp_path, plan)
    result = run_preview(plan, storage, state, Proposal(patch={'activities': [{'stop_id': 'cap-0', 'status': 'cancelled'}]},
        additions=[new_place(duration_min=20)]), day_windows=[{'day_index': 0, 'start_min': 540, 'end_min': 840}])
    assert result['preview'].get('preview_id'), result
    child = storage.apply(plan.id, result['preview']['preview_id'])
    assert len(child.itinerary.days[0].stops) == 8 and all(s.id != 'cap-0' for s in child.itinerary.days[0].stops)
    assert any(s.name == '合成新景点' for s in child.itinerary.days[0].stops)
