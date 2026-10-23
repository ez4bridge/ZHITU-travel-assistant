"""Exercise the real, independent Engine2.2 binary with frozen synthetic facts."""
import asyncio
import copy
import hashlib
from itertools import combinations
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.constraint_bridge import ConstraintBridge
from backend.constraint_reference import evaluate as reference_evaluate

FIXTURES = ROOT / 'cangjie/journey_planner/fixtures/cases.json'


def independent_checks(payload, result):
    """No scheduling/search logic: recompute actual sets, intervals, costs, windows."""
    assignments = result['assignments']
    if assignments is None:
        assert result['outcome'] in ('conflict', 'no_solution')
        return
    reference = reference_evaluate(payload, assignments)
    assert reference['total_cents'] == result['total_cents']
    assert set(reference['charged_cost_ids']) == set(result['charged_cost_ids'])
    assert set(reference['used_route_ids']) == set(result['used_route_ids'])
    if result['outcome'] == 'conflict':
        assert any(check['state'] == 'violated' for check in result['checks'])
        assert any(check['state'] == 'violated' for check in reference['checks'])
        return
    assert not any(check['state'] == 'violated' for check in reference['checks']), reference['checks']
    catalog = {task['task_id']: task for task in payload['source_catalog']}
    actual = {assignment['task_id']: assignment for assignment in assignments}
    assert len(actual) == len(assignments)
    assert set(actual) <= set(catalog)
    excluded = set(payload['completed_ids']) | set(payload['cancelled_ids'])
    assert not set(actual) & excluded
    assert {task['task_id'] for task in catalog.values() if task['priority'] == 'must' and task['task_id'] not in excluded} <= set(actual)
    before = {item['task_id']: item for item in payload['baseline']}
    days = {day['day_index']: day for day in payload['days']}
    for sid, item in actual.items():
        task = catalog[sid]
        day = days[item['day_index']]
        assert item['day_index'] in task['allowed_day_indexes']
        assert task['backlog'] or item['day_index'] == task['original_day_index']
        assert item['end_min'] - item['start_min'] == task['duration_min']
        assert day['start_min'] <= item['start_min'] < item['end_min'] <= day['end_min']
        if task['opening_state'] != 'unknown':
            assert any(window['day_index'] == item['day_index'] and window['start_min'] <= item['start_min']
                       and item['end_min'] <= window['end_min']
                       and (window['last_entry_min'] is None or item['start_min'] <= window['last_entry_min']) for window in task['windows'])
        if task['protected']:
            assert item == before[sid]
    routes = {(route['day_index'], route['from_id'], route['to_id']): route for route in payload['routes']}
    used = []
    cost_total = 0
    for day_index, day in days.items():
        items = [item for item in assignments if item['day_index'] == day_index]
        assert len(items) <= 8
        for first, second in combinations(items, 2):
            assert max(first['start_min'], second['start_min']) >= min(first['end_min'], second['end_min'])
        chain = [(day['start_anchor_id'], day['start_min'], day['start_min'])]
        chain += [(item['task_id'], item['start_min'], item['end_min']) for item in items]
        chain += [(day['end_anchor_id'], day['end_min'], day['end_min'])]
        for first, second in zip(chain, chain[1:]):
            assert first[2] <= second[1]
            if first[0] == second[0]:
                continue
            route = routes.get((day_index, first[0], second[0]))
            if route:
                used.append(route['route_id'])
                if route['time_state'] != 'unknown' and route['duration_s'] is not None:
                    assert second[1] - first[2] >= (route['duration_s'] + 59) // 60
                if route['cost_cents'] is not None and route['cost_state'] != 'unknown':
                    cost_total += route['cost_cents']
    charged = []
    for cost in payload['cost_catalog']:
        if cost['task_id'] is None or cost['charge_on_omit'] or cost['task_id'] in actual:
            charged.append(cost['cost_id'])
            if cost['remaining_cents'] is not None:
                cost_total += cost['remaining_cents']
    assert sorted(charged) == sorted(result['charged_cost_ids'])
    assert sorted(used) == sorted(result['used_route_ids'])
    assert len(charged) == len(set(charged))
    assert cost_total == result['total_cents'] <= payload['budget_cents']
    assert not any(check['state'] == 'violated' for check in result['checks'])
    expected_unplaced = set(catalog) - excluded - set(actual)
    assert expected_unplaced == {item['task_id'] for item in result['unplaced']}
    # Require conditional status whenever frozen input has unresolved used facts.
    unknown_time = any(route['time_state'] != 'verified' or route['duration_s'] is None for route in payload['routes'] if route['route_id'] in used)
    unknown_cost = any(cost['estimated'] or cost['remaining_cents'] is None for cost in payload['cost_catalog'] if cost['cost_id'] in charged)
    unknown_cost |= any(route['cost_state'] != 'verified' or route['cost_cents'] is None for route in payload['routes'] if route['route_id'] in used)
    unknown_hours = any(catalog[sid]['opening_state'] != 'verified' for sid in actual)
    if result['unknown_cost_ids'] or unknown_time or unknown_cost or unknown_hours:
        assert result['outcome'] == 'provisional'


