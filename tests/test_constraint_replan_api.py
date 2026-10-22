"""Offline HTTP contract regressions with an isolated Store and fixed native double."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend import constraint_replan
from backend.constraint_routes import install_constraint_routes
from backend.constraint_storage import fingerprint
from backend.storage import Store
from test_constraint_replan import FixedBridge, sample_plan


@pytest.fixture
def http_setup(tmp_path, monkeypatch):
    store = Store(tmp_path / 'http.sqlite3')
    plan = sample_plan()
    store.save(plan)
    monkeypatch.setattr(constraint_replan, 'constraint_planner', FixedBridge())
    app = FastAPI()
    install_constraint_routes(app, lambda: store)
    with TestClient(app) as client:
        yield client, store, plan


def request_body(plan):
    return {'schema_version': '1.0', 'base_fingerprint': fingerprint(plan), 'expected_routing_revision': 0,
            'expected_state_revision': 0, 'day_indexes': [0], 'mode': 'requirements', 'activities': []}


def test_http_context_preview_apply_and_duplicate_apply(http_setup):
    client, store, plan = http_setup
    context = client.get('/api/plans/base/constraint-context')
    assert context.status_code == 200 and context.json()['schema_version'] == '1.0'
    result = client.post('/api/plans/base/constraint-preview', json=request_body(plan))
    assert result.status_code == 200, result.text
    preview_id = result.json()['preview_id']
    assert len(store.list()) == 1
    applied = client.post('/api/plans/base/constraint-apply', json={'preview_id': preview_id})
    assert applied.status_code == 200, applied.text
    assert applied.json()['plan']['parent_id'] == 'base'
    repeated = client.post('/api/plans/base/constraint-apply', json={'preview_id': preview_id})
    assert repeated.json()['plan']['id'] == applied.json()['plan']['id']
    assert len(store.list()) == 2
    old_context = client.get('/api/plans/base/constraint-context').json()
    assert old_context['current_plan_id'] == applied.json()['plan']['id']
    assert client.post('/api/plans/base/constraint-preview', json=request_body(plan)).status_code == 409


def test_http_cannot_apply_arbitrary_client_candidate_or_foreign_preview(http_setup):
    client, store, plan = http_setup
    body = {'preview_id': 'invented', 'candidate': plan.model_dump(mode='json')}
    assert client.post('/api/plans/base/constraint-apply', json=body).status_code == 422
    assert client.post('/api/plans/base/constraint-apply', json={'preview_id': 'invented'}).status_code == 409
    assert len(store.list()) == 1


def test_http_invalid_input_and_missing_plan_are_explicit(http_setup):
    client, store, plan = http_setup
    body = request_body(plan)
    body['day_indexes'] = [0, 0]
    assert client.post('/api/plans/base/constraint-preview', json=body).status_code == 422
    assert client.get('/api/plans/missing/constraint-context').status_code == 404
    body = request_body(plan)
    body['base_fingerprint'] = '0' * 64
    assert client.post('/api/plans/base/constraint-preview', json=body).status_code == 409


def test_http_native_unavailable_is_503_without_plan_or_fact_changes(http_setup, monkeypatch):
    client, store, plan = http_setup
    class Unavailable(FixedBridge):
        async def ready(self):
            return False
    monkeypatch.setattr(constraint_replan, 'constraint_planner', Unavailable())
    response = client.post('/api/plans/base/constraint-preview', json=request_body(plan))
    assert response.status_code == 503
    assert len(store.list()) == 1
    assert client.get('/api/plans/base/constraint-context').json()['state_revision'] == 0


def test_http_intent_is_read_only_and_checks_current_revision(http_setup):
    client, store, plan = http_setup
    body = request_body(plan)
    body.pop('activities')
    body['instruction'] = '预算改为800元'
    response = client.post('/api/plans/base/constraint-intent', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['patch']['budget_cents'] == 80000
    assert response.json()['source'] == 'rules'
    assert len(store.list()) == 1 and store.get(plan.id).request.budget == plan.request.budget
    assert client.get('/api/plans/base/constraint-context').json()['state_revision'] == 0
    body['base_fingerprint'] = '0' * 64
    assert client.post('/api/plans/base/constraint-intent', json=body).status_code == 409


def test_http_intent_unexpressible_wishes_remain_visible_not_silently_applied(http_setup):
    client, store, plan = http_setup
    body = request_body(plan)
    body.pop('activities')
    body['instruction'] = '增加上海一个新景点，还想吃海鲜'
    response = client.post('/api/plans/base/constraint-intent', json=body)
    assert response.status_code == 200, response.text
    assert response.json()['unresolved']
    assert len(store.list()) == 1
