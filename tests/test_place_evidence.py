from decimal import Decimal
from unittest.mock import AsyncMock

import pytest

from backend.amap import Amap, MapServiceError
from backend.models import Expense, Stop, PlanningGuideSource, GuideDecision, cents, itinerary_expenses
from backend.place_info import enrich_place
from backend.itinerary_evidence import bind_stop_expenses, audit_guides
from test_core import sample


def region():
    return Stop(id='area', name='西湖区域', area_name='西湖', city='杭州', kind='sight',
        location_behavior='area', start='09:00', end='12:00', poi_id='parent',
        location=(120.1, 30.2), location_status='verified')


@pytest.mark.asyncio
@pytest.mark.parametrize('behavior', ['unlocated', 'same_as_previous'])
async def test_named_leisure_destination_is_verified_instead_of_inheriting_a_restaurant(behavior):
    amap = Amap('苏州市')
    stop = Stop(id='street', name='观前街自由活动', city='苏州市', kind='break',
        location_behavior=behavior, start='09:00', end='10:00')
    amap.search = AsyncMock(return_value=[{'id': 'street-poi', 'name': '观前街', 'city': '苏州市',
        'location': [120.62, 31.31], 'address': '观前街', 'type': '风景名胜'}])
    try:
        await amap.resolve(stop)
        assert stop.location_behavior == 'poi'
        assert stop.poi_id == 'street-poi' and stop.location_status == 'verified'
        assert stop.place_name == '观前街'
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_multiple_real_points_on_one_named_road_are_not_restaurant_branch_ambiguity():
    amap = Amap('上海')
    amap.search = AsyncMock(return_value=[{'id': code, 'name': '南京东路', 'city': '上海',
        'location': point, 'address': '南京东路', 'type': '地名地址信息;道路名'}
        for code, point in [('road-b', [121.49, 31.24]), ('road-a', [121.48, 31.24])]])
    stop = Stop(name='南京东路自由活动', city='上海', kind='break',
        start='13:00', end='14:00', location_behavior='unlocated')
    try:
        await amap.resolve(stop)
        assert stop.location_status == 'verified' and stop.poi_id == 'road-a'
        assert '通勤参考' in stop.anchor_note
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_historic_street_suffix_does_not_create_a_fictitious_separate_poi():
    amap = Amap('苏州')
    amap.search = AsyncMock(return_value=[{'id': code, 'name': '平江路', 'city': '苏州',
        'location': point, 'address': '平江路', 'type': '地名地址信息;道路名'}
        for code, point in [('road-b', [120.63, 31.32]), ('road-a', [120.62, 31.32])]])
    stop = Stop(name='平江路历史街区', place_name='平江路历史街区', area_name='平江路历史街区',
        city='苏州', kind='sight', start='13:00', end='14:00', location_behavior='area')
    try:
        await amap.resolve(stop)
        assert stop.location_status == 'verified' and stop.poi_id == 'road-a'
        assert stop.place_name == stop.area_name == '平江路'
        amap.search.assert_awaited_once_with('平江路')
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_open_street_without_a_gate_can_use_its_actual_provider_point():
    amap = Amap('杭州')
    stop = Stop(name='河坊街自由活动', city='杭州', kind='break', area_name='河坊街',
        location_behavior='area', poi_id='street', location_status='verified',
        location=(120.1, 30.2), start='13:00', end='14:00')
    amap.request = AsyncMock(return_value={'pois': [{'id': 'street', 'name': '河坊街景区',
        'cityname': '杭州市', 'location': '120.17,30.24'}]})
    try:
        await enrich_place(amap, stop)
        assert stop.location == (120.17, 30.24) and stop.location_status == 'verified'
        assert '不是核实入口' in stop.anchor_note
    finally:
        await amap.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('protected', [False, True])
