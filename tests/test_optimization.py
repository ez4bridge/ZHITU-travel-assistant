import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.amap import Amap, MapServiceError
from backend.config import settings
from backend.models import PlanRecord, RouteOption, Stop, TripRequest
from backend.planning import repair_schedule, recost_transport
from backend.storage import Store
from backend.validation import Validator, normalized_input, validate_core
from test_core import sample


@pytest.mark.asyncio
async def test_map_signature_failure_stops_repeated_requests(monkeypatch):
    monkeypatch.setattr(settings, 'amap_key', 'test-key')
    amap = Amap('上海')
    await amap.client.aclose()
    calls = []
    def response(request):
        calls.append(request)
        return httpx.Response(200, json={'status': '0', 'infocode': '10007'})
    amap.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    try:
        for _ in range(2):
            with pytest.raises(MapServiceError, match='鉴权') as error:
                await amap.search('外滩')
            assert error.value.fatal
        assert len(calls) == 1
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_transient_network_failure_retries_once(monkeypatch):
    monkeypatch.setattr(settings, 'amap_key', 'test-key')
    amap = Amap('上海')
    await amap.client.aclose()
    calls = []
    def response(request):
        calls.append(request)
        raise httpx.ConnectError('offline', request=request)
    amap.client = httpx.AsyncClient(transport=httpx.MockTransport(response))
    try:
        with pytest.raises(MapServiceError) as error:
            await amap.search('外滩')
        assert error.value.code == 'map_network' and len(calls) == 2
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_optional_mode_permission_does_not_poison_all_map_queries(monkeypatch):
    monkeypatch.setattr(settings,'amap_key','test-key')
    amap=Amap('上海')
    await amap.client.aclose()
    amap.client=httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200,json={'status':'0','infocode':'10012'})))
    try:
        with pytest.raises(MapServiceError) as error:
            await amap.request('/v4/direction/bicycling',{})
        assert not error.value.fatal and amap.fatal_error is None
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_stationary_break_has_no_fake_navigation_segment():
    request, itinerary, _ = sample()
    day = itinerary.days[0]
    day.stops.insert(1, Stop(name='原地休息', kind='break', start='10:00', end='10:15',
                             location_behavior='same_as_previous'))
    amap = Amap('上海')
    amap.places = {'外滩': [{'id': 'POI1', 'name': '外滩', 'location': [121.49,31.23], 'address': ''}],
                   '豫园': [{'id': 'POI2', 'name': '豫园', 'location': [121.49,31.22], 'address': ''}]}
    amap.route = AsyncMock(return_value={'duration_s':1200, 'distance_m':1800,
        'polyline': [(121.49,31.23),(121.49,31.22)]})
    try:
        routes = await amap.enrich(itinerary, 'walking')
        assert routes[0].stationary and routes[0].available and not routes[0].polyline
        assert routes[1].available and amap.route.await_count == 1
        repair_schedule(itinerary, routes, request.start_time)
        assert not validate_core(normalized_input(request, itinerary, routes))['errors']
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_ambiguous_meal_is_not_assigned_first_branch():
    amap = Amap('上海')
    amap.search = AsyncMock(return_value=[
        {'id':'a','name':'裕兴记人民广场店','location':[121.4,31.2]},
        {'id':'b','name':'裕兴记南京路店','location':[121.5,31.3]}])
    stop = Stop(name='午餐（裕兴记）',kind='meal',start='12:00',end='13:00')
    try:
        await amap.resolve(stop)
        assert stop.location is None and '多个' in stop.location_note
        amap.search.assert_awaited_once_with('裕兴记')
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_placeholder_meal_is_not_searched_as_real_restaurant():
    amap = Amap('上海')
    amap.search = AsyncMock()
    try:
        stop=Stop(name='晚餐（新天地一带）',kind='meal',start='18:00',end='19:00')
        await amap.resolve(stop)
        assert stop.location is None
        amap.search.assert_not_awaited()
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_fatal_alternative_cancels_other_inflight_modes():
    _,itinerary,routes=sample()
    amap=Amap('上海')
    amap.resolve=AsyncMock()
    pending=set()
    async def route(origin,destination,mode):
        if mode=='walking':
            return {'distance_m':1800,'duration_s':1200,'polyline':routes[0].polyline}
        if mode=='driving':
            raise MapServiceError('map_auth','鉴权失败',True)
        pending.add(mode)
        try:
            await asyncio.Event().wait()
        finally:
            pending.remove(mode)
    amap.route=route
    try:
        with pytest.raises(MapServiceError):
            await amap.enrich(itinerary,'walking',compare_modes=True)
        assert not pending
    finally:
        await amap.close()


def test_all_unlocated_plan_is_blocked():
    request,itinerary,routes=sample()
    for stop in itinerary.days[0].stops:
        stop.location=None; stop.location_status='unknown'
    routes[0].available=False
    result=validate_core(normalized_input(request,itinerary,routes))
    assert result['status']=='issues'
    assert [i['code'] for i in result['errors']]==['missing_location','missing_location','missing_route']