async def main():
    bridge = ConstraintBridge()
    if not bridge.executable.is_file():
        raise SystemExit('Missing journey_planner.exe; no production Python fallback exists.')
    cases = json.loads(FIXTURES.read_text(encoding='utf-8'))
    records = []
    for case in cases:
        original = copy.deepcopy(case['input'])
        result = await bridge.call(case['input'])
        assert result['outcome'] in case['expected_outcomes'], (case['name'], result)
        ids = {item['task_id'] for item in result['assignments'] or []}
        assert set(case.get('required_ids', [])) <= ids, case['name']
        assert not set(case.get('forbidden_ids', [])) & ids, case['name']
        for sid in case.get('protected_ids', []):
            assert next(item for item in result['assignments'] if item['task_id'] == sid) == next(item for item in original['baseline'] if item['task_id'] == sid)
        if 'expected_total' in case:
            assert result['total_cents'] == case['expected_total'], case['name']
        for kind in case.get('required_unknown_kinds', []):
            assert any(check['kind'] == kind and check['state'] == 'unknown' for check in result['checks']), case['name']
        if 'expected_termination' in case:
            assert result['search']['termination'] == case['expected_termination'], case['name']
        independent_checks(original, result)
        assert original == case['input']
        repeat = await bridge.call(original)
        assert {key: value for key, value in repeat.items() if key != '_engine'} == {key: value for key, value in result.items() if key != '_engine'}, case['name']
        records.append({'scenario': case['name'], 'source': 'synthetic_simulated', 'input': original, 'result': result})
    # Production-native guards; these are rejected rather than coerced or run by 2.0.
    invalid = []
    for modify in (
        lambda p: p.update(schema_version='2.1'),
        lambda p: p['routes'][0].update(mode='auto'),
        lambda p: p['routes'][0].update(mode='flight'),
        lambda p: p.update(extra='field'),
        lambda p: p['limits'].update(max_states=201),
        lambda p: p['limits'].update(max_search_ms=0),
        lambda p: p['source_catalog'][0].update(duration_min=True),
        lambda p: p['cost_catalog'][0].update(remaining_cents=-1),
        lambda p: p['source_catalog'][0].update(cost_ids=['unknown-cost']),
        lambda p: p['routes'][0].update(from_id='unauthorized'),
        lambda p: p['routes'][0].update(time_state='verified', duration_s=None),
        lambda p: p['days'].append(copy.deepcopy(p['days'][0])),
        lambda p: p['days'][0].update(date='2026-02-30'),
    ):
        payload = copy.deepcopy(cases[0]['input'])
        modify(payload)
        invalid.append(json.dumps(payload).encode('utf-8'))
    invalid.extend([b'{', b'[]', b'{"a":1,"a":2}', b'{"a":"\xff"}', b'['*34+b'0'+b']'*34,
        b'{"oversize":"'+b'x'*1000000+b'"}'])
    for index, data in enumerate(invalid):
        process = await asyncio.create_subprocess_exec(str(bridge.executable), stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        try:
            output, error = await asyncio.wait_for(process.communicate(data), 5)
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()
        assert process.returncode != 0 and not output, f'Actual native accepted invalid protocol {index}'
        assert b'Invalid journey planner input' in error
    rng = random.Random(20261016)
    for index in range(80):
        payload = copy.deepcopy(cases[0]['input'])
        payload['request_id'] = f'journey-seeded-{index}'
        payload['budget_cents'] = rng.randint(0, 8000)
        payload['source_catalog'][1]['priority'] = rng.choice(['must', 'want', 'optional'])
        if rng.random() < .35:
            payload['source_catalog'][0]['protected'] = True
        for route in payload['routes']:
            if rng.random() < .3:
                route.update(duration_s=None, time_state='unknown')
            else:
                route['duration_s'] = rng.randint(0, 2400)
        result = await bridge.call(payload)
        independent_checks(payload, result)
    assert await bridge.ready()
    directory = ROOT / '.runtime/constraint-native-check'
    directory.mkdir(parents=True, exist_ok=True)
    summary = {'native': True, 'engine': 'cangjie-journey-planner', 'schema_version': '2.2',
        'fixed_cases': len(cases), 'protocol_rejections': len(invalid), 'seeded_cases': 80, 'seed': 20261016,
        'engine_sha256': bridge.digest(), 'fixture_sha256': hashlib.sha256(FIXTURES.read_bytes()).hexdigest(),
        'source': 'synthetic_simulated', 'result': 'passed'}
    (directory / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (directory / 'fixed-results.json').write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary))


if __name__ == '__main__':
    asyncio.run(main())
