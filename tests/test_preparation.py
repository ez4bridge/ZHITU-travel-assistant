"""Read-only preparation decisions over explicit synthetic saved evidence."""
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from backend.bookings import hotel_ref
from backend.models import (BookedTransport, CityTransfer, ConfirmedHotel, Expense,
                            RouteLeg, Stop, TripRequest)
from backend.preparation import preparation_context
from backend.storage import Store
from backend.validation import normalized_input, validate_core
from plan_samples import sample_plan


def point(sid, name, city, start, end, kind='break', role='', ref=''):
    return Stop(id=sid, name=name, place_name=name, city=city, kind=kind,
        start=start, end=end, booking_anchor=role, booking_ref=ref,
        poi_id='synthetic-' + sid, location=(116.3, 39.9), location_status='verified',
        location_note='明确合成地点，仅测试准备状态，不代表真实核实')


def local_roads(plan):
    routes = []
    for index, day in enumerate(plan.itinerary.days):
        for before, after in zip(day.stops, day.stops[1:]):
            if before.city != after.city:
                continue
            routes.append(RouteLeg(day_index=index, from_id=before.id, to_id=after.id,
                mode='walking', available=True, distance_m=500, duration_s=600,
                polyline=[(116.3, 39.9), (116.31, 39.9)], queried_at=datetime.now(timezone.utc),
                note='明确合成道路，仅测试准备状态，不代表真实通行'))
    return routes


def complete_plan():
    plan = sample_plan(2)
    day = plan.request.start_date
    hotel = ConfirmedHotel(city='北京', name='合成酒店', address='合成酒店地址',
        check_in=day, check_out=day + timedelta(days=2), total_cost=300)
    booking = BookedTransport(from_city='天津', to_city='北京', departure_date=day,
        departure_time='10:00', arrival_date=day, arrival_time='12:00',
        departure_place='天津站', arrival_place='北京南站', total_cost=100, number='USER-TEST')
    plan.request = TripRequest(origin='天津', destination='北京', include_origin_transport=True,
        start_date=day, start_time='07:00', days=2, people=2, budget=5000,
        intercity_modes=['train', 'flight'], confirmed_hotels=[hotel], booked_transports=[booking])
    ref = hotel_ref(hotel)
    plan.itinerary.transfers = [CityTransfer(from_city='天津', to_city='北京', departure_date=day,
        departure_time='10:00', arrival_date=day, arrival_time='12:00', mode='train',
        unit_cost=100, basis='group', booking_status='user_confirmed',
        departure_station='天津站', arrival_station='北京南站', source='用户填写，未核验订单')]
    plan.itinerary.days[0].stops = [
        point('depart', '天津站', '天津', '09:00', '09:05', role='transport_departure', ref='transport-0'),
        point('arrive', '北京南站', '北京', '12:00', '12:05', role='transport_arrival', ref='transport-0'),
        point('hotel-drop', hotel.name, '北京', '12:30', '12:45', 'hotel', 'hotel_departure', ref),
        point('sight-0', '合成北京景点一', '北京', '14:00', '15:00', 'sight'),
        point('hotel-return', hotel.name, '北京', '17:00', '17:05', 'hotel', 'hotel_return', ref),
    ]
    plan.itinerary.days[0].expenses = [
        Expense(label='用户填住宿', category='hotel', unit_cost=300, basis='group', booking_ref=ref),
        Expense(label='合成景点票价', category='ticket', unit_cost=10, basis='group', stop_id='sight-0'),
        Expense(label='合成自理用餐预留', category='food', unit_cost=50, basis='group',
                note='人工构造的自理用餐预算，不代表真实餐馆报价'),
    ]
    plan.itinerary.days[1].stops = [
        point('hotel-next', hotel.name, '北京', '09:00', '09:05', 'hotel', 'hotel_departure', ref),
        point('sight-1', '合成北京景点二', '北京', '10:00', '11:00', 'sight'),
    ]
    plan.itinerary.days[1].expenses = [Expense(label='合成景点票价', category='ticket',
        unit_cost=10, basis='group', stop_id='sight-1')]
    plan.routes = local_roads(plan)
    return plan


