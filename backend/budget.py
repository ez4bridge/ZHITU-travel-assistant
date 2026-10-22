"""Reduce verified transport costs without new network requests or fictitious prices."""
import math
from decimal import Decimal

from .models import cents, city_key, itinerary_expenses
from .feasibility import schedule_journey
from .planning import recost_all
from .validation import normalized_input, validate_core
from .railway import Railway
from .meals import period, WINDOWS
from .validation import minutes


def report_for(trip, itinerary, routes):
    return validate_core(normalized_input(trip, itinerary, routes))


def route_cost(trip, route):
    cost = route.cost_estimate
    if not cost:
        return None
    return cents(cost.high) * (trip.people if cost.basis == 'person' else math.ceil(trip.people / 4))


def optimize_budget(trip, itinerary, routes, rail, only_day=None):
    """Greedily accept the largest verified saving that preserves feasibility.

    At most eight changes and 128 candidate validations; failed trials leave the
    original untouched. Meals, hotel allowances and admission prices are never cut.
    """
    current = report_for(trip, itinerary, routes)
    changes = []
    checked = 0
    for _ in range(8):
        if current['errors'] or current['total_cents'] <= current['budget_cents']:
            break
        proposals = []
        if trip.mode == 'auto':
            for index, route in enumerate(routes):
                if route.user_selected or route.scope != 'local' or (only_day is not None and route.day_index != only_day):
                    continue
                old_cost = route_cost(trip, route)
                if old_cost is None:
                    continue
                for option in route.alternatives:
                    new_cost = route_cost(trip, option)
                    if (not option.available or option.mode not in ('walking', 'transit', 'taxi')
                            or option.duration_s is None or option.distance_m is None or len(option.polyline) < 2
                            or new_cost is None or new_cost >= old_cost):
                        continue
                    if option.mode == 'walking':
                        walk = sum(r.distance_m or 0 for r in routes if r.day_index == route.day_index and r.mode == 'walking')
                        limit = {'relaxed': 6000, 'balanced': 10000, 'compact': 15000}[trip.style]
                        if option.distance_m > 3000 or walk + option.distance_m > limit:
                            continue
                    proposals.append((old_cost - new_cost, 'route', index, option))
        if only_day is None:
            for index, transfer in enumerate(itinerary.transfers):
                if transfer.mode != 'train' or transfer.schedule_status != 'queried':
                    continue
                key = (city_key(transfer.from_city), city_key(transfer.to_city), str(transfer.departure_date))
                old_cost = cents(transfer.unit_cost) * (trip.people if transfer.basis == 'person' else 1)
                for option in rail.query_cache.get(key, {}).get('options', []):
                    if option.get('reference_price') is None or (option['seat_type'] == '无座' and transfer.seat_type != '无座'):
                        continue
                    new_cost = cents(Decimal(option['reference_price'])) * trip.people
                    if new_cost < old_cost:
                        proposals.append((old_cost - new_cost, 'rail', index, option))
        accepted = False
        for saving, kind, index, option in sorted(proposals, key=lambda p: p[0], reverse=True):
            checked += 1
            if checked > 128:
                return itinerary, routes, changes
            trial = itinerary.model_copy(deep=True)
            trial_routes = [r.model_copy(deep=True) for r in routes]
            if kind == 'route':
                route = trial_routes[index]
                for key, value in option.model_dump().items():
                    setattr(route, key, value)
                route.selection_reason = '预算优化：使用已查到的较低费用路线，已重新核对时间衔接；未改变手动选择的路段。'
                description = f'第{route.day_index + 1}天一段市内交通改用{dict(walking="步行", transit="公交地铁", taxi="打车")[route.mode]}'
            else:
                Railway.apply_verified_option(trial.transfers[index], option)
                description = f'{trial.transfers[index].from_city}→{trial.transfers[index].to_city}改选已核验的{option["train_code"]} {option["seat_type"]}'
            try:
                timing = schedule_journey(trip, trial, trial_routes, only_day=only_day)
                for before_day, after_day in zip(itinerary.days, trial.days):
                    for before in before_day.stops:
                        if before.kind != 'meal':
                            continue
                        lo, hi = WINDOWS[period(before)]
                        after = next((s for s in after_day.stops if s.id == before.id), None)
                        if lo <= minutes(before.start) <= hi and (after is None or not lo <= minutes(after.start) <= hi):
                            raise ValueError('降本交通不能把正常午晚餐挪到凌晨或其他非用餐时段')
                recost_all(trip, trial, trial_routes, only_day)
                candidate = report_for(trip, trial, trial_routes)
            except ValueError:
                continue
            if candidate['errors'] or candidate['total_cents'] >= current['total_cents']:
                continue
            actual_saving = current['total_cents'] - candidate['total_cents']
            itinerary, routes, current = trial, trial_routes, candidate
            changes.extend([f'{description}，全员参考费用减少{actual_saving / 100:.2f}元。', *timing])
            accepted = True
            break
        if not accepted:
            break
    return itinerary, routes, changes


def budget_advice(trip, itinerary, report):
    if report.total_cents <= report.budget_cents:
        return
    totals = {}
    labels = {'transport': '交通', 'food': '餐饮', 'hotel': '住宿', 'ticket': '门票', 'other': '其他'}
    for expense in itinerary_expenses(itinerary):
        totals[expense.category] = totals.get(expense.category, 0) + cents(expense.unit_cost) * expense.quantity * (trip.people if expense.basis == 'person' else 1)
    top = sorted(totals, key=totals.get, reverse=True)[:2]
    excess = (report.total_cents - report.budget_cents) / 100
    summary = '、'.join(f'{labels[key]}{totals[key] / 100:.2f}元' for key in top)
    message = f'预算说明：当前全员估算{report.total_cents / 100:.2f}元，比目标超出{excess:.2f}元。主要支出为{summary}。可进一步选择更低人均的具体餐馆、经济住宿片区或减少可选付费项目；需重新核价，不以虚构低价满足预算。'
    itinerary.tips = [*itinerary.tips[:9], message]
