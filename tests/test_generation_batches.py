import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.generation_batches import generate_batches, DayBatch, task_limits
from test_core import sample


def reply(data):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(data)))])


@pytest.mark.asyncio
@pytest.mark.parametrize('day_count', [4, 10])
async def test_batches_share_outline_run_concurrently_and_merge_ids_and_expenses(day_count):
    request, itinerary, _ = sample()
    request.days = day_count
    outline = {'title': 'trip', 'summary': 'summary', 'days': [
        {'date': str(request.start_date + timedelta(days=i)), 'cities': ['上海'], 'title': 'day',
         'places': ['外滩'], 'group_budget': 100} for i in range(day_count)]}
    arrived = 0
    barrier = asyncio.Event()
    async def call(messages, phase, tokens):
        nonlocal arrived
        if phase == 'outline':
            return reply(outline)
        assert '固定框架' in messages[-1]['content']
        arrived += 1
        if arrived == 2:
            barrier.set()
        await asyncio.wait_for(barrier.wait(), 1)
        start = int(phase.split('_')[1]) - 1
        days = []
        for i in range(start, start + 2):
            day = itinerary.days[0].model_dump(mode='json')
            day['date'] = str(request.start_date + timedelta(days=i))
            # IDs are unique within a batch, deliberately reused across batches.
            for stop in day['stops']:
                stop['id'] += str(i % 2)
                stop['city'] = '上海'
            day['expenses'][0]['stop_id'] = day['stops'][0]['id']
            days.append(day)
        return reply({'days': days})
    result = await generate_batches(request, [], call, AsyncMock(), DayBatch.model_json_schema())
    assert len(result.days) == day_count and arrived == day_count // 2
    assert len({s.id for d in result.days for s in d.stops}) == day_count * 2
    assert all(d.expenses[0].stop_id == d.stops[0].id for d in result.days)
    assert result.days[3].date == request.start_date + timedelta(days=3)


@pytest.mark.asyncio
async def test_invalid_outline_repaired_once_then_stops_before_detail_calls():
    request, _, _ = sample()
    call = AsyncMock(return_value=reply({'title': 'bad'}))
    with pytest.raises(RuntimeError):
        await generate_batches(request, [], call, AsyncMock(), {})
    assert call.await_count == 2


@pytest.mark.asyncio
async def test_failed_batch_cancels_other_paid_stream():
    request, _, _ = sample()
    request.days = 4
    outline = {'title': 'trip', 'summary': '', 'days': [
        {'date': str(request.start_date + timedelta(days=i)), 'cities': ['上海'], 'title': '',
         'group_budget': 100} for i in range(4)]}
    waiting = asyncio.Event()
    closed = asyncio.Event()
    async def call(messages, phase, tokens):
        if phase == 'outline':
            return reply(outline)
        if phase == 'days_1_2':
            await waiting.wait()
            raise RuntimeError('provider failed')
        waiting.set()
        try:
            await asyncio.Future()
        finally:
            closed.set()
    with pytest.raises(RuntimeError, match='provider failed'):
        await asyncio.wait_for(generate_batches(request, [], call, AsyncMock(), {}), 2)
    assert closed.is_set()


def test_limits_scale_with_days_but_remain_bounded_and_refresh_skips_model_budget():
    request, _, _ = sample()
    short = task_limits(request)
    request.days = 10
    long = task_limits(request)
    assert long['model'] > short['model'] and long['map'] > short['map']
    assert long['total'] <= 900
    assert task_limits(request, refresh=True)['total'] == long['map'] + 30


