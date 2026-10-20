import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.config import settings
from backend.models import Itinerary, TripRequest, DayPlan
from backend.railway import Railway, train_options, reference_price, CHINA_TIMEZONE
from backend.rail_mcp import RailMcpClient


def fixture():
    day = datetime.now(CHINA_TIMEZONE).date() + timedelta(days=3)
    request = TripRequest(destination='北京→天津', start_date=day, days=2, people=2)
    itinerary = Itinerary(title='双城', summary='测试', days=[
        {'date': day, 'title': '北京', 'stops': []},
        {'date': day + timedelta(days=1), 'title': '天津', 'stops': []}], transfers=[{
            'from_city': '北京', 'to_city': '天津', 'departure_date': day,
            'arrival_date': day, 'departure_time': '09:00', 'arrival_time': '11:00',
            'unit_cost': '100', 'schedule_status': 'queried', 'train_code': 'FAKE',
            'source': 'model', 'rail_option_id': 'forged'}])
    stations_from = [{'name': '北京南', 'code': 'VNP', 'city': '北京'},
                     {'name': '北京丰台', 'code': 'FTP', 'city': '北京'}]
    stations_to = [{'name': '天津', 'code': 'TJP', 'city': '天津'}]
    ticket = {'train_no': 'C1234', 'from_station_code': 'FTP', 'to_station_code': 'TJP',
              'start_time': '10:00', 'arrive_time': '11:43', 'duration': '01:43',
              'seats': {'second_class': '1', 'hard_seat': '有'}}
    price = {'train_code': 'C1234', 'from_station': '北京丰台', 'to_station': '天津',
             'start_time': '10:00', 'arrive_time': '11:43', 'prices': {'硬座': '19.505'}}
    return request, itinerary, stations_from, stations_to, ticket, price


def options(ticket=None, price=None):
    request, _, origin, destination, original_ticket, original_price = fixture()
    return train_options([ticket or original_ticket], [price or original_price], origin, destination,
                         request.start_date, request.people, '北京', '天津')


def test_actual_stations_seat_count_and_rounded_reference_price():
    option = options()[0]
    assert option['departure_station'] == '北京丰台'
    assert option['seat_type'] == '硬座'  # one second-class seat cannot carry two people
    assert option['reference_price'] == '19.51'
    assert reference_price('NaN') is None
    assert reference_price('-1') is None


def test_filter_city_codes_price_identity_and_malformed_seats():
    *_, ticket, price = fixture()
    assert not options({**ticket, 'from_station_code': 'SHH'})
    assert not options({**ticket, 'seats': {'hard_seat': '无'}})
    assert not options({**ticket, 'seats': None})
    assert options(price={**price, 'from_station': '北京南'})[0]['reference_price'] is None
    assert options(price={**price, 'prices': None})[0]['reference_price'] is None
    assert not options({**ticket, 'arrive_time': '12:00'})


def test_overnight_arrival_derived_from_duration():
    *_, ticket, _ = fixture()
    option = options({**ticket, 'start_time': '23:30', 'arrive_time': '01:13'})[0]
    assert option['arrival_date'] > option['departure_date']
    assert option['reference_price'] is None


def enable_rail(monkeypatch):
    monkeypatch.setattr(settings, 'rail_mcp_enabled', True)
    monkeypatch.setattr(settings, 'rail_mcp_python', Path(__file__))
    request, itinerary, origin, destination, ticket, price = fixture()
    railway = Railway(request)
    async def call(name, args):
        if name == 'search-stations':
            return {'success': True, 'stations': (origin if args['query'] == '北京' else destination) +
                    [{'name': '不相关车站', 'code': 'XXX', 'city': '上海'}]}
        if name == 'query-tickets':
            return {'success': True, 'trains': [ticket]}
        return {'success': True, 'data': [price]}
    railway.client.call = AsyncMock(side_effect=call)
    return railway, itinerary


