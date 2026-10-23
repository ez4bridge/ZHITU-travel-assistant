"""Bounded web references, evidence validation and immutable itinerary integration."""
import asyncio
from datetime import datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import HTTPException
import httpx
import pytest

import backend.main as main
import backend.place_guide as guide
from backend.models import GuideQuery, PlaceGuide, PlanRecord, StudyOptions, ValidationReport
from backend.storage import Store
from backend.validation import normalized_input, validate_core
from test_core import sample


def plan(study=False, id='plan-one'):
    request, itinerary, routes = sample()
    if study:
        request.study = StudyOptions()
    itinerary.days[0].stops[0].name = '上海青年历史纪念馆'
    return PlanRecord(id=id, created_at=datetime.now(timezone.utc), request=request, itinerary=itinerary, routes=routes,
        validation=ValidationReport.model_validate(validate_core(normalized_input(request, itinerary, routes))))


def source(id='S1', kind='official'):
    return {'id': id, 'title': '上海青年历史纪念馆参观公告', 'url': 'https://www.example.gov.cn/news',
        'kind': kind, 'publication_date': '2026-10-01', 'retrieved_at': guide.now().isoformat(),
        'text': '发布时间：2026-10-01。上海青年历史纪念馆周二至周日开放，九点开始接待参观。入口设在正门，建议按一层到二层顺序参观。'}


def draft():
    return {'overview': [], 'opening': [
        {'text': '公告提及周二至周日开放。',
         'source_id': 'S1', 'evidence': '上海青年历史纪念馆周二至周日开放'}], 'visit': [
        {'text': '可从正门进入，按一层到二层的顺序参观。',
         'source_id': 'S1', 'evidence': '入口设在正门，建议按一层到二层顺序参观'}], 'tips': []}


def saved(p, text='已保存的参考'):
    return PlaceGuide(plan_id=p.id, stop_id='a', place_name=p.itinerary.days[0].stops[0].name,
        visit_date=p.request.start_date, status='partial', queried_at=guide.now(),
        sources=[{k: v for k, v in source().items() if k != 'text'}],
        items=[{**draft()['visit'][0], 'category': 'visit', 'text': text}], usage={'attempted_calls': 1, 'total_tokens': 234})


@pytest.mark.parametrize('url,kind', [
    ('https://www.shanghai.gov.cn/a', 'official'), ('https://library.sh.cn/a', 'official'),
    ('https://m.ctrip.com/a', 'guide'), ('https://www.mafengwo.cn/a', 'guide'),
    ('https://www.thepaper.cn/a', 'public'), ('https://zh.wikipedia.org/a', 'public'),
    ('https://paper.ce.cn/a', 'public'), ('https://www.gmw.cn/a', 'public'),
    ('https://museum-travel.com/a', 'public'), ('https://gov.cn.evil.com/a', 'public'),
    ('https://gov.cn.evil.example/a', None), ('http://127.0.0.1/a', None),
    ('http://192.168.1.1/a', None), ('http://169.254.169.254/a', None),
    ('http://[::1]/a', None), ('http://localhost/a', None), ('http://museum.local/a', None),
    ('http://2130706433/a', None), ('http://0177.0.0.1/a', None),
    ('https://user:password@www.shanghai.gov.cn/a', None), ('https://www.shanghai.gov.cn:123/a', None),
    (None, None), ('javascript:alert(1)', None),
])
def test_public_sources_are_open_but_official_labels_and_url_safety_are_preserved(url, kind):
    assert guide.source_kind(url) == kind


def test_official_domains_are_extensible_and_dates_not_inferred(monkeypatch):
    monkeypatch.setattr(guide.settings, 'guide_official_domains', ('museum.example.cn',))
    assert guide.source_kind('https://www.museum.example.cn/a') == 'official'
    assert guide.publication_date('2026-10-18 抓取，2025年旅游攻略') == ''
    assert guide.publication_date('发布时间：2026年10月18日 正文') == '2026年10月18日'


def test_short_place_names_require_city_and_other_places_rejected():
    stop = SimpleNamespace(name='外滩', matched_name='外滩')
    assert guide.relevant('上海外滩游览攻略', stop, '上海市')
    assert not guide.relevant('重庆外滩游览攻略', stop, '上海')
    assert not guide.relevant('上海豫园参观攻略', stop, '上海')
    assert guide.relevant('《新青年》编辑部参观公告', plan().itinerary.days[0].stops[0]) is False