def test_missing_hotel_and_transport_stay_preview_with_honest_query_links():
    plan = complete_plan()
    plan.request.confirmed_hotels = []
    plan.request.booked_transports = []
    transfer = plan.itinerary.transfers[0]
    transfer.booking_status, transfer.schedule_status = 'unconfirmed', 'queried'
    before = plan.model_dump_json()
    result = preparation_context(plan)
    assert result['state'] == 'preview' and result['missing_count'] == 2
    assert result['hotels'][0]['status'] == 'missing'
    assert result['transports'][0]['status'] == 'queried'
    assert [o['mode'] for o in result['transports'][0]['options']] == ['train', 'flight']
    assert result['transports'][0]['options'][0]['query_url'].startswith('https://www.12306.cn/')
    assert result['transports'][0]['options'][1]['query_url'].startswith('https://flights.ctrip.com/')
    suggestions = result['hotels'][0]['suggestions']
    assert suggestions and suggestions[0]['source_status'] == 'map_reference'
    assert '不是酒店评分、房价或有房证明' in suggestions[0]['basis']
    assert plan.model_dump_json() == before


def test_provided_hotel_without_verified_position_needs_review():
    plan = complete_plan()
    plan.itinerary.days[0].stops[-1].location = None
    plan.itinerary.days[0].stops[-1].location_status = 'unknown'
    result = preparation_context(plan)
    assert result['state'] == 'needs_review' and result['missing_count'] == 0
    assert result['hotels'][0]['status'] == 'provided'
    assert any('具体分店' in item for item in result['blocking'])


def test_provided_prices_and_station_names_must_be_complete():
    plan = complete_plan()
    plan.request.confirmed_hotels[0].total_cost = None
    plan.request.booked_transports[0].total_cost = None
    plan.request.booked_transports[0].arrival_place = ''
    result = preparation_context(plan)
    assert result['state'] == 'needs_review' and result['missing_count'] == 0
    assert any('总房费' in item for item in result['blocking'])
    assert any('未填全员金额' in item for item in result['blocking'])
    assert any('车站或机场尚未补全' in item for item in result['blocking'])


def test_complete_user_information_with_deterministic_checks_is_ready_but_not_order_verified():
    plan = complete_plan()
    report = validate_core(normalized_input(plan.request, plan.itinerary, plan.routes))
    assert not report['errors'] and not report['warnings']
    result = preparation_context(plan)
    assert result['state'] == 'ready' and not result['blocking'] and result['missing_count'] == 0
    assert result['hotels'][0]['status'] == 'located'
    assert result['hotels'][0]['suggestions'] == []  # No replacement hotel pitch once confirmed.
    assert result['transports'][0]['status'] == 'user_provided'
    assert [option['mode'] for option in result['transports'][0]['options']] == ['train']
    assert '采用你填写' in result['transports'][0]['options'][0]['summary']
    assert result['transports'][0]['reference_total_yuan'] == '100.00'
    assert any('未查验订单' in note for note in result['transports'][0]['notes'])
    assert result['unknowns'] and '按用户提供' in result['summary']


def test_partial_night_coverage_is_not_full_hotel_confirmation():
    plan = sample_plan(3)
    day = plan.request.start_date
    plan.request.confirmed_hotels = [ConfirmedHotel(city='北京', name='合成首晚酒店',
        check_in=day, check_out=day + timedelta(days=1), total_cost=100)]
    result = preparation_context(plan)
    assert result['state'] == 'preview' and result['missing_count'] == 1
    assert result['hotels'][0]['status'] == 'partial'
    assert result['hotels'][0]['nights'] == [str(day), str(day + timedelta(days=1))]
    assert str(day + timedelta(days=1)) in result['hotels'][0]['notes'][0]


def test_partial_city_coverage_keeps_other_city_night_missing():
    plan = sample_plan(3)
    day = plan.request.start_date
    plan.request = TripRequest(destination='北京→天津', start_date=day, days=3,
        confirmed_hotels=[ConfirmedHotel(city='天津', name='合成天津酒店', check_in=day + timedelta(days=1),
            check_out=day + timedelta(days=2), total_cost=100)])
    for index in (1, 2):
        for stop in plan.itinerary.days[index].stops:
            stop.city = '天津'
    plan.itinerary.transfers = [CityTransfer(from_city='北京', to_city='天津',
        departure_date=day + timedelta(days=1), departure_time='12:00',
        arrival_date=day + timedelta(days=1), arrival_time='13:00', unit_cost=50)]
    result = preparation_context(plan)
    hotels = {item['city']: item for item in result['hotels']}
    assert result['state'] == 'preview' and result['missing_count'] == 2  # Beijing night + unprovided train.
    assert hotels['北京']['status'] == 'missing' and not hotels['北京']['names']
    assert hotels['天津']['names'] == ['合成天津酒店']


