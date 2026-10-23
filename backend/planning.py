"""Bounded schedule repair and route selection without another model call."""
import math
from decimal import Decimal

from .models import Expense, Itinerary, RouteLeg, cents
from .validation import minutes


def clock(value: int) -> str:
    return f'{value // 60:02d}:{value % 60:02d}'


def repair_schedule(itinerary: Itinerary, routes: list[RouteLeg], start_time: str, only_day: int | None = None) -> list[str]:
    adjustments = []
    for day_index, day in enumerate(itinerary.days):
        if only_day is not None and day_index != only_day:
            continue
        proposed = []
        previous_end = minutes(start_time) if day_index == 0 else 0
        for index, stop in enumerate(day.stops):
            duration = minutes(stop.end) - minutes(stop.start)
            if duration <= 0:
                break
            transfer = 0
            if index:
                before = day.stops[index - 1]
                leg = next((r for r in routes if r.day_index == day_index and r.from_id == before.id and r.to_id == stop.id), None)
                if leg and leg.available and leg.duration_s is not None:
                    transfer = math.ceil(leg.duration_s / 60)
            start = max(minutes(stop.start), previous_end + transfer)
            end = start + duration
            if end > 23 * 60 + 59:
                adjustments.append(f'第 {day_index + 1} 天无法在当天完成，保留原时间并提示冲突；请删减活动或更换交通。')
                break
            proposed.append((clock(start), clock(end)))
            previous_end = end
        if len(proposed) != len(day.stops):
            continue
        changed = sum((s.start, s.end) != times for s, times in zip(day.stops, proposed))
        for stop, (start, end) in zip(day.stops, proposed):
            stop.start, stop.end = start, end
        if changed:
            adjustments.append(f'第 {day_index + 1} 天根据所选路线顺延了 {changed} 个活动；开放时间和预约仍需核实。')
    return adjustments


def recost_transport(itinerary: Itinerary, routes: list[RouteLeg], day_index: int) -> None:
    """Budget the upper reference per leg; separate per-person and vehicle costs."""
    day = itinerary.days[day_index]
    # A route refresh can replace an unspent estimate, never a payment or an
    # existing booking. New route estimates are separate reserves; no refund or
    # offset is inferred from a route changing or disappearing.
    day.expenses = [e for e in day.expenses if e.category != 'transport'
                    or e.commitment != 'planned' or e.booking_ref]
    fares = [r.fare_yuan for r in routes if r.day_index == day_index and r.available
             and r.fare_yuan is not None and r.mode == 'transit' and r.cost_estimate is None]
    if fares:
        if len(day.expenses) >= 20:
            raise ValueError('当天费用明细已达上限，请先精简费用项目后再切换交通方式')
        preserved = any(e.category == 'transport' for e in day.expenses)
        day.expenses.append(Expense(label='已查到的公共交通票价', category='transport',
            unit_cost=Decimal(sum(cents(fare) for fare in fares)) / 100, basis='person',
            note='仅汇总已查到的票价，其他交通费用仍需补齐' +
                ('；已付／承诺／预订费用保留另列，未假定退款或抵扣' if preserved else '')))


def recost_all(trip, itinerary, routes, only_day=None):
    from .transport_cost import price_routes
    price_routes(trip, itinerary, routes, only_day)
    for index, day in enumerate(itinerary.days):
        if only_day is not None and index != only_day:
            continue
        recost_transport(itinerary, routes, index)
        totals = {'person': 0, 'vehicle': 0}
        for route in routes:
            if route.day_index == index and route.available and route.cost_estimate:
                totals[route.cost_estimate.basis] += cents(route.cost_estimate.high)
        for basis, total in totals.items():
            if not total:
                continue
            if len(day.expenses) >= 20:
                raise ValueError('当天费用明细已达上限，请精简后重试')
            vehicles = math.ceil(trip.people / 4)
            preserved = any(e.category == 'transport' and (e.commitment != 'planned' or e.booking_ref)
                            for e in day.expenses)
            day.expenses.append(Expense(label='交通费用参考上限' if basis == 'person' else f'车辆费用参考上限（{vehicles}辆）',
                category='transport', unit_cost=Decimal(total) / 100,
                quantity=1 if basis == 'person' else vehicles, basis='person' if basis == 'person' else 'group',
                note='逐段参考区间取上限纳入全员预算；车辆按每车最多4人。计算依据见交通选项，不含租车与未返回的额外费用' +
                    ('；已付／承诺／预订费用保留另列，本次路线估算未假定退款或抵扣' if preserved else '')))