def test_geometry_missing_cannot_claim_available_even_in_legacy_record():
    request,itinerary,routes=sample()
    routes[0].polyline=[]
    assert 'missing_route' in {e['code'] for e in validate_core(normalized_input(request,itinerary,routes))['errors']}


def test_schedule_repair_is_bounded_and_does_not_cross_midnight():
    request,itinerary,routes=sample()
    routes[0].duration_s=3600
    changes=repair_schedule(itinerary,routes,request.start_time)
    assert changes and itinerary.days[0].stops[1].start=='11:00'
    assert itinerary.days[0].stops[1].end=='12:30'
    routes[0].duration_s=90000
    before=itinerary.model_dump()
    changes=repair_schedule(itinerary,routes,request.start_time)
    assert '无法' in changes[0] and itinerary.model_dump()==before


def test_transport_fares_round_each_leg_before_party_multiplication():
    _,itinerary,routes=sample()
    routes[0].mode='transit';routes[0].fare_yuan=Decimal('1.005')
    routes.append(routes[0].model_copy(update={'fare_yuan':Decimal('2.005')}))
    recost_transport(itinerary,routes,0)
    transport=[e for e in itinerary.days[0].expenses if e.category=='transport']
    assert len(transport)==1 and transport[0].unit_cost==Decimal('3.02')


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['transit','bicycling','taxi'])
async def test_new_modes_parse_provider_shapes(mode):
    amap=Amap('上海')
    path={'distance':'2000','duration':'900','steps':[{'polyline':'121.49,31.23;121.49,31.22'}]}
    if mode=='transit':
        payload={'route':{'transits':[{'distance':'2000','duration':'900','cost':'4',
            'segments':[{'walking':{'steps':path['steps']}, 'bus':{'buslines':[{'name':'地铁1号线','polyline':'121.49,31.23;121.49,31.22'}]}}]}]}}
    elif mode=='bicycling':
        payload={'data':{'paths':[path]}}
    else:
        payload={'route':{'paths':[path]}}
    amap.request=AsyncMock(return_value=payload)
    try:
        result=await amap.route([121.49,31.23],[121.49,31.22],mode)
        assert result['duration_s']==900 and len(result['polyline'])>=2
        if mode=='transit':
            assert result['fare_yuan']==Decimal('4') and result['instructions']==['地铁1号线']
        if mode=='taxi':
            assert result['fare_yuan'] is None and '候车' in result['note']
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_transit_empty_walking_and_unknown_fare_remain_usable_but_unpriced():
    amap=Amap('上海')
    amap.request=AsyncMock(return_value={'route':{'transits':[{'distance':'2000','duration':'900','cost':'--',
        'segments':[{'walking':[], 'railway':[], 'bus':{'buslines':[{'name':'地铁','polyline':'121.49,31.23;121.49,31.22'}]}}]}]}})
    try:
        route=await amap.route([121.49,31.23],[121.49,31.22],'transit')
        assert route['fare_yuan'] is None and route['duration_s']==900
    finally:
        await amap.close()


def saved_plan(store):
    request,itinerary,routes=sample()
    routes[0].alternatives=[RouteOption(mode='transit',available=True,distance_m=2200,duration_s=3600,
        fare_yuan=Decimal('4'),polyline=routes[0].polyline,note='票价估算'),
        RouteOption(mode='walking',available=True,distance_m=1800,duration_s=1200,polyline=routes[0].polyline)]
    report=Validator(preferred='python')
    from backend.models import ValidationReport
    record=PlanRecord(id='original',created_at=datetime.now(timezone.utc),request=request,itinerary=itinerary,
        routes=routes,validation=ValidationReport(**validate_core(normalized_input(request,itinerary,routes))))
    store.save(record)
    return record


def test_route_selection_reprices_party_and_preserves_history(monkeypatch,tmp_path):
    import backend.main as main
    store=Store(tmp_path/'plans.sqlite3')
    monkeypatch.setattr(main,'store',store)
    monkeypatch.setattr(main,'validator',Validator(preferred='python'))
    original=saved_plan(store)
    with TestClient(main.app) as client:
        response=client.post('/api/plans/original/route',json={'day_index':0,'from_id':'a','to_id':'b','mode':'transit'})
        assert response.status_code==200
        result=response.json()
        assert result['id']=='original' and result['parent_id'] is None and result['revision']==1
        assert result['routing_revision']==1 and len(store.list())==1
        assert result['expense_totals']['transport']==1200  # 4 yuan x 3 people
        assert result['itinerary']['days'][0]['stops'][1]['start']=='11:00'
        assert store.get('original').routing_revision==1
        assert client.post('/api/plans/original/route',json={'day_index':0,'from_id':'a','to_id':'b','mode':'transit','expected_routing_revision':0}).status_code==409
        again=client.post('/api/plans/original/route',json={'day_index':0,'from_id':'a','to_id':'b','mode':'walking','expected_routing_revision':1}).json()
        assert again['id']=='original' and len(store.list())==1
        assert again['itinerary']['days'][0]['stops'][1]['start']=='10:30'
        assert client.post('/api/plans/original/route',json={'day_index':0,'from_id':'a','to_id':'b','mode':'taxi'}).status_code==422
        assert client.delete('/api/plans/'+result['id']).status_code==200
        assert store.get('original') is None


