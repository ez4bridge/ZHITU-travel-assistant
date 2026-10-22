"""Offline regressions: model suggestions may yield, user facts and money may not."""
from datetime import date
from decimal import Decimal

import pytest

from backend.bookings import (drop_activity_ids, drop_last_optional_stop,
                              drop_movement_to_anchors, drop_named_activities,
                              drop_return_movement_stops)
from backend.models import DayPlan, Expense, Itinerary, Stop


def activity(sid, name='楼外楼午餐', **kwargs):
    return Stop(id=sid, name=name, city='杭州', kind='meal', start='12:00', end='13:00', **kwargs)


def itinerary(*days):
    return Itinerary(title='离线合成行程', summary='', days=[
        DayPlan(date=date(2026, 10, 28 + index), title=f'第{index + 1}天', stops=stops)
        for index, stops in enumerate(days)])


def expense(sid, amount='100', **kwargs):
    return Expense(label='离线合成预算', category='food', unit_cost=amount, stop_id=sid, **kwargs)


def ids(value):
    return [[stop.id for stop in day.stops] for day in value.days]


def test_remove_exact_id_and_day_keeps_same_name_on_another_day_and_its_money():
    trip = itinerary([activity('yesterday')], [activity('replaceable')])
    trip.days[0].expenses = [expense('yesterday', commitment='paid')]
    trip.days[1].expenses = [expense('replaceable')]
    before = trip.days[0].model_dump_json()
    assert drop_activity_ids(trip, {'replaceable'}, day_indices={1}, protected_ids={'yesterday'}) == ['楼外楼午餐']
    assert trip.days[0].model_dump_json() == before
    assert ids(trip) == [['yesterday'], []]
    assert trip.days[1].expenses == []


def test_day_scope_never_deletes_requested_id_on_another_day():
    trip = itinerary([activity('outside')], [activity('inside')])
    before = trip.model_dump_json()
    assert drop_activity_ids(trip, {'outside'}, day_indices={1}) == []
    assert trip.model_dump_json() == before


def test_duplicate_or_empty_ids_are_not_guessed_even_in_one_day_scope():
    trip = itinerary([activity('duplicate'), activity('')], [activity('duplicate')])
    before = trip.model_dump_json()
    assert drop_activity_ids(trip, {'duplicate', ''}, day_indices={1}) == []
    assert trip.model_dump_json() == before


@pytest.mark.parametrize('commitment,booking_ref', [('paid', ''), ('committed', ''), ('planned', 'order-ref')])
def test_protected_expense_prevents_activity_and_ledger_deletion(commitment, booking_ref):
    trip = itinerary([activity('protected')])
    trip.days[0].expenses = [expense('protected', commitment=commitment, booking_ref=booking_ref),
                             expense('protected', amount='20')]
    before = trip.model_dump_json()
    assert drop_activity_ids(trip, {'protected'}) == []
    assert trip.model_dump_json() == before


def test_payment_posted_on_another_date_still_protects_its_activity():
    trip = itinerary([activity('paid-later')], [activity('other')])
    trip.days[1].expenses = [expense('paid-later', commitment='paid')]
    before = trip.model_dump_json()
    assert drop_activity_ids(trip, {'paid-later'}, day_indices={0}) == []
    assert trip.model_dump_json() == before


@pytest.mark.parametrize('role', ['hotel_departure', 'hotel_return', 'transport_departure', 'transport_arrival'])
def test_fixed_hotel_and_station_anchors_are_never_removed(role):
    trip = itinerary([activity('fixed', name='杭州东站', booking_anchor=role, booking_ref='known')])
    before = trip.model_dump_json()
    assert drop_activity_ids(trip, {'fixed'}) == []
    assert trip.model_dump_json() == before


@pytest.mark.parametrize('kind,booking_ref', [('hotel', ''), ('break', 'provided-booking')])
def test_hotel_or_booking_reference_is_protected_without_anchor_role(kind, booking_ref):
    trip = itinerary([Stop(id='fixed', name='住宿', city='杭州', kind=kind,
                           start='12:00', end='13:00', booking_ref=booking_ref)])
    assert drop_activity_ids(trip, {'fixed'}) == []
    assert ids(trip) == [['fixed']]


@pytest.mark.parametrize('state', [{'locked': True}, {'status': 'completed'}])
def test_caller_passed_locked_or_completed_ids_are_preserved(state):
    trip = itinerary([activity('fixed'), activity('optional')])
    activities = {'fixed': state, 'optional': {'status': 'planned', 'locked': False}}
    protected = {sid for sid, value in activities.items()
                 if value.get('locked') or value.get('status') == 'completed'}
    assert drop_activity_ids(trip, {'fixed', 'optional'}, protected_ids=protected) == ['楼外楼午餐']
    assert ids(trip) == [['fixed']]


def test_planned_cost_is_removed_once_including_separate_posting_day():
    trip = itinerary([activity('remove')], [activity('keep')])
    trip.days[0].expenses = [expense('remove', '80'), expense('', '30')]
    trip.days[1].expenses = [expense('remove', '20'), expense('keep', '50')]
    assert sum(e.unit_cost for day in trip.days for e in day.expenses) == Decimal('180')
    assert drop_activity_ids(trip, {'remove'}, day_indices={0}) == ['楼外楼午餐']
    assert sum(e.unit_cost for day in trip.days for e in day.expenses) == Decimal('80')
    assert [e.stop_id for day in trip.days for e in day.expenses] == ['', 'keep']
    assert drop_activity_ids(trip, {'remove'}, day_indices={0}) == []


def test_legacy_name_helper_refuses_ambiguous_names_and_respects_day_scope():
    trip = itinerary([activity('first')], [activity('second')])
    assert drop_named_activities(trip, {'楼外楼午餐'}) == []
    assert ids(trip) == [['first'], ['second']]
    assert drop_named_activities(trip, {'楼外楼午餐'}, day_indices={1}) == ['楼外楼午餐']
    assert ids(trip) == [['first'], []]


def test_last_optional_skips_later_paid_and_completed_activity():
    stops = [activity('optional'), activity('locked'), activity('paid')]
    stops[1].start, stops[1].end = '14:00', '15:00'
    stops[2].start, stops[2].end = '16:00', '17:00'
    trip = itinerary(stops)
    trip.days[0].expenses = [expense('paid', commitment='paid')]
    assert drop_last_optional_stop(trip, 0, protected_ids={'locked'}) == '楼外楼午餐'
    assert ids(trip) == [['locked', 'paid']]
    assert drop_last_optional_stop(trip, 0, protected_ids={'locked'}) is None


@pytest.mark.parametrize('helper', [drop_return_movement_stops, drop_movement_to_anchors])
def test_movement_cleanup_respects_scope_state_and_paid_ledger(helper):
    def movements(prefix):
        return [Stop(id=f'{prefix}-hotel', name='合成酒店', city='杭州', kind='hotel',
                     start='20:00', end='20:10', booking_anchor='hotel_return'),
                Stop(id=f'{prefix}-move', name='返回酒店', city='杭州', kind='break',
                     place_name='合成酒店', start='19:00', end='20:00')]
    trip = itinerary(movements('outside'), movements('inside'))
    trip.days[1].expenses = [expense('inside-move', commitment='paid')]
    before = trip.model_dump_json()
    assert helper(trip, day_indices={1}, protected_ids={'outside-move'}) == []
    assert trip.model_dump_json() == before
    trip.days[1].expenses[0].commitment = 'planned'
    assert helper(trip, day_indices={1}) == ['返回酒店']
    assert ids(trip) == [['outside-hotel', 'outside-move'], ['inside-hotel']]
