import json
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from backend.knowledge import KnowledgeBase, load_bundle
from backend.models import (Expense, PlanRecord, RouteLeg, Stop, StudyContext, StudyOptions,
                            StudyPatch, StudyQuestion, TripRequest)
from backend.study_agent import (EvidenceSession, StudyDraft, answer_study, generate_study_plan,
                                 materialize, resolve_study_stop, update_study_request)
from backend.validation import Validator
from tools.import_study_materials import import_documents


@pytest.fixture
def corpus(tmp_path):
    """Original synthetic paragraphs only; no knowledge tables or model credentials."""
    source = tmp_path / 'input'
    source.mkdir()
    articles = {
        'old.md': ('老渔阳里与新青年', '《新青年》编辑部在老渔阳里南昌路100弄2号。这里曾开展编辑与学习活动。\n\n2026年9月1日起该馆周二至周日9:00-16:30开放，周一闭馆，16:00停止入场。免费，无需预约。'),
        'new.md': ('新渔阳里与外国语学社', '新渔阳里有外国语学社，青年在这里学习外语。中国社会主义青年团中央机关旧址纪念馆地址为淮海中路567弄1-6号。'),
        'print.md': ('又新印刷所', '又新印刷所承担了《共产党宣言》中译本的印刷出版，现址入口需要核实。'),
        'translation.txt': ('陈望道的翻译', '陈望道回浙江义乌分水塘翻译《共产党宣言》，随后携译稿来上海。'),
        'demolished.md': ('三益里遗址', '三益里相关旧居原址已拆除，现为其他建筑，无法进入原建筑参观。'),
    }
    for name, (title, text) in articles.items():
        (source / name).write_text(f'# {title}\n\n来源：https://example.cn/{name}\n\n---\n\n{text}', encoding='utf-8')
    for name in ('places.json', 'curated_facts.json', 'relations.jsonl', 'entities.json', 'visit_policies.json'):
        (source / name).write_text('THIS MUST NOT BE LOADED', encoding='utf-8')
    target = tmp_path / 'corpus'
    import_documents(source, target)
    return target


@pytest.fixture
def kb(corpus):
    result = KnowledgeBase(corpus)
    assert result.ready
    return result


def cid(kb, text):
    return next(c['id'] for c in kb.chunks.values() if text in c['text'])


def trip(**changes):
    return TripRequest(destination='上海', start_date='2026-10-21', start_time='09:00', days=1,
                       budget=500, study=StudyOptions(**changes))


def draft(kb):
    return StudyDraft(title='从报刊到青年学习', summary='通过两处旧址了解交流与学习。', stops=[
        {'name': '《新青年》编辑部旧址', 'poi_id': 'AMAP_OLD', 'start': '09:00', 'end': '09:40',
         'why_selected': '了解报刊活动', 'guide': '在老渔阳里了解《新青年》编辑活动。',
         'observation_task': '建议观察旧址门牌', 'fact_ids': [cid(kb, '南昌路100弄')]},
        {'name': '中国社会主义青年团中央机关旧址纪念馆', 'poi_id': 'AMAP_NEW', 'start': '10:00', 'end': '10:40',
         'why_selected': '了解青年学习', 'guide': '了解外国语学社。',
         'observation_task': '建议记录一个学习线索', 'fact_ids': [cid(kb, '学习外语')]},
    ])


class Message:
    def __init__(self, content='', calls=None):
        self.content, self.tool_calls = content, calls or []

    def model_dump(self, **kwargs):
        result = {'role': 'assistant', 'content': self.content or None}
        if self.tool_calls:
            result['tool_calls'] = [{'id': c.id, 'type': 'function', 'function': {
                'name': c.function.name, 'arguments': c.function.arguments}} for c in self.tool_calls]
        return result


def completion(value=None, calls=None):
    return SimpleNamespace(choices=[SimpleNamespace(message=Message(
        json.dumps(value, ensure_ascii=False) if value is not None else '', calls))])


