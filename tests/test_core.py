import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.models import TripRequest, Itinerary, RouteLeg, PlanRecord, cents
from backend.amap import Amap, signed_params
from backend.validation import Validator, normalized_input, validate_core
from backend.storage import Store
from backend.config import settings


def sample():
    request = TripRequest(destination='上海', start_date='2026-10-21', days=1, people=3, budget=1000)
    itinerary = Itinerary.model_validate({'title': '沿江漫游', 'summary': '一段城市旅行', 'days': [{
        'date': '2026-10-21', 'title': '沿江', 'stops': [
            {'id': 'a', 'name': '外滩', 'start': '09:00', 'end': '10:00', 'poi_id': 'POI1',
             'location': [121.49, 31.23], 'location_status': 'verified'},
            {'id': 'b', 'name': '豫园', 'start': '10:30', 'end': '12:00', 'poi_id': 'POI2',
             'location': [121.49, 31.22], 'location_status': 'verified'}],
        'expenses': [
            {'label': '午餐', 'category': 'food', 'unit_cost': '50.25', 'quantity': 2, 'basis': 'person'},
            {'label': '车费', 'category': 'transport', 'unit_cost': '30', 'basis': 'group'}]}]})
    routes = [RouteLeg(day_index=0, from_id='a', to_id='b', mode='walking', available=True,
                       duration_s=1200, distance_m=1800, polyline=[(121.49, 31.23), (121.49, 31.22)],
                       queried_at=datetime.now(timezone.utc))]
    return request, itinerary, routes


def test_sign_chinese_and_literal_plus_before_encoding():
    params = signed_params({'keywords': '上海+外滩', 'city': '上海', 'sig': 'discard'}, 'public', 'private')
    expected = hashlib.md5('city=上海&key=public&keywords=上海+外滩private'.encode('utf-8')).hexdigest()
    assert params['sig'] == expected
    assert params['keywords'] == '上海+外滩'


def test_money_is_whole_party_integer_cents():
    request, itinerary, routes = sample()
    report = validate_core(normalized_input(request, itinerary, routes))
    assert report['total_cents'] == 33150  # 50.25 x 2 meals x 3 people + 30 group cost
    assert report['budget_cents'] == 100000
    assert report['status'] == 'passed_with_unknowns'
    assert not report['errors']
    assert {'estimated_prices', 'opening_hours'} <= {i['code'] for i in report['unknowns']}
    assert cents('1.005') == 101


def test_time_overlap_insufficient_transfer_duplicate_and_budget():
    request, itinerary, routes = sample()
    request.budget = 100
    second = itinerary.days[0].stops[1]
    second.start = '09:50'; second.poi_id = 'POI1'
    report = validate_core(normalized_input(request, itinerary, routes))
    assert {'over_budget', 'time_overlap', 'transfer_time'} <= {i['code'] for i in report['errors']}
    assert {i['code'] for i in report['warnings']} == {'duplicate_stop'}


def test_route_duration_rounds_up_and_unknown_is_not_passed():
    request, itinerary, routes = sample()
    itinerary.days[0].stops[1].start = '10:20'
    routes[0].duration_s = 1201
    assert 'transfer_time' in {i['code'] for i in validate_core(normalized_input(request, itinerary, routes))['errors']}
    routes[0].available = False
    itinerary.days[0].stops[1].location = None
    report = validate_core(normalized_input(request, itinerary, routes))
    assert {'missing_location', 'missing_route'} <= {i['code'] for i in report['unknowns']}
    assert report['status'] == 'passed_with_unknowns'


@pytest.mark.asyncio
async def test_recheck_revision_recomputes_costs_routes_and_errors():
    request, itinerary, routes = sample()
    service = Validator(preferred='python')
    routes[0].duration_s = 3600
    assert (await service.validate(request, itinerary, routes)).errors
    itinerary.days[0].stops[1].start = '11:00'
    itinerary.days[0].expenses[0].unit_cost = 10
    report = await service.validate(request, itinerary, routes)
    assert not report.errors and report.total_cents == 9000


@pytest.mark.asyncio
async def test_missing_cangjie_falls_back(tmp_path):
    service = Validator(executable=tmp_path / 'missing.exe')
    assert (await service.validate(*sample())).backend == 'python'


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['crash', 'timeout', 'malformed', 'parity'])
async def test_cangjie_failures_do_not_break_python(tmp_path, failure):
    exe = tmp_path / 'validator.exe'; exe.write_bytes(b'fake binary')
    data = normalized_input(*sample())
    fixture = tmp_path / 'fixtures.json'
    fixture.write_text(json.dumps([{'name': 'valid', 'input': data}]), encoding='utf-8')
    service = Validator(executable=exe, fixtures=fixture)
    if failure == 'parity':
        service.call_cangjie = AsyncMock(return_value={**validate_core(data), 'status': 'passed'})
    else:
        service.call_cangjie = AsyncMock(side_effect={'crash': RuntimeError('crash'), 'timeout': asyncio.TimeoutError(),
                                                      'malformed': json.JSONDecodeError('bad', '', 0)}[failure])
    report = await service.validate(*sample())
    assert report.backend == 'python' and report.total_cents == 33150
    assert '回退' in report.diagnostic