@pytest.mark.asyncio
async def test_adapter_cache_actual_facts_and_request_isolation(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    day = railway.request.start_date.isoformat()
    result = await railway.query('北京', '天津', day, '09:00')
    option = result['options'][0]
    itinerary.transfers[0].rail_option_id = option['option_id']
    await railway.enrich(itinerary)
    transfer = itinerary.transfers[0]
    assert transfer.schedule_status == 'queried'
    assert transfer.departure_time == '10:00'
    assert transfer.departure_station == '北京丰台'
    assert transfer.unit_cost == Decimal('19.51')
    assert transfer.price_status == 'reference' and transfer.estimated
    assert transfer.source != 'model'
    assert railway.client.call.await_count == 4  # station pair + tickets + prices, no repeat
    assert len(railway.station_cache['北京']) == 2
    assert not (await railway.query('北京', '天津', day, '12:00'))['options']
    assert Railway(railway.request).options == {}


@pytest.mark.asyncio
async def test_offline_clears_forged_or_stale_proof():
    request, itinerary, *_ = fixture()
    await Railway(request).enrich(itinerary)
    transfer = itinerary.transfers[0]
    assert transfer.schedule_status == 'estimated' and not transfer.train_code
    assert not transfer.source and not transfer.rail_option_id


@pytest.mark.asyncio
async def test_failures_are_cached_and_time_window_does_not_claim_verified(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    railway.client.call.side_effect = RuntimeError('network unavailable')
    await railway.enrich(itinerary)
    await railway.enrich(itinerary)
    assert railway.client.call.await_count == 1
    assert itinerary.transfers[0].schedule_status == 'estimated'
    assert '估算' in itinerary.transfers[0].query_note
    railway, itinerary = enable_rail(monkeypatch)
    itinerary.days[0].stops = DayPlan.model_validate({
        'date': railway.request.start_date, 'title': '活动',
        'stops': [{'id': 'a', 'name': '游玩', 'city': '北京', 'start': '09:30', 'end': '12:00'}]}).stops
    await railway.enrich(itinerary)
    assert itinerary.transfers[0].schedule_status == 'estimated'
    assert '时间窗口' in itinerary.transfers[0].query_note


@pytest.mark.asyncio
async def test_query_scope_and_presale_bounds(monkeypatch):
    railway, _ = enable_rail(monkeypatch)
    day = railway.request.start_date.isoformat()
    with pytest.raises(ValueError):
        await railway.query('北京', '上海', day)
    past = (railway.request.start_date - timedelta(days=10)).isoformat()
    assert (await railway.query('北京', '天津', past))['status'] == 'unavailable'
    assert railway.client.call.await_count == 0
    with pytest.raises(ValueError):
        await railway.query('北京', '天津', (railway.request.start_date + timedelta(days=2)).isoformat())


@pytest.mark.asyncio
async def test_transport_cancellation_closes_process(monkeypatch):
    client = RailMcpClient('unused')
    client.start = AsyncMock()
    client.rpc = AsyncMock(side_effect=asyncio.CancelledError())
    client.close = AsyncMock()
    with pytest.raises(asyncio.CancelledError):
        await client.call('query-tickets', {})
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_transport_timeout_closes_connection():
    client = RailMcpClient(Path('unused'), timeout=0.01)
    client.process = SimpleNamespace(stdout=asyncio.StreamReader())
    client.start = AsyncMock()
    client.send = AsyncMock()
    client.close = AsyncMock()
    with pytest.raises(TimeoutError):
        await client.call('query-tickets', {})
    client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_price_failure_keeps_schedule_queried_and_cost_estimated(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    original = railway.client.call.side_effect
    async def call(name, args):
        if name == 'query-ticket-price':
            raise RuntimeError('price offline')
        return await original(name, args)
    railway.client.call.side_effect = call
    await railway.enrich(itinerary)
    transfer = itinerary.transfers[0]
    assert transfer.schedule_status == 'queried'
    assert transfer.price_status == 'estimated'
    assert transfer.unit_cost == Decimal('100.00')
