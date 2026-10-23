"""Adapt a bounded same-city window without replacing the ordinary trip flow.

Only the native engine searches. Python freezes evidence and independently checks
the returned assignment before a preview can be atomically applied.
"""
import asyncio
from collections import Counter
from datetime import datetime, time, timezone
from decimal import Decimal
from itertools import permutations
import json
import math
from uuid import uuid4

from .amap import Amap
from .config import settings
from .constraint_bridge import constraint_planner
from .constraint_reference import evaluate
from .constraint_storage import ConstraintStale, fingerprint
from .models import PlanRecord, RouteLeg, RouteOption, Stop, Expense, cents, city_key, itinerary_expenses
from .planning import clock, recost_all
from .transport_cost import estimate_cost
from .local_transport_policy import recommend_local
from .validation import minutes, normalized_input, validate_core


def cost_amount(expense, people):
    return cents(expense.unit_cost) * expense.quantity * (people if expense.basis == 'person' else 1)


def total(plan):
    return sum(cost_amount(e, plan.request.people) for e in itinerary_expenses(plan.itinerary))


def missing_saved_expenses(plan, states, sid):
    """Restore missing rows individually, including identical-row multiplicity."""
    def key(expense):
        return json.dumps(expense.model_dump(mode='json'), sort_keys=True, ensure_ascii=False)
    present = Counter(key(e) for day in plan.itinerary.days for e in day.expenses if e.stop_id == sid)
    missing = []
    for data in states[sid].get('_expenses', []):
        expense = Expense.model_validate(data)
        identity = key(expense)
        if present[identity]:
            present[identity] -= 1
        else:
            missing.append(expense)
    return missing


def removable_cost_allowed(plan, prior, sid):
    """UI eligibility and server validation share the same narrow cost rule."""
    linked = [e for day in plan.itinerary.days for e in day.expenses if e.stop_id == sid]
    linked += [Expense.model_validate(e) for e in prior.get('_expenses', [])]
    return prior.get('status') != 'completed' and any(not e.booking_ref and e.commitment == 'planned' for e in linked)


def removable_expense(expense, states):
    return bool(expense.stop_id in states and states[expense.stop_id]['removable_cost']
                and not states[expense.stop_id].get('retain_cost')
                and states[expense.stop_id]['status'] != 'completed'
                and not expense.booking_ref and expense.commitment == 'planned')


def budget_conflict_message(payload, candidate_total):
    budget = payload['budget_cents']
    fixed = sum(c['remaining_cents'] or 0 for c in payload['cost_catalog']
                if c['task_id'] is None or c['charge_on_omit'])
    amount = fixed if fixed > budget else max(candidate_total, budget)
    prefix = '必须保留的全程费用估算至少' if fixed > budget else '本次检查的方案费用估算为'
    extra = f'，预算为{Decimal(budget) / 100:.2f}元，超出{Decimal(amount - budget) / 100:.2f}元' if amount > budget else '，未找到同时满足预算、必去项和锁定内容的方案'
    return (f'{prefix}{Decimal(amount) / 100:.2f}元{extra}。'
            '取消活动不会自动免除费用；请增加预算，或仅对未付款且取消不收费的关联门票／其它费用明确确认后重试。')


def stop_catalog(plan, state=None):
    result = {}
    for index, day in enumerate(plan.itinerary.days):
        for stop in day.stops:
            if not stop.id or stop.id in result:
                raise ValueError('旧行程活动身份缺失或重复，请先用原规划功能生成独立的新方案')
            result[stop.id] = (index, stop)
    # Omitted tasks remain a task pool, rather than disappearing after apply.
    for sid, value in (state or {}).get('activities', {}).items():
        if sid not in result and value.get('_stop') and value.get('status') not in ('completed', 'cancelled'):
            result[sid] = (value['_day'], Stop.model_validate(value['_stop']))
    return result


def activity_state(plan, state, catalog):
    result = {}
    for sid, (index, stop) in catalog.items():
        from .bookings import is_confirmed_hotel
        hotel = is_confirmed_hotel(plan.request, stop, plan.itinerary.days[index].date)
        fixed_transport = getattr(stop, 'booking_anchor', '') in ('transport_departure', 'transport_arrival')
        result[sid] = {'stop_id': sid, 'status': 'planned', 'priority': 'must' if stop.kind in ('meal', 'hotel') else 'want',
                       'locked': hotel or fixed_transport, 'removable_cost': False, 'retain_cost': False, **state['activities'].get(sid, {})}
        if hotel or fixed_transport:
            result[sid]['locked'] = True
    return result


def default_day_windows(plan, state, catalog=None):
    catalog = catalog or stop_catalog(plan, state)
    saved = {w['day_index']: w for w in state.get('day_windows', []) if not w.get('scope_city')}
    result = {}
    for index, day in enumerate(plan.itinerary.days):
        stops = [s for sid, (i, s) in catalog.items() if i == index]
        start = min((minutes(s.start) for s in stops), default=minutes(plan.request.start_time) if index == 0 else 540)
        end = max((minutes(s.end) for s in stops), default=1260)
        result[index] = dict(saved.get(index, {'day_index': index, 'start_min': start, 'end_min': end}))
    return result


def stop_city(plan, stop):
    return stop.city or (plan.request.city_route[0] if len(plan.request.city_route) == 1 else '')


def city_scope(plan, index, city, window, catalog):
    """One contiguous city segment, bounded by frozen transfers and other cities."""
    day = plan.itinerary.days[index]
    key = city_key(city)
    stops = [s for i, s in catalog.values() if i == index and city_key(stop_city(plan, s)) == key]
    positions = [n for n, s in enumerate(day.stops) if city_key(stop_city(plan, s)) == key]
    reason = ''
    if not stops:
        reason = '这一天没有该城市的已有活动，请改选日期或城市。'
    elif len(stops) > 8:
        reason = '该城市每天最多8项，已有活动及待补游已超量；请缩小范围，系统不会截断。'
    elif positions and positions != list(range(min(positions), max(positions) + 1)):
        reason = '这一天多次往返同一城市，当前仅支持连续的一段同城活动，请使用原规划调整跨城结构。'
    start, end = window['start_min'], window['end_min']
    for transfer in plan.itinerary.transfers:
        if city_key(transfer.to_city) == key and transfer.arrival_date == day.date:
            start = max(start, minutes(transfer.arrival_time))
        if city_key(transfer.from_city) == key and transfer.departure_date == day.date:
            end = min(end, minutes(transfer.departure_time))
    if positions:
        before = day.stops[:min(positions)]
        after = day.stops[max(positions) + 1:]
        if before:
            start = max(start, max(minutes(s.end) for s in before))
        if after:
            end = min(end, min(minutes(s.start) for s in after))
        first, last = day.stops[min(positions)], day.stops[max(positions)]
        if getattr(first, 'booking_anchor', '') == 'transport_arrival':
            start = max(start, minutes(first.start))
        if getattr(last, 'booking_anchor', '') == 'transport_departure':
            end = min(end, minutes(last.end))
    if start >= end and not reason:
        reason = '跨城时刻与保留活动之间没有可调整时段；请先核对交通或改选日期。'
    return {'city': city, 'supported': not reason, 'reason': reason,
            'stop_count': len(stops), 'start_min': start, 'end_min': end}


