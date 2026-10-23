import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from backend.validation import Validator, validate_core
from backend.storage import Store
from backend.constraint_storage import ConstraintStore
from plan_samples import sample_plan


CASES = json.loads((Path(__file__).parent / 'fixtures/validation_cases.json').read_text(encoding='utf-8-sig'))


@pytest.mark.parametrize('case', CASES, ids=lambda case: case['name'])
def test_existing_validation_contract_is_preserved(case):
    report = validate_core(case['input'])
    assert report['status'] == case['expected_status']
    assert {e['code'] for e in report['errors']} == set(case['expected_errors'])
    assert {e['code'] for e in report['warnings']} == set(case['expected_warnings'])


@pytest.mark.asyncio
async def test_validation_never_starts_native_process(monkeypatch):
    monkeypatch.setattr(asyncio, 'create_subprocess_exec', AsyncMock(side_effect=AssertionError('No native processes')))
    plan = sample_plan()
    report = await Validator().validate(plan.request, plan.itinerary, plan.routes)
    assert report.backend == 'python' and report.total_cents == 4000


def test_health_and_retired_interfaces_remain_explicit(tmp_path, monkeypatch):
    import backend.main as main
    store = Store(tmp_path / 'python.sqlite3')
    plan = sample_plan()
    store.save(plan)
    monkeypatch.setattr(main, 'store', store)
    with TestClient(main.app) as client:
        health = client.get('/api/health').json()
        assert health['capabilities']['validator'] == 'python'
        assert health['capabilities']['ai_adjustment'] is True
        for path, method in [('constraint-context','GET'), ('constraint-preview','POST'), ('constraint-intent','POST'), ('conversation-preview','POST')]:
            response = client.request(method, '/api/plans/base/' + path)
            assert response.status_code == 410 and 'AI' in response.json()['detail']
    assert ConstraintStore(store.path).state(plan)['state_revision'] == 0
