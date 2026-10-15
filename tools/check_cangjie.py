"""Compile separately, then test the real Cangjie executable against Python."""
import asyncio
import copy
import json
from pathlib import Path
import random
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.validation import Validator, validate_core


async def main():
    validator = Validator()
    cases = json.loads((ROOT / 'cangjie' / 'fixtures.json').read_text(encoding='utf-8'))
    for case in cases:
        python = validate_core(case['input'])
        assert python['status'] == case['expected_status'], case['name']
        assert [i['code'] for i in python['errors']] == case['expected_errors'], case['name']
        assert [i['code'] for i in python['warnings']] == case['expected_warnings'], case['name']
        assert await validator.call_cangjie(case['input']) == python, case['name']
    rng = random.Random(20261014)
    for index in range(120):
        data = copy.deepcopy(cases[0]['input'])
        data['budget_cents'] = rng.randint(1, 50000)
        data['total_cents'] = rng.randint(0, 50000)
        count = rng.randint(1, 9)
        data['days'] = [{'stops': []}]
        data['legs'] = []
        for i in range(count):
            data['days'][0]['stops'].append({'id': str(i), 'name': f'地点{i} 上海+"\\\n🏞',
                'identity': str(rng.randint(1, 5)), 'start_min': rng.randint(0, 1300),
                'end_min': rng.randint(0, 1439), 'poi_ok': rng.choice([True, False]),
                'needs_poi': True, 'check_duplicate': rng.choice([True, False])})
            if i and rng.random() > .2:
                data['legs'].append({'day_index': 0, 'from_id': str(i-1), 'to_id': str(i),
                                     'duration_min': rng.randint(1, 100), 'available': rng.choice([True, False])})
        assert await validator.call_cangjie(data) == validate_core(data), f'random {index}'
    assert await validator.ready()
    result = {'golden_cases': len(cases), 'random_cases': 120, 'parity': 'passed', 'compiler': 'Cangjie 1.0.5'}
    (ROOT / '.runtime' / 'cangjie-check.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


asyncio.run(main())