def selected_city(plan, command, catalog):
    cities = {city_key(stop_city(plan, s)) for i, s in catalog.values() if i in command.day_indexes}
    supplied = getattr(command, 'scope_city', None)
    if supplied:
        city = city_key(supplied)
        if not city or city not in cities:
            raise ValueError('请选择所选日期中已有活动所属的城市')
        return city
    if len(cities) != 1 or not next(iter(cities), ''):
        raise ValueError('请选择同一城市的活动段；跨城日期需要先明确要调整的城市，其余城市保持原样')
    return next(iter(cities))


def effective_day_windows(plan, state, command, catalog=None):
    catalog = catalog or stop_catalog(plan, state)
    result = default_day_windows(plan, state, catalog)
    city = selected_city(plan, command, catalog)
    saved = {w['day_index']: w for w in state.get('day_windows', []) if city_key(w.get('scope_city', '')) == city}
    for index in command.day_indexes:
        if index in saved:
            result[index] = {k: saved[index][k] for k in ('day_index', 'start_min', 'end_min')}
        else:
            bounds = city_scope(plan, index, city, result[index], catalog)
            result[index] = {'day_index': index, 'start_min': bounds['start_min'], 'end_min': bounds['end_min']}
    for index in command.day_indexes:
        if getattr(command, 'daily_start_min', None) is not None:
            result[index]['start_min'] = command.daily_start_min
        if getattr(command, 'daily_end_min', None) is not None:
            result[index]['end_min'] = command.daily_end_min
    for window in getattr(command, 'day_windows', []):
        result[window.day_index] = window.model_dump()
    for index in command.day_indexes:
        if result[index]['start_min'] >= result[index]['end_min']:
            raise ValueError(f'第{index + 1}天的结束时间须晚于开始时间')
    return result


def window_preferences(state, command, windows):
    city = getattr(command, 'scope_city', None)
    saved = {(w['day_index'], city_key(w.get('scope_city', ''))): w for w in state.get('day_windows', [])}
    explicit = {w.day_index for w in command.day_windows}
    if command.daily_start_min is not None or command.daily_end_min is not None:
        explicit.update(command.day_indexes)
    saved.update({(index, city_key(city or '')): {**windows[index], **({'scope_city': city} if city else {})} for index in explicit})
    return [saved[key] for key in sorted(saved)]


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
    chosen.selection_reason = '用户在仓颉约束预览中明确选择；耗时与费用仍为参考'
    return chosen


def route_queryable(plan, route, catalog):
    if (route.scope != 'local' or route.stationary or route.from_id not in catalog or route.to_id not in catalog
            or not 0 <= route.day_index < len(plan.itinerary.days)):
        return False
    before, after = catalog[route.from_id][1], catalog[route.to_id][1]
    pairs = {(a.id, b.id) for a, b in zip(plan.itinerary.days[route.day_index].stops,
                                        plan.itinerary.days[route.day_index].stops[1:])}
    return bool((route.from_id, route.to_id) in pairs and before.location and after.location
                and before.location_status == after.location_status == 'verified'
                and city_key(stop_city(plan, before)) == city_key(stop_city(plan, after))
                and tuple(before.location) != tuple(after.location))


def context_routes(plan, catalog):
    result = []
    for route in plan.routes:
        if route.scope != 'local' or route.from_id not in catalog or route.to_id not in catalog:
            continue
        before, after = catalog[route.from_id][1], catalog[route.to_id][1]
        values = route_options(route)
        recommendation, reason = recommend_local(list(values.values()), after.city, plan.request.people)
        recommended = recommendation.mode if recommendation is not None else None
        options = []
        for mode in ('walking', 'transit', 'taxi', 'driving', 'bicycling'):
            value = values.get(mode)
            if value is None:
                options.append({'mode': mode, 'available': False, 'duration_s': None, 'distance_m': None,
                                'cost_cents': None, 'cost_state': 'unknown', 'recommended': False,
                                'note': '尚未查询；选择后先查真实路线，再进入约束预览'})
                continue
            usable = _route_usable(value)
            amount = _route_cost(value, after, plan.request.people) if usable else None
            options.append({'mode': mode, 'available': usable, 'duration_s': value.duration_s,
                            'distance_m': value.distance_m, 'cost_cents': amount,
                            'cost_state': 'estimated' if amount is not None else 'unknown',
                            'recommended': usable and mode == recommended, 'note': value.note})
        result.append({'day_index': route.day_index, 'from_id': route.from_id, 'to_id': route.to_id,
                       'from_name': before.name, 'to_name': after.name, 'mode': route.mode,
                       'can_query': route_queryable(plan, route, catalog),
                       'recommended_mode': recommended, 'recommendation_reason': reason, 'options': options})
    return result