class FakeClient:
    def __init__(self, responses):
        self.create = AsyncMock(side_effect=responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class FakeMap:
    def __init__(self):
        self.places = {}

    async def search(self, keywords):
        name, address, pid = ('《新青年》编辑部旧址', '南昌路100弄2号', 'AMAP_OLD') if ('新青年' in keywords or '老渔阳里' in keywords) else (
            ('又新印刷所旧址', '黄陂南路近复兴中路', 'AMAP_PRINT') if '印刷' in keywords else
            ('中国社会主义青年团中央机关旧址纪念馆', '淮海中路567弄1-6号', 'AMAP_NEW'))
        result = [{'id': pid, 'name': name, 'address': address,
                   'location': [121.47 + (0 if pid == 'AMAP_OLD' else .001), 31.23]}]
        self.places[keywords] = result
        return result

    async def route(self, origin, destination, mode):
        return {'distance_m': 500, 'duration_s': 600, 'polyline': [origin, destination]}


async def prepared_map():
    amap = FakeMap()
    await amap.search('《新青年》编辑部旧址')
    await amap.search('新渔阳里')
    return amap


def all_evidence(kb):
    session = EvidenceSession(kb, date(2026, 10, 21))
    session.remember(list(kb.chunks.values()))
    return session


def test_import_raw_only_and_integrity(corpus, kb):
    assert set(p.name for p in corpus.iterdir()) == {'documents.jsonl'}
    assert kb.catalog()['source_count'] == 5
    assert kb.catalog()['retrieval_mode'] == 'raw_text' and not kb.catalog()['places']
    catalog = kb.catalog()
    assert len(catalog['documents']) == catalog['source_count']
    assert {document['title'] for document in catalog['documents']} == {
        '老渔阳里与新青年', '新渔阳里与外国语学社', '又新印刷所', '陈望道的翻译', '三益里遗址'}
    assert all(set(document) == {'id', 'title'} for document in catalog['documents'])
    path = corpus / 'documents.jsonl'
    documents = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    documents[0]['text'] += '改写正文'
    path.write_text('\n'.join(json.dumps(d) for d in documents), encoding='utf-8')
    assert not KnowledgeBase(corpus).ready


def test_import_failure_does_not_overwrite_existing(corpus, tmp_path):
    before = (corpus / 'documents.jsonl').read_bytes()
    source = tmp_path / 'invalid'
    source.mkdir()
    (source / 'bad.md').write_text('# 错误\n来源：javascript:alert(1)\n---\n正文', encoding='utf-8')
    with pytest.raises(ValueError, match='HTTP'):
        import_documents(source, corpus)
    assert (corpus / 'documents.jsonl').read_bytes() == before


def test_no_grade_and_existing_single_day_scope():
    with pytest.raises(ValidationError):
        StudyOptions(audience='middle')
    with pytest.raises(ValidationError):
        TripRequest(destination='杭州', start_date='2026-10-21', days=1, study=StudyOptions())
    with pytest.raises(ValidationError):
        TripRequest(destination='上海', start_date='2026-10-21', days=2, study=StudyOptions())
    with pytest.raises(ValidationError):
        trip(end_time='08:00')


def test_retrieval_keeps_original_and_offsets(kb):
    hits = kb.search('陈望道在哪里翻译共产党宣言')
    assert '义乌' in hits[0]['text']
    assert not kb.search('量子纠缠实验设备')
    for chunk in kb.chunks.values():
        text = kb.texts[chunk['source_ids'][0]]
        assert chunk['text'] == text[chunk['start_char']:chunk['end_char']]
    # No precomputed date filtering: the model receives original announcements.
    assert kb.search('周一闭馆', date(2026, 10, 21)) == kb.search('周一闭馆', date(2026, 10, 19))


@pytest.mark.asyncio
async def test_unseen_citation_and_fabricated_poi_are_rejected(kb):
    amap = await prepared_map()
    with pytest.raises(ValueError, match='引用'):
        materialize(draft(kb), trip(), kb, EvidenceSession(kb, date(2026, 10, 21)), amap)
    invalid = draft(kb).model_copy(deep=True)
    invalid.stops[0].poi_id = 'INVENTED'
    with pytest.raises(ValueError, match='POI'):
        materialize(invalid, trip(), kb, all_evidence(kb), amap)


@pytest.mark.asyncio
async def test_duplicate_and_unproven_opening_policy(kb):
    amap = await prepared_map()
    invalid = draft(kb).model_copy(deep=True)
    invalid.stops[1].poi_id = 'AMAP_OLD'
    with pytest.raises(ValueError, match='重复'):
        materialize(invalid, trip(), kb, all_evidence(kb), amap)
    invalid = draft(kb).model_copy(deep=True)
    invalid.stops[0].visit_policy.status = 'documented_open'
    _, context = materialize(invalid, trip(), kb, all_evidence(kb), amap)
    assert context.policies['AMAP_OLD'].status == 'unknown'
    invalid.stops[0].visit_policy.fact_ids = invalid.stops[0].fact_ids
    invalid.stops[0].visit_policy.status = 'documented_closed'
    _, context = materialize(invalid, trip(), kb, all_evidence(kb), amap)
    assert context.policies['AMAP_OLD'].status == 'documented_closed'


@pytest.mark.asyncio
@pytest.mark.parametrize('status', ['documented_closed', 'not_visitable', 'documented_open'])
async def test_visit_conditions_are_advisories_not_route_errors(kb, status):
    value = draft(kb).model_dump()
    value['stops'][0]['visit_policy'] = {
        'status': status, 'note': '收录资料的参观提醒', 'fact_ids': value['stops'][0]['fact_ids'],
        'windows': [['11:00', '12:00']], 'last_entry': '08:59'}
    proposal = StudyDraft.model_validate(value)
    itinerary, context = materialize(proposal, trip(), kb, all_evidence(kb), await prepared_map())
    for index, stop in enumerate(itinerary.days[0].stops):
        stop.id, stop.location_status, stop.location = str(index), 'verified', (121.47, 31.23)
    routes = [RouteLeg(day_index=0, from_id='0', to_id='1', mode='walking', available=True,
        distance_m=500, duration_s=600, polyline=[(121.47, 31.23), (121.48, 31.24)], queried_at=datetime.now(timezone.utc))]
    report = await Validator().validate(trip(), itinerary, routes, context)
    assert not report.errors
    assert context.policies['AMAP_OLD'].status == status
    assert context.policies['AMAP_OLD'].last_entry == '08:59'


@pytest.mark.asyncio
async def test_unproven_or_malformed_visit_advisory_does_not_block_history(kb):
    proposal = draft(kb)
    proposal.stops[0].visit_policy.status = 'documented_closed'
    proposal.stops[0].visit_policy.fact_ids = ['NOT_RETRIEVED']
    _, context = materialize(proposal, trip(), kb, all_evidence(kb), await prepared_map())
    assert context.policies['AMAP_OLD'].status == 'unknown'
    proposal.stops[0].visit_policy.fact_ids = proposal.stops[0].fact_ids
    proposal.stops[0].visit_policy.windows = [('invalid', '12:00')]
    _, context = materialize(proposal, trip(), kb, all_evidence(kb), await prepared_map())
    assert context.policies['AMAP_OLD'].windows == []
    assert '核对原文' in context.policies['AMAP_OLD'].note
    proposal.stops[0].fact_ids = ['NOT_RETRIEVED']
    with pytest.raises(ValueError, match='引用'):
        materialize(proposal, trip(), kb, all_evidence(kb), await prepared_map())


@pytest.mark.asyncio
async def test_monday_closed_place_still_generates_without_extra_round(kb, monkeypatch):
    calls = [SimpleNamespace(id='call_' + str(i), function=SimpleNamespace(name='search_places',
        arguments=json.dumps({'keywords': name}))) for i, name in enumerate(['《新青年》编辑部旧址', '新渔阳里'])]
    proposal = draft(kb)
    proposal.stops[0].visit_policy.status = 'documented_closed'
    proposal.stops[0].visit_policy.note = '收录公告记载周一闭馆，请以最新安排为准'
    proposal.stops[0].visit_policy.fact_ids = proposal.stops[0].fact_ids
    client = FakeClient([completion(calls=calls), completion(), completion(proposal.model_dump()), completion({'accepted': True})])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    monkeypatch.setattr('backend.study_agent.settings.amap_key', 'fake-test-key')
    request = trip().model_copy(update={'start_date': date(2026, 10, 26)})
    itinerary, routes, context, report = await generate_study_plan(request, kb, FakeMap(), AsyncMock(), Validator())
    assert itinerary.days[0].date == date(2026, 10, 26)
    assert len(itinerary.days[0].stops) == 2 and not report.errors
    assert context.policies['AMAP_OLD'].status == 'documented_closed'
    assert client.create.await_count == 4
    review = client.create.call_args_list[-1].kwargs['messages']
    assert json.loads(review[1]['content'])['actual_data']['visit_policy_mode'] == 'advisory_only'
    assert '只放warnings' in review[0]['content']


@pytest.mark.asyncio
async def test_limits_and_raw_snapshot_use_existing_validator(kb):
    request, amap = trip(end_time='10:30', max_walking_m=500, required_place_ids=['AMAP_PRINT']), await prepared_map()
    itinerary, context = materialize(draft(kb), request, kb, all_evidence(kb), amap)
    for i, stop in enumerate(itinerary.days[0].stops):
        stop.id, stop.location_status, stop.location = str(i), 'verified', (121.47, 31.23)
    routes = [RouteLeg(day_index=0, from_id='0', to_id='1', mode='walking', available=True,
                      distance_m=800, duration_s=600, queried_at=datetime.now(timezone.utc))]
    report = await Validator().validate(request, itinerary, routes, context)
    assert {'study_end_time', 'study_required_place', 'study_walking_limit'} <= {i.code for i in report.errors}
    assert 'opening_hours' in {i.code for i in report.unknowns}
    assert context.retrieval_mode == 'raw_text' and set(context.places) == {'AMAP_OLD', 'AMAP_NEW'}
    assert all(f.role == 'raw_passage' for f in context.facts)


@pytest.mark.asyncio
async def test_revision_uses_plan_pois_not_global_place_catalog(kb, monkeypatch):
    itinerary, context = materialize(draft(kb), trip(), kb, all_evidence(kb), await prepared_map())
    previous = SimpleNamespace(itinerary=itinerary, study_context=context)
    client = FakeClient([completion({'end_time': '11:00', 'required_place_ids': ['AMAP_NEW']})])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    revised = await update_study_request(trip(), '缩短到两小时，保留新渔阳里', None, kb, previous)
    assert revised.study.end_time == '11:00' and revised.study.required_place_ids == ['AMAP_NEW']
    with pytest.raises(ValueError, match='已有行程'):
        await update_study_request(trip(), '保留某处', StudyPatch(required_place_ids=['FAKE']), kb, previous)


@pytest.mark.asyncio
async def test_map_uses_only_queried_poi_and_rejects_mismatched_name():
    amap = await prepared_map()
    stop = Stop(name='《新青年》编辑部旧址', start='09:00', end='10:00', location=(1, 2))
    await resolve_study_stop(stop, {'id': 'AMAP_OLD'}, amap)
    assert stop.location == (121.47, 31.23) and stop.location_status == 'verified'
    with pytest.raises(ValueError, match='名称'):
        await resolve_study_stop(stop, {'id': 'AMAP_NEW'}, amap)


def answer_value(kb, text='陈望道在义乌分水塘翻译。', needle='义乌'):
    return {'status': 'supported', 'paragraphs': [{'text': text, 'fact_ids': [cid(kb, needle)]}]}


@pytest.mark.asyncio
async def test_qa_cites_original_and_semantically_reviews(kb, monkeypatch):
    client = FakeClient([completion(), completion(answer_value(kb)), completion({'accepted': True})])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    answer = await answer_study(StudyQuestion(question='陈望道在哪里翻译共产党宣言'), kb)
    assert answer.status == 'supported' and '义乌' in answer.context.facts[0].text
    assert answer.context.retrieval_mode == 'raw_text' and client.create.await_count == 3
    fake = completion({'status': 'supported', 'paragraphs': [{'text': '伪造出处', 'fact_ids': ['NOT_FOUND']}]})
    client = FakeClient([completion(), fake, fake])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    with pytest.raises(RuntimeError, match='引用'):
        await answer_study(StudyQuestion(question='陈望道在哪里翻译'), kb)


@pytest.mark.asyncio
async def test_qa_contradictory_opening_is_repaired_once(kb, monkeypatch):
    bad = answer_value(kb, '可以进去。当天周一闭馆，进不去。', '周一闭馆')
    good = answer_value(kb, '按原文的周一闭馆规则，当天不能入内。', '周一闭馆')
    client = FakeClient([completion(), completion(bad), completion({'accepted': False, 'issues': ['首句与闭馆结论矛盾']}),
                         completion(good), completion({'accepted': True})])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    result = await answer_study(StudyQuestion(question='2026年10月19日去老渔阳里能进去吗'), kb)
    assert result.paragraphs[0].text == good['paragraphs'][0]['text']
    assert client.create.await_count == 5


@pytest.mark.asyncio
async def test_qa_no_hits_never_calls_provider(kb, monkeypatch):
    client = FakeClient([])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    result = await answer_study(StudyQuestion(question='量子纠缠实验设备'), kb)
    assert result.status == 'insufficient_evidence'
    client.create.assert_not_awaited()


@pytest.mark.asyncio
async def test_qa_followup_can_research_after_empty_initial_search(kb, monkeypatch):
    from backend.qa_usage import UsageCollector
    original = kb.search
    count = 0

    def initial_miss(query):
        nonlocal count
        count += 1
        return [] if count == 1 else original(query)

    call = SimpleNamespace(id='fresh_lookup', function=SimpleNamespace(name='search_knowledge',
                           arguments=json.dumps({'query': '陈望道 义乌 翻译'})))
    client = FakeClient([completion(calls=[call]), completion(), completion(answer_value(kb)), completion({'accepted': True})])
    monkeypatch.setattr(kb, 'search', initial_miss)
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    memory = {'recent_turns': [{'question': '陈望道与这条路线有什么关系？', 'answer_excerpt': '他参与翻译与传播。', 'place': '上海'}]}
    usage = UsageCollector()
    answer = await answer_study(StudyQuestion(question='他在哪里翻译的？'), kb, memory=memory, usage=usage)
    assert answer.status == 'supported' and '义乌' in answer.paragraphs[0].text
    assert usage.result()['attempted_calls'] == 4
    review = json.loads(client.create.call_args_list[-1].kwargs['messages'][1]['content'])
    assert review['question']['conversation_context'] == memory


@pytest.mark.asyncio
async def test_agent_tools_and_one_constraint_repair(kb, monkeypatch):
    calls = [SimpleNamespace(id='call_' + str(i), function=SimpleNamespace(name='search_places',
             arguments=json.dumps({'keywords': name}))) for i, name in enumerate(['《新青年》编辑部旧址', '新渔阳里'])]
    invalid = draft(kb).model_dump()
    invalid['stops'][1]['end'] = '15:00'
    client = FakeClient([completion(calls=calls), completion(), completion(invalid),
                         completion(draft(kb).model_dump()), completion({'accepted': True})])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    monkeypatch.setattr('backend.study_agent.settings.amap_key', 'fake-test-key')
    events = []

    async def emit(kind, value):
        events.append((kind, value))

    from backend.models import WeatherDay, WeatherSnapshot
    weather = WeatherSnapshot(requested_city='上海', city='上海市', status='available',
        fetched_at=datetime.now(timezone.utc), days=[WeatherDay(date=trip().start_date, status='available', day_weather='小雨')])
    itinerary, routes, context, report = await generate_study_plan(trip(), kb, FakeMap(), emit,
        Validator(), weather=weather)
    assert len(itinerary.days[0].stops) == 2 and routes[0].available and not report.errors
    assert client.create.await_count == 5
    first_payload = json.loads(client.create.call_args_list[0].kwargs['messages'][1]['content'])
    review_payload = json.loads(client.create.call_args_list[-1].kwargs['messages'][1]['content'])
    assert first_payload['weather']['days'][0]['day_weather'] == '小雨'
    assert review_payload['actual_data']['weather'] == first_payload['weather']
    assert set(context.places) == {'AMAP_OLD', 'AMAP_NEW'}
    assert any('修复一次' in v.get('message', '') for _, v in events)
    repair_messages = client.create.call_args_list[3].kwargs['messages']
    assert any('actual_routes' in (m.get('content') or '') for m in repair_messages)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['walking', 'driving'])
