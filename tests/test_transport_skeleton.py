from datetime import datetime, timedelta
from decimal import Decimal
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.config import settings
from backend.models import Itinerary, TripRequest
from backend.railway import Railway, CHINA_TIMEZONE
from test_roundtrip_generation import model_client
from backend.agent import research_coverage_ready
from backend.locality import prepare_accommodation


def scenario():
    trip = TripRequest(origin='长沙', destinations=[{'city': '上海', 'preferred_days': 3},
        {'city': '杭州市', 'preferred_days': 2}, {'city': '苏州市', 'preferred_days': 2}],
        start_date=datetime.now(CHINA_TIMEZONE).date() + timedelta(days=1), start_time='09:00',
        days=7, people=2, budget=8000, include_origin_transport=True, return_to_origin=True)
    return trip


def fake_query():
    async def query(origin, destination, travel_date, earliest_time='00:00'):
        departure, arrival = ('09:00', '14:00') if origin == '长沙' else ('17:00', '22:00') if destination == '长沙' else ('08:00', '09:00')
        return {'status': 'queried', 'message': '真实数据模拟', 'options': [{
            'option_id': origin + destination, 'from_city': origin, 'to_city': destination,
            'departure_date': travel_date, 'arrival_date': travel_date,
            'departure_time': departure, 'arrival_time': arrival, 'duration_minutes': 60,
            'reference_price': '100', 'train_code': 'G123', 'departure_station': origin + '站',
            'arrival_station': destination + '站', 'seat_type': '二等座', 'seat_availability': '有',
            'source': '12306 / drfccv MCP', 'queried_at': datetime.now(CHINA_TIMEZONE).isoformat()}]}
    return AsyncMock(side_effect=query)


@pytest.mark.asyncio
async def test_exact_changsha_roundtrip_builds_four_edges_on_stay_boundaries(monkeypatch):
    monkeypatch.setattr(settings, 'rail_mcp_enabled', True)
    monkeypatch.setattr(settings, 'rail_mcp_python', Path(__file__))
    trip = scenario()
    rail = Railway(trip)
    rail.query = fake_query()
    transfers = await rail.prepare_plan()
    assert [(t.from_city, t.to_city) for t in transfers] == [
        ('长沙', '上海'), ('上海', '杭州市'), ('杭州市', '苏州市'), ('苏州市', '长沙')]
    assert [(t.departure_date - trip.start_date).days for t in transfers] == [0, 3, 5, 6]
    assert all(t.schedule_status == 'queried' and t.unit_cost == Decimal('100') for t in transfers)
    again = await rail.prepare_plan()
    assert again == transfers and rail.query.await_count == 4
    again[0].unit_cost = Decimal('999')
    assert rail.prepared_transfers[0].unit_cost == Decimal('100')


@pytest.mark.asyncio
@pytest.mark.parametrize('model_transfers', [None, [], 'invalid model field', [{'from_city': '错误城市'}]])
async def test_model_cannot_omit_or_corrupt_the_server_owned_roundtrip(monkeypatch, model_transfers):
    monkeypatch.setattr(settings, 'rail_mcp_enabled', True)
    monkeypatch.setattr(settings, 'rail_mcp_python', Path(__file__))
    trip = scenario()
    rail = Railway(trip)
    rail.query = fake_query()
    cities = ['上海'] * 3 + ['杭州市'] * 2 + ['苏州市'] * 2
    days = [{'date': str(trip.start_date + timedelta(days=i)), 'title': city,
        'stops': [{'id': f'd{i}', 'name': city + '景点', 'city': city,
                   'start': '14:45' if i == 0 else '10:00', 'end': '15:45' if i == 0 else '11:00'}]}
        for i, city in enumerate(cities)]
    payload = {'title': '长沙出发江南七日', 'summary': '', 'days': days}
    if model_transfers is not None:
        payload['transfers'] = model_transfers
    response = SimpleNamespace(model_dump_json=lambda: json.dumps(payload, ensure_ascii=False))
    agent, create = model_client(monkeypatch, [response])
    result = await agent.generate_itinerary(trip, SimpleNamespace(), AsyncMock(), rail=rail)
    assert len(result.transfers) == 4 and result.transfers[-1].to_city == '长沙'
    assert sum(t.unit_cost * trip.people for t in result.transfers) == Decimal('800')
    assert create.await_count == 2  # One research turn + one final, no omitted-edge retry.
    assert not any(tool['function']['name'] == 'query_train_tickets' for tool in create.call_args_list[0].kwargs['tools'])


def test_research_stops_only_after_all_cities_have_sights_and_meal_evidence():
    places = {}
    amap = SimpleNamespace(places=places)
    for city in ('上海', '杭州市', '苏州市'):
        places[(city, '景点甲')] = [{'type': '风景名胜'}]
        places[(city, '景点乙')] = [{'type': '科教文化服务;博物馆'}]
        assert not research_coverage_ready(amap, ['上海', '杭州', '苏州'])
        places[(city, '餐馆')] = [{'type': '餐饮服务'}]
    assert research_coverage_ready(amap, ['上海', '杭州', '苏州'])


def test_seven_day_budget_includes_six_nights_without_duplicating_existing_costs():
    trip = scenario()
    itinerary = Itinerary(title='完整预算', summary='', days=[
        {'date': str(trip.start_date + timedelta(days=i)), 'title': '旅行', 'stops': []} for i in range(7)])
    prepare_accommodation(itinerary, trip)
    costs = [e for day in itinerary.days for e in day.expenses if e.category == 'hotel']
    assert len(costs) == 6
    assert sum(e.unit_cost * e.quantity for e in costs) == Decimal('1800')
    assert all('非酒店报价' in e.label and '每间2人' in e.note for e in costs)
    costs[0].unit_cost = Decimal('450')
    prepare_accommodation(itinerary, trip)
    assert len([e for day in itinerary.days for e in day.expenses if e.category == 'hotel']) == 6
    assert itinerary.days[0].expenses[-1].unit_cost == Decimal('450')


@pytest.mark.asyncio
async def test_missing_price_does_not_turn_real_trains_into_missing_transport(monkeypatch):
    monkeypatch.setattr(settings, 'rail_mcp_enabled', True)
    monkeypatch.setattr(settings, 'rail_mcp_python', Path(__file__))
    trip = scenario()
    rail = Railway(trip)
    original = fake_query()
    async def missing_price(*args):
        result = await original(*args)
        for option in result['options']:
            option['reference_price'] = None
        return result
    rail.query = AsyncMock(side_effect=missing_price)
    transfers = await rail.prepare_plan()
    assert len(transfers) == 4
    assert all(t.schedule_status == 'queried' and t.train_code for t in transfers)
    assert all(t.price_status == 'estimated' and t.unit_cost > 0 and '不是车票报价' in t.note for t in transfers)


@pytest.mark.asyncio
async def test_transient_failure_retries_only_its_edge_before_building_complete_roundtrip(monkeypatch):
    monkeypatch.setattr(settings, 'rail_mcp_enabled', True)
    monkeypatch.setattr(settings, 'rail_mcp_python', Path(__file__))
    rail = Railway(scenario())
    original = fake_query()
    failed = False
    async def query(*args):
        nonlocal failed
        if not failed:
            failed = True
            return {'status': 'unavailable', 'message': 'temporary timeout', 'retryable': True, 'options': []}
        return await original(*args)
    rail.query = AsyncMock(side_effect=query)
    result = await rail.prepare_plan()
    assert len(result) == 4
    assert rail.query.await_count == 5 and original.await_count == 4
