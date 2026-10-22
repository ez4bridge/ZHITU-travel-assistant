import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap
from backend.day_revision import merge_day, generate_day_itinerary
from backend.feasibility import schedule_journey
from test_core import sample


def previous_plan():
    trip, itinerary, routes = sample()
    trip.days = 2
    second = itinerary.days[0].model_copy(deep=True)
    second.date += timedelta(days=1)
    for stop in second.stops:
        stop.id += '-day2'
    itinerary.days.append(second)
    return SimpleNamespace(request=trip, itinerary=itinerary, routes=routes)


@pytest.mark.parametrize('wrapper', ['plain', 'day', 'days', 'fenced'])
def test_single_day_wrappers_preserve_other_days_and_transfers(wrapper):
    previous = previous_plan()
    old = previous.itinerary.model_dump()
    day = previous.itinerary.days[1].model_dump(mode='json')
    day['title'] = '新的第二天'
    day['stops'][0]['id'] = 'a'  # A model often numbers its sole day from 1 again.
    day['expenses'][0]['stop_id'] = 'a'
    payload = {'day': day} if wrapper == 'day' else {'days': [day]} if wrapper == 'days' else day
    content = json.dumps(payload, ensure_ascii=False)
    if wrapper == 'fenced':
        content = '```json\n' + content + '\n```'
    result = merge_day(content, previous, 1)
    assert result.days[0].model_dump() == old['days'][0]
    assert result.transfers == previous.itinerary.transfers
    assert result.days[1].title == '新的第二天'
    assert result.days[1].stops[0].id != 'a'
    assert result.days[1].expenses[0].stop_id == result.days[1].stops[0].id
    assert previous.itinerary.model_dump() == old


def test_wrong_day_and_unknown_fields_do_not_overwrite_history():
    previous = previous_plan()
    with pytest.raises(ValueError, match='只能修改'):
        merge_day(previous.itinerary.days[0].model_dump_json(), previous, 1)
    payload = previous.itinerary.days[1].model_dump(mode='json')
    payload['invented_field'] = 'invalid'
    with pytest.raises(ValueError):
        merge_day(json.dumps(payload), previous, 1)


@pytest.mark.asyncio
async def test_day_revision_uses_one_short_model_output_without_research(monkeypatch):
    import backend.day_revision as module
    previous = previous_plan()
    day = previous.itinerary.days[1].model_dump(mode='json')
    day['title'] = '新第二天'
    create = AsyncMock(return_value=SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'day': day})))]))
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(module, 'AsyncOpenAI', lambda **kwargs: Client())
    amap = SimpleNamespace(search=AsyncMock())
    result = await generate_day_itinerary(previous.request, amap, AsyncMock(), previous, '少一个景点', 1)
    assert create.await_count == 1
    assert create.await_args.kwargs['max_tokens'] == 5000
    assert 'tools' not in create.await_args.kwargs
    amap.search.assert_not_awaited()
    assert result.days[0] == previous.itinerary.days[0]
    assert result.days[1].title == '新第二天'


@pytest.mark.asyncio
async def test_independent_pois_and_route_legs_run_concurrently_in_stable_order():
    _, itinerary, _ = sample()
    day = itinerary.days[0]
    third = day.stops[0].model_copy(update={'id': 'c', 'name': '第三站', 'start': '13:00', 'end': '14:00'}, deep=True)
    day.stops.append(third)
    amap = Amap('上海')
    resolving, routing = set(), set()
    all_resolving, all_routing = asyncio.Event(), asyncio.Event()
    async def resolve(stop):
        resolving.add(stop.id)
        if len(resolving) == 3:
            all_resolving.set()
        await all_resolving.wait()
    async def route(origin, destination, mode, city):
        routing.add((tuple(origin), tuple(destination)))
        if len(routing) == 2:
            all_routing.set()
        await all_routing.wait()
        return {'duration_s': 600, 'distance_m': 1000, 'polyline': [origin, destination]}
    amap.resolve = resolve
    amap.route_in_city = route
    try:
        result = await asyncio.wait_for(amap.enrich(itinerary, 'walking'), 2)
        assert [leg.from_id for leg in result] == ['a', 'b']
        assert all_resolving.is_set() and all_routing.is_set()
    finally:
        await amap.close()


def test_local_time_repair_cannot_move_another_day():
    previous = previous_plan()
    previous.itinerary.days[0].stops[0].start = '08:00'
    with pytest.raises(ValueError, match='其他日期'):
        schedule_journey(previous.request, previous.itinerary, only_day=1)
