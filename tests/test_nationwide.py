import copy
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from backend.amap import Amap
from backend.journey import normalize_cities
from backend.models import Itinerary, PlanRecord, TripRequest, ValidationReport
from backend.storage import Store
from backend.validation import normalized_input, validate_core


def nationwide():
    request = TripRequest(destination='上海→杭州→苏州', start_date='2026-10-21', days=3,
                          people=3, budget=10000, origin='北京', include_origin_transport=True,
                          return_to_origin=True)
    days = []
    for index, (city, sight) in enumerate([('上海', '外滩'), ('杭州', '西湖'), ('苏州', '拙政园')]):
        days.append({'date': f'2026-10-{21 + index}', 'title': city, 'stops': [{
            'id': f'd{index + 1}s1', 'name': sight, 'city': city, 'start': '14:00', 'end': '16:00',
            'location': [120 + index, 30], 'location_status': 'verified', 'poi_id': f'poi-{index}'
        }], 'expenses': [{'label': '餐饮', 'category': 'food', 'unit_cost': '50.25', 'basis': 'person'}]})
    transfers = []
    for index, (origin, destination) in enumerate(zip(request.journey_cities, request.journey_cities[1:])):
        day = f'2026-10-{21 + min(index, 2)}'
        transfers.append({'from_city': origin, 'to_city': destination, 'departure_date': day,
                          'arrival_date': day, 'departure_time': '18:00' if index == 3 else '09:00',
                          'arrival_time': '23:00' if index == 3 else '12:00', 'unit_cost': '100.005'})
    itinerary = Itinerary(title='江南三城', summary='三城顺游', days=days, transfers=transfers)
    return request, itinerary, []


def report(request, itinerary, routes=None):
    return validate_core(normalized_input(request, itinerary, routes or []))


@pytest.mark.parametrize('text', ['上海→杭州→苏州', '上海 -> 杭州 -> 苏州', '上海、杭州、苏州', '上海，杭州,苏州'])
def test_ordered_city_input_and_roundtrip(text):
    request = TripRequest(destination=text, origin='北京', return_to_origin=True, start_date='2026-10-21')
    assert request.city_route == ['上海', '杭州', '苏州']
    assert request.journey_cities == ['北京', '上海', '杭州', '苏州', '北京']
    assert request.include_origin_transport


@pytest.mark.parametrize('changes', [
    {'destination': '→'}, {'destination': '上海→上海市'},
    {'destination': '北京→上海→杭州→苏州→成都→重庆→广州'}, {'return_to_origin': True},
    {'include_origin_transport': True},
])
def test_invalid_city_requests_rejected(changes):
    with pytest.raises(ValidationError):
        TripRequest(**{'destination': '上海', 'start_date': '2026-10-21', **changes})


def test_old_request_keeps_origin_as_background_and_legacy_fields_readable(tmp_path):
    request, itinerary, _ = nationwide()
    data = request.model_dump(mode='json')
    for key in ['intercity_mode', 'include_origin_transport', 'return_to_origin']:
        data.pop(key)
    data['destination'] = '上海'
    old_request = TripRequest.model_validate(data)
    assert old_request.journey_cities == ['上海']
    legacy = itinerary.model_dump(mode='json')
    legacy.pop('transfers')
    for day in legacy['days']:
        for stop in day['stops']:
            stop.pop('city')
    old_itinerary = Itinerary.model_validate(legacy)
    assert old_itinerary.transfers == []
    normalize_cities(old_request, old_itinerary)
    assert all(stop.city == '上海' for day in old_itinerary.days for stop in day.stops)
    service = Store(tmp_path / 'old.sqlite3')
    plan = PlanRecord(id='legacy', created_at=datetime.now(timezone.utc), request=old_request,
                      itinerary=old_itinerary, routes=[], validation=ValidationReport(**report(old_request, old_itinerary)))
    service.save(plan)
    assert service.get('legacy').id == 'legacy'


