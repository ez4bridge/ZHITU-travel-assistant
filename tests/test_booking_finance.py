"""Offline ledger evidence must survive a fresh model itinerary."""
from datetime import date
from decimal import Decimal

import pytest

from backend.booking_finance import confirmed_expense_conflicts, preserve_confirmed_expenses
from backend.models import ConfirmedHotel, Expense
from plan_samples import sample_plan


def plans():
    original = sample_plan(2)
    for day in original.itinerary.days:
        day.expenses = []
    return original, original.model_copy(deep=True)


def fee(label='原门票', amount='37.50', **kwargs):
    return Expense(label=label, category='ticket', unit_cost=amount, **kwargs)


def ledger_total(plan):
    return sum((e.unit_cost * e.quantity * (plan.request.people if e.basis == 'person' else 1)
                for day in plan.itinerary.days for e in day.expenses), Decimal('0'))


@pytest.mark.parametrize('commitment,booking_ref', [('paid', ''), ('committed', ''), ('planned', 'external-order')])
def test_regeneration_restores_protected_money_on_original_date_idempotently(commitment, booking_ref):
    original, candidate = plans()
    original.itinerary.days[1].expenses = [fee(commitment=commitment, booking_ref=booking_ref,
                                                stop_id='s1-0', quantity=2, basis='person')]
    before = original.model_dump_json()
    assert confirmed_expense_conflicts(original, candidate)
    notices = preserve_confirmed_expenses(original, candidate)
    assert notices and confirmed_expense_conflicts(original, candidate) == []
    assert not candidate.itinerary.days[0].expenses
    restored = candidate.itinerary.days[1].expenses[0]
    assert restored.unit_cost == Decimal('37.50') and restored.quantity == 2
    assert restored.commitment == commitment and restored.booking_ref == booking_ref
    assert restored.stop_id == 's1-0'
    assert ledger_total(candidate) == Decimal('75') * candidate.request.people
    once = candidate.model_dump_json()
    preserve_confirmed_expenses(original, candidate)
    assert candidate.model_dump_json() == once and original.model_dump_json() == before


def test_reused_stop_id_detaches_old_paid_ticket_and_keeps_new_activity_budget():
    original, candidate = plans()
    old = original.itinerary.days[0].stops[0]
    original.itinerary.days[0].expenses = [fee(commitment='paid', stop_id=old.id)]
    candidate.itinerary.days[0].stops[0].name = '完全不同的新景点'
    candidate.itinerary.days[0].stops[0].poi_id = 'another-place'
    candidate.itinerary.days[0].expenses = [fee(stop_id=old.id)]
    notices = preserve_confirmed_expenses(original, candidate)
    rows = candidate.itinerary.days[0].expenses
    paid = next(e for e in rows if e.commitment == 'paid')
    assert paid.stop_id == '' and '未抵扣为新活动门票' in paid.note
    assert any(e.commitment == 'planned' and e.stop_id == old.id for e in rows)
    assert any('脱钩' in notice for notice in notices)
    assert ledger_total(candidate) == Decimal('75')
    assert confirmed_expense_conflicts(original, candidate) == []


def test_removed_activity_keeps_real_payment_without_fabricating_an_activity():
    original, candidate = plans()
    sid = original.itinerary.days[0].stops[0].id
    original.itinerary.days[0].expenses = [fee(commitment='paid', stop_id=sid)]
    candidate.itinerary.days[0].stops = []
    preserve_confirmed_expenses(original, candidate)
    assert not candidate.itinerary.days[0].stops
    assert candidate.itinerary.days[0].expenses[0].stop_id == ''
    assert confirmed_expense_conflicts(original, candidate) == []


def test_economic_multiset_does_not_accept_one_row_for_two_identical_payments():
    original, candidate = plans()
    original.itinerary.days[0].expenses = [fee(commitment='paid'), fee(commitment='paid')]
    candidate.itinerary.days[0].expenses = [fee(commitment='paid')]
    assert len(confirmed_expense_conflicts(original, candidate)) == 1
    preserve_confirmed_expenses(original, candidate)
    assert len(candidate.itinerary.days[0].expenses) == 2
    assert confirmed_expense_conflicts(original, candidate) == []


@pytest.mark.parametrize('mutation', ['unit_cost', 'quantity', 'basis', 'booking_ref', 'commitment'])
def test_guard_checks_economic_fields_without_trusting_stop_id_or_note(mutation):
    original, candidate = plans()
    original.itinerary.days[0].expenses = [fee(commitment='paid', booking_ref='known')]
    candidate.itinerary.days[0].expenses = [original.itinerary.days[0].expenses[0].model_copy(deep=True)]
    candidate.itinerary.days[0].expenses[0].stop_id = 'new-id'
    candidate.itinerary.days[0].expenses[0].note = '备注可变化'
    assert confirmed_expense_conflicts(original, candidate) == []
    setattr(candidate.itinerary.days[0].expenses[0], mutation, {
        'unit_cost': Decimal('1'), 'quantity': 2, 'basis': 'person',
        'booking_ref': 'other-order', 'commitment': 'planned'}[mutation])
    assert confirmed_expense_conflicts(original, candidate)