async def context(plan, state, bridge=None):
    bridge = bridge or constraint_planner
    available = await bridge.ready()
    notices = ['费用仍为全员整趟估算；未知营业、接驳和价格不会视为已核实。',
               '先预览再应用；预览不会记录已完成或修改已确认事实。']
    if state['current_plan_id'] != plan.id:
        notices.append('当前打开的是旧版本，请打开最新约束版本后调整，避免撤销已确认事实。')
    try:
        catalog = stop_catalog(plan, state)
    except ValueError as error:
        return {'schema_version': '1.0', 'base_plan_id': plan.id, 'base_fingerprint': fingerprint(plan),
                'routing_revision': plan.routing_revision, 'state_revision': state['state_revision'],
                'current_plan_id': state['current_plan_id'], 'engine': {'available': False,
                'name': 'cangjie-journey-planner', 'version': '2.2', 'diagnostic': str(error)},
                'budget_cents': cents(plan.request.budget), 'days': [], 'stops': [], 'routes': [], 'notices': notices}
    states = activity_state(plan, state, catalog)
    windows = default_day_windows(plan, state, catalog)
    days, stops = [], []
    for index, day in enumerate(plan.itinerary.days):
        city_stops = [s for sid, (i, s) in catalog.items() if i == index]
        cities = sorted({stop_city(plan, s) for s in city_stops})
        scopes = []
        for city in cities:
            if not city:
                continue
            saved = next((w for w in state.get('day_windows', []) if w['day_index'] == index
                          and city_key(w.get('scope_city', '')) == city_key(city)), windows[index])
            scopes.append(city_scope(plan, index, city, saved, catalog))
        supported = any(s['supported'] for s in scopes)
        reason = '' if supported else ('；'.join(s['reason'] for s in scopes) or '这一天没有明确城市的活动，请先核对地点。')
        days.append({'day_index': index, 'date': str(day.date), 'cities': cities,
                     'stop_count': len(day.stops), 'supported': supported, 'reason': reason,
                     'start_min': windows[index]['start_min'], 'end_min': windows[index]['end_min'], 'city_scopes': scopes})
        for stop in day.stops:
            amount = sum(cost_amount(e, plan.request.people) for d in plan.itinerary.days for e in d.expenses if e.stop_id == stop.id)
            stops.append({'stop_id': stop.id, 'day_index': index, 'date': str(day.date),
                          'name': stop.name, 'city': stop.city or (plan.request.city_route[0] if len(plan.request.city_route) == 1 else ''),
                          'kind': stop.kind, 'start': stop.start, 'end': stop.end, 'cost_cents': amount,
                          'booking_anchor': getattr(stop, 'booking_anchor', ''),
                          **{k: v for k, v in states[stop.id].items() if not k.startswith('_')},
                          'removable_cost_allowed': removable_cost_allowed(plan, states[stop.id], stop.id)})
    visible = {s['stop_id'] for s in stops}
    for sid, (index, stop) in catalog.items():
        if sid not in visible:
            stops.append({'stop_id': sid, 'day_index': index, 'date': str(plan.itinerary.days[index].date),
                          'name': stop.name, 'city': stop.city, 'kind': stop.kind, 'start': stop.start,
                          'end': stop.end, 'cost_cents': sum(cost_amount(Expense.model_validate(e), plan.request.people) for e in states[sid].get('_expenses', [])),
                          **{k: v for k, v in states[sid].items() if not k.startswith('_')}, 'in_task_pool': True,
                          'removable_cost_allowed': removable_cost_allowed(plan, states[sid], sid)})
    return {'schema_version': '1.0', 'base_plan_id': plan.id, 'base_fingerprint': fingerprint(plan),
            'routing_revision': plan.routing_revision, 'state_revision': state['state_revision'],
            'current_plan_id': state['current_plan_id'], 'engine': {'available': available,
            'name': 'cangjie-journey-planner', 'version': '2.2', 'diagnostic': bridge.diagnostic},
            'budget_cents': cents(plan.request.budget), 'days': days, 'stops': stops,
            'routes': context_routes(plan, catalog), 'notices': notices}


class AmapConstraintProvider:
    def __init__(self, city):
        self.client = Amap(city)

    async def route(self, before, after, mode, city):
        data = await self.client.route_in_city(before.location, after.location, mode, city)
        # Amap's parser returns verified metrics/geometry, not a RouteLeg flag.
        # Availability is asserted only after that real provider call succeeds.
        return {**data, 'available': True}

    async def close(self):
        await self.client.close()


def _check(code, message, day_index=None, stop_id=None):
    return {'code': code, 'message': message, 'day_index': day_index, 'stop_id': stop_id}