def test_crosscity_costs_use_same_rounding_and_whole_party_budget():
    request, itinerary, routes = nationwide()
    assert itinerary.transfers[0].unit_cost == Decimal('100.01')
    result = report(request, itinerary)
    assert result['errors'] == []
    assert result['total_cents'] == 3 * 5025 * 3 + 4 * 10001 * 3
    assert 'intercity_estimates' in {item['code'] for item in result['unknowns']}
    plan = PlanRecord(id='nationwide', created_at=datetime.now(timezone.utc), request=request,
                      itinerary=itinerary, routes=routes, validation=ValidationReport(**result))
    assert plan.expense_totals['transport'] == 120012
    assert sum(plan.expense_totals.values()) == result['total_cents']
    request.budget = 1000
    assert 'over_budget' in {item['code'] for item in report(request, itinerary)['errors']}
    itinerary.transfers[0].basis = 'group'
    assert report(request, itinerary)['total_cents'] == result['total_cents'] - 20002


@pytest.mark.parametrize('mutation,code', [
    ('missing_transfer', 'intercity_route'), ('missing_return', 'intercity_route'),
    ('wrong_city', 'city_sequence'), ('activity_during_train', 'intercity_activity_overlap'),
    ('before_arrival', 'city_arrival'), ('invalid_duration', 'intercity_duration'),
    ('outside_dates', 'intercity_dates'), ('overlapping_transfers', 'intercity_overlap'),
])
def test_transport_coverage_and_conflicts(mutation, code):
    request, itinerary, _ = nationwide()
    if mutation == 'missing_transfer':
        itinerary.transfers.pop(1)
    elif mutation == 'missing_return':
        itinerary.transfers.pop()
    elif mutation == 'wrong_city':
        itinerary.days[1].stops[0].city = '成都'
    elif mutation == 'activity_during_train':
        itinerary.days[1].stops[0].start = '11:00'
    elif mutation == 'before_arrival':
        itinerary.days[1].stops[0].start = '08:00'
        itinerary.days[1].stops[0].end = '09:00'
    elif mutation == 'invalid_duration':
        itinerary.transfers[1].arrival_time = '08:00'
    elif mutation == 'outside_dates':
        itinerary.transfers[-1].arrival_date = request.start_date
        itinerary.transfers[0].departure_time = '08:00'
    else:
        itinerary.transfers[1].departure_date = request.start_date
        itinerary.transfers[1].arrival_date = request.start_date
    assert code in {item['code'] for item in report(request, itinerary)['errors']}


def test_overnight_transport_and_same_day_crosscity_are_not_local_routes():
    request, itinerary, _ = nationwide()
    itinerary.transfers[1].departure_date = request.start_date
    itinerary.transfers[1].departure_time = '22:00'
    itinerary.transfers[1].arrival_time = '07:00'
    assert report(request, itinerary)['errors'] == []
    # Two cities in one day; no false request for a walking route between cities.
    itinerary.days[0].stops[0].end = '10:00'
    itinerary.transfers[0].arrival_time = '09:30'
    itinerary.days[0].stops[0].start = '09:30'
    itinerary.transfers[1].departure_time = '10:30'
    itinerary.transfers[1].arrival_date = request.start_date
    itinerary.transfers[1].arrival_time = '12:00'
    moved = itinerary.days[1].stops.pop()
    itinerary.days[0].stops.append(moved)
    result = report(request, itinerary)
    assert result['errors'] == []
    assert 'missing_route' not in {item['code'] for item in result['unknowns']}


