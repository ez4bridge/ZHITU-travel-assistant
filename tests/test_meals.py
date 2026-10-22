from datetime import datetime, timezone

from backend.meals import align_meals, meal_notes
from backend.models import Stop, RouteLeg
from backend.validation import normalized_input, validate_core
from test_core import sample


async def dining_evidence_stub(itinerary, *args, **kwargs):
    """Map-boundary stub for SSE tests; fill evidence for server-added meals too."""
    from decimal import Decimal
    result = []
    for index, day in enumerate(itinerary.days):
        if kwargs.get('day_indices') is not None and index not in kwargs['day_indices']:
            continue
        for stop in day.stops:
            if stop.kind == 'meal':
                stop.poi_id = stop.id + '-poi'
                stop.location = (121.49, 31.22)
                stop.location_status = 'verified'
                stop.average_cost_yuan = Decimal('35')
                stop.information_source = 'https://www.amap.com/place/' + stop.poi_id
        for a, b in zip(day.stops, day.stops[1:]):
            result.append(RouteLeg(day_index=index, from_id=a.id, to_id=b.id, mode='walking', available=True,
                duration_s=600, distance_m=500, queried_at=datetime.now(timezone.utc), polyline=[a.location, b.location]))
    return result


def test_afternoon_end_does_not_remove_dinner_from_multiday_travel():
    from datetime import timedelta
    from backend.meals import prepare_meal_slots, require_meal_coverage
    trip, itinerary, _ = sample()
    trip.days = 2
    itinerary.days.append(itinerary.days[0].model_copy(deep=True))
    itinerary.days[1].date += timedelta(days=1)
    for i, day in enumerate(itinerary.days):
        for stop in day.stops:
            stop.city = '上海'
            stop.id += str(i)
        day.stops[-1].start = '13:30'
        day.stops[-1].end = '16:00'
    prepare_meal_slots(trip, itinerary)
    require_meal_coverage(trip, itinerary)
    for day in itinerary.days:
        assert {s.meal_period for s in day.stops if s.kind == 'meal'} == {'lunch', 'dinner'}
        assert next(s for s in day.stops if s.meal_period == 'dinner').start == '18:00'
    # Repeating preparation must not duplicate meals or IDs.
    prepare_meal_slots(trip, itinerary)
    assert len({s.id for d in itinerary.days for s in d.stops}) == 8


def test_first_lunch_and_final_dinner_on_trains_are_costed_and_not_fake_restaurants():
    from test_roundtrip_generation import roundtrip
    from backend.meals import prepare_meal_slots, require_meal_coverage, provisions
    from backend.feasibility import price_meals
    from backend.itinerary_evidence import bind_stop_expenses
    trip, itinerary = roundtrip()
    prepare_meal_slots(trip, itinerary)
    assert 'lunch' in provisions(itinerary.days[0])
    assert 'dinner' in provisions(itinerary.days[-1])
    assert not any(s.meal_period == 'dinner' for s in itinerary.days[-1].stops)
    import asyncio
    asyncio.run(dining_evidence_stub(itinerary))
    price_meals(itinerary)
    bind_stop_expenses(itinerary)
    price_meals(itinerary)
    require_meal_coverage(trip, itinerary)
    assert len(provisions(itinerary.days[-1])) == 1
    note = meal_notes(itinerary.days[-1])
    assert '晚餐' in note and '提前' in note and '不保证列车供餐' in note
    assert provisions(itinerary.days[-1])['dinner'].unit_cost == 50


def test_busy_noon_must_be_fixed_instead_of_disguised_as_transit_food():
    from backend.meals import prepare_meal_slots
    import pytest
    trip, itinerary, _ = sample()
    trip.days = 2
    itinerary.days[0].stops[-1].end = '16:00'
    with pytest.raises(ValueError, match='午餐'):
        prepare_meal_slots(trip, itinerary)


def test_missing_dinner_cannot_pass_publish_gate():
    import pytest
    from backend.meals import require_meal_coverage
    trip, itinerary, _ = sample()
    trip.days = 2
    itinerary.days[0].stops.append(Stop(name='午餐', city='上海', kind='meal', meal_period='lunch', start='12:30', end='13:15'))
    with pytest.raises(ValueError, match='晚餐'):
        require_meal_coverage(trip, itinerary)


def test_fifteen_oclock_meal_cannot_satisfy_dinner_coverage():
    import pytest
    from backend.meals import require_meal_coverage
    trip, itinerary, _ = sample()
    trip.days = 2
    itinerary.days[0].stops += [
        Stop(name='午餐', kind='meal', meal_period='lunch', start='12:30', end='13:15'),
        Stop(name='晚餐', kind='meal', meal_period='dinner', start='15:00', end='15:45')]
    with pytest.raises(ValueError, match='晚餐'):
        require_meal_coverage(trip, itinerary)