def prepare(plan, state, command):
    if plan.request.study:
        raise ValueError('历史研学请使用研学调整入口，以保留原文引用与现场任务')
    if (fingerprint(plan) != command.base_fingerprint or plan.routing_revision != command.expected_routing_revision
            or state['state_revision'] != command.expected_state_revision or state['current_plan_id'] != plan.id):
        raise ConstraintStale('行程或已确认事实已经变化，请刷新最新版本')
    catalog = stop_catalog(plan, state)
    if command.day_indexes[-1] >= len(plan.itinerary.days):
        raise ValueError('调整日期不属于此行程')
    selected = set(command.day_indexes)
    city = selected_city(plan, command, catalog)
    windows = effective_day_windows(plan, state, command, catalog)
    for index in selected:
        scope = city_scope(plan, index, city, windows[index], catalog)
        if not scope['supported']:
            raise ValueError(f'第{index + 1}天：{scope["reason"]}')
    scoped_ids = {sid for sid, (i, s) in catalog.items() if i in selected and city_key(stop_city(plan, s)) == city}
    states = activity_state(plan, state, catalog)
    effective_day_windows(plan, state, command, catalog)
    for activity in command.activities:
        if activity.stop_id not in catalog:
            raise ValueError('活动不属于父行程，请刷新')
        index, stop = catalog[activity.stop_id]
        prior = states[activity.stop_id]
        if prior['status'] == 'completed' and activity.status != 'completed':
            raise ValueError('已确认完成的活动不能从旧版本改回未完成')
        booking = getattr(stop, 'booking_anchor', '')
        if prior['locked'] and (stop.kind == 'hotel' or booking) and (not activity.locked or activity.status in ('missed', 'cancelled')):
            raise ValueError('已确认酒店与交通时刻默认保护；本轮不办理取消或改签')
        public_prior = {k: v for k, v in prior.items() if not k.startswith('_')}
        if activity.stop_id not in scoped_ids and activity.model_dump() != public_prior:
            if activity.status != 'missed' or city_key(stop_city(plan, stop)) != city:
                raise ValueError('窗口外只能明确纳入同城漏游活动，其余日期保持原样')
        if activity.status in ('cancelled', 'missed') and activity.locked:
            raise ValueError('锁定活动不能同时取消或设为漏游，请先明确解除活动锁')
        if activity.removable_cost and not removable_cost_allowed(plan, prior, stop.id):
            raise ValueError('只能重算已关联、未发生且未预订的规划费用；已完成、已付款和已预订费用保留')
        states[activity.stop_id] = {**prior, **activity.model_dump()}
    active_ids = [sid for sid in catalog if sid in scoped_ids and states[sid]['status'] not in ('completed', 'cancelled')]
    extra = [sid for sid, (i, stop) in catalog.items() if i not in selected and states[sid]['status'] == 'missed'
             and city_key(stop_city(plan, stop)) == city and any(a.stop_id == sid for a in command.activities)]
    active_ids += extra
    routes = {(r.day_index, r.from_id, r.to_id): r for r in plan.routes if r.scope == 'local'}
    for choice in command.route_choices:
        key = (choice.day_index, choice.from_id, choice.to_id)
        if key not in routes:
            raise ValueError('仅能选择当前行程中已有市内路段的可用交通候选')
        if any(sid not in scoped_ids for sid in (choice.from_id, choice.to_id)):
            raise ValueError('交通选择只能针对所选城市活动段，其余城市的路段保持原样')
        if any(sid not in catalog or states[sid]['status'] == 'cancelled' for sid in (choice.from_id, choice.to_id)):
            raise ValueError('不能同时取消活动并指定其相邻路段，请先确认活动安排')
        if all(states[sid]['status'] == 'completed' for sid in (choice.from_id, choice.to_id)):
            raise ValueError('已完成记录之间的交通不能重新选择；实际已走方式须保留待核实')
        selected = select_route_mode(routes[key], choice.mode, allow_unqueried=True)
        if not _route_usable(selected) and not route_queryable(plan, routes[key], catalog):
            raise ValueError('按需查询须为当前同城相邻路段，且两端地点均已核实；请先明确地点')
    if len(active_ids) > 24:
        raise ValueError('所选活动连同待补游最多24项，请缩小窗口或漏游集合')
    errors = []
    if command.mode == 'during':
        for sid in active_ids:
            index, stop = catalog[sid]
            at = datetime.combine(plan.itinerary.days[index].date, time.fromisoformat(stop.end), command.as_of.tzinfo)
            if at <= command.as_of and states[sid]['status'] == 'planned':
                errors.append(_check('past_unconfirmed', f'{stop.name}已到原定时刻，请确认已完成、漏游或取消；系统不会自动判完成', index, sid))
    if command.current_stop_id is not None:
        if command.current_stop_id not in catalog or city_key(stop_city(plan, catalog[command.current_stop_id][1])) != city:
            raise ValueError('当前位置须从所选同城的已知地点中确认')
    return catalog, states, city, active_ids, errors


def _route_usable(route):
    return bool(route.available and route.duration_s is not None and route.distance_m is not None
                and (len(route.polyline) >= 2 or getattr(route, 'stationary', False)))


def _route_cost(route, stop, people):
    value = estimate_cost(route, stop.city, max(0, minutes(stop.end) - minutes(stop.start)))
    if value is None:
        return None
    return cents(value.high) * (people if value.basis == 'person' else math.ceil(people / 4))


