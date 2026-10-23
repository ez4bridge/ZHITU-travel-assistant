"""Shared Python route selection and integer-cent cost helpers."""
import math
from .models import cents, itinerary_expenses, RouteOption
from .transport_cost import estimate_cost
from .validation import minutes

def cost_amount(expense, people):
    return cents(expense.unit_cost) * expense.quantity * (people if expense.basis == 'person' else 1)


def total(plan):
    return sum(cost_amount(e, plan.request.people) for e in itinerary_expenses(plan.itinerary))


def route_options(route):
    result = {}
    for value in [route, *route.alternatives]:
        result.setdefault(value.mode, value)
    return result


def select_route_mode(route, mode, *, allow_unqueried=False):
    if mode not in ('walking', 'transit', 'taxi', 'driving', 'bicycling'):
        raise ValueError('仅支持步行、公交地铁、打车、自驾或骑行')
    option = route_options(route).get(mode)
    if option is None or not _route_usable(option):
        if not allow_unqueried:
            raise ValueError('所选交通方式没有已查询的可用路线，请先核对该路段的交通候选')
        option = RouteOption(mode=mode, note='所选方式尚无可用快照；预览须先查询真实路线，未查询成功不能应用',
                             failure_code='query_required')
    chosen = route.model_copy(deep=True)
    fields = RouteOption.model_fields
    chosen.alternatives = [RouteOption.model_validate({key: value for key, value in other.model_dump().items() if key in fields})
                           for name, other in route_options(route).items() if name != mode]
    for key, value in option.model_dump().items():
        if key in fields:
            setattr(chosen, key, value)
    chosen.stationary = bool(getattr(option, 'stationary', False))
    chosen.user_selected = True
    chosen.selection_reason = '用户在行程预览中明确选择；耗时与费用仍为参考'
    return chosen


def _route_usable(route):
    return bool(route.available and route.duration_s is not None and route.distance_m is not None
                and (len(route.polyline) >= 2 or getattr(route, 'stationary', False)))


def _route_cost(route, stop, people):
    value = estimate_cost(route, stop.city, max(0, minutes(stop.end) - minutes(stop.start)))
    if value is None:
        return None
    return cents(value.high) * (people if value.basis == 'person' else math.ceil(people / 4))
