"""Explicit live acceptance test: calls configured model/map/rail services via HTTP.

Run manually, never as part of pytest. A successful run saves a real application plan.
"""
import argparse
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import json
from pathlib import Path
import time

import httpx
import re


def verify(plan, request, recheck):
    assert plan['request']['people'] == 2
    assert Decimal(plan['request']['budget']) == Decimal(request['budget'])
    assert plan['request']['include_origin_transport'] and plan['request']['return_to_origin']
    assert [(s['city'].removesuffix('市'), Decimal(s['preferred_days'])) for s in plan['request']['destinations']] == [('上海', Decimal(3)), ('杭州', Decimal(2)), ('苏州', Decimal(2))]
    assert len(plan['itinerary']['days']) == 7
    calendar_days = {city: set() for city in ('上海', '杭州', '苏州')}
    for day in plan['itinerary']['days']:
        for stop in day['stops']:
            city = stop['city'].removesuffix('市')
            if city in calendar_days and stop['kind'] in ('sight', 'break'):
                calendar_days[city].add(day['date'])
    assert [len(calendar_days[city]) for city in ('上海', '杭州', '苏州')] == [3, 2, 2], calendar_days
    transfers = plan['itinerary']['transfers']
    actual = [(t['from_city'].removesuffix('市'), t['to_city'].removesuffix('市')) for t in transfers]
    assert actual == [('长沙', '上海'), ('上海', '杭州'), ('杭州', '苏州'), ('苏州', '长沙')], actual
    assert all(t['schedule_status'] == 'queried' and t['train_code'] and t['source'] and t['rail_option_id'] for t in transfers)
    for transfer in transfers:
        assert transfer['price_status'] == 'reference' or ('预算预留' in transfer['note'] and Decimal(transfer['unit_cost']) > 0)
    assert not plan['validation']['errors'], plan['validation']['errors']
    assert not recheck['errors'], recheck['errors']
    assert sum(plan['expense_totals'].values()) == plan['validation']['total_cents'] == recheck['total_cents']
    meals = 0
    restaurants = set()
    for day in plan['itinerary']['days']:
        periods = {s['meal_period'] or ('lunch' if int(s['start'][:2]) < 16 else 'dinner')
                   for s in day['stops'] if s['kind'] == 'meal'}
        for key in ('lunch', 'dinner'):
            if key not in periods:
                reserve = [e for e in day['expenses'] if e['category'] == 'food'
                           and not e['stop_id'] and e['note'].startswith(f'在途用餐预算预留：{key}；')]
                assert len(reserve) == 1 and Decimal(reserve[0]['unit_cost']) > 0, (day['date'], key)
                assert '提前' in day['meal_note'] and '非餐馆或车餐报价' in day['meal_note']
        for stop in day['stops']:
            if stop['kind'] != 'meal':
                continue
            meals += 1
            key = stop['meal_period'] or ('lunch' if int(stop['start'][:2]) < 16 else 'dinner')
            if key in ('lunch', 'dinner'):
                assert ('11:00' <= stop['start'] <= '14:00') if key == 'lunch' else ('17:00' <= stop['start'] <= '20:00'), stop
            assert not any(word in stop['name'] for word in ('列车', '提前备餐', '候车'))
            name = re.split(r'[（(]', stop['matched_name'] or stop['place_name'] or stop['name'])[0].strip()
            name = re.sub(r'(?<=..)(?:餐厅|餐馆|饭店|酒楼|酒家|生煎馆|生煎|面馆|汤包馆|汤包)$', '', name)
            identity = (stop['city'].removesuffix('市'), name)
            assert identity not in restaurants, f'Repeated restaurant brand: {identity}'
            restaurants.add(identity)
            assert stop['average_cost_yuan'] and stop['information_source'].startswith('https://www.amap.com/place/')
            expenses = [e for e in day['expenses'] if e['category'] == 'food' and e['stop_id'] == stop['id']]
            assert len(expenses) == 1
            assert Decimal(expenses[0]['unit_cost']) == Decimal(stop['average_cost_yuan'])
            assert expenses[0]['basis'] == 'person' and expenses[0]['quantity'] == 1
    assert meals, 'No verified meals'
    for day in plan['itinerary']['days'][:-1]:
        in_train = any(t['departure_date'] <= day['date'] < t['arrival_date'] for t in transfers)
        if not in_train:
            assert any(e['category'] == 'hotel' and Decimal(e['unit_cost']) > 0 for e in day['expenses']), f'Missing overnight cost: {day["date"]}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:7862')
    parser.add_argument('--date', default=str((datetime.now(timezone(timedelta(hours=8))) + timedelta(days=1)).date()))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budget', type=int, default=8000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    request = {'origin': '长沙', 'destinations': [{'city': '上海', 'preferred_days': 3},
        {'city': '杭州市', 'preferred_days': 2}, {'city': '苏州市', 'preferred_days': 2}],
        'start_date': args.date, 'start_time': '09:00', 'days': 7, 'people': 2,
        'budget': args.budget, 'mode': 'auto', 'intercity_mode': 'train',
        'include_origin_transport': True, 'return_to_origin': True, 'compare_modes': True}
    (args.output / 'request.json').write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding='utf-8')
    started = time.monotonic()
    result = None
    with httpx.Client(base_url=args.url, timeout=210, trust_env=False) as client, (args.output / 'events.jsonl').open('w', encoding='utf-8') as log:
        with client.stream('POST', '/api/plans/generate', json=request) as response:
            response.raise_for_status()
            kind = ''
            for line in response.iter_lines():
                if line.startswith('event: '):
                    kind = line[7:]
                elif line.startswith('data: '):
                    data = json.loads(line[6:])
                    elapsed = round(time.monotonic() - started, 2)
                    log.write(json.dumps({'elapsed_s': elapsed, 'event': kind, 'data': data}, ensure_ascii=False) + '\n')
                    log.flush()
                    if kind in ('status', 'error'):
                        print(f'{elapsed:.2f}s {kind}: {data.get("message", "")}', flush=True)
                    if kind == 'error':
                        raise RuntimeError(data['message'])
                    if kind == 'result':
                        result = data
        assert result, 'No result event'
        saved = client.get('/api/plans/' + result['id']).raise_for_status().json()
        assert saved == result, 'Saved result differs'
        recheck = client.post('/api/plans/' + result['id'] + '/validate').raise_for_status().json()
        verify(saved, request, recheck)
        (args.output / 'plan.json').write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding='utf-8')
        summary = {'passed': True, 'id': saved['id'], 'elapsed_s': round(time.monotonic() - started, 2),
            'budget_cents': saved['validation']['budget_cents'], 'total_cents': saved['validation']['total_cents'],
            'blocking_errors': recheck['errors'], 'warnings': recheck['warnings'],
            'transfers': [{k: t[k] for k in ('from_city', 'to_city', 'train_code', 'departure_date', 'departure_time', 'arrival_date', 'arrival_time', 'unit_cost', 'seat_type')} for t in saved['itinerary']['transfers']]}
        (args.output / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(summary, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