def build_projection(plan, command, catalog, states, city, active_ids, *, day_windows=None):
    local = {index: n for n, index in enumerate(command.day_indexes)}
    reverse = {value: key for key, value in local.items()}
    days, anchors, notices = [], {}, []
    day_windows = day_windows or effective_day_windows(plan, {}, command, catalog)
    for index in command.day_indexes:
        day = plan.itinerary.days[index]
        bounds = city_scope(plan, index, city, day_windows[index], catalog)
        start, end = bounds['start_min'], bounds['end_min']
        scoped_stops = [s for s in day.stops if city_key(stop_city(plan, s)) == city]
        if index == 0:
            start = max(start, minutes(plan.request.start_time))
        if command.mode == 'during':
            if day.date < command.as_of.date():
                continue
            if day.date == command.as_of.date():
                start = max(start, command.as_of.hour * 60 + command.as_of.minute + bool(command.as_of.second or command.as_of.microsecond))
        # Completed records remain intact; their occupied interval cannot overlap a new task.
        completed = [s for s in scoped_stops if states[s.id]['status'] == 'completed']
        if completed:
            start = max(start, max(minutes(s.end) for s in completed))
        for transfer in plan.itinerary.transfers:
            if city_key(transfer.to_city) == city and transfer.arrival_date == day.date:
                start = max(start, minutes(transfer.arrival_time))
                notices.append('跨城到达时刻保持；出站及车站到活动地点接驳尚需核实。')
            if city_key(transfer.from_city) == city and transfer.departure_date == day.date:
                end = min(end, minutes(transfer.departure_time))
                notices.append('跨城出发时刻保持；进站安检及活动地点到车站接驳尚需核实。')
        if start >= end:
            continue
        first = last = None
        if command.mode == 'during' and day.date == command.as_of.date() and command.current_stop_id:
            first = 'boundary:start:' + str(index)
            anchors[first] = catalog[command.current_stop_id][1]
        elif completed:
            first = 'boundary:start:' + str(index)
            anchors[first] = max(completed, key=lambda s: minutes(s.end))
        elif (scoped_stops and states[scoped_stops[0].id]['locked']
              and (scoped_stops[0].kind == 'hotel' or getattr(scoped_stops[0], 'booking_anchor', '') == 'transport_arrival')):
            first = 'boundary:start:' + str(index)
            anchors[first] = scoped_stops[0]
        if (scoped_stops and states[scoped_stops[-1].id]['locked']
              and (scoped_stops[-1].kind == 'hotel' or getattr(scoped_stops[-1], 'booking_anchor', '') == 'transport_departure')):
            last = 'boundary:end:' + str(index)
            anchors[last] = scoped_stops[-1]
        days.append({'day_index': local[index], 'date': str(day.date), 'start_min': int(start), 'end_min': end,
                     'start_anchor_id': first, 'end_anchor_id': last})
    tasks, baseline = [], []
    for sid in active_ids:
        index, stop = catalog[sid]
        missed = states[sid]['status'] == 'missed'
        added = bool(states[sid].get('_addition'))
        allowed = [d['day_index'] for d in days if missed or reverse[d['day_index']] == index]
        original_index = local.get(index, -1)
        tasks.append({'task_id': sid, 'original_day_index': original_index, 'original_start_min': minutes(stop.start),
                      'duration_min': minutes(stop.end) - minutes(stop.start), 'priority': states[sid]['priority'],
                      'backlog': missed or added, 'protected': states[sid]['locked'], 'allowed_day_indexes': allowed,
                      'opening_state': 'unknown', 'windows': [], 'cost_ids': []})
        if tasks[-1]['duration_min'] <= 0:
            raise ValueError('原活动时长无效，请先修正原方案')
        if not missed and not added and any(d['day_index'] == original_index for d in days):
            baseline.append({'task_id': sid, 'day_index': original_index, 'start_min': minutes(stop.start), 'end_min': minutes(stop.end)})
        if states[sid]['locked'] and not any(a['task_id'] == sid for a in baseline):
            raise ValueError('固定活动不在剩余可用日期内，请确认其完成状态或调整窗口')
    task_by_id = {t['task_id']: t for t in tasks}
    source_days = {catalog[sid][0] for sid in active_ids if catalog[sid][0] not in local}
    costs = []
    for index, day in enumerate(plan.itinerary.days):
        for number, expense in enumerate(day.expenses):
            # Existing route estimates in editable days are independently replaced below.
            if (index in local or index in source_days) and expense.category == 'transport':
                continue
            sid = expense.stop_id
            if sid in states and states[sid]['status'] == 'cancelled' and removable_expense(expense, states):
                continue
            removable = bool(sid in task_by_id and removable_expense(expense, states))
            entry = {'cost_id': f'expense:{index}:{number}', 'task_id': sid if removable else None,
                     'remaining_cents': cost_amount(expense, plan.request.people), 'charge_on_omit': not removable,
                     'estimated': True}
            costs.append(entry)
            if removable:
                task_by_id[sid]['cost_ids'].append(entry['cost_id'])
    # Reintroduced task-pool tickets were previously omitted only after an
    # explicit unpaid/free-cancellation confirmation. Restore their forecast once.
    for sid in active_ids:
        for number, expense in enumerate(missing_saved_expenses(plan, states, sid)):
            removable = removable_expense(expense, states)
            entry = {'cost_id': f'pool:{sid}:{number}', 'task_id': sid if removable else None,
                     'remaining_cents': cost_amount(expense, plan.request.people), 'charge_on_omit': not removable, 'estimated': True}
            costs.append(entry)
            if removable:
                task_by_id[sid]['cost_ids'].append(entry['cost_id'])
    # The native route graph starts after the last completed activity. Already
    # travelled legs remain part of the whole-trip budget, independently priced.
    for route in plan.routes:
        if (route.day_index in local and route.scope == 'local' and route.from_id in states and route.to_id in states
                and (all(city_key(stop_city(plan, catalog[sid][1])) != city for sid in (route.from_id, route.to_id))
                     or states[route.from_id]['status'] == states[route.to_id]['status'] == 'completed')):
            amount = _route_cost(route, catalog[route.to_id][1], plan.request.people) if _route_usable(route) else None
            costs.append({'cost_id': f'historical-route:{route.day_index}:{route.from_id}:{route.to_id}',
                          'task_id': None, 'remaining_cents': amount, 'charge_on_omit': True, 'estimated': bool(amount)})
    for index, transfer in enumerate(plan.itinerary.transfers):
        costs.append({'cost_id': 'intercity:' + str(index), 'task_id': None,
                      'remaining_cents': cents(transfer.unit_cost) * (plan.request.people if transfer.basis == 'person' else 1),
                      'charge_on_omit': True, 'estimated': True})
    if len(costs) > 100:
        raise ValueError('全程费用明细超过100项原生边界，请缩小明细后再试；不会截断账目')
    payload = {'schema_version': '2.2', 'request_id': str(uuid4()), 'op': 'repair', 'days': days,
               'source_catalog': tasks, 'baseline': baseline, 'candidate': None, 'completed_ids': [], 'cancelled_ids': [],
               'budget_cents': command.budget_cents if command.budget_cents is not None else cents(plan.request.budget),
               'cost_catalog': costs, 'routes': [], 'limits': {'max_states': 200, 'max_search_ms': 2000}}
    return payload, reverse, anchors, list(dict.fromkeys(notices))