def test_fake_train_dinner_is_a_provision_not_a_fifteen_oclock_restaurant():
    from test_roundtrip_generation import roundtrip
    from backend.meals import prepare_meal_slots, provisions
    trip, itinerary = roundtrip()
    itinerary.days[-1].stops.append(Stop(id='fake-dinner', name='返程列车晚餐（提前备餐）',
        city='拉萨市', kind='meal', meal_period='dinner', start='15:00', end='15:45'))
    prepare_meal_slots(trip, itinerary)
    assert all(s.id != 'fake-dinner' for s in itinerary.days[-1].stops)
    assert 'dinner' in provisions(itinerary.days[-1])


def test_same_chain_different_branches_are_repeated_but_unrelated_restaurants_are_not():
    from backend.meals import restaurant_name_key
    assert restaurant_name_key('上海', '小杨生煎（宏伊广场店）') == restaurant_name_key('上海市', '小杨生煎(商城路店)')
    assert restaurant_name_key('上海', '小杨生煎（宏伊广场店）') != restaurant_name_key('上海', '老盛昌（人民广场店）')
    assert restaurant_name_key('杭州', '新白鹿餐厅（银泰店）') == restaurant_name_key('杭州', '新白鹿（湖滨店）')
    assert restaurant_name_key('上海', '大壶春生煎（福州路店）') == restaurant_name_key('上海', '大壶春（四川中路店）')


def test_single_day_morning_visit_does_not_gain_unrequested_dinner():
    from backend.meals import prepare_meal_slots
    trip, itinerary, _ = sample()
    assert not prepare_meal_slots(trip, itinerary)
    assert not any(s.kind == 'meal' for s in itinerary.days[0].stops)


def late_lunch():
    trip, itinerary, _ = sample()
    day = itinerary.days[0]
    day.stops[0].end = '12:00'
    day.stops[1].start, day.stops[1].end = '17:00', '18:00'
    meal = Stop(id='meal', name='午餐', kind='meal', start='15:00', end='16:00',
        meal_period='lunch', location=(121.49, 31.225), location_status='verified', poi_id='meal-poi')
    day.stops.insert(1, meal)
    routes = [RouteLeg(day_index=0, from_id=a.id, to_id=b.id, mode='walking', available=True,
        queried_at=datetime.now(timezone.utc), duration_s=600, distance_m=800,
        polyline=[a.location, b.location]) for a, b in zip(day.stops, day.stops[1:])]
    return trip, itinerary, routes


def test_meal_moves_into_free_normal_lunch_slot_without_changing_order_or_duration():
    trip, itinerary, routes = late_lunch()
    changes = align_meals(trip, itinerary, routes)
    meal = itinerary.days[0].stops[1]
    assert meal.start == '12:30' and meal.end == '13:30' and changes
    assert [s.id for s in itinerary.days[0].stops] == ['a', 'meal', 'b']
    assert not any(e['code'] in ('time_overlap', 'transfer_time') for e in validate_core(normalized_input(trip, itinerary, routes))['errors'])


def test_no_feasible_normal_slot_keeps_real_schedule_and_prompts_snacks():
    trip, itinerary, routes = late_lunch()
    itinerary.days[0].stops[0].end = '14:20'
    assert not align_meals(trip, itinerary, routes)
    assert itinerary.days[0].stops[1].start == '15:00'
    assert '超出常见用餐时段' in itinerary.days[0].meal_note
    assert '自带干粮' in itinerary.days[0].meal_note


def test_user_explicit_meal_time_is_respected():
    trip, itinerary, routes = late_lunch()
    align_meals(trip, itinerary, routes, protected_text='我想15:00再吃午餐')
    assert itinerary.days[0].stops[1].start == '15:00'


def test_missing_meals_have_explicit_along_way_or_packed_food_fallback_without_fake_stops():
    trip, itinerary, routes = sample()
    itinerary.days[0].stops[-1].end = '18:00'
    before = [s.id for s in itinerary.days[0].stops]
    align_meals(trip, itinerary, routes)
    note = itinerary.days[0].meal_note
    assert '午餐未安排' in note and '晚餐未安排' in note
    assert '沿途就餐' in note and '自带干粮' in note
    assert [s.id for s in itinerary.days[0].stops] == before
    report = validate_core(normalized_input(trip, itinerary, routes))
    assert any(u['code'] == 'meal_arrangement' for u in report['unknowns'])


def test_pure_transfer_day_does_not_pretend_food_is_available_onboard():
    _, itinerary, _ = sample()
    itinerary.days[0].stops = []
    note = meal_notes(itinerary.days[0])
    assert '车站或沿途' in note and '自带干粮' in note
