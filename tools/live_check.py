"""Opt-in real API acceptance test; makes billable model requests. Outputs no keys."""
import argparse
import asyncio
import json
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--mode', choices=['walking', 'driving'], default='walking')
parser.add_argument('--days', type=int, default=1)
parser.add_argument('--revise', action='store_true')
args = parser.parse_args()


async def consume(client, path, body):
    started = time.monotonic()
    result = None
    tools = 0
    async with client.stream('POST', 'http://127.0.0.1:7862' + path, json=body) as response:
        response.raise_for_status()
        kind = ''
        async for line in response.aiter_lines():
            if line.startswith('event:'):
                kind = line[6:].strip()
            if line.startswith('data:'):
                data = json.loads(line[5:])
                if kind == 'tool': tools += 1
                if kind == 'status': print(data['message'], flush=True)
                if kind == 'error': raise RuntimeError(data['message'])
                if kind == 'result': result = data
    if not result:
        raise RuntimeError('No final result')
    stats = {'id': result['id'], 'revision': result['revision'], 'days': len(result['itinerary']['days']),
             'routes': len(result['routes']), 'available_routes': sum(r['available'] for r in result['routes']),
             'located_stops': sum(s['location_status'] == 'verified' for d in result['itinerary']['days'] for s in d['stops']),
             'status': result['validation']['status'], 'errors': [e['code'] for e in result['validation']['errors']],
             'validator': result['validation']['backend'], 'tool_events': tools, 'elapsed_s': round(time.monotonic() - started, 2)}
    (ROOT / '.runtime' / f'live-{args.mode}-{args.days}-v{result["revision"]}.json').write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(stats, ensure_ascii=False), flush=True)
    return result


async def main():
    async with httpx.AsyncClient(timeout=210, trust_env=False) as client:
        plan = await consume(client, '/api/plans/generate', {
            'destination': '上海', 'start_date': '2026-10-21', 'start_time': '09:00', 'days': args.days,
            'people': 2, 'budget': 3000, 'style': 'relaxed', 'mode': args.mode,
            'preferences': '以外滩、豫园等经典地点为主，景点数量适中。只选有具体名称的餐馆，留足真实通行时间。',
        })
        if args.revise:
            revised = await consume(client, f'/api/plans/{plan["id"]}/revise', {
                'instruction': '根据校验结果修正时间安排，留足实际路线时长。减少一个景点，安排得更轻松，保留餐饮。不要自动重复修改。'})
            assert revised['parent_id'] == plan['id'] and revised['revision'] == plan['revision'] + 1
            old = (await client.get('http://127.0.0.1:7862/api/plans/' + plan['id'])).json()
            assert old['revision'] == plan['revision']


asyncio.run(main())
