"""Carry confirmed ledger facts across regeneration without inventing refunds."""
from collections import Counter, defaultdict
from decimal import Decimal

from .bookings import confirmed_hotels, hotel_ref
from .models import Expense, city_key


def _protected(expense):
    return expense.commitment in ('paid', 'committed') or bool(expense.booking_ref)


def _economic_key(day_date, expense):
    # Stop ids can be regenerated or detached; they are not economic identity.
    return (day_date, expense.category, expense.label, Decimal(str(expense.unit_cost)),
            expense.quantity, expense.basis, expense.commitment, expense.booking_ref)


def _hotel_updates(original, candidate):
    old = {hotel_ref(hotel): hotel for hotel in confirmed_hotels(original.request)}
    new = {hotel_ref(hotel): hotel for hotel in confirmed_hotels(candidate.request)}
    totals = {ref: hotel for ref, hotel in new.items() if hotel.total_cost is not None}
    # Changing or removing a requested hotel may change its old planned estimate,
    # but never its paid/committed ledger entries.
    mutable = set(totals) | (set(old) - set(new))
    return totals, mutable


def _required_expenses(original, candidate):
    _, mutable = _hotel_updates(original, candidate)
    return [(day.date, expense) for day in original.itinerary.days for expense in day.expenses
            if _protected(expense) and not (expense.commitment == 'planned'
                and expense.category == 'hotel' and expense.booking_ref in mutable)]


def _same_activity(original, candidate, day_date, sid):
    before = [(day.date, stop) for day in original.itinerary.days for stop in day.stops if stop.id == sid]
    after = [(day.date, stop) for day in candidate.itinerary.days for stop in day.stops if stop.id == sid]
    if len(before) != 1 or len(after) != 1 or before[0][0] != day_date or after[0][0] != day_date:
        return False
    old, new = before[0][1], after[0][1]
    if city_key(old.city) != city_key(new.city) or old.kind != new.kind:
        return False
    if old.poi_id and new.poi_id:
        return old.poi_id == new.poi_id
    return (old.name, old.place_name) == (new.name, new.place_name)


def _note(expense, message):
    if message not in expense.note:
        expense.note = (expense.note[:max(0, 499 - len(message))] + ' ' + message).strip()


def _amount(expense, people):
    return Decimal(str(expense.unit_cost)) * expense.quantity * (people if expense.basis == 'person' else 1)


def preserve_confirmed_expenses(original_plan, candidate) -> list[str]:
    """Restore protected source expenses on their original dates, idempotently.

    A paid ticket is not a ticket for a newly generated activity sharing its id.
    Its ledger row stays, detached from that activity, until a human confirms any
    refund/change. A user-entered hotel total includes earlier payments for the
    same booking reference, so only the unpaid difference is a planned expense.
    """
    notices = []
    dates = {day.date: day for day in candidate.itinerary.days}
    positions = defaultdict(list)
    for day in candidate.itinerary.days:
        for index, expense in enumerate(day.expenses):
            if _protected(expense):
                positions[_economic_key(day.date, expense)].append((day, index))
    required = _required_expenses(original_plan, candidate)
    for day_date, source in required:
        day = dates.get(day_date)
        if day is None:
            notices.append(f'{day_date}的已付／承诺／预订费用「{source.label}」缺少记账日期，须保留该日期后才能保存')
            continue
        restored = source.model_copy(deep=True)
        if restored.stop_id and not _same_activity(original_plan, candidate, day_date, restored.stop_id):
            restored.stop_id = ''
            _note(restored, '保留原已付／承诺／预订费用；原活动已变化，未抵扣为新活动门票，也未假定退款。')
            notices.append(f'保留原费用「{restored.label}」，原活动已变化，费用与新活动脱钩，未假定退款')
        key = _economic_key(day_date, source)
        if positions[key]:
            existing_day, index = positions[key].pop(0)
            existing_day.expenses[index] = restored
        else:
            day.expenses.append(restored)
            notices.append(f'保留{day_date}的原已付／承诺／预订费用「{restored.label}」')

    # A regenerated model may repeat the same provisional row. Do not collapse
    # actual payments, different bookings, or estimates for a changed activity.
    for day in candidate.itinerary.days:
        seen, kept = set(), []
        protected_signatures = {(expense.category, expense.label, Decimal(str(expense.unit_cost)),
            expense.quantity, expense.basis, expense.stop_id) for expense in day.expenses
            if _protected(expense) and expense.stop_id}
        for expense in day.expenses:
            signature = (expense.category, expense.label, Decimal(str(expense.unit_cost)),
                         expense.quantity, expense.basis, expense.stop_id)
            if expense.commitment == 'planned' and not expense.booking_ref:
                if signature in protected_signatures or signature in seen:
                    notices.append(f'去除{day.date}重复的未付款估算「{expense.label}」')
                    continue
                seen.add(signature)
            kept.append(expense)
        day.expenses = kept

    totals, mutable = _hotel_updates(original_plan, candidate)
    for day in candidate.itinerary.days:
        day.expenses = [expense for expense in day.expenses
                        if not (expense.category == 'hotel' and expense.commitment == 'planned'
                                and expense.booking_ref in mutable)]
    for ref, hotel in totals.items():
        known = [expense for day in candidate.itinerary.days for expense in day.expenses
                 if expense.category == 'hotel' and expense.booking_ref == ref
                 and expense.commitment in ('paid', 'committed')]
        protected_total = sum((_amount(expense, candidate.request.people) for expense in known), Decimal('0'))
        total = Decimal(str(hotel.total_cost))
        if total < protected_total:
            message = (f'酒店「{hotel.name}」新填总额¥{total:.2f}低于已付／承诺¥{protected_total:.2f}；'
                       '保留原金额，未假定差额退款')
            notices.append(message)
            for expense in known:
                _note(expense, message)
            continue
        remaining = total - protected_total
        if not remaining and known:
            continue
        posting = next((day for day in candidate.itinerary.days if day.date >= hotel.check_in), None)
        if posting is None:
            notices.append(f'酒店「{hotel.name}」缺少可记账日期，请检查行程日期')
            continue
        label = f'已填住宿总额：{hotel.name}' if not known else f'住宿待付差额：{hotel.name}'
        note = '用户填写的本次入住全员总额；非实时房价或订单核验。'
        if known:
            note = (f'用户填写全员总额¥{total:.2f}，已单列保留已付／承诺¥{protected_total:.2f}；'
                    f'本项仅列剩余¥{remaining:.2f}，避免重复计费；非订单核验。')
            notices.append(f'酒店「{hotel.name}」总额已包含原已付／承诺费用，仅另列待付差额¥{remaining:.2f}')
        posting.expenses.append(Expense(label=label, category='hotel', unit_cost=remaining,
            basis='group', booking_ref=ref, note=note))
    return list(dict.fromkeys(notices))


def confirmed_expense_conflicts(original_plan, candidate) -> list[str]:
    """Independent economic multiset guard; ids/wording of notes are not proof."""
    expected = Counter(_economic_key(day_date, expense)
                       for day_date, expense in _required_expenses(original_plan, candidate))
    actual = Counter(_economic_key(day.date, expense) for day in candidate.itinerary.days
                     for expense in day.expenses if _protected(expense))
    conflicts = []
    for key, count in (expected - actual).items():
        day_date, _, label, _, _, _, commitment, _ = key
        state = {'paid': '已付', 'committed': '已承诺', 'planned': '已关联预订'}[commitment]
        conflicts.append(f'{day_date}的{state}费用「{label}」有{count}项未原额保留，不能自动删除、改价或假定退款')
    return conflicts
