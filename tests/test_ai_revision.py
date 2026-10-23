import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend import ai_revision as revision
from backend.constraint_storage import ConstraintStore, ConstraintStale, fingerprint
from backend.models import ConfirmedHotel, Expense, RouteLeg, RouteOption, Stop
from backend.storage import Store
from plan_samples import sample_plan


class OfflineMap:
    closed = False

    def __init__(self, cities):
        self.cities = cities

    async def enrich(self, itinerary, mode, **options):
        routes = []
        for index, day in enumerate(itinerary.days):
            if index not in options['day_indices']:
                continue
            for stop in day.stops:
                stop.location, stop.location_status = (116, 39), 'verified'
                stop.poi_id = stop.poi_id or 'offline-' + stop.id
                stop.matched_name = stop.place_name or stop.name
            for left, right in zip(day.stops, day.stops[1:]):
                routes.append(RouteLeg(day_index=index, from_id=left.id, to_id=right.id, mode='walking',
                    available=True, distance_m=100, duration_s=60, polyline=[(116, 39), (116.001, 39)],
                    queried_at=datetime.now(timezone.utc)))
        return routes

    async def close(self):
        self.__class__.closed = True


def setup(tmp_path, plan=None):
    plan = plan or sample_plan()
    store = Store(tmp_path / 'ai-revision.sqlite3')
    store.save(plan)
    storage = ConstraintStore(store.path)
    return plan, store, storage, storage.state(plan)


def body(plan, state, **kwargs):
    return revision.AIRevisionRequest(base_fingerprint=fingerprint(plan), expected_routing_revision=plan.routing_revision,
        expected_state_revision=state['state_revision'], instruction='增加一个具体景点并保留已有预订', **kwargs)


def generate(itinerary, **kwargs):
    return AsyncMock(return_value=revision.RevisionDraft(itinerary=itinerary, assistant_message='已调整，请预览。', **kwargs))


def test_ai_preview_works_without_native_limits_and_requires_apply(tmp_path, monkeypatch):
    plan = sample_plan(days=4)
    draft = plan.itinerary.model_copy(deep=True)
    for day_index, day in enumerate(draft.days):
        for n in range(2, 9):
            hour = 9 + n
            day.stops.append(Stop(id=f'new-{day_index}-{n}', name=f'合成新地点{n}', city='北京',
                                  start=f'{hour}:20', end=f'{hour}:40'))
        day.stops.sort(key=lambda s: s.start)
    plan, store, storage, state = setup(tmp_path, plan)
    result = asyncio.run(revision.ai_revision_preview(plan, state, body(plan, state), storage,
        generator=generate(draft), amap_factory=OfflineMap))
    preview = result['preview']
    assert preview['preview_id'] and preview['engine']['name'] == 'ai-revision', preview
    assert len(store.list()) == 1 and OfflineMap.closed
    child = storage.apply(plan.id, preview['preview_id'])
    assert child.parent_id == plan.id and child.revision == plan.revision + 1
    assert len(child.itinerary.days) == 4 and len(child.itinerary.days[0].stops) == 9
    assert len(store.list()) == 2 and storage.apply(plan.id, preview['preview_id']).id == child.id


def test_day_revision_retains_other_day_snapshots_and_paid_expenses(tmp_path):
    plan = sample_plan()
    plan.itinerary.days[0].expenses[0].commitment = 'paid'
    plan, store, storage, state = setup(tmp_path, plan)
    draft = plan.itinerary.model_copy(deep=True)
    draft.days[0].stops = draft.days[0].stops[1:]
    draft.days[0].expenses = []
    draft.days[1].stops[0].name = '不得修改其他日期'
    response = asyncio.run(revision.ai_revision_preview(plan, state, body(plan, state, day_index=0), storage,
        generator=generate(draft, request_patch={'budget': '1500'}), amap_factory=OfflineMap))
    child = storage.apply(plan.id, response['preview']['preview_id'])
    assert child.itinerary.days[1] == plan.itinerary.days[1]
    assert child.request.budget == 1500
    assert child.weather == plan.weather and child.weather_by_city == plan.weather_by_city
    assert child.itinerary.transfers == plan.itinerary.transfers
    paid = [e for d in child.itinerary.days for e in d.expenses if e.commitment == 'paid']
    assert len(paid) == 1 and paid[0].unit_cost == 10 and not paid[0].stop_id