async def freeze_routes(plan, payload, reverse, anchors, catalog, city, provider_factory=None, *, states=None, route_choices=None):
    points = {sid: value[1] for sid, value in catalog.items()}
    points.update(anchors)
    choices = {(r.day_index, r.from_id, r.to_id): r.mode for r in (route_choices or [])}
    existing, originals = {}, {}
    for route in plan.routes:
        if route.scope != 'local':
            continue
        key = (route.day_index, route.from_id, route.to_id)
        originals[key] = route
        chosen = select_route_mode(route, choices[key], allow_unqueried=True) if key in choices else route
        if _route_usable(chosen):
            existing[key] = chosen
    tasks = payload['source_catalog']
    needed = []
    def edge(day, before, after):
        value = (day, before, after)
        if before and after and city_key(stop_city(plan, points[before])) != city_key(stop_city(plan, points[after])):
            return  # Frozen cross-city boundary is represented by its transfer, never a local road.
        if before and after and before != after and value not in needed:
            needed.append(value)
    imported = {t['task_id'] for t in tasks if t['backlog'] and t['original_day_index'] == -1}
    source_indexes = {catalog[sid][0] for sid in imported}
    # Removing a confirmed missed activity also reconnects its original day's
    # neighbours; no unrelated source activity is moved or rescheduled.
    for index in sorted(source_indexes):
        remaining = [s.id for s in plan.itinerary.days[index].stops if s.id not in imported]
        for before, after in zip(remaining, remaining[1:]):
            edge('source:' + str(index), before, after)
    states = states or {}
    for day in payload['days']:
        index = reverse[day['day_index']]
        completed = sorted((s for s in plan.itinerary.days[index].stops
                            if states.get(s.id, {}).get('status') == 'completed' and city_key(stop_city(plan, s)) == city), key=lambda s: (minutes(s.start), s.id))
        # A confirmed set may skip an original intermediate activity. These are
        # public-plan reference connections, not assertions about actual travel.
        for before, after in zip(completed, completed[1:]):
            if (index, before.id, after.id) not in existing:
                edge('prefix:' + str(index), before.id, after.id)
        anchor = anchors.get(day['start_anchor_id'])
        if completed and anchor is not None and completed[-1].id != anchor.id:
            edge('prefix:' + str(index), completed[-1].id, anchor.id)
    for day in payload['days']:
        n = day['day_index']
        ordered = [a['task_id'] for a in payload['baseline'] if a['day_index'] == n]
        for before, after in zip([day['start_anchor_id'], *ordered], [*ordered, day['end_anchor_id']]):
            edge(n, before, after)
        available = [t['task_id'] for t in tasks if n in t['allowed_day_indexes']]
        for sid in available:
            edge(n, day['start_anchor_id'], sid)
            edge(n, sid, day['end_anchor_id'])
        for before, after in permutations(available, 2):
            edge(n, before, after)
    # The wire carries at most250 roads; unqueried roads remain unknown and block
    # application if the chosen candidate actually needs them.
    def requested(day, before, after):
        index = int(day.split(':')[1]) if isinstance(day, str) else reverse[day]
        return choices.get((index, before, after)) or choices.get((index, points[before].id, points[after].id))
    needed.sort(key=lambda item: requested(*item) is None)
    needed = needed[:250]
    provider, queries, frozen, roads = None, 0, [], {}
    try:
        for day, before_id, after_id in needed:
            before, after = points[before_id], points[after_id]
            outside = isinstance(day, str)
            index = int(day.split(':')[1]) if outside else reverse[day]
            prior = existing.get((index, before_id, after_id)) or existing.get((index, before.id, after.id))
            route = prior.model_copy(deep=True) if prior else None
            selected_mode = requested(day, before_id, after_id)
            if route is None and before.location and after.location and before.location_status == after.location_status == 'verified':
                mode = selected_mode or (plan.request.mode if plan.request.mode != 'auto' else next((r.mode for r in plan.routes if r.day_index == index and r.scope == 'local' and r.available), 'walking'))
                if tuple(before.location) == tuple(after.location):
                    route = RouteLeg(day_index=index, from_id=before_id, to_id=after_id, mode='walking', available=True,
                        stationary=True, distance_m=0, duration_s=0, queried_at=datetime.now(timezone.utc), note='已核实的同一坐标，无需位置转移')
                elif queries < 20 and (provider_factory or settings.amap_key):
                    if provider is None:
                        provider = (provider_factory or AmapConstraintProvider)(city)
                    queries += 1
                    try:
                        async with asyncio.timeout(18):
                            data = await provider.route(before, after, mode, city)
                        route = data.model_copy(deep=True) if isinstance(data, RouteLeg) else RouteLeg(
                            day_index=index, from_id=before_id, to_id=after_id, mode=mode,
                            queried_at=datetime.now(timezone.utc), **data)
                        route.day_index, route.from_id, route.to_id = index, before_id, after_id
                        if route.mode != mode:
                            route = None
                    except Exception:
                        route = None  # Public result records unknown, never invents a path.
                if route and _route_usable(route) and selected_mode:
                    original = originals.get((index, before_id, after_id)) or originals.get((index, before.id, after.id))
                    if original:
                        fields = RouteOption.model_fields
                        alternatives = {name: option for name, option in route_options(original).items()
                                        if name != route.mode}
                        alternatives.update({option.mode: option for option in route.alternatives if option.mode != route.mode})
                        route.alternatives = [RouteOption.model_validate({key: value for key, value in option.model_dump().items()
                                                                         if key in fields}) for option in alternatives.values()]
                    route.user_selected = True
                    route.selection_reason = '用户在约束预览中选择；本次取得该方式的路线资料，耗时与费用仍为参考'
            amount = _route_cost(route, after, plan.request.people) if route and _route_usable(route) else None
            if outside:
                payload['cost_catalog'].append({'cost_id': f'{day.split(":")[0]}-road:{index}:{before_id}:{after_id}',
                    'task_id': None, 'remaining_cents': amount, 'charge_on_omit': True, 'estimated': bool(amount)})
                if route and _route_usable(route):
                    if day.startswith('prefix:'):
                        route.note = (route.note + '；补齐已完成记录到用户确认当前位置的道路参考；不证明实际走过此路线、使用此方式或已经支付。')[:2000]
                    roads[(index, before_id, after_id)] = route
                continue
            route_id = f'route:{day}:{before_id}:{after_id}'
            value = {'route_id': route_id, 'day_index': day, 'from_id': before_id, 'to_id': after_id,
                     'mode': route.mode if route else selected_mode or 'walking', 'duration_s': route.duration_s if route and _route_usable(route) else None,
                     'cost_cents': amount, 'time_state': ('verified' if route.stationary else 'estimated') if route and _route_usable(route) else 'unknown',
                     'cost_state': 'estimated' if amount is not None and amount else ('verified' if amount == 0 else 'unknown'),
                     'evidence_id': 'road-snapshot:' + route_id if route and _route_usable(route) else ''}
            frozen.append(value)
            if route and _route_usable(route):
                roads[(index, before_id, after_id)] = route
                # Boundary aliases preserve actual stop identities in the merged
                # public plan, including completed->first remaining activity.
                if before_id in anchors or after_id in anchors:
                    alias = route.model_copy(deep=True)
                    alias.from_id, alias.to_id = before.id, after.id
                    roads[(index, before.id, after.id)] = alias
    finally:
        if provider:
            await provider.close()
    if len(payload['cost_catalog']) > 100:
        raise ValueError('费用明细连同补游源日接续超过100项，不能截断账目')
    return frozen, roads, queries