def test_overnight_train_does_not_create_a_hotel_requirement():
    plan = sample_plan(2)
    day = plan.request.start_date
    plan.request = TripRequest(destination='北京→上海', start_date=day, days=2)
    for stop in plan.itinerary.days[1].stops:
        stop.city = '上海'
    plan.itinerary.transfers = [CityTransfer(from_city='北京', to_city='上海', departure_date=day,
        departure_time='23:00', arrival_date=day + timedelta(days=1), arrival_time='07:00', unit_cost=200)]
    result = preparation_context(plan)
    assert result['hotels'] == []
    assert result['missing_count'] == 1  # The unprovided train, never a fictitious hotel.


def test_night_city_follows_later_arrival_even_without_a_model_arrival_stop():
    plan = sample_plan(2)
    day = plan.request.start_date
    plan.request = TripRequest(destination='北京→天津', start_date=day, days=2)
    for stop in plan.itinerary.days[1].stops:
        stop.city = '天津'
    plan.itinerary.transfers = [CityTransfer(from_city='北京', to_city='天津', departure_date=day,
        departure_time='16:00', arrival_date=day, arrival_time='18:00', unit_cost=50)]
    result = preparation_context(plan)
    assert [item['city'] for item in result['hotels']] == ['天津']


def test_missing_food_cost_warning_cannot_be_labeled_check_passed():
    plan = complete_plan()
    plan.itinerary.days[0].expenses = [e for e in plan.itinerary.days[0].expenses if e.category != 'food']
    plan.itinerary.days[0].stops[3].kind = 'meal'
    plan.itinerary.days[0].stops[3].start = '12:55'
    plan.itinerary.days[0].stops[3].end = '13:55'
    plan.itinerary.days[0].stops[3].meal_period = 'lunch'
    report = validate_core(normalized_input(plan.request, plan.itinerary, plan.routes))
    assert not report['errors'] and any(i['code'] == 'missing_food_cost' for i in report['warnings'])
    result = preparation_context(plan)
    assert result['state'] == 'needs_review'
    assert any('餐饮活动缺少费用' in item for item in result['blocking'])


def test_missing_meal_is_never_assumed_free_when_no_restaurant_was_selected():
    plan = complete_plan()
    plan.itinerary.days[0].expenses = [e for e in plan.itinerary.days[0].expenses if e.category != 'food']
    result = preparation_context(plan)
    assert result['state'] == 'needs_review'
    assert any('跨越饭点但缺少餐费' in item for item in result['blocking'])


def test_missing_booked_transport_anchor_requires_location_review():
    plan = complete_plan()
    plan.itinerary.days[0].stops = [s for s in plan.itinerary.days[0].stops
        if not s.booking_anchor.startswith('transport_')]
    plan.routes = local_roads(plan)
    result = preparation_context(plan)
    assert result['state'] == 'needs_review'
    assert any('车站' in item or '接驳' in item for item in result['blocking'])


@pytest.mark.parametrize('code', ['stop_cost', 'ticket_coverage', 'transport_fare'])
def test_unresolved_cost_coverage_cannot_be_ready(code):
    plan = complete_plan()
    if code == 'stop_cost':
        plan.itinerary.days[1].expenses[0].stop_id = ''
    elif code == 'ticket_coverage':
        plan.itinerary.days[1].expenses = [Expense(label='其他预算', category='other', unit_cost=10)]
    else:
        route = next(r for r in plan.routes if r.day_index == 0)
        route.mode, route.fare_yuan, route.cost_estimate = 'transit', None, None
        plan.itinerary.days[0].expenses.append(Expense(label='交通预算假设', category='transport', unit_cost=10))
    report = validate_core(normalized_input(plan.request, plan.itinerary, plan.routes))
    assert not report['errors'] and any(item['code'] == code for item in report['unknowns'])
    result = preparation_context(plan)
    assert result['state'] == 'needs_review'
    assert any(item['message'] in result['blocking'] for item in report['unknowns'] if item['code'] == code)