async def test_unverified_optional_stop_can_be_removed_but_required_place_is_preserved(protected):
    _, itinerary, _ = sample()
    amap = Amap('上海')
    async def resolve(stop):
        stop.city = '上海'
        if stop.id == 'b':
            stop.location = None
            stop.location_status = 'unknown'
    amap.resolve = resolve
    amap.route_in_city = AsyncMock()
    itinerary.days[0].expenses.append(Expense(label='住宿', category='hotel', unit_cost=300, stop_id='b'))
    try:
        routes = await amap.enrich(itinerary, 'walking', require_meal_prices=True,
            protected_text='一定要去豫园' if protected else '')
        assert len(itinerary.days[0].stops) == (2 if protected else 1)
        assert bool(amap.planning_adjustments) is not protected
        assert any(e.category == 'hotel' for e in itinerary.days[0].expenses)
        amap.route_in_city.assert_not_awaited()
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_repair_never_removes_the_only_visit_for_a_city_on_that_day():
    _, itinerary, _ = sample()
    itinerary.days[0].stops = itinerary.days[0].stops[:1]
    stop = itinerary.days[0].stops[0]
    stop.location_status = 'unknown'
    stop.location = None
    amap = Amap('上海')
    amap.resolve = AsyncMock()
    try:
        await amap.enrich(itinerary, 'walking', require_meal_prices=True)
        assert itinerary.days[0].stops == [stop]
        assert not amap.planning_adjustments
    finally:
        await amap.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('protected', [False, True])
async def test_unverified_meal_is_replaced_only_by_a_real_priced_restaurant(protected):
    from backend.locality import repair_day_meals
    _, itinerary, _ = sample()
    for stop in itinerary.days[0].stops:
        stop.city = '上海'
    meal = Stop(id='meal', name='武康路周边午餐', kind='meal', city='上海',
        start='12:00', end='13:00', location_behavior='unlocated')
    itinerary.days[0].stops.append(meal)
    amap = Amap('上海')
    amap.places[('上海', '餐馆')] = [{'id': 'priced', 'name': '真实面馆', 'city': '上海',
        'location': [121.49, 31.22], 'address': '上海', 'type': '餐饮服务'}]
    amap.request = AsyncMock(return_value={'pois': [{'id': 'priced', 'name': '真实面馆',
        'cityname': '上海市', 'business': {'cost': '36'}}]})
    try:
        await repair_day_meals(amap, itinerary.days[0], '一定要去武康路周边午餐' if protected else '')
        if protected:
            assert meal.location_status == 'unknown'
            amap.request.assert_not_awaited()
        else:
            assert meal.name == '真实面馆' and meal.location_status == 'verified'
            assert meal.average_cost_yuan == Decimal('36')
            assert meal.information_source == 'https://www.amap.com/place/priced'
            assert meal.meal_period == 'lunch'
    finally:
        await amap.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('protected', [False, True])
async def test_repeat_chain_branch_replaced_with_different_real_priced_restaurant(protected):
    from backend.locality import repair_day_meals
    from backend.meals import restaurant_name_key, restaurant_key
    _, itinerary, _ = sample()
    day = itinerary.days[0]
    for stop in day.stops:
        stop.city = '上海'
    meal = Stop(id='dinner', name='小杨生煎（商城路店）', kind='meal', city='上海',
        start='18:00', end='19:00', meal_period='dinner', location=(121.49, 31.22),
        poi_id='old', location_status='verified', average_cost_yuan=25,
        information_source='https://www.amap.com/place/old')
    day.stops.append(meal)
    used = {restaurant_name_key('上海', '小杨生煎（宏伊广场店）')}
    amap = Amap('上海')
    async def request(path, params):
        if path == '/v3/place/around':
            return {'pois': [
                {'id': 'repeat', 'name': '小杨生煎（第三分店）', 'location': '121.49,31.22', 'typecode': '050100'},
                {'id': 'no-price', 'name': '无参考价餐馆', 'location': '121.49,31.22', 'typecode': '050100'},
                {'id': 'new', 'name': '本帮菜饭店（外滩店）', 'location': '121.49,31.22', 'typecode': '050100'}]}
        return {'pois': [{'id': params['id'], 'name': '本帮菜饭店（外滩店）' if params['id'] == 'new' else '无参考价餐馆',
            'cityname': '上海市', 'business': {'cost': '48'} if params['id'] == 'new' else {}}]}
    amap.request = AsyncMock(side_effect=request)
    try:
        await repair_day_meals(amap, day, '一定要吃小杨生煎（商城路店）' if protected else '', used)
        assert meal.meal_period == 'dinner'
        if protected:
            assert meal.poi_id == 'old'
            amap.request.assert_not_awaited()
        else:
            assert meal.poi_id == 'new' and meal.average_cost_yuan == Decimal('48')
            assert meal.information_source == 'https://www.amap.com/place/new'
            assert restaurant_key(meal) in used
            assert not any(c.args[1].get('id') == 'repeat' for c in amap.request.await_args_list)
    finally:
        await amap.close()