@pytest.mark.asyncio
async def test_cangjie_runtime_failure_after_parity_falls_back(tmp_path):
    exe = tmp_path / 'validator.exe'; exe.write_bytes(b'fake binary')
    data = normalized_input(*sample())
    fixture = tmp_path / 'fixtures.json'; fixture.write_text(json.dumps([{'name': 'valid', 'input': data}]))
    service = Validator(executable=exe, fixtures=fixture)
    service.call_cangjie = AsyncMock(side_effect=[validate_core(data), RuntimeError('runtime crash')])
    report = await service.validate(*sample())
    assert report.backend == 'python' and '运行失败' in report.diagnostic
    assert not await service.ready()
    assert service.call_cangjie.await_count == 2


@pytest.mark.asyncio
async def test_fixture_changes_invalidate_native_approval(tmp_path):
    exe = tmp_path / 'validator.exe'; exe.write_bytes(b'fake binary')
    data = normalized_input(*sample())
    fixture = tmp_path / 'fixtures.json'; fixture.write_text(json.dumps([{'name': 'valid', 'input': data}]))
    service = Validator(executable=exe, fixtures=fixture)
    service.call_cangjie = AsyncMock(return_value=validate_core(data))
    assert await service.ready()
    fixture.write_text('[]')
    assert not await service.ready()
    assert (await service.validate(*sample())).backend == 'python'


@pytest.mark.asyncio
async def test_model_coordinates_cannot_be_trusted_and_search_is_city_limited(monkeypatch):
    request, itinerary, _ = sample()
    seen = []
    amap = Amap('上海')
    async def mock_request(path, params):
        seen.append(params)
        return {'pois': []}
    amap.request = mock_request
    stop = itinerary.days[0].stops[0]
    await amap.resolve(stop)
    assert stop.location is None and stop.location_status == 'unknown' and not stop.poi_id
    assert seen[0]['city'] == '上海' and seen[0]['citylimit'] == 'true'
    await amap.close()


@pytest.mark.asyncio
async def test_real_geometry_is_required_for_available_route():
    amap = Amap('上海')
    amap.request = AsyncMock(return_value={'route': {'paths': [{'distance': '10', 'duration': '10', 'steps': []}]}})
    with pytest.raises(RuntimeError, match='路线形状'):
        await amap.route([121.49, 31.23], [121.49, 31.22], 'walking')
    await amap.close()


def test_history_is_independent_and_previous_revision_preserved(tmp_path):
    store = Store(tmp_path / 'plans.sqlite3')
    request, itinerary, routes = sample()
    from backend.models import ValidationReport
    report = ValidationReport(**validate_core(normalized_input(request, itinerary, routes)))
    p1 = PlanRecord(id='first', created_at=datetime.now(timezone.utc), request=request, itinerary=itinerary, routes=routes, validation=report)
    p2 = p1.model_copy(update={'id': 'second', 'parent_id': 'first', 'revision': 2})
    store.save(p1); store.save(p2)
    assert store.get('first').revision == 1 and store.get('second').parent_id == 'first'
    assert len(store.list()) == 2


def test_expense_categories_use_same_decimal_rounding_as_total():
    from backend.models import ValidationReport
    request, itinerary, routes = sample()
    itinerary.days[0].expenses[0].unit_cost = '1.005'
    report = ValidationReport(**validate_core(normalized_input(request, itinerary, routes)))
    plan = PlanRecord(id='rounded', created_at=datetime.now(timezone.utc), request=request, itinerary=itinerary, routes=routes, validation=report)
    assert plan.expense_totals['food'] == 606
    assert sum(plan.expense_totals.values()) == report.total_cents == 3606


def test_api_missing_keys_and_public_map_config_never_exposes_secrets(monkeypatch):
    from backend.main import app
    monkeypatch.setattr(settings, 'llm_key', '')
    monkeypatch.setattr(settings, 'amap_sign_secret', 'private-sign-do-not-send')
    monkeypatch.setattr(settings, 'amap_js_sec', 'private-js-do-not-send')
    with TestClient(app) as client:
        assert not client.get('/api/health').json()['capabilities']['planning']
        config = client.get('/api/map-config').text
        assert 'private-sign-do-not-send' not in config and 'private-js-do-not-send' not in config
        response = client.post('/api/plans/generate', json=sample()[0].model_dump(mode='json'))
        assert 'event: error' in response.text and 'LLM_API_KEY' in response.text
        assert client.post('/api/plans/generate', json={'days': 0}).status_code == 422
        assert client.get('/api/plans/unknown').status_code == 404