def materialize(plan, command, states, catalog, active_ids, assignments, reverse, roads):
    candidate = plan.model_copy(deep=True)
    by_id = {a['task_id']: a for a in assignments}
    affected = set(command.day_indexes)
    city = selected_city(plan, command, catalog)
    scoped_ids = {sid for sid, (i, s) in catalog.items() if i in command.day_indexes and city_key(stop_city(plan, s)) == city}
    # An explicitly imported missed activity is transferred once, preserving other
    # records of its original date. No unrelated outside activity is changed.
    imported = {sid for sid in active_ids if catalog[sid][0] not in command.day_indexes}
    for sid in imported:
        if catalog[sid][0] not in affected:
            affected.add(catalog[sid][0])
    for index in affected:
        original = plan.itinerary.days[index]
        keep = [s.model_copy(deep=True) for s in original.stops if
                (index not in command.day_indexes and s.id not in imported)
                or (index in command.day_indexes and (s.id not in scoped_ids or states[s.id]['status'] == 'completed'))]
        for sid, assignment in by_id.items():
            if reverse[assignment['day_index']] != index:
                continue
            stop = catalog[sid][1].model_copy(deep=True)
            stop.start, stop.end = clock(assignment['start_min']), clock(assignment['end_min'])
            keep.append(stop)
        candidate.itinerary.days[index].stops = sorted(keep, key=lambda s: (minutes(s.start), s.id))
    assigned_days = {sid: reverse[a['day_index']] for sid, a in by_id.items()}
    for index in affected:
        candidate.itinerary.days[index].expenses = []
    for index, day in enumerate(plan.itinerary.days):
        if index not in affected:
            continue
        for expense in day.expenses:
            sid = expense.stop_id
            omitted = sid in catalog and states[sid]['status'] != 'completed' and sid not in by_id and (sid in scoped_ids or sid in active_ids)
            removable = removable_expense(expense, states)
            if omitted and removable:
                continue
            target = assigned_days.get(sid, index) if sid else index
            if target not in affected:
                target = index
            candidate.itinerary.days[target].expenses.append(expense.model_copy(deep=True))
    for sid, target in assigned_days.items():
        for expense in missing_saved_expenses(plan, states, sid):
            candidate.itinerary.days[target].expenses.append(expense)
    retained_routes = [r.model_copy(deep=True) for r in plan.routes if r.day_index not in affected]
    for index in affected:
        day = candidate.itinerary.days[index]
        for before, after in zip(day.stops, day.stops[1:]):
            route = roads.get((index, before.id, after.id))
            if route is None:
                route = next((r for r in plan.routes if r.day_index == index and r.from_id == before.id and r.to_id == after.id and _route_usable(r)), None)
            if route:
                retained_routes.append(route.model_copy(deep=True))
        # Recompute this day's actual chosen legs independently of native totals.
        recost_all(candidate.request, candidate.itinerary, retained_routes, index)
    candidate.routes = retained_routes
    if command.budget_cents is not None:
        candidate.request.budget = Decimal(command.budget_cents) / 100
    # Model validation enforces cost/stop list bounds after moving one task.
    return PlanRecord.model_validate(candidate.model_dump(mode='json'), context={'legacy_record': True}), affected