def test_guide_matches_real_place_without_activity_or_map_anchor_labels():
    stop = SimpleNamespace(name='包公园（含包公祠）', matched_name='包公园（地图入口）',
        place_name='包公园', area_name='包公园')
    assert guide.place_names(stop)[0] == '包公园'
    assert guide.relevant('合肥包公园景点介绍和参观安排。', stop, '合肥市')
    assert not guide.relevant('其他城市的包公园介绍。', stop, '合肥市')
    assert not guide.relevant('合肥地图入口及游览指南。', stop, '合肥市')
    # Legacy records can lack the explicit search name, but still have the verified map name.
    legacy = SimpleNamespace(name=stop.name, matched_name=stop.matched_name)
    assert guide.relevant('合肥包公园景点介绍和参观安排。', legacy, '合肥市')


def test_common_place_names_and_combined_activities_require_city_context():
    stop = SimpleNamespace(name='城市记忆纪念塔与滨江公园', place_name='临海市人民城市记忆纪念塔')
    assert guide.relevant('临海城市记忆纪念塔的建筑与参观介绍。', stop, '临海市')
    assert guide.relevant('临海滨江公园的入口与步道。', stop, '临海市')
    assert guide.relevant('临海城市记忆纪念塔的建筑介绍。',
                          SimpleNamespace(name='临海市人民城市记忆纪念塔'), '临海市')
    assert not guide.relevant('其他城市的城市记忆纪念塔建筑介绍。', stop, '临海市')
    assert not guide.relevant('其他城市的滨江公园入口。', stop, '临海市')
    assert not guide.relevant('临海博物馆开放信息。', stop, '临海市')


@pytest.mark.asyncio
async def test_search_fetch_caps_identity_and_no_summary_as_evidence(monkeypatch):
    monkeypatch.setattr(guide.settings, 'guide_search_key', 'fake-key')
    bodies = []
    def handle(request):
        payload = json.loads(request.content)
        bodies.append((request.url.path, payload))
        if request.url.path.endswith('search'):
            return httpx.Response(200, json={'results': [
                {'title': '上海青年历史纪念馆', 'url': 'https://example.gov.cn/a', 'summary': '不要将摘要当作正文'},
                {'title': '同名其他城市纪念馆', 'url': 'https://example.gov.cn/b', 'content': '其他城市的博物馆介绍。' * 30},
                {'title': '攻略', 'url': 'https://www.mafengwo.cn/c', 'content': ('上海青年历史纪念馆入口位于正门，可按楼层参观。' * 80)},
                {'title': '公开百科', 'url': 'https://zh.wikipedia.org/a', 'content': '上海青年历史纪念馆的建筑及历史介绍。' * 10}]})
        return httpx.Response(200, json={'content': '上海青年历史纪念馆周二至周日开放，入口位于正门。' * 80})
    calls = {'web_calls': []}
    p = plan()
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        result = await guide.GuideService().sources(http, calls, p, p.itinerary.days[0].stops[0])
    assert len(result) == 3
    assert all(len(s['text']) <= 900 for s in result)
    assert sum(len(s['text']) for s in result) <= 3600
    assert not any('不要将摘要当作正文' in s['text'] for s in result)
    assert sum(path.endswith('search') for path, _ in bodies) == 2
    assert sum(path.endswith('fetch') for path, _ in bodies) == 1
    assert any(s['url'] == 'https://zh.wikipedia.org/a' and s['kind'] == 'public' for s in result)
    assert all('site:' not in payload['query'] for path, payload in bodies if path.endswith('search'))
    assert all('api' not in str(payload) for _, payload in bodies)


def model_client(monkeypatch, output, captured):
    async def create(**kwargs):
        captured.append(kwargs)
        if isinstance(output, Exception):
            raise output
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(output, ensure_ascii=False)))],
            usage=SimpleNamespace(model_dump=lambda **_: {'prompt_tokens': 100, 'completion_tokens': 50, 'total_tokens': 150}))
    class Client:
        chat = SimpleNamespace(completions=SimpleNamespace(create=create))
        async def __aenter__(self): return self
        async def __aexit__(self, *_): pass
    monkeypatch.setattr(guide, 'AsyncOpenAI', lambda **_: Client())
    monkeypatch.setattr(guide.settings, 'llm_key', 'fake-model')
    monkeypatch.setattr(guide.settings, 'guide_search_key', 'fake-search')