@pytest.mark.asyncio
async def test_stage_deadline_reports_phase_and_cancels_without_saving(monkeypatch, tmp_path):
    import httpx
    import backend.main as main
    from backend.storage import Store
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'deadline.sqlite3'))
    monkeypatch.setattr(main, 'task_limits', lambda *args: {'model': .02, 'rail': 1, 'map': 1, 'total': 2})
    closed = asyncio.Event()
    async def generate(request, amap, emit, *args, **kwargs):
        await emit('status', {'message': 'outline', 'phase': 'outline'})
        try:
            await asyncio.Future()
        finally:
            closed.set()
    monkeypatch.setattr(main, 'generate_itinerary', generate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        response = await client.post('/api/plans/generate', json=sample()[0].model_dump(mode='json'))
    assert 'event: error' in response.text and 'event: result' not in response.text
    assert main._last_failure['model_phase'] == 'outline'
    assert 'agent_incomplete' in main._metrics['last_phase_seconds']
    assert closed.is_set() and main.store.list() == []


@pytest.mark.asyncio
async def test_nine_day_revisit_route_and_origin_return_are_valid():
    from backend.models import TripRequest
    request = TripRequest(origin='长沙', destinations=[{'city': city, 'preferred_days': days}
        for city, days in [('喀什', 2), ('克孜勒苏柯尔克孜', 3), ('喀什', 2), ('阿克苏', 2)]],
        start_date=sample()[0].start_date, people=1, budget=10000, return_to_origin=True)
    cities = ['喀什'] * 2 + ['克孜勒苏柯尔克孜'] * 3 + ['喀什'] * 2 + ['阿克苏'] * 2
    outline = {'title': '南疆', 'summary': '', 'days': [{'date': str(request.start_date + timedelta(days=i)),
        'cities': ([city, '长沙'] if i == 8 else [city]), 'title': '', 'group_budget': 500} for i, city in enumerate(cities)]}
    phases = []
    async def call(messages, phase, tokens):
        phases.append(phase)
        if phase == 'outline':
            return reply(outline)
        start = int(phase.split('_')[1]) - 1
        return reply({'days': [{'date': outline['days'][i]['date'], 'title': '', 'stops': [
            {'id': f'old{i}', 'name': '景点', 'city': cities[i], 'start': '10:00', 'end': '11:00'}]}
            for i in range(start, min(start + 2, request.days))]})
    result = await generate_batches(request, [], call, AsyncMock(), DayBatch.model_json_schema())
    assert len(result.days) == 9 and phases.count('outline') == 1
    assert result.days[5].stops[0].city == '喀什'
    assert result.days[8].stops[0].city == '阿克苏'


@pytest.mark.asyncio
async def test_outline_allows_travel_only_day_but_rejects_wrong_city_order():
    from backend.generation_batches import BatchStructureError
    request, _, _ = sample()
    request.days = 4
    outline = {'title': 'trip', 'summary': '', 'days': [{'date': str(request.start_date + timedelta(days=i)),
        'cities': [] if i == 3 else ['上海'], 'title': '', 'group_budget': 100} for i in range(4)]}
    async def call(messages, phase, tokens):
        if phase == 'outline':
            return reply(outline)
        start = int(phase.split('_')[1]) - 1
        return reply({'days': [{'date': outline['days'][i]['date'], 'title': '', 'stops': []} for i in range(start, start + 2)]})
    assert len((await generate_batches(request, [], call, AsyncMock(), {})).days) == 4
    outline['days'][1]['cities'] = ['北京']
    with pytest.raises(BatchStructureError) as failure:
        await generate_batches(request, [], call, AsyncMock(), {})
    assert failure.value.phase == 'outline'
    assert failure.value.validation_issues[0]['path'] == 'journey_constraints'
    assert 'outline' not in str(failure.value)


@pytest.mark.asyncio
@pytest.mark.parametrize('fallback_valid', [True, False])
async def test_outline_failure_uses_research_for_one_public_schema_fallback(monkeypatch, fallback_valid):
    import backend.agent as agent
    from contextlib import asynccontextmanager
    request, itinerary, _ = sample()
    request.days = 4
    itinerary.days = [itinerary.days[0].model_copy(deep=True) for _ in range(4)]
    for i, day in enumerate(itinerary.days):
        day.date = request.start_date + timedelta(days=i)
    class Research:
        tool_calls = []
        def model_dump(self, **kwargs):
            return {'role': 'assistant', 'content': 'researched facts'}
    phases = []
    async def complete(client, emit, phase, **kwargs):
        phases.append(phase)
        if phase == 'research':
            return SimpleNamespace(choices=[SimpleNamespace(message=Research())])
        if phase == 'outline':
            return reply({'title': 'invalid'})
        assert 'researched facts' in str(kwargs['messages'])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=itinerary.model_dump_json() if fallback_valid else '{}'))])
    @asynccontextmanager
    async def client():
        yield SimpleNamespace()
    monkeypatch.setattr(agent, 'model_client', client)
    monkeypatch.setattr(agent, 'complete_model', complete)
    monkeypatch.setattr(agent.settings, 'llm_key', 'test')
    monkeypatch.setattr(agent.settings, 'amap_key', 'test')
    if fallback_valid:
        result = await agent.generate_itinerary(request, SimpleNamespace(), AsyncMock())
        assert len(result.days) == 4
        assert phases == ['research', 'outline', 'outline', 'itinerary']
    else:
        from backend.generation_batches import BatchStructureError
        with pytest.raises(BatchStructureError) as failure:
            await agent.generate_itinerary(request, SimpleNamespace(), AsyncMock())
        assert failure.value.phase == 'itinerary'
        assert failure.value.validation_issues[0] == {'path': 'title', 'type': 'missing'}
        assert phases == ['research', 'outline', 'outline', 'itinerary', 'itinerary']