async def preview(plan, state, command, storage, *, bridge=None, provider_factory=None):
    bridge = bridge or constraint_planner
    catalog, states, city, active_ids, errors = prepare(plan, state, command)
    response = {'schema_version': '1.0', 'base_plan_id': plan.id, 'outcome': 'conflict',
                'engine': {'name': 'cangjie-journey-planner', 'version': '2.2'},
                'summary': {'before_cents': total(plan), 'after_cents': total(plan),
                    'budget_cents': command.budget_cents if command.budget_cents is not None else cents(plan.request.budget), 'changed_count': 0, 'route_changed_count': 0},
                'changes': [], 'route_changes': [], 'unplaced': [], 'checks': [], 'errors': errors, 'warnings': [], 'unknowns': [],
                'notices': ['费用默认保留；可免付取消仅按用户明确确认执行，不代表退款。'], 'search': {}}
    if errors:
        return response
    windows = effective_day_windows(plan, state, command, catalog)
    payload, reverse, anchors, notices = build_projection(plan, command, catalog, states, city, active_ids, day_windows=windows)
    response['notices'] += notices
    if any(catalog[sid][0] not in command.day_indexes for sid in active_ids):
        response['notices'].append('按你明确纳入的同城漏游，原日期仅移除该活动并转移关联费用、重接原邻点；其它窗口外活动时间和跨城交通保持。')
    if not payload['days'] and active_ids:
        response['errors'] = [_check('no_remaining_window', '所选日期没有剩余可用时段，请改选未来日期或确认活动状态')]
        return response
    if not await bridge.ready():
        raise RuntimeError(bridge.diagnostic or '仓颉重规划引擎尚未就绪；原旅游规划保持可用')
    routes, roads, queries = await freeze_routes(plan, payload, reverse, anchors, catalog, city, provider_factory, states=states, route_choices=command.route_choices)
    payload['routes'] = routes
    native = await bridge.call(payload)
    response['engine'] = native.get('_engine', response['engine'])
    response['search'] = native.get('search', {})
    response['notices'].append(f'本次新增地图路线查询{queries}次，上限20次；未查询成功的边保留未知。')
    response['notices'].append('地图道路形状有来源，查询时耗时用于排时估算，不保证未来出发时交通、公交班次或候车情况。')
    if any(c['cost_id'].startswith('prefix-road:') for c in payload['cost_catalog']):
        response['notices'].append('已完成记录与当前位置之间仅补充有来源的道路和费用参考，不代表实际已走或已付款；真实交通方式与消费仍待用户核实。')
    response['unplaced'] = [{'stop_id': x['task_id'], 'name': catalog[x['task_id']][1].name,
                            'priority': x['priority'], 'reason': x['message']} for x in native.get('unplaced', [])]
    if native.get('assignments') is None:
        response['outcome'] = 'no_solution_found' if native.get('outcome') == 'no_solution' else 'conflict'
        response['errors'] = [_check(x['kind'], budget_conflict_message(payload, native['total_cents']) if x['kind'] == 'remaining_budget' else x['message'], reverse.get(x.get('day_index')), x.get('task_id'))
                              for x in native.get('checks', []) if x['state'] == 'violated']
        response['notices'].append('有限搜索未找到可应用方案；旧行程及确认事实均保持，费用对比仍显示原方案。')
        return response
    oracle = evaluate(payload, native['assignments'])
    if (oracle['total_cents'] != native['total_cents'] or set(oracle['used_route_ids']) != set(native['used_route_ids'])
            or set(oracle['charged_cost_ids']) != set(native['charged_cost_ids'])):
        response['errors'] = [_check('independent_disagreement', 'Python独立复核与仓颉账目或使用路线不一致，拒绝本次候选')]
        return response
    for value in oracle['checks']:
        message = budget_conflict_message(payload, oracle['total_cents']) if value['kind'] == 'remaining_budget' and value['state'] == 'violated' else value['message']
        item = _check(value['kind'], message, reverse.get(value.get('day_index')), value.get('task_id'))
        response['checks'].append({**item, 'state': value['state']})
        if value['state'] == 'violated':
            response['errors'].append(item)
        elif value['state'] == 'unknown':
            response['unknowns'].append(item)
            if value['kind'] == 'transfer_time':
                response['errors'].append(_check('critical_route_unknown', '实际需要的关键接驳缺少真实路线，不能应用', item['day_index'], item['stop_id']))
    if response['errors']:
        return response
    # The public plan has no separate waypoint domain. If an explicitly confirmed
    # different current point would be skipped by its first future activity, do
    # not fabricate a direct completed->future route that bypasses that position.
    for day in payload['days']:
        index = reverse[day['day_index']]
        anchor = anchors.get(day['start_anchor_id'])
        completed = [s for s in plan.itinerary.days[index].stops if states[s.id]['status'] == 'completed' and city_key(stop_city(plan, s)) == city]
        future = sorted((a for a in native['assignments'] if a['day_index'] == day['day_index']), key=lambda a: (a['start_min'], a['task_id']))
        if completed and anchor is not None and anchor.id not in {s.id for s in completed} and (not future or future[0]['task_id'] != anchor.id):
            response['errors'].append(_check('current_position_connection', '当前位置与已完成记录不连续；请核对当前位置对应活动的进度或调整窗口，系统不会用直达线代替经过当前位置的真实接续。', index, anchor.id))
    if response['errors']:
        return response
    candidate, affected = materialize(plan, command, states, catalog, active_ids, native['assignments'], reverse, roads)
    prior_routes = {(r.day_index, r.from_id, r.to_id): r for r in plan.routes}
    new_routes = {(r.day_index, r.from_id, r.to_id): r for r in candidate.routes}
    for choice in command.route_choices:
        key = (choice.day_index, choice.from_id, choice.to_id)
        selected = new_routes.get(key)
        if selected is None or selected.mode != choice.mode:
            response['errors'].append(_check('route_choice_not_used', '本次候选没有保留你指定的相邻路段，请核对活动顺序或取消该路段选择后重新预览。', choice.day_index, choice.to_id))
            continue
        before = prior_routes[key]
        if before.mode != selected.mode:
            stop = catalog[choice.to_id][1]
            response['route_changes'].append({'day_index': choice.day_index, 'from_id': choice.from_id, 'to_id': choice.to_id,
                'from_name': catalog[choice.from_id][1].name, 'to_name': stop.name, 'before_mode': before.mode, 'after_mode': selected.mode,
                'before_cost_cents': _route_cost(before, stop, plan.request.people),
                'after_cost_cents': _route_cost(selected, stop, plan.request.people)})
    response['summary']['route_changed_count'] = len(response['route_changes'])
    full = validate_core(normalized_input(candidate.request, candidate.itinerary, candidate.routes))
    response['errors'].extend(full['errors'])
    response['warnings'] = full['warnings']
    response['unknowns'].extend(full['unknowns'])
    response['summary']['after_cents'] = full['total_cents']
    if full['total_cents'] != oracle['total_cents']:
        # The full ledger is authoritative. Exact comparison catches accidental
        # double counting or dropped expense rows, including shared transport.
        response['errors'].append(_check('whole_cost_disagreement', '合并后全程费用与冻结账目不一致，请核对共享或已保留费用，本次不应用'))
    if candidate.itinerary.transfers != plan.itinerary.transfers:
        response['errors'].append(_check('transfer_protection', '跨城交通发生非授权变化，拒绝应用'))
    for index, day in enumerate(plan.itinerary.days):
        if index not in affected and candidate.itinerary.days[index] != day:
            response['errors'].append(_check('outside_window_protection', '窗口外内容发生变化，拒绝应用', index))
        if index in command.day_indexes:
            other_stops = [s for s in day.stops if city_key(stop_city(plan, s)) != city]
            after_other = [s for s in candidate.itinerary.days[index].stops if city_key(stop_city(plan, s)) != city]
            if after_other != other_stops:
                response['errors'].append(_check('outside_city_protection', '所选日期中其他城市的活动发生变化，拒绝应用', index))
    for sid, (index, stop) in catalog.items():
        if states[sid]['status'] == 'completed' or states[sid]['locked']:
            after = next((s for d in candidate.itinerary.days for s in d.stops if s.id == sid), None)
            if after != stop:
                response['errors'].append(_check('locked_changed', '已完成或锁定活动发生变化，拒绝应用', index, sid))
    after_map = {s.id: (i, s) for i, day in enumerate(candidate.itinerary.days) for s in day.stops}
    for sid, (index, stop) in catalog.items():
        after = after_map.get(sid)
        fields = []
        if after is None:
            fields.append('unplaced')
        else:
            if after[0] != index:
                fields.append('day')
            if (after[1].start, after[1].end) != (stop.start, stop.end):
                fields.append('time')
        if fields:
            response['changes'].append({'stop_id': sid, 'name': stop.name, 'fields': fields,
                'before_day': index, 'after_day': after[0] if after else None,
                'before_start': stop.start, 'after_start': after[1].start if after else None})
    response['summary']['changed_count'] = len(response['changes'])
    if response['errors']:
        return response
    from .models import ValidationReport
    candidate.validation = ValidationReport(**full, backend='python-independent', diagnostic='仓颉2.2候选经独立协议及多城全程复核；未知仍待核实')
    candidate.id, candidate.parent_id = str(uuid4()), plan.id
    candidate.revision, candidate.routing_revision = plan.revision + 1, 0
    candidate.created_at = datetime.now(timezone.utc)
    candidate.revision_instruction = '仓颉约束与途中调整（用户确认应用）'
    candidate.adjustments = [*plan.adjustments, f'仓颉2.2在同城连续{len(command.day_indexes)}天内有限调整；跨城交通保持，Python独立复核。']
    candidate.schedule_baseline = {s.id: (s.start, s.end) for d in candidate.itinerary.days for s in d.stops}
    # Retain source activity/cost details for a future user-authorized missed-task
    # insertion; they are not silently marked completed or charged twice.
    for sid, (index, stop) in catalog.items():
        if '_stop' not in states[sid]:
            states[sid]['_stop'] = stop.model_dump(mode='json')
            states[sid]['_day'] = index
            states[sid]['_expenses'] = [e.model_dump(mode='json') for d in plan.itinerary.days for e in d.expenses if e.stop_id == sid]
        if sid in after_map:
            states[sid]['_day'] = after_map[sid][0]
            states[sid]['_stop'] = after_map[sid][1].model_dump(mode='json')
    response['outcome'] = 'provisional' if response['unknowns'] else ('unchanged' if not response['changes'] else 'feasible')
    return storage.save_preview(plan, state, candidate, states, response,
                                day_windows=window_preferences(state, command, windows))