@pytest.mark.asyncio
async def test_failed_search_reports_network_failure_without_calling_model(monkeypatch):
    captured, p = [], plan()
    model_client(monkeypatch, draft(), captured)
    client_factory = httpx.AsyncClient
    def fail(request):
        raise httpx.ConnectError('network unavailable', request=request)
    monkeypatch.setattr(guide.httpx, 'AsyncClient', lambda **kwargs:
                        client_factory(transport=httpx.MockTransport(fail), **kwargs))
    result = await guide.GuideService().generate(p, p.itinerary.days[0].stops[0], p.request.start_date)
    assert result.status == 'unavailable' and '联网搜索未成功' in result.message
    assert result.usage['search_calls'] == 2 and not captured


@pytest.mark.asyncio
async def test_four_page_budget_balances_search_groups_and_caps_fetch(monkeypatch):
    monkeypatch.setattr(guide.settings, 'guide_search_key', 'fake-search')
    p, calls = plan(), {'web_calls': []}
    searches = 0
    def handle(request):
        nonlocal searches
        if request.url.path.endswith('search'):
            searches += 1
            host = 'example.gov.cn' if searches == 1 else 'www.mafengwo.cn'
            return httpx.Response(200, json={'results': [
                {'title': '上海青年历史纪念馆', 'url': f'https://{host}/{i}',
                 'content': '' if i < 2 else source()['text'] * 50} for i in range(8)]})
        return httpx.Response(200, json={'content': source()['text'] * 50})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        sources = await guide.GuideService().sources(http, calls, p, p.itinerary.days[0].stops[0])
    assert len(sources) == 4
    assert sum(s['kind'] == 'official' for s in sources) == 2
    assert sum(s['kind'] == 'guide' for s in sources) == 2
    assert sum(len(s['text']) for s in sources) <= 3600
    assert max(len(s['text']) for s in sources) <= 900
    assert sum(c['kind'] == 'fetch' for c in calls['web_calls']) == 2