@pytest.mark.asyncio
async def test_detour_optimization_never_substitutes_a_restaurant_without_price_data():
    from backend.locality import optimize_day_meals
    _, itinerary, _ = sample()
    day = itinerary.days[0]
    for stop in day.stops:
        stop.city = '上海'
    meal = Stop(id='meal', name='原餐馆', kind='meal', city='上海', start='10:00', end='10:30',
        location=(121.7, 31.5), location_status='verified')
    day.stops.insert(1, meal)
    amap = Amap('上海')
    async def route(origin, destination, mode, city):
        return {'distance_m': 10000 if (121.7, 31.5) in (tuple(origin), tuple(destination)) else 1000}
    amap.route_in_city = route
    async def request(path, params):
        if path == '/v3/place/around':
            return {'pois': [{'id': code, 'name': name, 'location': '121.49,31.225',
                'typecode': '050000', 'cityname': '上海市'} for code, name in [('tea', '无价格茶室'), ('priced', '真实面馆')]]}
        return {'pois': [{'id': params['id'], 'name': '真实面馆', 'cityname': '上海市',
                         'business': {'cost': '25'} if params['id'] == 'priced' else {}}]}
    amap.request = request
    try:
        await optimize_day_meals(amap, day, require_prices=True)
        assert meal.poi_id == 'priced'
        await enrich_place(amap, meal)
        assert meal.average_cost_yuan == Decimal('25')
    finally:
        await amap.close()


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
    trip.guides = [PlanningGuideSource(title='攻略', text='先去外滩看江景，再到豫园用餐。')]
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
    trip.guides = [PlanningGuideSource(title='攻略', text='去外滩')]
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions[0].status == 'not_used'
    trip.guides = []
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions == []


def test_partial_guide_places_cannot_all_be_claimed_as_adopted():
    trip, itinerary, _ = sample()
    trip.guides = [PlanningGuideSource(title='攻略', text='先去外滩再去故宫。')]
    itinerary.guide_decisions = [GuideDecision(source_index=0, excerpt='先去外滩再去故宫',
        place_names=['外滩', '故宫'], status='adopted', stop_ids=['a'], reason='安排外滩')]
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions[0].status == 'adjusted'
    assert '故宫' in itinerary.guide_decisions[0].reason


def test_guide_dishes_are_linked_to_actual_meal_recommendations():
    trip, itinerary, _ = sample()
    trip.guides = [PlanningGuideSource(title='美食攻略', text='逛外滩后午餐尝试烤鸭。')]
    itinerary.days[0].stops.append(Stop(id='food', name='午餐餐馆', kind='meal', start='12:00', end='13:00', food_recommendations=['烤鸭']))
    itinerary.guide_decisions = [GuideDecision(source_index=0, excerpt='逛外滩后午餐尝试烤鸭',
        place_names=['外滩'], status='adopted', stop_ids=['a'], reason='保留游览和特色餐饮建议')]
    audit_guides(trip, itinerary)
    assert itinerary.guide_decisions[0].stop_ids == ['a', 'food']
