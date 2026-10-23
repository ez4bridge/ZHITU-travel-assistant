from datetime import datetime, timezone

from backend.meals import align_meals, meal_notes
from backend.models import Stop, RouteLeg
from backend.validation import normalized_input, validate_core
from test_core import sample


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