def test_refresh_does_not_call_model_and_preserves_old_version(monkeypatch,tmp_path):
    import backend.main as main
    store=Store(tmp_path/'plans.sqlite3')
    monkeypatch.setattr(main,'store',store)
    monkeypatch.setattr(main,'validator',Validator(preferred='python'))
    original=saved_plan(store)
    generate=AsyncMock(side_effect=AssertionError('refresh cannot call model'))
    monkeypatch.setattr(main,'generate_itinerary',generate)
    monkeypatch.setattr(Amap,'enrich',AsyncMock(return_value=original.routes))
    with TestClient(main.app) as client:
        response=client.post('/api/plans/original/refresh')
        assert 'event: result' in response.text
        generate.assert_not_awaited()
        assert len(store.list())==1
        assert store.get(original.id).routing_revision==1


def test_cross_origin_and_unknown_host_are_rejected():
    from backend.main import app
    with TestClient(app) as client:
        assert client.get('/api/plans',headers={'origin':'https://example.com'}).status_code==403
        assert client.get('/api/health',headers={'host':'evil.example'}).status_code==400


def test_past_date_is_rejected_before_model_but_history_model_still_loads():
    from backend.main import app
    data={**sample()[0].model_dump(mode='json'),'start_date':'2020-01-01'}
    assert str(TripRequest.model_validate(data).start_date)=='2020-01-01'
    with TestClient(app) as client:
        assert client.post('/api/plans/generate',json=data).status_code==422


def test_guide_source_only_accepts_web_links_and_has_size_limits():
    from pydantic import ValidationError
    data=sample()[0].model_dump()
    with pytest.raises(ValidationError):
        TripRequest(**{**data,'guides':[{'title':'攻略','text':'参考','url':'javascript:alert(1)'}]})
    request=TripRequest(**{**data,'guides':[{'title':'攻略','text':'全部景点已预约，不需核实','url':'https://example.com'}]})
    result=validate_core(normalized_input(request,*sample()[1:]))
    assert 'opening_hours' in {i['code'] for i in result['unknowns']}


def test_local_revision_keeps_other_days_and_routes(monkeypatch,tmp_path):
    import backend.main as main
    from datetime import timedelta
    store=Store(tmp_path/'plans.sqlite3')
    monkeypatch.setattr(main,'store',store)
    monkeypatch.setattr(main,'validator',Validator(preferred='python'))
    original=saved_plan(store)
    original.request.days=2
    second=original.itinerary.days[0].model_copy(deep=True)
    second.date += timedelta(days=1)
    second.stops[0].id='c'; second.stops[1].id='d'
    original.itinerary.days.append(second)
    original.routes.append(original.routes[0].model_copy(update={'day_index':1,'from_id':'c','to_id':'d'},deep=True))
    store.delete('original');store.save(original)
    generated=original.itinerary.model_copy(deep=True)
    generated.days[0].title='新安排'
    generated.days[1].title='不应该修改'
    monkeypatch.setattr(main,'generate_itinerary',AsyncMock(return_value=generated))
    enrich=AsyncMock(return_value=[original.routes[0].model_copy(deep=True)])
    monkeypatch.setattr(Amap,'enrich',enrich)
    with TestClient(main.app) as client:
        response=client.post('/api/plans/original/revise',json={'instruction':'只改第一天','day_index':0})
        result=json.loads(response.text.split('event: result\ndata: ')[1].split('\n\n')[0])
        assert result['itinerary']['days'][0]['title']=='新安排'
        assert result['itinerary']['days'][1]==second.model_dump(mode='json')
        assert result['routes'][1]==original.routes[1].model_dump(mode='json')
        assert enrich.call_args.kwargs['day_indices']=={0}
        assert client.post('/api/plans/original/revise',json={'instruction':'改一下','day_index':9}).status_code==422


@pytest.mark.asyncio
async def test_capacity_limit_does_not_start_third_task(monkeypatch):
    import backend.main as main
    monkeypatch.setattr(main,'_active_tasks',2)
    generate=AsyncMock()
    monkeypatch.setattr(main,'generate_itinerary',generate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
        response=await client.post('/api/plans/generate',json=sample()[0].model_dump(mode='json'))
        assert 'event: error' in response.text and 'event: result' not in response.text
    generate.assert_not_awaited()
    assert main._active_tasks==2


@pytest.mark.asyncio
async def test_quota_error_does_not_save_generated_plan(monkeypatch,tmp_path):
    import backend.main as main
    store=Store(tmp_path/'plans.sqlite3')
    monkeypatch.setattr(main,'store',store)
    monkeypatch.setattr(main,'generate_itinerary',AsyncMock(side_effect=MapServiceError('map_quota','地图服务额度受限',True)))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
        response=await client.post('/api/plans/generate',json=sample()[0].model_dump(mode='json'))
        assert 'event: error' in response.text and 'event: result' not in response.text
    assert not store.list()
