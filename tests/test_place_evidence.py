from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap, MapServiceError
from backend.models import Expense, Stop, GuideSource, GuideDecision, cents, itinerary_expenses
from backend.place_info import enrich_place
from backend.itinerary_evidence import bind_stop_expenses, audit_guides
from test_core import sample


def region():
    return Stop(id='area', name='西湖区域', area_name='西湖', city='杭州', kind='sight',
        location_behavior='area', start='09:00', end='12:00', poi_id='parent',
        location=(120.1, 30.2), location_status='verified')


@pytest.mark.asyncio
async def test_area_selects_real_child_and_refresh_preserves_anchor(monkeypatch):
    amap = Amap('杭州')
    parent = {'id': 'parent', 'name': '西湖', 'cityname': '杭州市', 'business': {'cost': '999'},
        'children': [{'id': 'child', 'name': '西湖观景码头', 'location': '120.15,30.24', 'typecode': '110000'}]}
    child = {'id': 'child', 'cityname': '杭州市', 'business': {'opentime_week': '周一至周日 08:00-18:00',
        'opentime_today': '08:00-18:00', 'cost': '30'}}
    async def request(path, params):
        assert path == '/v5/place/detail' and params['show_fields'] == 'business,children,navi'
        return {'pois': [parent if params['id'] == 'parent' else child]}
    amap.request = AsyncMock(side_effect=request)
    try:
        stop = region()
        await enrich_place(amap, stop)
        assert stop.poi_id == 'child' and stop.location == (120.15, 30.24)
        assert stop.area_poi_id == 'parent' and '真实子地点' in stop.anchor_note
        assert stop.average_cost_yuan == Decimal(30) and stop.opening_hours_status == 'map_reference'
        assert stop.information_queried_at and stop.opening_hours_today == '08:00-18:00'
        assert 'child' in stop.information_source
        monkeypatch.setattr('backend.place_info.secrets.choice', lambda _: pytest.fail('refresh must retain existing anchor'))
        stop.poi_id = 'parent'  # resolve rechecks the parent POI before enrichment.
        await enrich_place(amap, stop, previous_anchor='child')
        assert stop.poi_id == 'child' and amap.request.await_count == 2
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_region_without_verified_child_or_entry_does_not_randomize_coordinates():
    amap = Amap('杭州')
    amap.request = AsyncMock(return_value={'pois': [{'id': 'parent', 'cityname': '杭州市', 'children': [], 'business': {}}]})
    try:
        stop = region()
        await enrich_place(amap, stop)
        assert stop.location is None and stop.location_status == 'unknown'
        assert '未返回' in stop.anchor_note
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_area_excludes_parking_and_labels_parent_opening_reference():
    amap = Amap('杭州')
    amap.request = AsyncMock(return_value={'pois': [{
        'id': 'parent', 'name': '西湖', 'cityname': '杭州市',
        'business': {'opentime_week': '每日08:00-18:00'},
        'children': [
            {'id': 'parking', 'name': '西湖南门停车场(大巴专用)', 'location': '120.1,30.2', 'subtype': '停车场'},
            {'id': 'gate', 'name': '西湖宫门', 'location': '120.15,30.24', 'subtype': '门'},
        ]}]})
    try:
        stop = region()
        await enrich_place(amap, stop)
        assert stop.poi_id == 'gate' and '停车' not in stop.matched_name
        assert stop.opening_hours_scope == 'area' and '08:00' in stop.opening_hours
        assert stop.information_source.endswith('/parent') and amap.request.await_count == 1
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_unlocated_regional_rest_gets_verified_anchor_but_does_not_invent_return_navigation():
    _, itinerary, _ = sample()
    itinerary.days[0].stops = [region(), Stop(id='rest', name='园区周边休息与返程', city='杭州', kind='break',
        start='12:00', end='12:30', description='在园区出口附近补水休息，随后自行返程。', location_behavior='unlocated')]
    amap = Amap('杭州')
    amap.places = {'region': [{'id': 'parent', 'name': '西湖', 'city': '杭州',
        'location': [120.1, 30.2], 'address': '', 'type': '风景名胜'}]}
    amap.request = AsyncMock(return_value={'pois': [{'id': 'parent', 'name': '西湖', 'cityname': '杭州市',
        'business': {'opentime_week': '08:00-18:00'},
        'children': [{'id': 'gate', 'name': '西湖正门', 'location': '120.15,30.24', 'subtype': '门'}]}]})
    amap.route = AsyncMock()
    try:
        routes = await amap.enrich(itinerary, 'auto', enrich_details=True)
        rest = itinerary.days[0].stops[1]
        assert rest.location_status == 'verified' and rest.poi_id == 'gate'
        assert '返程路线未纳入核实' in rest.anchor_note
        assert routes[0].stationary and routes[0].available
        amap.route.assert_not_awaited()
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_detail_permission_failure_is_optional_and_not_retried_per_stop():
    amap = Amap('杭州')
    amap.request = AsyncMock(side_effect=MapServiceError('map_permission', '尚未授权'))
    try:
        for poi_id in ['one', 'two']:
            stop = Stop(name='餐厅', kind='meal', city='杭州', start='12:00', end='13:00',
                poi_id=poi_id, location=(120.1, 30.2), location_status='verified')
            await enrich_place(amap, stop)
            assert stop.location_status == 'verified' and stop.opening_hours_status == 'unknown'
        assert amap.request.await_count == 1
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_detail_from_another_city_is_not_used():
    amap = Amap('杭州')
    amap.request = AsyncMock(return_value={'pois': [{'id': 'parent', 'cityname': '北京市',
        'business': {'opentime_week': '09:00-17:00'}}]})
    try:
        stop = region()
        await enrich_place(amap, stop)
        assert stop.opening_hours_status == 'unknown' and not stop.opening_hours
        assert stop.location_status == 'unknown'
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_model_opening_claims_are_discarded_even_without_optional_enrichment():
    amap = Amap('上海')
    amap.search = AsyncMock(return_value=[{'id': 'real', 'name': '外滩', 'city': '上海', 'location': [121.49, 31.23], 'address': ''}])
    stop = Stop(name='外滩', start='09:00', end='10:00', opening_hours='模型编的24小时',
        opening_hours_status='map_reference', average_cost_yuan=88)
    try:
        await amap.resolve(stop)
        assert not stop.opening_hours and stop.opening_hours_status == 'unknown' and stop.average_cost_yuan is None
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_model_area_id_is_replaced_by_city_and_name_verified_parent():
    amap = Amap('杭州')
    amap.search = AsyncMock(return_value=[{'id': 'real-parent', 'name': '西湖', 'city': '杭州',
        'location': [120.1, 30.2], 'address': '', 'type': '风景名胜'}])
    stop = region()
    stop.area_poi_id = 'untrusted-model-id'
    try:
        await amap.resolve(stop)
        assert stop.poi_id == stop.area_poi_id == 'real-parent'
    finally:
        await amap.close()