@pytest.mark.asyncio
async def test_city_cache_and_poi_ids_never_cross_city_boundaries():
    service = Amap(['上海', '杭州'])
    seen = []
    async def fake_request(path, params):
        entries = {'上海': ('310000', 'province'), '杭州': ('330100', 'city')}
        if path == '/v3/config/district':
            code, level = entries[params['keywords']]
            return {'count': '1', 'districts': [{'name': params['keywords'] + '市', 'adcode': code, 'level': level}]}
        seen.append(params)
        name = next(name for name, (code, _) in entries.items() if code == params['city'])
        return {'pois': [{'id': name, 'name': '人民公园', 'cityname': name + '市',
                          'adcode': params['city'], 'location': '120,30'}, {'id': 'wrong', 'name': '人民公园', 'cityname': '成都',
                                                'location': '104,30'}]}
    service.request = fake_request
    try:
        shanghai = await service.search('人民公园', '上海')
        hangzhou = await service.search('人民公园', '杭州市')
        assert shanghai[0]['id'] == '上海' and hangzhou[0]['id'] == '杭州'
        assert len(shanghai) == len(hangzhou) == 1
        assert len(seen) == 2 and all(params['citylimit'] == 'true' for params in seen)
        assert await service.search('人民公园', '上海市') == shanghai
        assert len(seen) == 2
        from backend.models import Stop
        stop = Stop(name='人民公园', city='杭州', poi_id='上海', start='09:00', end='10:00', location=[1, 1])
        await service.resolve(stop)
        assert stop.poi_id == '杭州' and stop.location_status == 'verified'
        with pytest.raises(ValueError, match='目的地列表'):
            await service.search('人民公园', '成都')
        stop.city = ''
        await service.resolve(stop)
        assert stop.location is None and stop.location_status == 'unknown'
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_enrichment_never_uses_walking_between_cities():
    request, itinerary, _ = nationwide()
    itinerary.days[0].stops.append(itinerary.days[1].stops.pop())
    service = Amap(request.city_route)
    service.resolve = AsyncMock()
    service.route = AsyncMock()
    try:
        routes = await service.enrich(itinerary, 'walking')
        assert len(routes) == 1 and routes[0].scope == 'intercity'
        assert not routes[0].available and not routes[0].polyline
        service.route.assert_not_awaited()
    finally:
        await service.close()


@pytest.mark.asyncio
async def test_multicity_generate_revise_and_history_keep_independent_costs(monkeypatch, tmp_path, fixture_trip_clock):
    import httpx
    import backend.main as main
    from backend.validation import Validator
    request, original, _ = nationwide()
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'national.sqlite3'))
    monkeypatch.setattr(main, 'validator', Validator())
    async def fake_generate(trip, amap, emit, previous=None, instruction='', rail=None, weather=None, weather_by_city=None):
        # The origin is also needed to resolve departure station/airport anchors.
        assert amap.cities == trip.journey_cities
        itinerary = original.model_copy(deep=True)
        if previous:
            itinerary.transfers[0].unit_cost = Decimal('50')
        return itinerary
    monkeypatch.setattr(main, 'generate_itinerary', fake_generate)
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=[]))
    def result(response):
        assert 'event: error' not in response.text
        return json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        first = result(await client.post('/api/plans/generate', json=request.model_dump(mode='json')))
        second = result(await client.post(f'/api/plans/{first["id"]}/revise', json={'instruction': '降低交通费用'}))
        saved = (await client.get(f'/api/plans/{first["id"]}')).json()
        recheck = (await client.post(f'/api/plans/{second["id"]}/validate')).json()
    assert saved == first
    assert second['id'] != first['id'] and second['parent_id'] == first['id']
    assert second['revision'] == 2 and len(second['itinerary']['transfers']) == 4
    assert recheck['total_cents'] == second['validation']['total_cents']
    assert first['validation']['total_cents'] - second['validation']['total_cents'] == 15003
    assert sum(second['expense_totals'].values()) == recheck['total_cents']


def test_nationwide_fixture_contract():
    from pathlib import Path
    cases = json.loads((Path(__file__).resolve().parents[1] / 'tests/fixtures/validation_cases.json').read_text(encoding='utf-8'))
    for case in cases:
        data = copy.deepcopy(case['input'])
        result = validate_core(data)
        assert result['status'] == case['expected_status'], case['name']
        assert [item['code'] for item in result['errors']] == case['expected_errors'], case['name']
        assert [item['code'] for item in result['warnings']] == case['expected_warnings'], case['name']
        assert data == case['input']  # Engine checks must not mutate saved normalized inputs.