async def test_study_route_tool_preserves_decimal_costs(kb, monkeypatch, mode):
    class PricedMap(FakeMap):
        async def route(self, origin, destination, mode):
            return {**await super().route(origin, destination, mode),
                    'taxi_cost_yuan': Decimal('12.30'), 'tolls_yuan': Decimal('0.10'),
                    'fare_yuan': None}

    def call(name, arguments, index):
        return SimpleNamespace(id=f'call_{index}', function=SimpleNamespace(
            name=name, arguments=json.dumps(arguments, ensure_ascii=False)))

    proposal = draft(kb)
    proposal.expenses = [Expense(
        label='交通估算', category='transport', unit_cost=Decimal('12.30'), quantity=1, basis='group')]
    searches = [call('search_places', {'keywords': name}, index)
                for index, name in enumerate(['《新青年》编辑部旧址', '新渔阳里'])]
    route = call('get_route', {'origin_id': 'AMAP_OLD', 'destination_id': 'AMAP_NEW'}, 2)
    client = FakeClient([completion(calls=searches), completion(calls=[route]), completion(),
                         completion(proposal.model_dump(mode='json')), completion({'accepted': True})])
    monkeypatch.setattr('backend.study_agent.llm_client', lambda: client)
    monkeypatch.setattr('backend.study_agent.settings.amap_key', 'fake-test-key')
    request = trip().model_copy(update={'mode': mode})
    itinerary, routes, _, report = await generate_study_plan(
        request, kb, PricedMap(), AsyncMock(), Validator())

    messages = client.create.call_args_list[2].kwargs['messages']
    result = json.loads(next(message['content'] for message in messages
                             if message.get('tool_call_id') == 'call_2'))
    assert result['taxi_cost_yuan'] == '12.30'
    assert result['tolls_yuan'] == '0.10'
    assert result['fare_yuan'] is None
    assert isinstance(result['distance_m'], int) and result['duration_min'] == 10
    assert 'polyline' not in result
    assert routes[0].available and not report.errors
    assert itinerary.days[0].expenses[0].unit_cost == Decimal('12.30')
    assert report.total_cents == 1230


