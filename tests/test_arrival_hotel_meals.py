"""Station→declared hotel→meal→sight flow with explicit synthetic evidence."""
from datetime import datetime, timezone

import pytest

from backend.arrival_flow import prepare_arrival_flow
from backend.bookings import apply_hotel_anchors, repair_booking_schedule
from backend.meals import align_meals, meal_opening_reference, meal_schedule_checks
from backend.models import CityTransfer, ConfirmedHotel, RouteLeg, Stop, StudyOptions, TripRequest
from backend.transfer_timeline import prepare_transfer_anchors
from test_meals import late_lunch
from test_merged_transfer_timeline import itinerary, transfer, trip


def arrival_case(time='12:00', with_hotel=True):
    request = trip()
    plan = itinerary(transfer(arrival_time=time))
    if time > '20:00':
        plan.days[0].stops[0].start, plan.days[0].stops[0].end = '21:00', '22:00'
    if with_hotel:
        request.confirmed_hotels = [ConfirmedHotel(city='北京', name='用户选定的合成酒店',
            address='合成分店地址', check_in='2026-11-02', check_out='2026-11-04', total_cost='300')]
    prepare_transfer_anchors(plan, request)
    apply_hotel_anchors(plan, request)
    return request, plan


def test_arrival_hotel_is_first_local_business_point_without_fabricated_location():
    request, plan = arrival_case()
    stops = plan.days[0].stops
    arrive = next(i for i, s in enumerate(stops) if s.booking_anchor == 'transport_arrival')
    hotel = stops[arrive + 1]
    assert hotel.kind == 'hotel' and hotel.booking_anchor == 'hotel_departure'
    assert hotel.name == request.confirmed_hotels[0].name
    assert (hotel.start, hotel.end) == ('12:05', '12:20')
    assert hotel.location is None and hotel.location_status == 'unknown'
    assert hotel.booking_address == '合成分店地址'
    assert '尚不表示已入住' in hotel.description and '须向酒店确认' in hotel.description
    costs = [expense for day in plan.days for expense in day.expenses if expense.category == 'hotel']
    assert len(costs) == 1 and costs[0].unit_cost == 300
    assert costs[0].commitment == 'planned'  # User-entered amount is not a payment receipt.


def test_hotel_then_lunch_then_sight_is_idempotent_and_keeps_original_expenses():
    request, plan = arrival_case()
    before_expenses = [expense.model_dump_json() for day in plan.days for expense in day.expenses]
    notices = prepare_arrival_flow(request, plan, only_day=0)
    local = [s for s in plan.days[0].stops if s.city == '北京']
    assert [s.kind for s in local[:4]] == ['break', 'hotel', 'meal', 'sight']
    lunch = local[2]
    assert lunch.meal_period == 'lunch' and lunch.location is None and lunch.poi_id == ''
    assert '费用待' in lunch.location_note or '餐费待' in lunch.location_note
    assert notices and [expense.model_dump_json() for day in plan.days for expense in day.expenses] == before_expenses
    snapshot = plan.model_dump_json()
    apply_hotel_anchors(plan, request, only_day=0)
    prepare_arrival_flow(request, plan, only_day=0)
    assert plan.model_dump_json() == snapshot


def test_unknown_hotel_is_not_invented_when_a_meal_need_is_added():
    request, plan = arrival_case(with_hotel=False)
    prepare_arrival_flow(request, plan, only_day=0)
    assert not any(s.kind == 'hotel' for s in plan.days[0].stops)
    meal = next(s for s in plan.days[0].stops if s.kind == 'meal')
    assert meal.location is None and meal.location_behavior == 'unlocated'
    assert not any(e.category == 'food' for e in plan.days[0].expenses)


def test_late_arrival_gets_meal_before_sights_after_hotel_and_real_roads_repair_all():
    request, plan = arrival_case('20:50')
    prepare_arrival_flow(request, plan, only_day=0)
    local = [s for s in plan.days[0].stops if s.city == '北京']
    assert [s.kind for s in local[:4]] == ['break', 'hotel', 'meal', 'sight']
    assert local[2].id.startswith('late-meal-')
    routes = []
    for day_index, day in enumerate(plan.days):
        for number, stop in enumerate(day.stops):
            stop.location, stop.location_status = (116.3 + number * .01, 39.9), 'verified'
        for left, right in zip(day.stops, day.stops[1:]):
            if left.city != right.city:
                continue
            routes.append(RouteLeg(day_index=day_index, from_id=left.id, to_id=right.id, mode='walking',
                available=True, duration_s=600, distance_m=500, polyline=[left.location, right.location],
                queried_at=datetime.now(timezone.utc), note='仅合成离线道路'))
    from types import SimpleNamespace
    conflicts, changes = repair_booking_schedule(SimpleNamespace(request=request, itinerary=plan, routes=routes), only_days={0})
    assert not conflicts and changes
    assert local[1].start == '21:05' and local[2].start == '21:30' and local[3].start == '22:10'
    meal_conflicts, unknowns = meal_schedule_checks(request, plan, routes, only_day=0)
    assert not meal_conflicts and any('抵达较晚' in notice for notice in unknowns)
    assert any('营业时间尚未核实' in notice for notice in unknowns)