def test_legacy_per_stop_allocation_preserves_cents_and_does_not_double_count():
    trip, itinerary, _ = sample()
    day = itinerary.days[0]
    day.expenses.append(Expense(label='当天合计门票', category='ticket', unit_cost='14.005', quantity=2))
    def total():
        return sum(cents(e.unit_cost) * e.quantity * (trip.people if e.basis == 'person' else 1) for e in itinerary_expenses(itinerary))
    before = total()
    bind_stop_expenses(itinerary)
    assert total() == before
    tickets = [e for e in day.expenses if e.category == 'ticket']
    assert len(tickets) == 2 and {e.stop_id for e in tickets} == {'a', 'b'}
    assert all('分摊' in e.note for e in tickets)
    snapshot = itinerary.model_dump()
    bind_stop_expenses(itinerary)
    assert itinerary.model_dump() == snapshot


def test_map_average_is_food_reference_not_a_ticket_price():
    _, itinerary, _ = sample()
    day = itinerary.days[0]
    day.expenses = []
    day.stops[0].average_cost_yuan = Decimal(100)
    day.stops.append(Stop(id='meal', name='餐厅', kind='meal', start='12:00', end='13:00', average_cost_yuan=60))
    bind_stop_expenses(itinerary)
    assert len(day.expenses) == 1 and day.expenses[0].category == 'food'
    assert day.expenses[0].stop_id == 'meal' and day.expenses[0].unit_cost == 60


def test_guide_audit_rejects_fabricated_quotes_and_wrong_activity_links():
    trip, itinerary, _ = sample()
    trip.guides = [GuideSource(title='攻略', text='先去外滩看江景，再到豫园用餐。')]
    itinerary.guide_decisions = [
        GuideDecision(source_index=0, excerpt='先去外滩看江景', place_names=['外滩'], status='adopted', stop_ids=['a'], reason='安排了外滩'),
        GuideDecision(source_index=0, excerpt='先去外滩看江景', place_names=['外滩'], status='adopted', stop_ids=['b'], reason='错误关联'),
        GuideDecision(source_index=0, excerpt='攻略没有说的话', status='adopted', stop_ids=['a'], reason='不应展示'),
    ]
    audit_guides(trip, itinerary)
    assert len(itinerary.guide_decisions) == 2
    assert itinerary.guide_decisions[0].status == 'adopted'
    assert itinerary.guide_decisions[1].status == 'not_used' and not itinerary.guide_decisions[1].stop_ids


def test_guide_without_trace_is_explicitly_unconfirmed_and_empty_guides_clear_trace():
    trip, itinerary, _ = sample()
    trip.guides = [GuideSource(title='攻略', text='去外滩')]
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions[0].status == 'not_used'
    trip.guides = []
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions == []


def test_partial_guide_places_cannot_all_be_claimed_as_adopted():
    trip, itinerary, _ = sample()
    trip.guides = [GuideSource(title='攻略', text='先去外滩再去故宫。')]
    itinerary.guide_decisions = [GuideDecision(source_index=0, excerpt='先去外滩再去故宫',
        place_names=['外滩', '故宫'], status='adopted', stop_ids=['a'], reason='安排外滩')]
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions[0].status == 'adjusted'
    assert '故宫' in itinerary.guide_decisions[0].reason


def test_guide_dishes_are_linked_to_actual_meal_recommendations():
    trip, itinerary, _ = sample()
    trip.guides = [GuideSource(title='美食攻略', text='逛外滩后午餐尝试烤鸭。')]
    itinerary.days[0].stops.append(Stop(id='food', name='午餐餐馆', kind='meal', start='12:00', end='13:00', food_recommendations=['烤鸭']))
    itinerary.guide_decisions = [GuideDecision(source_index=0, excerpt='逛外滩后午餐尝试烤鸭',
        place_names=['外滩'], status='adopted', stop_ids=['a'], reason='保留游览和特色餐饮建议')]
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions[0].stop_ids == ['a', 'food']