def test_old_context_keeps_legacy_fields():
    context = StudyContext.model_validate({'corpus_version': 'old', 'facts': [
        {'id': 'H001', 'title': '旧记录', 'text': '保留旧版本快照', 'source_ids': ['S001']}], 'sources': [], 'policies': {}})
    assert context.retrieval_mode == 'legacy_structured' and context.facts[0].id == 'H001'


def test_api_qa_independent_and_no_global_place_catalog(monkeypatch, kb, tmp_path, offline_domestic_scope):
    import backend.main as main
    from backend.storage import Store
    monkeypatch.setattr(main, 'knowledge', kb)
    monkeypatch.setattr(main, 'store', Store(tmp_path / 'history.sqlite3'))
    with TestClient(main.app) as client:
        catalog = client.get('/api/study/catalog').json()
        assert catalog['retrieval_mode'] == 'raw_text'
        assert len(catalog['documents']) == 5
        result = client.post('/api/study/ask', json={'question': '量子纠缠实验设备'})
        assert result.status_code == 200 and result.json()['status'] == 'insufficient_evidence'
        assert main.store.list() == []
        assert client.post('/api/study/ask', json={'question': '这里', 'place_id': 'FAKE'}).status_code == 422
        monkeypatch.setattr(main, 'knowledge', KnowledgeBase(tmp_path / 'missing'))
        missing = client.get('/api/study/catalog').json()
        assert missing['ready'] is False and missing['documents'] == []
        assert client.get('/api/plans').status_code == 200
        assert client.post('/api/plans/generate', json=trip().model_dump(mode='json')).status_code == 503


@pytest.mark.asyncio
async def test_qa_disconnect_cancels_model_work(monkeypatch, kb):
    import asyncio
    import backend.main as main
    from fastapi import HTTPException
    cancelled = asyncio.Event()

    async def pending(*args):
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(main, 'knowledge', kb)
    monkeypatch.setattr(main, 'answer_study', pending)
    request = SimpleNamespace(is_disconnected=AsyncMock(return_value=True))
    with pytest.raises(HTTPException) as error:
        await main.study_ask(StudyQuestion(question='陈望道在哪里翻译'), request)
    assert error.value.status_code == 499 and cancelled.is_set()