def test_missing_original_posting_date_is_reported_instead_of_moving_payment():
    original, candidate = plans()
    original.itinerary.days[1].expenses = [fee(commitment='paid')]
    candidate.itinerary.days = candidate.itinerary.days[:1]
    notices = preserve_confirmed_expenses(original, candidate)
    assert any('缺少记账日期' in notice for notice in notices)
    assert confirmed_expense_conflicts(original, candidate)
    assert candidate.itinerary.days[0].expenses == []


def test_repeated_provisional_rows_and_shadow_of_existing_payment_are_not_double_counted():
    original, candidate = plans()
    sid = original.itinerary.days[0].stops[0].id
    original.itinerary.days[0].expenses = [fee(commitment='paid', stop_id=sid)]
    candidate.itinerary.days[0].expenses = [fee(stop_id=sid),
        fee('普通预算', '20', stop_id='s0-1'), fee('普通预算', '20', stop_id='s0-1')]
    preserve_confirmed_expenses(original, candidate)
    assert len(candidate.itinerary.days[0].expenses) == 2
    assert ledger_total(candidate) == Decimal('57.50')


def hotel(total):
    return ConfirmedHotel(city='北京', name='离线合成酒店', booking_id='same-hotel-order',
        check_in=date(2026, 11, 2), check_out=date(2026, 11, 4), total_cost=total)


@pytest.mark.parametrize('commitment', ['paid', 'committed'])
def test_hotel_total_includes_old_payment_and_only_difference_is_planned(commitment):
    original, candidate = plans()
    original.request.confirmed_hotels = [hotel('600')]
    candidate.request.confirmed_hotels = [hotel('800')]
    original.itinerary.days[0].expenses = [Expense(label='住宿已付', category='hotel', unit_cost='200',
        booking_ref='same-hotel-order', commitment=commitment),
        Expense(label='旧住宿预计总额', category='hotel', unit_cost='600', booking_ref='same-hotel-order')]
    candidate.itinerary.days[0].expenses = [Expense(label='已填住宿总额：离线合成酒店', category='hotel',
        unit_cost='800', booking_ref='same-hotel-order')]
    preserve_confirmed_expenses(original, candidate)
    rows = candidate.itinerary.days[0].expenses
    assert len(rows) == 2
    assert next(e for e in rows if e.commitment == commitment).unit_cost == Decimal('200')
    planned = next(e for e in rows if e.commitment == 'planned')
    assert planned.unit_cost == Decimal('600') and '仅列剩余' in planned.note
    assert ledger_total(candidate) == Decimal('800')
    assert confirmed_expense_conflicts(original, candidate) == []
    once = candidate.model_dump_json()
    preserve_confirmed_expenses(original, candidate)
    assert candidate.model_dump_json() == once


def test_hotel_new_total_below_payment_never_implies_an_automatic_refund():
    original, candidate = plans()
    original.request.confirmed_hotels = [hotel('600')]
    candidate.request.confirmed_hotels = [hotel('200')]
    original.itinerary.days[0].expenses = [Expense(label='住宿已付', category='hotel', unit_cost='400',
        booking_ref='same-hotel-order', commitment='paid')]
    notices = preserve_confirmed_expenses(original, candidate)
    assert ledger_total(candidate) == Decimal('400')
    assert any('低于已付' in notice and '未假定差额退款' in notice for notice in notices)
    assert confirmed_expense_conflicts(original, candidate) == []


def test_user_can_reduce_or_remove_a_purely_planned_hotel_estimate():
    original, candidate = plans()
    original.request.confirmed_hotels = [hotel('600')]
    candidate.request.confirmed_hotels = [hotel('200')]
    original.itinerary.days[0].expenses = [Expense(label='旧住宿预计', category='hotel', unit_cost='600',
        booking_ref='same-hotel-order')]
    preserve_confirmed_expenses(original, candidate)
    assert ledger_total(candidate) == Decimal('200')
    assert confirmed_expense_conflicts(original, candidate) == []
    candidate.request.confirmed_hotels = []
    preserve_confirmed_expenses(original, candidate)
    assert ledger_total(candidate) == 0
    assert confirmed_expense_conflicts(original, candidate) == []


def test_unmodified_hotel_without_a_new_total_retains_its_old_estimate():
    original, candidate = plans()
    original.request.confirmed_hotels = [hotel('600')]
    candidate.request.confirmed_hotels = [hotel(None)]
    original.itinerary.days[0].expenses = [Expense(label='旧住宿预计', category='hotel', unit_cost='600',
        booking_ref='same-hotel-order')]
    preserve_confirmed_expenses(original, candidate)
    assert ledger_total(candidate) == Decimal('600')
    assert confirmed_expense_conflicts(original, candidate) == []