def test_existing_late_meal_keeps_identity_and_price_without_duplicate_meal():
    request, plan = arrival_case('20:50')
    existing = Stop(id='chosen-food', name='用户想去的合成餐厅', kind='meal', city='北京',
                    start='21:30', end='22:00', meal_period='dinner')
    plan.days[0].stops.insert(-1, existing)
    before = existing.model_dump_json()
    prepare_arrival_flow(request, plan, only_day=0, protected_text='想去用户想去的合成餐厅')
    assert [s.id for s in plan.days[0].stops if s.kind == 'meal'] == ['chosen-food']
    assert existing.model_dump_json() == before
    assert plan.days[0].stops.index(next(s for s in plan.days[0].stops if s.id == existing.id)) < plan.days[0].stops.index(next(s for s in plan.days[0].stops if s.id == 'sight'))


def test_1321_arrival_and_real_hotel_roads_allow_1430_lunch_as_explicit_catchup():
    request, plan = arrival_case('13:21')
    prepare_arrival_flow(request, plan, only_day=0)
    meal = next(s for s in plan.days[0].stops if s.kind == 'meal')
    assert meal.meal_period == 'lunch' and meal.start == '13:41'
    routes = []
    for number, stop in enumerate(plan.days[0].stops):
        stop.location, stop.location_status = (116.3 + number * .01, 39.9), 'verified'
    for left, right in zip(plan.days[0].stops, plan.days[0].stops[1:]):
        if left.city != right.city:
            continue
        duration = 40 if left.booking_anchor == 'transport_arrival' else 9 if right.id == meal.id else 10
        routes.append(RouteLeg(day_index=0, from_id=left.id, to_id=right.id, mode='walking',
            available=True, duration_s=duration * 60, distance_m=1000, polyline=[left.location, right.location],
            queried_at=datetime.now(timezone.utc), note='仅离线合成接驳'))
    from types import SimpleNamespace
    conflicts, _ = repair_booking_schedule(SimpleNamespace(request=request, itinerary=plan, routes=routes), only_days={0})
    assert not conflicts and meal.start == '14:30'
    conflicts, notices = meal_schedule_checks(request, plan, routes, only_day=0)
    assert not conflicts and any('优先补餐' in note for note in notices)
    meal.start, meal.end = '17:00', '17:45'
    assert meal_schedule_checks(request, plan, routes, only_day=0)[0]


def test_estimated_incoming_without_station_does_not_put_breakfast_on_the_train():
    request = trip()
    plan = itinerary(transfer(schedule_status='estimated'))
    assert not any(s.booking_anchor for s in plan.days[0].stops)
    prepare_arrival_flow(request, plan, only_day=0)
    meals = [s for s in plan.days[0].stops if s.kind == 'meal']
    assert [s.meal_period for s in meals] == ['lunch']
    assert all(s.start >= '12:00' for s in meals)


def test_departure_cutoff_does_not_insert_lunch_after_boarding_deadline():
    request = TripRequest(destination='北京→天津', start_date='2026-11-02', start_time='09:00', days=2)
    plan = itinerary(CityTransfer(from_city='北京', to_city='天津', departure_date='2026-11-02',
        departure_time='12:00', arrival_date='2026-11-02', arrival_time='13:00', unit_cost=100))
    plan.days[0].stops = [Stop(id='beijing', name='北京合成地点', city='北京', start='09:00', end='10:00'),
        Stop(id='departure', name='合成北京站', city='北京', kind='break', start='11:30', end='11:35', booking_anchor='transport_departure'),
        Stop(id='tianjin', name='天津合成地点', city='天津', start='14:00', end='16:00')]
    notices = prepare_arrival_flow(request, plan, only_day=0)
    assert not any(s.kind == 'meal' and s.city == '北京' and s.meal_period == 'lunch' for s in plan.days[0].stops)
    assert any('提前备餐' in note for note in notices)
    assert next(s for s in plan.days[0].stops if s.kind == 'meal' and s.city == '天津').start >= '13:00'


def test_explicit_breakfast_opt_out_and_cuisine_preference_are_not_fact_claims():
    request = TripRequest(destination='北京', start_date='2026-11-02', days=2, start_time='08:00',
                          preferences='早餐自理，希望午餐吃素食')
    plan = itinerary()
    plan.transfers = []
    plan.days[0].stops[0].start, plan.days[0].stops[0].end = '08:00', '19:00'
    notices = prepare_arrival_flow(request, plan, only_day=0)
    meals = [s for s in plan.days[0].stops if s.kind == 'meal']
    assert {s.meal_period for s in meals} == {'lunch', 'dinner'}
    assert any('早餐按你的要求' in note for note in notices)
    assert meals[0].food_recommendations == ['素食']
    assert meals[0].location_status == 'unknown' and '待核实' in meals[0].description