@pytest.mark.asyncio
async def test_sources_keep_introduction_passages_alongside_opening_notices(monkeypatch):
    monkeypatch.setattr(guide.settings, 'guide_search_key', 'fake-search')
    p, calls, queries = plan(), {'web_calls': []}, []
    notice = '上海青年历史纪念馆开放时间为九点到十六点，购票和预约见公告，可在入口咨询讲解场次。' * 40
    introduction = '上海青年历史纪念馆景点简介：石库门建筑保留历史特色，展陈、展览和陈列介绍青年刊物传播，推荐看点是出版主题。' * 25
    def handle(request):
        payload = json.loads(request.content)
        queries.append(payload['query'])
        if '推荐看点' in payload['query']:
            return httpx.Response(200, json={'results': [
                {'title': '上海青年历史纪念馆开放公告', 'url': 'https://example.gov.cn/notice0', 'content': notice},
                *[{'title': '上海青年历史纪念馆建筑与展陈', 'url': f'https://www.thepaper.cn/article{i}',
                   'content': notice + introduction} for i in range(2)]]})
        return httpx.Response(200, json={'results': [
            {'title': '上海青年历史纪念馆开放公告', 'url': f'https://example.gov.cn/notice{i}', 'content': notice}
            for i in range(5)]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        sources = await guide.GuideService().sources(http, calls, p, p.itinerary.days[0].stops[0])
    assert len(queries) == 2 and len(sources) == 4
    assert len({s['url'] for s in sources}) == 4
    assert sum('石库门建筑' in s['text'] for s in sources) == 2
    assert sum('开放时间为九点到十六点' in s['text'] for s in sources) >= 2
    assert all(len(s['text']) <= 900 for s in sources)
    assert all(s['text'] in (notice if s['kind'] == 'official' else notice + introduction) for s in sources)
    assert sum(c['kind'] == 'fetch' for c in calls['web_calls']) == 0


@pytest.mark.asyncio
async def test_model_once_and_only_known_exact_evidence_survives(monkeypatch):
    captured, service, p = [], guide.GuideService(), plan()
    output = draft()
    output['overview'] = [
        {**draft()['visit'][0], 'source_id': 'MADEUP'},
        {**draft()['visit'][0], 'evidence': '不存在的预约成功证据'}]
    output['opening'].append({**draft()['visit'][0], 'evidence': ' ' * 8})
    model_client(monkeypatch, output, captured)
    monkeypatch.setattr(service, 'sources', AsyncMock(return_value=[source()]))
    result = await service.generate(p, p.itinerary.days[0].stops[0], p.request.start_date)
    assert result.status == 'ready' and len(result.items) == 2
    assert len(captured) == 1 and captured[0]['max_tokens'] == 1600
    assert result.usage['total_tokens'] == 150 and result.usage['attempted_calls'] == 1
    assert 'text' not in result.sources[0].model_dump()
    assert result.message == ''
    task = json.loads(captured[0]['messages'][1]['content'])
    assert task['visit_date'] == str(p.request.start_date) and 'sources' in task
    assert 'itinerary' not in task and 'history' not in task


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['missing_section', 'too_many_opening', 'all_empty'])
async def test_incomplete_or_overfilled_model_format_never_triggers_repair(failure, monkeypatch):
    captured, service, p = [], guide.GuideService(), plan()
    output = draft()
    if failure == 'missing_section':
        del output['overview']
    elif failure == 'too_many_opening':
        output['opening'] *= 3
    else:
        output = {key: [] for key in ('overview', 'opening', 'visit', 'tips')}
    model_client(monkeypatch, output, captured)
    monkeypatch.setattr(service, 'sources', AsyncMock(return_value=[source()]))
    result = await service.generate(p, p.itinerary.days[0].stops[0], p.request.start_date)
    assert result.status == 'unavailable' and result.items == []
    assert len(captured) == 1 and result.usage['attempted_calls'] == 1


@pytest.mark.asyncio
async def test_overview_uses_model_output_with_exact_evidence_and_persists(monkeypatch, tmp_path):
    captured, service, p = [], guide.GuideService(), plan()
    page = {**source(), 'text': source()['text'] + '馆内主题展介绍青年刊物的传播，保留石库门建筑外观，展览沿时间线呈现出版活动。团队最多接待25人，请随工作人员分组。'}
    introduction = {'text': '这里以青年刊物传播为主题，可留意石库门外观，沿展览时间线了解出版活动。',
                    'source_id': 'S1', 'evidence': '馆内主题展介绍青年刊物的传播，保留石库门建筑外观'}
    output = {**draft(), 'overview': [introduction, {**introduction, 'text': '这里有全市最大的原版藏品。', 'evidence': '全市最大的原版藏品'}],
              'tips': [{'text': '团队参观请按工作人员安排分组，每团最多25人。', 'source_id': 'S1', 'evidence': '团队最多接待25人，请随工作人员分组'}]}
    model_client(monkeypatch, output, captured)
    monkeypatch.setattr(service, 'sources', AsyncMock(return_value=[page]))
    result = await service.generate(p, p.itinerary.days[0].stops[0], p.request.start_date)
    assert len(captured) == 1 and result.usage['attempted_calls'] == 1
    overviews = [item for item in result.items if item.category == 'overview']
    assert len(overviews) == 1 and overviews[0].model_dump() == {**introduction, 'category': 'overview'}
    assert len(result.items) == 4 and result.status == 'ready'
    assert [item.category for item in result.items] == ['overview', 'opening', 'visit', 'tips']
    references = guide.GuideStore(tmp_path / 'plans.sqlite3')
    references.save(result)
    assert references.get(p.id, result.stop_id).model_dump() == result.model_dump()


@pytest.mark.asyncio
async def test_ticket_channel_and_opening_times_use_cited_source_once(monkeypatch):
    captured, service, p = [], guide.GuideService(), plan()
    text = '上海青年历史纪念馆开放时间为09:00至16:30，16:00停止入场。2026年常设展门票20元，通过场馆官方微信公众号购票。'
    page = {**source(), 'text': text}
    output = {'overview': [], 'visit': [], 'tips': [], 'opening': [
        {'text': '公告票价：2026年常设展20元，可通过场馆官方微信公众号购票。',
         'source_id': 'S1', 'evidence': '2026年常设展门票20元，通过场馆官方微信公众号购票'},
        {'text': '公告开放时间为09:00–16:30，16:00停止入场。',
         'source_id': 'S1', 'evidence': '开放时间为09:00至16:30，16:00停止入场'}]}
    model_client(monkeypatch, output, captured)
    monkeypatch.setattr(service, 'sources', AsyncMock(return_value=[page]))
    result = await service.generate(p, p.itinerary.days[0].stops[0], p.request.start_date)
    assert len(captured) == 1 and result.usage['attempted_calls'] == 1
    assert len(result.items) == 2
    assert '20元' in result.items[0].text and '微信公众号' in result.items[0].text
    assert '16:00' in result.items[1].text
    assert all(item.source_id == result.sources[0].id for item in result.items)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', ['no_body', 'malformed', 'provider', 'timeout', 'no_key', 'public_official'])
async def test_failures_never_trigger_extra_model_rounds(failure, monkeypatch):
    captured, service, p = [], guide.GuideService(timeout=.01), plan()
    output = {} if failure == 'malformed' else RuntimeError('signed request secret') if failure == 'provider' else draft()
    if failure == 'public_official':
        output['opening'][0]['text'] = '官方确认开放。'
    model_client(monkeypatch, output, captured)
    async def sources(*_):
        if failure == 'timeout':
            await asyncio.sleep(1)
        return [] if failure == 'no_body' else [source(kind='public' if failure == 'public_official' else 'official')]
    monkeypatch.setattr(service, 'sources', sources)
    if failure == 'no_key':
        monkeypatch.setattr(guide.settings, 'guide_search_key', '')
    result = await service.generate(p, p.itinerary.days[0].stops[0], p.request.start_date)
    assert len(captured) <= 1
    if failure == 'public_official':
        assert result.status == 'partial' and len(result.items) == 1
        assert result.message == ''
    else:
        assert result.status == 'unavailable'
    if failure in ('no_body', 'timeout', 'no_key'):
        assert captured == [] and result.usage['total_tokens'] == 0
    if failure == 'provider':
        assert result.usage['total_tokens'] is None and 'secret' not in result.message


@pytest.mark.asyncio
@pytest.mark.parametrize('study', [False, True])
async def test_http_persistence_version_isolation_refresh_and_immutability(study, monkeypatch, tmp_path):
    p = plan(study)
    path = tmp_path / 'plans.sqlite3'
    store, references, service = Store(path), guide.GuideStore(path), guide.GuideService()
    store.save(p)
    child = p.model_copy(update={'id': 'child', 'parent_id': p.id, 'revision': 2}, deep=True)
    store.save(child)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'guide_store', references)
    monkeypatch.setattr(main, 'guide_service', service)
    work = AsyncMock(return_value=saved(p))
    monkeypatch.setattr(service, 'generate', work)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://test') as http:
        url = f'/api/plans/{p.id}/stops/a/guide'
        assert (await http.get(f'/api/plans/{p.id}/place-guides')).json() == []
        assert (await http.post(url, json={})).json()['status'] == 'partial'
        assert (await http.post(url, json={})).status_code == 200
        assert work.await_count == 1
        assert (await http.get(f'/api/plans/{p.id}/place-guides')).json()[0]['items'][0]['text'] == '已保存的参考'
        assert (await http.get('/api/plans/child/place-guides')).json() == []
        unavailable = saved(p).model_copy(update={'status': 'unavailable', 'items': [], 'message': '暂不可用'})
        work.return_value = unavailable
        assert (await http.post(url, json={'refresh': True})).json()['status'] == 'unavailable'
        assert references.get(p.id, 'a').items[0].text == '已保存的参考'
        assert store.get(p.id).model_dump() == p.model_dump()
        assert store.get('child').model_dump() == child.model_dump()
        assert (await http.post('/api/plans/missing/stops/a/guide', json={})).status_code == 404
        assert (await http.post(f'/api/plans/{p.id}/stops/fake/guide', json={})).status_code == 404
        assert (await http.post(url, json={'url': 'http://127.0.0.1'})).status_code == 422
        p.itinerary.days[0].stops[1].kind = 'meal'
        other = p.model_copy(update={'id': 'other'}, deep=True); store.save(other)
        assert (await http.post('/api/plans/other/stops/b/guide', json={})).status_code == 422
        assert work.await_count == 2


@pytest.mark.asyncio
async def test_disconnect_cancels_and_inflight_rejects_duplicate(monkeypatch, tmp_path):
    p = plan()
    path = tmp_path / 'plans.sqlite3'
    store, service = Store(path), guide.GuideService()
    store.save(p)
    monkeypatch.setattr(main, 'store', store)
    monkeypatch.setattr(main, 'guide_service', service)
    monkeypatch.setattr(main, 'guide_store', guide.GuideStore(path))
    entered, cancelled = asyncio.Event(), asyncio.Event()
    async def pending(*_):
        entered.set()
        try: await asyncio.Event().wait()
        finally: cancelled.set()
    monkeypatch.setattr(service, 'generate', pending)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=False))
    first = asyncio.create_task(main.place_guide(p.id, 'a', GuideQuery(), request))
    await entered.wait()
    with pytest.raises(HTTPException) as duplicate:
        await main.place_guide(p.id, 'a', GuideQuery(), request)
    assert duplicate.value.status_code == 409
    request.is_disconnected.return_value = True
    with pytest.raises(HTTPException) as disconnected:
        await first
    assert disconnected.value.status_code == 499 and cancelled.is_set()
    assert service.active == set() and main.guide_store.list(p.id) == []
