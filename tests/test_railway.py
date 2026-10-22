import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.config import settings
from backend.models import Itinerary, TripRequest, DayPlan
from backend.railway import Railway, train_options, reference_price, sample_options, CHINA_TIMEZONE
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
    earlier = await railway.query('北京', '天津', day, '12:00')
    assert earlier['options'] and earlier['reason'] == 'preferred_window'
    assert '不是无票' in earlier['message']
    assert Railway(railway.request).options == {}


@pytest.mark.asyncio
async def test_offline_clears_forged_or_stale_proof():
    request, itinerary, *_ = fixture()
    await Railway(request).enrich(itinerary)
    transfer = itinerary.transfers[0]
    assert transfer.schedule_status == 'estimated' and not transfer.train_code
    assert not transfer.source and not transfer.rail_option_id


@pytest.mark.asyncio
async def test_selected_option_id_does_not_bypass_activity_conflicts(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    result = await railway.query('北京', '天津', railway.request.start_date.isoformat())
    itinerary.transfers[0].rail_option_id = result['options'][0]['option_id']
    itinerary.days[0].stops = DayPlan.model_validate({'date': railway.request.start_date,
        'title': '冲突活动', 'stops': [{'id': 'a', 'name': '游玩', 'city': '北京',
                                      'start': '09:30', 'end': '12:00'}]}).stops
    with pytest.raises(ValueError, match='不是无票'):
        await railway.enrich(itinerary)
    assert itinerary.transfers[0].schedule_status == 'estimated'
    assert not itinerary.transfers[0].rail_option_id


@pytest.mark.asyncio
async def test_failures_are_cached_and_time_window_does_not_claim_verified(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    railway.client.call.side_effect = RuntimeError('network unavailable')
    for _ in range(2):
        with pytest.raises(ValueError, match='network unavailable'):
            await railway.enrich(itinerary)
    assert railway.client.call.await_count == 1
    assert itinerary.transfers[0].schedule_status == 'estimated'
    assert '尚不能判断有无车票' in itinerary.transfers[0].query_note
    railway, itinerary = enable_rail(monkeypatch)
    itinerary.days[0].stops = DayPlan.model_validate({
        'date': railway.request.start_date, 'title': '活动',
        'stops': [{'id': 'a', 'name': '游玩', 'city': '北京', 'start': '09:30', 'end': '12:00'}]}).stops
    with pytest.raises(ValueError, match='不是无票'):
        await railway.enrich(itinerary)
    assert itinerary.transfers[0].schedule_status == 'estimated'
    assert '不是无票' in itinerary.transfers[0].query_note


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


@pytest.mark.asyncio
async def test_price_api_recovers_without_repeating_ticket_or_model_queries(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    original = railway.client.call.side_effect
    price_calls = 0
    async def call(name, args):
        nonlocal price_calls
        if name == 'query-ticket-price':
            price_calls += 1
            if price_calls == 1:
                return {'success': False, 'message': 'temporary price outage'}
        return await original(name, args)
    railway.client.call.side_effect = call
    await railway.enrich(itinerary)
    assert itinerary.transfers[0].price_status == 'reference'
    assert price_calls == 2
    assert sum(call.args[0] == 'query-tickets' for call in railway.client.call.call_args_list) == 1


@pytest.mark.asyncio
async def test_estimated_departure_is_not_a_hard_cutoff(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    itinerary.transfers[0].departure_time = '15:00'
    itinerary.transfers[0].arrival_time = '17:00'
    await railway.enrich(itinerary)
    assert itinerary.transfers[0].departure_time == '10:00'
    assert itinerary.transfers[0].train_code == 'C1234'
    railway.require_verified(itinerary)


def test_unverified_model_times_cannot_be_republished_as_usable_rail(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    itinerary.transfers[0].schedule_status = 'estimated'
    with pytest.raises(ValueError, match='模型估计时刻'):
        railway.require_verified(itinerary)


@pytest.mark.asyncio
async def test_real_arrival_shifts_activities_instead_of_rejecting_seats(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    itinerary.days[0].stops = DayPlan.model_validate({'date': railway.request.start_date,
        'title': '天津游玩', 'stops': [{'id': 'visit', 'name': '天津景点', 'city': '天津',
            'start': '11:30', 'end': '12:30'}]}).stops
    await railway.enrich(itinerary)
    assert itinerary.transfers[0].schedule_status == 'queried'
    assert itinerary.days[0].stops[0].start == '12:28'
    assert itinerary.days[0].stops[0].end == '13:28'


@pytest.mark.asyncio
async def test_network_failure_message_is_not_replaced_with_no_seats(monkeypatch):
    railway, _ = enable_rail(monkeypatch)
    railway.client.call.side_effect = RuntimeError('network unavailable')
    result = await railway.query('北京', '天津', railway.request.start_date.isoformat(), '23:59')
    assert result['status'] == 'unavailable'
    assert 'network unavailable' in result['message']
    assert '无可用直达席位' not in result['message']


@pytest.mark.asyncio
async def test_short_public_snapshot_keeps_timestamp_but_regenerates_selection_ids(monkeypatch):
    import backend.railway as module
    monkeypatch.setattr(module, 'PUBLIC_SNAPSHOTS', {})
    monkeypatch.setattr(module, 'monotonic', lambda: 100.0)
    first, _ = enable_rail(monkeypatch)
    first.share_snapshots = True
    day = first.request.start_date.isoformat()
    a = await first.query('北京', '天津', day)
    second = Railway(first.request, share_snapshots=True)
    second.client.call = AsyncMock(side_effect=AssertionError('warm snapshot must avoid provider requests'))
    b = await second.query('北京', '天津', day)
    assert a['options'][0]['option_id'] != b['options'][0]['option_id']
    assert a['options'][0]['queried_at'] == b['options'][0]['queried_at']
    assert b['snapshot_age_s'] == 0
    second.client.call.assert_not_awaited()
    monkeypatch.setattr(module, 'monotonic', lambda: 401.0)
    third, _ = enable_rail(monkeypatch)
    third.share_snapshots = True
    await third.query('北京', '天津', day)
    assert third.client.call.await_count == 4  # Expired data is refreshed.


@pytest.mark.asyncio
async def test_parallel_same_query_waits_for_completed_cache(monkeypatch):
    railway, _ = enable_rail(monkeypatch)
    original = railway.client.call.side_effect
    started, release = asyncio.Event(), asyncio.Event()
    async def call(name, args):
        if name == 'query-tickets':
            started.set()
            await release.wait()
        return await original(name, args)
    railway.client.call.side_effect = call
    day = railway.request.start_date.isoformat()
    first = asyncio.create_task(railway.query('北京', '天津', day))
    await started.wait()
    second = asyncio.create_task(railway.query('北京', '天津', day))
    await asyncio.sleep(0)
    assert not second.done()
    release.set()
    a, b = await asyncio.gather(first, second)
    assert a['options'] and a['options'] == b['options']
    assert railway.client.call.await_count == 4


@pytest.mark.asyncio
async def test_stdio_calls_have_a_single_reader_even_with_parallel_tools():
    import json
    client = RailMcpClient(Path('unused'))
    client.start = AsyncMock()
    started, release = asyncio.Event(), asyncio.Event()
    active, peak = 0, 0
    async def rpc(method, arguments):
        nonlocal active, peak
        active += 1
        peak = max(active, peak)
        started.set()
        await release.wait()
        active -= 1
        return {'content': [{'type': 'text', 'text': json.dumps({'success': True, 'name': arguments['name']})}]}
    client.rpc = rpc
    first = asyncio.create_task(client.call('search-stations', {'query': '北京'}))
    await started.wait()
    second = asyncio.create_task(client.call('query-tickets', {}))
    await asyncio.sleep(0)
    assert peak == 1 and not second.done()
    release.set()
    results = await asyncio.gather(first, second)
    assert peak == 1 and all(result['success'] for result in results)


def test_compact_options_include_later_trains_not_just_first_eight():
    candidates = [{'option_id': str(i), 'departure_time': f'{i:02d}:00',
                   'duration_minutes': 60, 'reference_price': '50'} for i in range(24)]
    result = sample_options(candidates, '17:30')
    assert len(result) == 8
    assert any(o['departure_time'] == '23:00' for o in result)
    assert any(o['departure_time'] in ('17:00', '18:00') for o in result)


@pytest.mark.asyncio
async def test_avoid_costly_exact_time_when_affordable_train_can_shift_activities(monkeypatch):
    railway, itinerary = enable_rail(monkeypatch)
    railway.request.budget = Decimal('100')
    request, _, origin, destination, ticket, price = fixture()
    later = {**ticket, 'train_no': 'C2234', 'start_time': '11:00', 'arrive_time': '12:43'}
    expensive = {**price, 'prices': {'硬座': '150'}}
    affordable = {**price, 'train_code': 'C2234', 'start_time': '11:00', 'arrive_time': '12:43', 'prices': {'硬座': '20'}}
    original = railway.client.call.side_effect
    async def call(name, args):
        if name == 'query-tickets':
            return {'success': True, 'trains': [ticket, later]}
        if name == 'query-ticket-price':
            return {'success': True, 'data': [expensive, affordable]}
        return await original(name, args)
    railway.client.call.side_effect = call
    itinerary.days[0].stops = DayPlan.model_validate({'date': request.start_date,
        'title': '天津游玩', 'stops': [{'id': 'visit', 'name': '景点', 'city': '天津',
                                     'start': '12:30', 'end': '13:30'}]}).stops
    await railway.enrich(itinerary)
    assert itinerary.transfers[0].train_code == 'C2234'
    assert itinerary.transfers[0].unit_cost == Decimal('20')
    assert itinerary.days[0].stops[0].start == '13:28'