@pytest.mark.asyncio
async def test_large_research_tool_list_executes_six_and_keeps_protocol_paired(monkeypatch):
    import backend.agent as agent
    from openai.types.chat import ChatCompletionMessage
    from contextlib import asynccontextmanager
    request, itinerary, _ = sample()
    first = ChatCompletionMessage(role='assistant', content=None, tool_calls=[
        {'id': f'call{i}', 'type': 'function', 'function': {'name': 'search_places',
        'arguments': '{"keywords":"site","city":"上海"}'}} for i in range(20)])
    calls = 0
    async def complete(client, emit, phase, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return SimpleNamespace(choices=[SimpleNamespace(message=first)])
        if calls == 2:
            messages = kwargs['messages']
            assistant = next(m for m in messages if m.get('tool_calls'))
            results = [m for m in messages if m['role'] == 'tool']
            assert len(assistant['tool_calls']) == len(results) == 6
            return SimpleNamespace(choices=[SimpleNamespace(message=ChatCompletionMessage(role='assistant', content='done'))])
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=itinerary.model_dump_json()))])
    @asynccontextmanager
    async def client():
        yield SimpleNamespace()
    search = AsyncMock(return_value=[{'id': 'p', 'name': 'site'}])
    monkeypatch.setattr(agent, 'model_client', client)
    monkeypatch.setattr(agent, 'complete_model', complete)
    monkeypatch.setattr(agent.settings, 'llm_key', 'test')
    monkeypatch.setattr(agent.settings, 'amap_key', 'test')
    await agent.generate_itinerary(request, SimpleNamespace(search=search), AsyncMock())
    assert search.await_count == 6 and calls == 3


@pytest.mark.asyncio
async def test_outline_truncation_can_fallback_but_network_errors_propagate():
    from backend.generation_batches import BatchStructureError
    from backend.model_stream import ModelOutputError
    call = AsyncMock(side_effect=ModelOutputError('length', 'outline', reason='length'))
    with pytest.raises(BatchStructureError) as failure:
        await generate_batches(sample()[0], [], call, AsyncMock(), {})
    assert failure.value.validation_issues == [{'path': 'model_output', 'type': 'length'}]
    assert call.await_count == 1
    call = AsyncMock(side_effect=ConnectionError('offline'))
    with pytest.raises(ConnectionError):
        await generate_batches(sample()[0], [], call, AsyncMock(), {})
    assert call.await_count == 1
