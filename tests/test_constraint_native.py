"""Offline checks of the independent Cangjie boundary and pure-wire oracle."""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from backend.constraint_bridge import (
    ConstraintBridge, ConstraintUnavailable, strict_json, validate_output,
)
from backend.constraint_reference import evaluate


ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'cangjie/journey_planner/fixtures/cases.json').read_text(encoding='utf-8'))


def sample():
    return copy.deepcopy(CASES[0]['input'])


@pytest.mark.parametrize('data', [
    b'{"duplicate":1,"duplicate":2}', b'{"float":1.5}', b'{"bad":NaN}',
    b'{"bad":Infinity}', b'{"bad":"\xff"}', '[' * 34 + '0' + ']' * 34,
])
def test_strict_wire_json_rejects_duplicate_noninteger_and_invalid_encoding(data):
    with pytest.raises((ValueError, UnicodeError)):
        strict_json(data)


def test_reference_checks_protected_must_and_real_remaining_cost_without_search():
    payload = sample()
    payload['source_catalog'][0]['protected'] = True
    payload['budget_cents'] = 1000
    reference = evaluate(payload, [dict(task_id='F', day_index=1, start_min=631, end_min=691)])
    violated = {check['kind'] for check in reference['checks'] if check['state'] == 'violated'}
    assert {'booking_protection', 'must_placed', 'remaining_budget'} <= violated
    assert reference['total_cents'] == 2000
    assert reference['status'] == 'conflict'


def test_reference_does_not_count_missing_data_as_verified_or_free():
    payload = sample()
    payload['routes'] = []
    payload['cost_catalog'][0]['remaining_cents'] = None
    payload['source_catalog'][0].update(opening_state='unknown', windows=[])
    payload['source_catalog'][1]['priority'] = 'optional'
    result = evaluate(payload, payload['baseline'])
    unknown = {check['kind'] for check in result['checks'] if check['state'] == 'unknown'}
    assert {'transfer_time', 'cost_unknown', 'opening_hours'} <= unknown
    assert 'cost-F' in result['unknown_cost_ids']
    assert result['status'] == 'provisional'


def test_missing_binary_has_explicit_unavailability_without_python_search(tmp_path):
    bridge = ConstraintBridge(tmp_path / 'absent.exe')
    assert asyncio.run(bridge.ready()) is False
    with pytest.raises(ConstraintUnavailable):
        asyncio.run(bridge.call(sample()))


def test_stale_build_manifest_is_unavailable_before_process_spawn(tmp_path):
    binary = tmp_path / 'journey_planner.exe'
    binary.write_bytes(b'not-a-real-program')
    binary.with_name('journey_planner.build.json').write_text(json.dumps({
        'schema_version': '2.2', 'source_sha256': '0' * 64, 'executable_sha256': '0' * 64,
    }), encoding='utf-8')
    bridge = ConstraintBridge(binary)
    assert asyncio.run(bridge.ready()) is False
    with pytest.raises(ConstraintUnavailable, match='清单不一致'):
        asyncio.run(bridge.call(sample()))


@pytest.mark.skipif(not (ROOT / 'cangjie/bin/journey_planner.exe').is_file(), reason='Engine2.2 native binary not built')
@pytest.mark.parametrize('case', CASES, ids=[case['name'] for case in CASES])
def test_real_native_fixed_cases_and_independent_reference(case):
    bridge = ConstraintBridge()
    payload = copy.deepcopy(case['input'])
    result = asyncio.run(bridge.call(payload))
    assert result['_engine']['version'] == '2.2'
    assert result['outcome'] in case['expected_outcomes']
    assert payload == case['input']
    actual = {a['task_id']: a for a in result['assignments'] or []}
    assert set(case.get('required_ids', [])) <= set(actual)
    assert not set(case.get('forbidden_ids', [])) & set(actual)
    for sid in case.get('protected_ids', []):
        assert actual[sid] == next(item for item in payload['baseline'] if item['task_id'] == sid)
    if 'expected_total' in case:
        assert result['total_cents'] == case['expected_total']
    if result['assignments'] is not None:
        reference = evaluate(payload, result['assignments'])
        assert reference['total_cents'] == result['total_cents']
        assert set(reference['charged_cost_ids']) == set(result['charged_cost_ids'])
        assert set(reference['used_route_ids']) == set(result['used_route_ids'])
        if result['outcome'] == 'conflict':
            assert reference['status'] == 'conflict'
        else:
            assert reference['status'] != 'conflict'


@pytest.mark.skipif(not (ROOT / 'cangjie/bin/journey_planner.exe').is_file(), reason='Engine2.2 native binary not built')
def test_real_native_ready_and_strict_output_contract():
    async def run():
        bridge = ConstraintBridge()
        assert await bridge.ready()
        result = await bridge.call(sample())
        result.pop('_engine')
        for field, replacement in (
            ('schema_version', '2.1'), ('engine', 'python'), ('request_id', 'another-request'),
            ('assignments', [{'task_id': 'F', 'day_index': True, 'start_min': 600, 'end_min': 660}]),
            ('checks', [dict(constraint_id='fake', kind='budget', state='violated', task_id=None,
                             day_index=None, message='hard conflict', evidence_ids=[])]),
            ('search', dict(expanded_states=201, evaluated_candidates=1, termination='completed')),
        ):
            bad = copy.deepcopy(result)
            bad[field] = replacement
            with pytest.raises(ValueError):
                validate_output(bad, sample())
    asyncio.run(run())