@pytest.mark.parametrize(('hours', 'expected'), [('11:00-22:00', 'open'),
    ('17:00-22:00', 'closed'), ('17:00-02:00', 'closed'), ('全天营业', 'open'), ('暂停营业', 'closed'), ('营业时间未提供', 'unknown')])
def test_restaurant_hours_reference_is_explicit_and_unknown_is_not_open(hours, expected):
    stop = Stop(name='合成餐厅', kind='meal', start='12:30', end='13:15',
                opening_hours_status='map_reference', opening_hours=hours)
    assert meal_opening_reference(stop, 750, 795) == expected
    stop.opening_hours_scope = 'area'
    assert meal_opening_reference(stop, 750, 795) == 'unknown'


def test_strict_meal_alignment_does_not_move_real_restaurant_without_opening_support():
    request, plan, routes = late_lunch()
    assert align_meals(request, plan, routes, respect_opening=True) == []
    assert plan.days[0].stops[1].start == '15:00'
    meal = plan.days[0].stops[1]
    meal.opening_hours_status, meal.opening_hours = 'map_reference', '11:00-22:00'
    assert align_meals(request, plan, routes, respect_opening=True)
    assert meal.start == '12:30'


def test_known_closed_restaurant_is_conflict_but_missing_hours_stays_unknown():
    request, plan, routes = late_lunch()
    meal = plan.days[0].stops[1]
    meal.start, meal.end = '12:30', '13:30'
    conflicts, notices = meal_schedule_checks(request, plan, routes)
    assert not conflicts and any('营业时间尚未核实' in notice for notice in notices)
    meal.opening_hours_status, meal.opening_hours = 'map_reference', '17:00-22:00'
    assert any('不在已取得' in text for text in meal_schedule_checks(request, plan, routes)[0])


def test_unrequested_late_lunch_is_conflict_and_explicit_user_time_is_preserved():
    request, plan, routes = late_lunch()
    assert any('常见用餐时段' in text for text in meal_schedule_checks(request, plan, routes)[0])
    conflicts, notices = meal_schedule_checks(request, plan, routes, protected_text='我想15:00再吃午餐')
    assert not conflicts and any('保留你明确指定' in text for text in notices)


def test_meal_period_field_wins_over_a_restaurant_name_containing_breakfast():
    stop = Stop(name='合成早餐店', kind='meal', meal_period='lunch', start='12:00', end='12:45')
    from backend.meals import period
    assert period(stop) == 'lunch'


def test_late_night_meal_cannot_wrap_to_false_earlier_time():
    request, plan = arrival_case('23:40', with_hotel=False)
    plan.days[0].stops[-1].start, plan.days[0].stops[-1].end = '23:45', '23:55'
    before = plan.model_dump_json()
    with pytest.raises(ValueError, match='无法在当天安排晚餐'):
        prepare_arrival_flow(request, plan, only_day=0)
    assert plan.model_dump_json() == before


def test_study_flow_is_not_given_travel_hotels_or_meal_requirements():
    request = TripRequest(destination='上海', start_date='2026-11-02', days=1, study=StudyOptions())
    plan = itinerary()
    before = plan.model_dump_json()
    assert prepare_arrival_flow(request, plan) == [] and plan.model_dump_json() == before


def test_existing_afternoon_lunch_is_not_hoisted_in_front_of_the_day():
    """A saved late lunch must not jump ahead of sightseeing and push the day.

    Reported 2026-10-22: 13:21 arrival + hotel + a 16:25 lunch in the saved plan
    produced an itinerary ending 23:30, because the existing lunch was moved
    right after the hotel and its fixed 16:25 start pushed every later stop.
    """
    request, plan = arrival_case('13:21')
    day = plan.days[0]
    day.stops.insert(-1, Stop(id='sight-a', name='合成景点甲', city='北京', start='14:10', end='15:40'))
    day.stops.insert(-1, Stop(id='late-lunch', name='合成晚午餐', kind='meal', city='北京',
        meal_period='lunch', start='15:50', end='16:30'))
    prepare_arrival_flow(request, plan, only_day=0)
    ids = [stop.id for stop in day.stops]
    assert ids.index('late-lunch') > ids.index('sight-a')
    assert next(stop for stop in day.stops if stop.id == 'late-lunch').start == '15:50'


def test_transit_food_is_a_notice_without_fake_onboard_restaurant():
    request, plan = arrival_case()
    plan.transfers[0].departure_time = '10:00'
    plan.transfers[0].arrival_time = '14:30'
    before = [s.id for s in plan.days[0].stops]
    _, notices = meal_schedule_checks(request, plan, [], only_day=0)
    assert any('车上供餐未核实' in notice for notice in notices)
    assert [s.id for s in plan.days[0].stops] == before
