from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.models import Itinerary, TripRequest
from backend.validation import normalized_input


def roundtrip():
    request = TripRequest(origin='广州', destination='拉萨市', return_to_origin=True,
                          start_date=datetime.now().date() + timedelta(days=2), days=3, people=2, budget=10000)
    itinerary = Itinerary(title='拉萨三日', summary='往返交通测试', days=[
        {'date': request.start_date + timedelta(days=i), 'title': '拉萨', 'stops': [],
         'expenses': [{'label': '餐饮', 'category': 'food', 'unit_cost': 50, 'basis': 'person'}]}
        for i in range(3)], transfers=[
        {'from_city': '广州市', 'to_city': '拉萨', 'departure_date': request.start_date,
         'arrival_date': request.start_date, 'departure_time': '09:00', 'arrival_time': '14:00',
         'mode': 'flight', 'unit_cost': '800.25'},
        {'from_city': '拉萨市', 'to_city': '广州', 'departure_date': request.start_date + timedelta(days=2),
         'arrival_date': request.start_date + timedelta(days=2), 'departure_time': '17:00', 'arrival_time': '22:00',
         'mode': 'flight', 'unit_cost': '900.50'}])
    return request, itinerary


def model_client(monkeypatch, itineraries):
    import backend.agent as agent
    class Message:
        tool_calls = []
        def model_dump(self, **kwargs):
            return {'role': 'assistant', 'content': '资料已整理'}
    research = SimpleNamespace(choices=[SimpleNamespace(message=Message())])
    finals = [SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=i.model_dump_json()))])
              for i in itineraries]
    create = AsyncMock(side_effect=[research, *finals])
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(agent, 'AsyncOpenAI', lambda **kwargs: Client())
    monkeypatch.setattr(agent.settings, 'llm_key', 'test-key')
    monkeypatch.setattr(agent.settings, 'amap_key', 'test-key')
    return agent, create


@pytest.mark.asyncio
@pytest.mark.parametrize('missing', ['both', 'return', 'reversed'])
async def test_missing_or_wrong_roundtrip_uses_existing_single_repair(monkeypatch, missing):
    request, valid = roundtrip()
    invalid = valid.model_copy(deep=True)
    invalid.transfers = [] if missing == 'both' else invalid.transfers[:1] if missing == 'return' else invalid.transfers[::-1]
    agent, create = model_client(monkeypatch, [invalid, valid])
    result = await agent.generate_itinerary(request, SimpleNamespace(), AsyncMock())
    assert len(result.transfers) == 2
    assert result.transfers[0].from_city == '广州市'
    assert create.await_count == 3  # research + invalid final + the existing one repair
    repair = create.await_args.kwargs['messages'][-1]['content']
    assert '广州' in repair and '拉萨' in repair
    assert normalized_input(request, result, [])['total_cents'] == 370150


@pytest.mark.asyncio
async def test_repeated_missing_transfers_fail_instead_of_returning_partial_budget(monkeypatch):
    request, invalid = roundtrip()
    invalid.transfers = []
    # research + three bounded attempts (repair, automatic retry) before failing.
    agent, create = model_client(monkeypatch, [invalid, invalid, invalid])
    with pytest.raises(RuntimeError, match='跨城交通'):
        await agent.generate_itinerary(request, SimpleNamespace(), AsyncMock())
    assert create.await_count == 4


@pytest.mark.asyncio
async def test_valid_roundtrip_costs_need_no_extra_model_round(monkeypatch):
    request, valid = roundtrip()
    agent, create = model_client(monkeypatch, [valid])
    result = await agent.generate_itinerary(request, SimpleNamespace(), AsyncMock())
    assert create.await_count == 2
    assert normalized_input(request, result, [])['total_cents'] == 370150


@pytest.mark.asyncio
async def test_origin_boarding_is_not_assigned_to_destination_city(monkeypatch):
    from backend.models import Stop
    request, valid = roundtrip()
    invalid = valid.model_copy(deep=True)
    invalid.days[0].stops = [Stop(id='wrong-city', name='广州白云站候车', city='拉萨市',
        kind='break', location_behavior='unlocated', start='09:00', end='10:00')]
    agent, create = model_client(monkeypatch, [invalid, valid])
    result = await agent.generate_itinerary(request, SimpleNamespace(), AsyncMock())
    assert not result.days[0].stops and create.await_count == 3
    assert '不作为目的地城市' in create.await_args.kwargs['messages'][-1]['content']


def test_booked_tickets_fill_legs_the_model_omitted():
    """A missing booked leg must not abort generation (reported 2026-10-22)."""
    from decimal import Decimal
    from backend.journey import ensure_transfer_coverage, merge_booked_transfers
    from backend.models import BookedTransport

    request, itinerary = roundtrip()
    start = request.start_date
    request.booked_transports = [
        BookedTransport(from_city='广州', to_city='拉萨', mode='flight', departure_date=start,
            departure_time='07:30', arrival_date=start, arrival_time='12:30', total_cost='1600'),
        BookedTransport(from_city='拉萨', to_city='广州', mode='flight', departure_date=start + timedelta(days=2),
            departure_time='17:00', arrival_date=start + timedelta(days=2), arrival_time='22:00',
            total_cost='1800')]
    itinerary.transfers = []  # the model returned none at all
    merge_booked_transfers(request, itinerary)
    ensure_transfer_coverage(request, itinerary)
    assert [t.booking_status for t in itinerary.transfers] == ['user_confirmed', 'user_confirmed']
    assert [t.unit_cost for t in itinerary.transfers] == [Decimal('1600'), Decimal('1800')]
    assert [t.arrival_time for t in itinerary.transfers] == ['12:30', '22:00']


