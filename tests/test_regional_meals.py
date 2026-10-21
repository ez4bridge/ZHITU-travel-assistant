import asyncio
from unittest.mock import AsyncMock
from decimal import Decimal
import pytest
from backend.amap import Amap
from backend.models import Stop, Itinerary
from backend.place_info import prepare_regional_stop, enrich_place


def stop(name, **kwargs):
    return Stop(name=name, city='泉州市', start='12:00', end='13:00', **kwargs)


@pytest.mark.asyncio
async def test_area_dining_accepts_real_landmark_without_claiming_restaurant_or_average():
    amap=Amap('泉州市')
    meal=stop('西街钟楼夜市晚餐',kind='meal',place_name='泉州钟楼')
    amap.search=AsyncMock(return_value=[{'id':'tower','name':'泉州钟楼','city':'泉州市',
        'type':'风景名胜','location':[118.59,24.91],'address':'西街'}])
    amap.request=AsyncMock(return_value={'pois':[{'id':'tower','name':'泉州钟楼','cityname':'泉州市',
        'business':{'cost':'80'},'navi':{'entr_location':'118.59,24.91'}}]})
    try:
        prepare_regional_stop(meal)
        await amap.resolve(meal)
        await enrich_place(amap,meal)
        assert meal.location_behavior=='area' and meal.location_status=='verified'
        assert meal.poi_id=='tower' and meal.average_cost_yuan is None
    finally:await amap.close()


def test_regional_meal_inherits_matching_verified_region_only():
    before=stop('五店市传统街区',kind='sight',area_name='五店市传统街区',area_poi_id='parent',
        poi_id='gate',location_status='verified',location=(118.5,24.8))
    meal=stop('五店市街区午餐',kind='meal',location_behavior='unlocated')
    prepare_regional_stop(meal,before)
    assert meal.area_poi_id=='parent' and meal.poi_id=='gate'
    assert meal.location_behavior=='area' and meal.area_name==before.area_name
    other=stop('其他村晚餐',kind='meal',location_behavior='unlocated')
    prepare_regional_stop(other,before)
    assert not other.area_poi_id and other.area_name!='五店市传统街区'
    restaurant=stop('蟳埔村海鲜晚餐',kind='meal',place_name='某某海鲜餐厅')
    prepare_regional_stop(restaurant,before)
    assert restaurant.location_behavior=='poi' and not restaurant.area_name
    unknown=stop('晚餐待定',kind='meal',location_behavior='unlocated')
    prepare_regional_stop(unknown,before)
    assert unknown.location_behavior=='unlocated'


@pytest.mark.asyncio
async def test_area_text_fallback_uses_related_real_gate_and_excludes_parking():
    amap=Amap('泉州市')
    region=stop('某古城区域',kind='sight',place_name='某古城',area_name='某古城',location_behavior='area',
        poi_id='parent',location_status='verified',location=(118.5,24.8))
    amap.request=AsyncMock(return_value={'pois':[{'id':'parent','name':'某古城','cityname':'泉州市'}]})
    amap.search=AsyncMock(return_value=[{'id':'bad','name':'某古城停车场入口','location':[118.5,24.8]},
        {'id':'far','name':'别的景区南门','location':[118.6,24.9]},
        {'id':'hotel','name':'某古城游客中心酒店','location':[118.5,24.8]},
        {'id':'gate','name':'某古城南门','location':[118.51,24.81],'address':'真实地址'}])
    try:
        await enrich_place(amap,region)
        assert region.location_status=='verified' and region.poi_id=='gate'
        assert region.location==(118.51,24.81)
    finally:await amap.close()


@pytest.mark.asyncio
async def test_identical_simultaneous_place_searches_share_one_request():
    amap=Amap('泉州市')
    started=asyncio.Event();release=asyncio.Event()
    async def response(*args):
        started.set();await release.wait()
        return {'pois':[{'id':'tower','name':'泉州钟楼','location':'118.59,24.91','cityname':'泉州市'}]}
    amap.request=AsyncMock(side_effect=response)
    try:
        one=asyncio.create_task(amap.search('泉州钟楼'))
        await started.wait()
        two=asyncio.create_task(amap.search('泉州钟楼'))
        await asyncio.sleep(0)
        release.set()
        first,second=await asyncio.gather(one,two)
        assert first==second and amap.request.await_count==1
    finally:await amap.close()


@pytest.mark.asyncio
async def test_agent_search_batch_runs_independent_searches_concurrently(monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from openai.types.chat import ChatCompletionMessage
    import backend.agent as agent
    from test_core import sample
    request,itinerary,_=sample()
    arrived=0
    both=asyncio.Event()
    async def search(keywords,city):
        nonlocal arrived
        arrived+=1
        if arrived==2:both.set()
        await both.wait()
        return []
    first=ChatCompletionMessage(role='assistant',content=None,tool_calls=[
        {'id':str(i),'type':'function','function':{'name':'search_places','arguments':'{"keywords":"site","city":"上海"}'}} for i in range(2)])
    last=ChatCompletionMessage(role='assistant',content='资料已收集')
    final=ChatCompletionMessage(role='assistant',content=itinerary.model_dump_json())
    complete=AsyncMock(side_effect=[SimpleNamespace(choices=[SimpleNamespace(message=m)]) for m in (first,last,final)])
    @asynccontextmanager
    async def client():yield SimpleNamespace()
    monkeypatch.setattr(agent,'model_client',client)
    monkeypatch.setattr(agent,'complete_model',complete)
    monkeypatch.setattr(agent.settings,'llm_key','test')
    monkeypatch.setattr(agent.settings,'amap_key','test')
    await asyncio.wait_for(agent.generate_itinerary(request,SimpleNamespace(search=search),AsyncMock()),2)
    assert arrived==2


def test_meal_label_prefix_is_not_part_of_region_lookup():
    meal=stop('午餐：西街附近',kind='meal',location_behavior='unlocated')
    prepare_regional_stop(meal)
    assert meal.area_name=='西街附近'



def test_regional_anchor_correction_preserves_activity_but_new_venue_does_not():
    from backend.planning import same_activity_structure
    from test_core import sample
    _, before, _ = sample()
    old=before.days[0].stops[0]
    old.poi_id='';old.location_status='unknown';old.location=None
    after=before.model_copy(deep=True)
    new=after.days[0].stops[0]
    new.location_behavior='area';new.area_poi_id='region';new.poi_id='gate'
    new.location_status='verified';new.location=(121.4,31.2)
    assert same_activity_structure(before,after)
    old.poi_id='restaurant-one';old.location_status='verified'
    new.location_behavior='poi';new.poi_id='restaurant-two'
    assert not same_activity_structure(before,after)