def test_confirmed_restaurant_closure_reference_prevents_ready():
    plan = complete_plan()
    meal = plan.itinerary.days[0].stops[3]
    meal.kind, meal.meal_period = 'meal', 'lunch'
    meal.start, meal.end = '12:55', '13:55'
    meal.opening_hours_status, meal.opening_hours = 'map_reference', '18:00-22:00'
    plan.itinerary.days[0].expenses[1].category = 'food'
    result = preparation_context(plan)
    assert result['state'] == 'needs_review'
    assert any('营业时间参考' in item for item in result['blocking'])


def test_get_is_pure_read_only_and_does_not_call_tools(monkeypatch, tmp_path):
    import backend.main as main
    from backend.amap import Amap
    from backend.railway import Railway
    store = Store(tmp_path / 'preparation.sqlite3')
    plan = complete_plan()
    store.save(plan)
    stored_json = store.get(plan.id).model_dump_json()
    monkeypatch.setattr(main, 'store', store)
    amap = AsyncMock(side_effect=AssertionError('GET preparation must not call map'))
    rail = AsyncMock(side_effect=AssertionError('GET preparation must not call railway'))
    model = AsyncMock(side_effect=AssertionError('GET preparation must not call model'))
    monkeypatch.setattr(Amap, 'enrich', amap)
    monkeypatch.setattr(Railway, 'query', rail)
    monkeypatch.setattr(main, 'generate_itinerary', model)
    with store.connect() as db:
        before = tuple(db.iterdump())
    with TestClient(main.app) as client:
        response = client.get('/api/plans/base/preparation')
        assert response.status_code == 200 and response.json()['state'] == 'ready'
        assert client.get('/api/plans/missing/preparation').status_code == 404
    with store.connect() as db:
        assert tuple(db.iterdump()) == before
    assert store.get(plan.id).model_dump_json() == stored_json
    amap.assert_not_awaited()
    rail.assert_not_awaited()
    model.assert_not_awaited()


def test_get_rejects_study_without_touching_its_saved_context(monkeypatch, tmp_path):
    import backend.main as main
    plan = sample_plan(1)
    plan.request = TripRequest(destination='上海', start_date=plan.request.start_date, days=1, study={})
    for stop in plan.itinerary.days[0].stops:
        stop.city = '上海'
    store = Store(tmp_path / 'study-preparation.sqlite3')
    store.save(plan)
    monkeypatch.setattr(main, 'store', store)
    before = store.get(plan.id).model_dump_json()
    with TestClient(main.app) as client:
        response = client.get('/api/plans/base/preparation')
        assert response.status_code == 422 and '历史研学' in response.text
    assert store.get(plan.id).model_dump_json() == before

def test_issues_keep_each_item_but_drop_repeated_wording():
    """Each affected stop stays named; the same finding is not said twice."""
    from types import SimpleNamespace

    from backend.preparation import build_issues

    report = {
        'errors': [{'code': 'over_budget', 'message': '估算总费用超过全员预算',
                    'day_index': None, 'stop_id': None}],
        'warnings': [{'code': 'meal_arrangement', 'message': '午餐安排在14:53，超出常见用餐时段',
                      'day_index': 0, 'stop_id': 'm'}],
        'unknowns': [{'code': 'stop_cost', 'message': f'「景点{i}」尚无可对应的门票费用参考；不视为免费',
                      'day_index': 0, 'stop_id': f's{i}'} for i in range(6)],
    }
    itinerary = SimpleNamespace(days=[SimpleNamespace(stops=[])])
    issues = build_issues(report, [], itinerary, ['第1天午餐排在14:53，无法兼顾常见用餐时段'])
    tickets = [item for item in issues if item['code'] == 'stop_cost']
    assert len(tickets) == 6                      # never merged into “6 处景点”
    assert {item['label'] for item in tickets} == {f'景点{i}' for i in range(6)}
    assert sum(1 for item in issues if item['code'] == 'meal_arrangement') == 1
    budget = next(item for item in issues if item['code'] == 'over_budget')
    assert budget['severity'] == 'block' and budget['target'] == 'budget'
    meal = next(item for item in issues if item['code'] == 'meal_arrangement')
    assert meal['target'] == 'day' and meal['day_index'] == 0