def test_completed_stop_and_cost_are_not_rewritten(tmp_path):
    plan, store, storage, state = setup(tmp_path)
    sid = plan.itinerary.days[0].stops[0].id
    state['activities'][sid] = {'status': 'completed', 'locked': True}
    draft = plan.itinerary.model_copy(deep=True)
    draft.days[0].stops = draft.days[0].stops[1:]
    draft.days[0].expenses = [Expense(label='新估算', category='ticket', unit_cost=1, stop_id=sid)]
    response = asyncio.run(revision.ai_revision_preview(plan, state, body(plan, state), storage,
        generator=generate(draft), amap_factory=OfflineMap))
    assert response['preview'].get('preview_id'), response
    child = storage.apply(plan.id, response['preview']['preview_id'])
    restored = next(s for s in child.itinerary.days[0].stops if s.id == sid)
    assert (restored.start, restored.end, restored.name) == ('09:00', '10:00', plan.itinerary.days[0].stops[0].name)
    assert sum(e.unit_cost for e in child.itinerary.days[0].expenses if e.stop_id == sid) == 10
    assert storage.state(child)['activities'][sid]['status'] == 'completed'


def test_partial_ai_output_requeries_only_returned_dates(tmp_path):
    plan, store, storage, state = setup(tmp_path)
    draft = plan.itinerary.model_copy(deep=True)
    draft.days = [draft.days[1]]
    draft.days[0].stops[0].name = '合成替换景点'

    class SelectedDateMap(OfflineMap):
        async def enrich(self, itinerary, mode, **kwargs):
            assert kwargs['day_indices'] == {1}
            return await super().enrich(itinerary, mode, **kwargs)

    response = asyncio.run(revision.ai_revision_preview(plan, state, body(plan, state), storage,
        generator=generate(draft), amap_factory=SelectedDateMap))
    assert response['preview'].get('preview_id'), response
    child = storage.apply(plan.id, response['preview']['preview_id'])
    assert len(child.itinerary.days) == 2
    assert child.itinerary.days[0] == plan.itinerary.days[0]
    assert [r for r in child.routes if r.day_index == 0] == [r for r in plan.routes if r.day_index == 0]
    assert child.itinerary.days[1].stops[0].name == '合成替换景点'


def test_clarification_and_stale_revision_do_not_change_history(tmp_path):
    plan, store, storage, state = setup(tmp_path)
    ambiguous = AsyncMock(return_value=revision.RevisionDraft(unresolved=['请明确是哪一天。']))
    response = asyncio.run(revision.ai_revision_preview(plan, state, body(plan, state), storage, generator=ambiguous))
    assert response['preview'] is None and response['unresolved']
    stale = body(plan, state).model_copy(update={'expected_state_revision': 99})
    with pytest.raises(ConstraintStale):
        asyncio.run(revision.ai_revision_preview(plan, state, stale, storage, generator=ambiguous))
    assert ambiguous.await_count == 1 and len(store.list()) == 1


def test_selected_transport_and_confirmed_hotel_survive_ai_revision(tmp_path):
    plan = sample_plan()
    plan.request.confirmed_hotels = [ConfirmedHotel(city='北京', name='合成测试酒店',
        check_in='2026-11-02', check_out='2026-11-03', total_cost=200)]
    plan, store, storage, state = setup(tmp_path, plan)

    class WithTaxi(OfflineMap):
        async def enrich(self, itinerary, mode, **kwargs):
            result = await super().enrich(itinerary, mode, **kwargs)
            for route in result:
                route.alternatives.append(RouteOption(mode='taxi', available=True, distance_m=200,
                    duration_s=120, polyline=[(116, 39), (116.001, 39)]))
            return result

    response = asyncio.run(revision.ai_revision_preview(plan, state, body(plan, state, route_choices=[{
        'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'taxi'}]), storage,
        generator=generate(plan.itinerary), amap_factory=WithTaxi))
    assert response['preview'].get('preview_id'), response
    child = storage.apply(plan.id, response['preview']['preview_id'])
    assert child.request.confirmed_hotels == plan.request.confirmed_hotels
    assert any(s.booking_anchor.startswith('hotel_') for d in child.itinerary.days for s in d.stops)
    assert any(e.category == 'hotel' and e.unit_cost == 200 for d in child.itinerary.days for e in d.expenses)
    route = next(r for r in child.routes if r.from_id == 's0-0' and r.to_id == 's0-1')
    assert route.mode == 'taxi' and route.user_selected
    assert response['preview']['route_changes'][0]['after_mode'] == 'taxi'


def test_ai_context_and_route_use_independent_preview_path(tmp_path, monkeypatch):
    import backend.main as main
    import backend.planning_conversation_routes as routes
    plan, store, _, state = setup(tmp_path)
    monkeypatch.setattr(main, 'store', store)
    fake = AsyncMock(return_value={'assistant_message': '已调整', 'unresolved': [], 'proposal_summary': [], 'preview': None})
    monkeypatch.setattr(routes, 'ai_revision_preview', fake)
    with TestClient(main.app) as client:
        context = client.get('/api/plans/base/ai-adjust-context')
        assert context.status_code == 200 and context.json()['base_fingerprint'] == fingerprint(plan)
        response = client.post('/api/plans/base/ai-adjust-preview', json=body(plan, state).model_dump(mode='json'))
    assert response.status_code == 200 and fake.await_count == 1
    assert routes.active_conversations() == 0
