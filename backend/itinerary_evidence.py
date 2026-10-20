"""Associate costs and guide excerpts with actual itinerary stops without inventing facts."""
import re
from decimal import Decimal

from .models import Expense, GuideDecision, cents


def bind_stop_expenses(itinerary, only_day=None):
    categories = {'food': 'meal', 'ticket': 'sight', 'hotel': 'hotel'}
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        stops = {s.id: s for s in day.stops}
        result = []
        for expense in day.expenses:
            expected_kind = categories.get(expense.category)
            existing = stops.get(expense.stop_id)
            if existing and (not expected_kind or existing.kind == expected_kind):
                result.append(expense)
                continue
            expense.stop_id = ''
            eligible = [s for s in day.stops if s.kind == expected_kind] if expected_kind else []
            matches = [s for s in eligible if any(name and name in expense.label for name in (s.name, s.place_name, s.matched_name))]
            if len(matches) == 1:
                expense.stop_id = matches[0].id
                result.append(expense)
            elif eligible and len(result) + len(eligible) + len(day.expenses) <= 20:
                # Allocate a legacy day-level budget, conserving its authoritative cents.
                total = cents(expense.unit_cost) * expense.quantity
                unit, remainder = divmod(total, len(eligible))
                for position, stop in enumerate(eligible):
                    result.append(Expense(label=f'{stop.name} · {expense.label}'[:160], category=expense.category,
                        unit_cost=Decimal(unit + (position < remainder)) / 100, basis=expense.basis,
                        stop_id=stop.id, note=('按当天合并预算分摊，非该地点查询报价；' + expense.note)[:500]))
            else:
                result.append(expense)
        day.expenses = result
        for stop in day.stops:
            if stop.kind == 'meal' and stop.average_cost_yuan is not None and not any(e.stop_id == stop.id and e.category == 'food' for e in result) and len(result) < 20:
                result.append(Expense(label=f'{stop.name} · 餐饮人均参考'[:160], category='food',
                    unit_cost=stop.average_cost_yuan, basis='person', stop_id=stop.id,
                    note='高德地点商业信息的人均消费参考，不是本次菜单报价；实际消费以商家为准'))


def normalize_text(value):
    return re.sub(r'\s+', '', value)


def audit_guides(trip, itinerary):
    stops = {s.id: s for day in itinerary.days for s in day.stops}
    decisions = []
    for decision in itinerary.guide_decisions:
        if decision.source_index >= len(trip.guides):
            continue
        original = normalize_text(trip.guides[decision.source_index].text)
        if normalize_text(decision.excerpt) not in original:
            continue  # A fabricated quote cannot be shown as source evidence.
        decision.stop_ids = [sid for sid in dict.fromkeys(decision.stop_ids) if sid in stops]
        supplied_places = decision.place_names[:]
        decision.place_names = [name for name in supplied_places if normalize_text(name) in normalize_text(decision.excerpt)]
        if supplied_places and not decision.place_names:
            decision.stop_ids = []
        if decision.place_names:
            decision.stop_ids = [sid for sid in decision.stop_ids if any(
                name in candidate or candidate in name for name in decision.place_names
                for candidate in (stops[sid].name, stops[sid].place_name, stops[sid].area_name, stops[sid].matched_name) if candidate)]
        if decision.status != 'not_used':
            for sid, stop in stops.items():
                if stop.kind == 'meal' and any(len(food) >= 2 and normalize_text(food) in normalize_text(decision.excerpt) for food in stop.food_recommendations):
                    if sid not in decision.stop_ids:
                        decision.stop_ids.append(sid)
        decision.stop_ids = decision.stop_ids[:12]
        if decision.place_names:
            missing = [name for name in decision.place_names if not any(
                name in candidate or candidate in name for sid in decision.stop_ids
                for candidate in (stops[sid].name, stops[sid].place_name, stops[sid].area_name, stops[sid].matched_name) if candidate)]
            if missing and decision.status == 'adopted' and decision.stop_ids:
                decision.status = 'adjusted'
                decision.reason = ('摘录中尚未对应活动的地点：' + '、'.join(missing) + '。' + decision.reason)[:600]
        if decision.status != 'not_used' and not decision.stop_ids:
            decision.status = 'not_used'
            decision.reason = '未找到可对应的最终活动，不能确认已采纳。原规划说明：' + decision.reason[:450]
        if decision.status == 'not_used':
            decision.stop_ids = []
        decisions.append(decision)
    decisions = decisions[:27]
    for index, guide in enumerate(trip.guides):
        if not any(d.source_index == index for d in decisions):
            decisions.append(GuideDecision(source_index=index, excerpt=guide.text[:120], status='not_used',
                reason='本次没有提供可核对的攻略采纳说明；不能仅凭参考摘要声称已落实。可要求按该攻略优先重新规划。'))
    itinerary.guide_decisions = decisions[:30]