def test_safe_errors_mask_all_credentials(monkeypatch):
    monkeypatch.setattr(settings, 'llm_key', 'key-private')
    monkeypatch.setattr(settings, 'amap_sign_secret', 'sign-private')
    assert settings.safe_error('key-private sign-private sig=' + 'a' * 32) == '[hidden] [hidden] sig=[hidden]'


@pytest.mark.asyncio
async def test_two_requests_keep_separate_results(monkeypatch, tmp_path):
    import backend.main as main
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'concurrent.sqlite3'))
    monkeypatch.setattr(main, 'validator', Validator(preferred='python'))
    async def fake_generate(request, amap, emit, previous=None, instruction='', rail=None):
        await asyncio.sleep(.01)
        itinerary = sample()[1]
        itinerary.title = request.destination
        return itinerary
    monkeypatch.setattr(main, 'generate_itinerary', fake_generate)
    monkeypatch.setattr(Amap, 'enrich', AsyncMock(return_value=sample()[2]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as client:
        first = sample()[0].model_dump(mode='json')
        second = {**first, 'destination': '杭州'}
        responses = await asyncio.gather(client.post('/api/plans/generate', json=first), client.post('/api/plans/generate', json=second))
    records = []
    for response in responses:
        chunks = response.text.split('event: result\ndata: ')
        records.append(json.loads(chunks[1].split('\n\n')[0]))
    assert records[0]['id'] != records[1]['id']
    assert [r['itinerary']['title'] for r in records] == ['上海', '杭州']


@pytest.mark.asyncio
async def test_stream_disconnect_cancels_inflight_work(monkeypatch):
    import backend.main as main
    started, cancelled = asyncio.Event(), asyncio.Event()
    async def slow_generate(*args, **kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    monkeypatch.setattr(main, 'generate_itinerary', slow_generate)
    class LocalRequest:
        async def is_disconnected(self): return False
    response = main.stream_plan(LocalRequest(), sample()[0])
    body = response.body_iterator
    assert 'event: status' in await body.__anext__()
    await asyncio.wait_for(started.wait(), 1)
    await body.aclose()
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_native_cangjie_unicode_and_malformed_json():
    executable = Path(__file__).resolve().parents[1] / 'cangjie' / 'bin' / 'validator.exe'
    if not executable.is_file():
        pytest.skip('Optional native module not built')
    data = normalized_input(*sample())
    data['unknowns'].append({'code': 'utf8', 'message': '上海+\"\\\n🏞', 'day_index': None, 'stop_id': None})
    process = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE)
    output, _ = await process.communicate(json.dumps(data, ensure_ascii=True).encode())
    assert process.returncode == 0 and json.loads(output) == validate_core(data)
    process = await asyncio.create_subprocess_exec(str(executable), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    output, _ = await process.communicate(b'{malformed input')
    assert process.returncode != 0 and not output


def test_blank_destination_and_revision_rejected():
    from pydantic import ValidationError
    from backend.models import RevisionRequest
    with pytest.raises(ValidationError):
        TripRequest(destination='  ', start_date='2026-10-21')
    with pytest.raises(ValidationError):
        RevisionRequest(instruction='  ')


@pytest.mark.asyncio
@pytest.mark.parametrize('repair_succeeds', [True, False])
async def test_malformed_model_json_gets_one_bounded_repair(monkeypatch, repair_succeeds):
    import backend.agent as agent
    from types import SimpleNamespace
    class ResearchMessage:
        tool_calls = []
        def model_dump(self, **kwargs):
            return {'role': 'assistant', 'content': '资料已整理'}
    def response(content):
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])
    research = SimpleNamespace(choices=[SimpleNamespace(message=ResearchMessage())])
    final = sample()[1].model_dump_json() if repair_succeeds else '{}'
    complete = AsyncMock(side_effect=[research, response('{bad json'), response(final)])
    class FakeClient:
        chat = SimpleNamespace(completions=SimpleNamespace(create=complete))
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    monkeypatch.setattr(agent, 'AsyncOpenAI', lambda **kwargs: FakeClient())
    monkeypatch.setattr(settings, 'llm_key', 'fake')
    monkeypatch.setattr(settings, 'amap_key', 'fake')
    emit = AsyncMock()
    if repair_succeeds:
        result = await agent.generate_itinerary(sample()[0], SimpleNamespace(), emit)
        assert result.title == '沿江漫游'
    else:
        with pytest.raises(RuntimeError, match='有效的行程结构'):
            await agent.generate_itinerary(sample()[0], SimpleNamespace(), emit)
    assert complete.await_count == 3  # research + invalid JSON + only one repair