def test_missing_unbooked_leg_still_reports_what_is_missing():
    from backend.journey import TransferCoverageError, ensure_transfer_coverage, merge_booked_transfers

    request, itinerary = roundtrip()
    start = request.start_date
    request.booked_transports = []
    itinerary.transfers = itinerary.transfers[:1]
    merge_booked_transfers(request, itinerary)
    with pytest.raises(TransferCoverageError, match='当前为'):
        ensure_transfer_coverage(request, itinerary)


@pytest.mark.asyncio
async def test_a_late_dinner_does_not_discard_the_generated_plan(monkeypatch, tmp_path):
    """Reported 2026-10-22: a 20:56 dinner aborted the whole generation."""
    import json
    import httpx
    import backend.main as main
    from backend.amap import Amap
    from backend.models import Stop
    from backend.storage import Store
    from backend.validation import Validator

    request, valid = roundtrip()
    valid.days[0].stops = [
        Stop(id='evening', name='合成夜游', kind='sight', city='拉萨市', start='19:30', end='21:00',
             location=(91.1, 29.6), location_status='verified'),
        Stop(id='late-dinner', name='合成晚餐', kind='meal', city='拉萨市', start='21:30', end='22:15',
             meal_period='dinner', location=(91.11, 29.61), location_status='verified')]

    async def generate(trip, *args, **kwargs):
        return valid.model_copy(deep=True)

    monkeypatch.setattr(main, 'generate_itinerary', generate)
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'late-meal.sqlite3'))
    monkeypatch.setattr(main, 'validator', Validator())
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=[]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/generate', json=request.model_dump(mode='json'))
        assert 'event: error' not in response.text
        result = json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
        saved = (await client.get('/api/plans/' + result['id'])).json()
    meal = next(stop for day in saved['itinerary']['days'] for stop in day['stops'] if stop['kind'] == 'meal')
    assert meal['start']  # the meal survived; the plan was saved instead of discarded
    assert any('用餐安排待调整' in item for item in saved['adjustments'])


@pytest.mark.asyncio
async def test_pre_planning_booked_transport_becomes_a_fixed_fact(monkeypatch, tmp_path):
    """A ticket entered on the first form overrides whatever the model drafts."""
    import json
    import httpx
    import backend.main as main
    from backend.agent import booking_facts_instruction
    from backend.amap import Amap
    from backend.models import BookedTransport
    from backend.storage import Store
    from backend.validation import Validator

    request, valid = roundtrip()
    request.booked_transports = [BookedTransport(from_city='广州', to_city='拉萨', mode='flight',
        departure_date=request.start_date, departure_time='07:30', arrival_date=request.start_date,
        arrival_time='12:30', departure_place='广州白云机场', arrival_place='拉萨贡嘎机场',
        number='CA1234', total_cost='1600')]
    seen = {}

    async def generate(trip, *args, **kwargs):
        seen['booking'] = trip.booked_transports[0]
        assert '固定事实' in booking_facts_instruction(trip)
        return valid.model_copy(deep=True)  # the model drafted a different flight on purpose

    monkeypatch.setattr(main, 'generate_itinerary', generate)
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'prebooking.sqlite3'))
    monkeypatch.setattr(main, 'validator', Validator())
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=[]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/generate', json=request.model_dump(mode='json'))
        assert 'event: error' not in response.text
        result = json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
        saved = (await client.get('/api/plans/' + result['id'])).json()
    assert seen['booking'].number == 'CA1234'
    transfer = saved['itinerary']['transfers'][0]
    assert transfer['booking_status'] == 'user_confirmed'
    assert (transfer['departure_time'], transfer['arrival_time']) == ('07:30', '12:30')
    assert transfer['arrival_station'] == '拉萨贡嘎机场'
    anchor = next(stop for day in saved['itinerary']['days'] for stop in day['stops']
                  if stop.get('booking_anchor') == 'transport_arrival')
    assert anchor['start'] == '13:30'  # 12:30 arrival + 60 min flight exit allowance


@pytest.mark.asyncio
async def test_roundtrip_request_and_whole_party_fares_survive_sse_and_history(monkeypatch, tmp_path):
    import httpx
    import backend.main as main
    from backend.amap import Amap
    from backend.storage import Store
    from backend.validation import Validator
    request, valid = roundtrip()
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'roundtrip.sqlite3'))
    monkeypatch.setattr(main, 'validator', Validator())
    async def generate(trip, *args, **kwargs):
        assert trip.include_origin_transport and trip.return_to_origin
        assert trip.journey_cities == ['广州', '拉萨市', '广州']
        return valid.model_copy(deep=True)
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=[]))
    import json
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/generate', json=request.model_dump(mode='json'))
        assert 'event: error' not in response.text
        result = json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
        saved = (await client.get('/api/plans/' + result['id'])).json()
    assert saved['request']['include_origin_transport'] and saved['request']['return_to_origin']
    assert len(saved['itinerary']['transfers']) == 2
    assert saved['expense_totals']['transport'] == 340150
    assert saved['validation']['total_cents'] == 370150
