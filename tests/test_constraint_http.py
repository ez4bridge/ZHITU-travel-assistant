"""Offline HTTP/native integration and legacy-write race protection."""
import asyncio

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from backend import main, constraint_routes, constraint_replan
from backend.constraint_storage import ConstraintStore
from backend.storage import Store
from test_constraint_replan import sample_plan, command


@pytest.fixture
def local_store(tmp_path, monkeypatch):
    store = Store(tmp_path / 'plans.sqlite3')
    plan = sample_plan()
    store.save(plan)
    monkeypatch.setattr(main, 'store', store)
    return store, plan


def test_real_native_http_preview_apply_and_legacy_protection(local_store):
    store, base = local_store
    original = base.model_dump(mode='json')
    with TestClient(main.app) as client:
        context = client.get(f'/api/plans/{base.id}/constraint-context').json()
        assert context['engine']['available'], context['engine']['diagnostic']
        assert context['engine']['version'] == '2.2'
        body = command(base, ConstraintStore(store.path).state(base)).model_dump(mode='json')
        response = client.post(f'/api/plans/{base.id}/constraint-preview', json=body)
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['engine']['name'] == 'cangjie-journey-planner'
        assert preview['engine']['version'] == '2.2'
        assert preview['preview_id'] and not preview['errors']
        assert len(store.list()) == 1
        saved = client.post(f'/api/plans/{base.id}/constraint-apply', json={'preview_id': preview['preview_id']})
        assert saved.status_code == 200, saved.text
        child = saved.json()['plan']
        assert child['parent_id'] == base.id and child['id'] != base.id
        assert store.get(base.id).model_dump(mode='json') == original
        assert child['itinerary']['days'][1] == original['itinerary']['days'][1]
        again = client.post(f'/api/plans/{base.id}/constraint-apply', json={'preview_id': preview['preview_id']})
        assert again.json()['plan']['id'] == child['id'] and len(store.list()) == 2
        latest = client.get(f'/api/plans/{base.id}/constraint-context').json()
        assert latest['current_plan_id'] == child['id'] and latest['state_revision'] == 1
        for identifier in (base.id, child['id']):
            assert client.delete(f'/api/plans/{identifier}').status_code == 409
            assert store.get(identifier) is not None
        # No LLM call: these requests must fail before starting ordinary editing.
        for suffix, payload in [('revise', {'instruction': 'move everything'}), ('refresh', {}),
                                ('route', {'day_index': 0, 'from_id': 's0-0', 'to_id': 's0-1', 'mode': 'walking'})]:
            blocked = client.post(f'/api/plans/{child["id"]}/{suffix}', json=payload)
            assert blocked.status_code == 409, blocked.text


def test_late_ordinary_write_cannot_fork_or_reflow_confirmed_chain(local_store):
    store, base = local_store
    with TestClient(main.app) as client:
        body = command(base, ConstraintStore(store.path).state(base)).model_dump(mode='json')
        preview = client.post(f'/api/plans/{base.id}/constraint-preview', json=body).json()
        applied = client.post(f'/api/plans/{base.id}/constraint-apply', json={'preview_id': preview['preview_id']})
        assert applied.status_code == 200, applied.text
    # Represents an old model/refresh task which passed its initial guard before apply.
    late = base.model_copy(deep=True)
    late.id, late.parent_id = 'late-ordinary-child', base.id
    with pytest.raises(ValueError, match='已确认约束'):
        store.save(late)
    late_routes = base.model_copy(deep=True)
    late_routes.routing_revision += 1
    with pytest.raises(ValueError, match='已确认约束'):
        store.update_routes(late_routes, base.routing_revision)
    assert store.get(late.id) is None and store.get(base.id).routing_revision == 0


def test_cancelled_browser_preview_cancels_work_and_releases_slot(local_store, monkeypatch):
    store, base = local_store
    released = []

    async def never_complete(*args, **kwargs):
        try:
            await asyncio.sleep(60)
        finally:
            released.append(True)

    class Disconnected:
        async def is_disconnected(self):
            return True

    monkeypatch.setattr(constraint_replan, 'preview', never_complete)
    route = next(route for route in main.app.routes if getattr(route, 'path', '') == '/api/plans/{plan_id}/constraint-preview')
    body = command(base, ConstraintStore(store.path).state(base))

    async def run():
        with pytest.raises(HTTPException) as error:
            await route.endpoint(base.id, body, Disconnected())
        assert error.value.status_code == 499

    asyncio.run(run())
    assert released == [True] and constraint_routes.active_constraint_previews() == 0
    assert len(store.list()) == 1


def test_preexisting_ordinary_descendants_cannot_bypass_ancestor_facts(local_store):
    store, base = local_store
    previous = base
    for identifier in ('old-branch-b', 'old-branch-c'):
        child = previous.model_copy(deep=True)
        child.id, child.parent_id = identifier, previous.id
        store.save(child)
        previous = child
    with TestClient(main.app) as client:
        body = command(base, ConstraintStore(store.path).state(base)).model_dump(mode='json')
        preview = client.post(f'/api/plans/{base.id}/constraint-preview', json=body).json()
        applied = client.post(f'/api/plans/{base.id}/constraint-apply', json={'preview_id': preview['preview_id']})
        assert applied.status_code == 200, applied.text
    late = previous.model_copy(deep=True)
    late.id, late.parent_id = 'old-branch-d', previous.id
    with pytest.raises(ValueError, match='已确认约束'):
        store.save(late)
    with pytest.raises(ValueError, match='已确认约束'):
        store.update_routes(previous, previous.routing_revision)
    with pytest.raises(ValueError, match='已确认约束'):
        store.delete('old-branch-b')
    assert store.get(late.id) is None


def test_parallel_preview_limit_returns_clear_error(local_store, monkeypatch):
    store, base = local_store
    monkeypatch.setattr(constraint_routes, '_active_previews', 2)
    with TestClient(main.app) as client:
        body = command(base, ConstraintStore(store.path).state(base)).model_dump(mode='json')
        response = client.post(f'/api/plans/{base.id}/constraint-preview', json=body)
        assert response.status_code == 429
        health = client.get('/api/health').json()
        assert health['tasks']['constraint_active'] == 2 and health['tasks']['active'] >= 2
