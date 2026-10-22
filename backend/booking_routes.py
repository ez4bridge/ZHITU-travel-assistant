"""Bounded user-booking preview/apply with existing CAS and confirmed-fact ledger."""
import asyncio
from datetime import datetime, time, timezone
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import Field

from .models import StrictModel, ConfirmedHotel, BookedTransport, TripRequest
from .amap import Amap, MapServiceError
from .bookings import (confirmed_hotels, drop_activity_ids, drop_movement_to_anchors,
                       drop_return_movement_stops, validate_bookings,
                       apply_hotel_anchors, apply_booked_transports, apply_transport_anchors,
                       repair_booking_schedule)
from .constraint_storage import ConstraintStore, ConstraintStale, fingerprint
from .constraint_models import ConstraintApplyRequest
from .planning import recost_all
from .arrival_flow import prepare_arrival_flow
from .meals import meal_schedule_checks
from .validation import minutes, validator
from .booking_recovery import (protected_activity_ids, recovery_target,
                               preserved_fact_conflicts, fixed_unknown_errors)
from .booking_finance import preserve_confirmed_expenses, confirmed_expense_conflicts

_active_previews = 0


class BookingMapUnavailable(RuntimeError):
    """Safe, authored diagnostic; never expose HTTP payloads or credentials."""


def active_booking_previews():
    return _active_previews


class BookingPreviewRequest(StrictModel):
    base_fingerprint: str = Field(min_length=64, max_length=64)
    expected_routing_revision: int = Field(ge=0)
    expected_state_revision: int = Field(ge=0)
    confirmed_hotels: list[ConfirmedHotel] = Field(default_factory=list, max_length=20)
    booked_transports: list[BookedTransport] = Field(default_factory=list, max_length=14)


def assert_current(plan, state, body):
    if (fingerprint(plan) != body.base_fingerprint or plan.routing_revision != body.expected_routing_revision
            or state['state_revision'] != body.expected_state_revision or state['current_plan_id'] != plan.id):
        raise ConstraintStale('行程或已确认事实已变化，请打开最新版本重新补录')


REGENERATE_INSTRUCTION = ('用户已填写已订交通与已定酒店，请把这些当作固定事实重新制定整份行程：'
    '沿用其车次／航班时刻、车站／机场与酒店位置，重新安排每天的景点、餐饮、接驳与费用；'
    '不要改写班次、不要在活动里重复列车站，也不要编造订单、已付款或已退改。')


async def _quiet_emit(*_args, **_kwargs):
    return None


def booking_trip(plan, state, body):
    """Validate the submitted bookings and build the trip they describe."""
    if plan.request.study:
        raise ValueError('历史研学暂不使用普通酒店补录，请通过研学调整保留原文与现场任务')
    assert_current(plan, state, body)
    trip_data = plan.request.model_dump()
    trip_data.update(confirmed_hotel='', confirmed_hotels=body.confirmed_hotels, booked_transports=body.booked_transports)
    trip = TripRequest.model_validate(trip_data)
    previous_start = trip.start_time
    from .validation import minutes
    from .planning import clock
    from .bookings import normalize_booking_start_time
    normalize_booking_start_time(trip)
    for hotel in trip.confirmed_hotels:
        if not hotel.booking_id:
            from .bookings import hotel_ref
            hotel.booking_id = hotel_ref(hotel)
    validate_bookings(trip)
    from .models import city_key
    new_pairs = {(city_key(b.from_city), city_key(b.to_city), b.transfer_index) for b in body.booked_transports}
    if any((city_key(b.from_city), city_key(b.to_city), b.transfer_index) not in new_pairs for b in plan.request.booked_transports):
        raise ValueError('不能通过删除补录项撤销已有预订或假定退款；请保留原段，按实际改签信息修改时刻与费用')
    return trip, previous_start


def guard_regeneration(plan, state):
    """A full regeneration cannot carry facts that only exist in the saved plan."""
    facts = [stop_id for stop_id, fact in state.get('activities', {}).items()
             if fact.get('status') == 'completed' or fact.get('locked')]
    if facts:
        raise ValueError('这份行程已有完成或锁定的安排，整份重新生成会丢失这些事实；'
                         '请改用“调整行程”只重排未开始的日期')
    if state.get('day_windows'):
        raise ValueError('这份行程已确认每日活动时段，整份重新生成会绕开它们；'
                         '请先用“调整行程”在新时段内重排')


def drop_model_hotels(itinerary):
    """A confirmed hotel replaces any lodging stop the model invented."""
    for day in itinerary.days:
        removed = {stop.id for stop in day.stops if stop.kind == 'hotel' and not stop.booking_anchor}
        if not removed:
            continue
        day.stops = [stop for stop in day.stops if stop.id not in removed]
        day.expenses = [expense for expense in day.expenses
                        if not (expense.category == 'hotel' and expense.stop_id in removed)]


def clear_model_booking_claims(itinerary):
    """A model may not declare that user bookings or payments were verified."""
    for day in itinerary.days:
        for stop in day.stops:
            stop.booking_anchor = stop.booking_ref = stop.booking_address = ''
        for expense in day.expenses:
            expense.booking_ref = ''
            expense.commitment = 'planned'
    for transfer in itinerary.transfers:
        transfer.booking_status = 'unconfirmed'
        if transfer.price_status == 'user_reported':
            transfer.price_status = 'estimated'
    return itinerary


def drop_unlocated_stops(itinerary, day_indices=None, *, protected=()):
    """Remove suggestions the map cannot place; never user-supplied anchors.

    Booked hotel/transport anchors remain visible as unknown user facts.
    """
    ids = {s.id for index, day in enumerate(itinerary.days)
           if day_indices is None or index in day_indices for s in day.stops
           if not s.location or s.location_status != 'verified'}
    return drop_activity_ids(itinerary, ids, day_indices=day_indices, protected_ids=protected)


async def preview_bookings(plan, state, body, storage, *, amap_factory=Amap, regenerate=False, generator=None):
    trip, previous_start = booking_trip(plan, state, body)
    moved = []
    if regenerate:
        guard_regeneration(plan, state)
        make = generator
        if make is None:
            from .agent import generate_itinerary as make
        # A separate client for the agent tools; enrichment opens its own below.
        tools_map = amap_factory(trip.journey_cities)
        try:
            generated = await make(trip, tools_map, _quiet_emit, previous=None,
                                   instruction=REGENERATE_INSTRUCTION, rail=None)
        finally:
            await tools_map.close()
        clear_model_booking_claims(generated)
        if trip.confirmed_hotels:
            drop_model_hotels(generated)
        moved = drop_return_movement_stops(generated)
    candidate = plan.model_copy(deep=True)
    candidate.id, candidate.parent_id = str(uuid4()), plan.id
    candidate.revision, candidate.created_at = plan.revision + 1, datetime.now(timezone.utc)
    candidate.request = trip
    candidate.revision_instruction = ('按已填酒店与已订交通整份重新生成' if regenerate
        else '确认补录酒店与已订交通；按住宿起点与固定交通时刻核对接驳')
    if regenerate:
        candidate.itinerary = generated
        candidate.routes = []
        candidate.itinerary.summary = ('已按你填写的酒店与已订交通请模型整份重新生成，旧版本保留在历史中；'
            '车次、酒店与成交金额来自用户填写，未查验订单。' + (generated.summary or ''))[:1000]
        affected = set(range(len(generated.days)))
    else:
        candidate.itinerary.summary = ('已按你填写的酒店位置与已订交通核对当天起点、接驳和费用，保留原景点顺序。'
            '本版时间与预算以跨城卡片、每日时间轴和预算明细为准，原方案保留在历史版本中。'
            '预订信息来自用户，酒店入住／寄存时间、班次变更、营业与预约仍需向官方确认。')
    if plan.request.confirmed_hotel:
        for day in candidate.itinerary.days:
            day.stops = [s for s in day.stops if s.kind != 'hotel' or s.booking_anchor]
    apply_booked_transports(candidate.itinerary, trip)
    from .transfer_timeline import prepare_transfer_anchors
    # The user's own declaration wins over a stale model label: opt in only here,
    # after the booking is applied to the matching transfer, so a queried
    # timetable alone can still never rewrite a model's station.
    prepare_transfer_anchors(candidate.itinerary, trip, prefer_user_bookings=True)
    apply_hotel_anchors(candidate.itinerary, trip)
    moved += drop_movement_to_anchors(candidate.itinerary,
        protected_ids=protected_activity_ids(candidate.itinerary, state))
    if not regenerate:
        affected = {i for i, (old, new) in enumerate(zip(plan.itinerary.days, candidate.itinerary.days)) if old != new}
        if body.booked_transports != plan.request.booked_transports:
            affected.update(i for i, day in enumerate(candidate.itinerary.days) if any(
                day.date in (b.departure_date, b.arrival_date) for b in [*body.booked_transports, *plan.request.booked_transports]))
        if not affected and trip.confirmed_hotels == plan.request.confirmed_hotels and trip.booked_transports == plan.request.booked_transports:
            raise ValueError('补录内容没有变化，无需生成新版本')
    flow_notices = []
    for index in affected:
        flow_notices += prepare_arrival_flow(trip, candidate.itinerary, index)
    completed_until = max((datetime.combine(day.date, time.fromisoformat(stop.end))
        for day in plan.itinerary.days for stop in day.stops
        if state['activities'].get(stop.id, {}).get('status') == 'completed'), default=None)
    if completed_until:
        previous_ids = {stop.id for day in plan.itinerary.days for stop in day.stops}
        past_additions = [stop.name for day in candidate.itinerary.days for stop in day.stops
            if stop.id not in previous_ids and datetime.combine(day.date, time.fromisoformat(stop.start)) < completed_until]
        if past_additions:
            return {'status': 'conflict', 'changes': [], 'notices': flow_notices,
                'conflicts': ['补录会在已确认完成的时间之前新增活动：' + '、'.join(dict.fromkeys(past_additions)) +
                    '。过去行程不能自动补排；请只补充未开始日期，或先在途中调整中确认实际进度。']}
    finance_notices = preserve_confirmed_expenses(plan, candidate)
    # One request-local client caches unchanged POIs/roads between recovery
    # rounds. Every removed vertex still requires querying its new adjacency.
    from .constraint_replan import select_route_mode
    from .meals import period, WINDOWS
    old_errors = set() if regenerate else {(e.code, e.day_index, e.stop_id) for e in plan.validation.errors}
    baseline_meals, accepted_late = set(), set()
    if not regenerate:
        for index in affected:
            old_conflicts, _ = meal_schedule_checks(plan.request, plan.itinerary, plan.routes, index,
                                                    plan.request.preferences)
            baseline_meals.update(old_conflicts)
        for day in plan.itinerary.days:
            for stop in day.stops:
                if stop.kind == 'meal':
                    lower, upper = WINDOWS[period(stop)]
                    if not lower <= minutes(stop.start) <= upper:
                        accepted_late.add(stop.id)
    changes, recovery_notices = [], []
    candidate.routes = [r.model_copy(deep=True) for r in plan.routes if r.day_index not in affected]
    amap = amap_factory(trip.journey_cities)

    async def refresh(indices):
        async with asyncio.timeout(75):
            queried = await amap.enrich(candidate.itinerary, trip.mode, compare_modes=True,
                day_indices=indices, optimize_meals=False, people=trip.people)
        # Provider outages/quota failures do not prove a suggestion is unreachable.
        # Do not delete the whole itinerary in response to a service outage.
        service_codes = {'map_auth', 'map_ip_limit', 'map_quota', 'map_rate_limit', 'map_response', 'map_network'}
        if (getattr(amap, 'unresolved_provider_errors', ()) or
                any(not r.available and r.failure_code in service_codes for r in queried)):
            raise BookingMapUnavailable('地图服务暂不可用，未据此删减行程；原版本保留。请检查地图服务配额／权限，或稍后核对路线。')
        # POI enrichment may reveal that a generated activity reused an old id
        # for a different place. Keep the payment, detach its old association.
        if regenerate:
            finance_notices.extend(preserve_confirmed_expenses(plan, candidate))
        selection_conflicts = []
        for index, route in enumerate(queried):
            previous = next((r for r in plan.routes if
                (r.day_index, r.from_id, r.to_id) == (route.day_index, route.from_id, route.to_id)
                and r.user_selected), None)
            if previous and not regenerate:
                try:
                    selected = select_route_mode(route, previous.mode)
                    selected.user_selected = True
                    selected.selection_reason = '保留你明确选择的交通方式；本次按新住宿／交通核对路线。'
                    queried[index] = selected
                except ValueError:
                    selection_conflicts.append('原来明确选择的交通方式本次未查到可用路线，请先在调整行程中确认替代方式。')
        candidate.routes = [r for r in candidate.routes if r.day_index not in indices] + queried
        candidate.routes.sort(key=lambda r: r.day_index)
        for index in indices:
            recost_all(trip, candidate.itinerary, candidate.routes, index)
        return selection_conflicts

    try:
        selection_conflicts = await refresh(affected)
        # A finite graph shrinks by exactly one variable activity each round.
        # This handles more than three consecutive failures without retrying a
        # failed external interface indefinitely.
        remaining = sum(len(candidate.itinerary.days[i].stops) for i in affected)
        for _ in range(remaining + 1):
            schedule_conflicts, extra = repair_booking_schedule(candidate, state, affected)
            changes += extra
            candidate.validation = await validator.validate(trip, candidate.itinerary, candidate.routes)
            meal_conflicts, meal_notices = [], []
            for index in sorted(affected):
                failures, notes = meal_schedule_checks(trip, candidate.itinerary, candidate.routes,
                    index, trip.preferences, accepted_late=accepted_late)
                meal_conflicts.extend((index, message) for message in failures if message not in baseline_meals)
                meal_notices += notes
            # An old error with the same destination id can describe a *new*
            # edge after adding a hotel. Baselines affect blocking only, never
            # whether a variable suggestion can be healed on an affected day.
            target = recovery_target(candidate, state, affected, candidate.validation.errors,
                                     schedule_conflicts, meal_conflicts)
            # Explicit route choices and saved progress must not be bypassed by
            # deleting another activity. The final guards are rerun after repair.
            if target and not selection_conflicts:
                index, sid, reason = target
                stop = next(s for s in candidate.itinerary.days[index].stops if s.id == sid)
                removed = drop_activity_ids(candidate.itinerary, {sid}, day_indices={index},
                    protected_ids=protected_activity_ids(candidate.itinerary, state))
                if removed:
                    recovery_notices.append(f'第{index + 1}天「{stop.name}」{reason}，已移除这项可变建议及未发生的关联费用估算；酒店与已订交通保留。')
                    if stop.kind == 'meal':
                        recovery_notices.append(f'第{index + 1}天该餐馆已取消，需在空余时间另选就近餐馆或自理用餐；对应餐费尚待补充，未当作免费。')
                    selection_conflicts = await refresh({index})
                    continue
            break
    finally:
        await amap.close()

    # Final checks are deliberately outside the healing loop. No retry can erase
    # a previously detected completed/locked/window/paid-fact violation.
    unlocated_anchor_errors = fixed_unknown_errors(candidate, state)
    stop_names = {s.id: s.name for d in candidate.itinerary.days for s in d.stops}
    conflicts = [*selection_conflicts, *schedule_conflicts,
                 *(message for _, message in meal_conflicts),
                 *preserved_fact_conflicts(plan, candidate, state, affected),
                 *confirmed_expense_conflicts(plan, candidate)]
    for index, day in enumerate(candidate.itinerary.days):
        if len(day.expenses) > 20:
            conflicts.append(f'第{index + 1}天保留真实费用后超过20项明细；请合并普通预算项目，已付／承诺费用未自动删除')
    conflicts += [(f'「{stop_names[e.stop_id]}」' if e.stop_id in stop_names else '') + e.message
                  for e in candidate.validation.errors
                  if (e.code, e.day_index, e.stop_id) not in old_errors
                  and (e.code, e.day_index, e.stop_id) not in unlocated_anchor_errors]
    notices = ['酒店、班次与成交金额来自用户填写，未查验预订订单；地图只核实地点和道路。',
        ('按新预订整份重排；景点与餐馆是可变建议，不可达或时间不合适时可删减，并同步更新费用。'
         if regenerate else '按实际路线核对时间；不可达或时间不合适的普通建议可删减，已确认事实与真实费用保留。'),
        *flow_notices, *dict.fromkeys(finance_notices), *recovery_notices, *meal_notices]
    if unlocated_anchor_errors:
        names = '、'.join(dict.fromkeys(stop_names.get(stop_id, '已填地点') for _, _, stop_id in unlocated_anchor_errors))
        notices.append('以下固定地点或接驳路线仍未核实，已保留原填写内容：' + names
            + '。接驳时间与费用待核实；已保存不代表这些路线已可执行，请出发前确认。')
    if moved:
        notices.append('已移除模型生成的移动活动：' + '、'.join(dict.fromkeys(moved))
            + '；返程时间改由路线与酒店节点计算，不重复计时。')
    missing = [s.name for d in candidate.itinerary.days for s in d.stops if s.booking_anchor and s.location_status != 'verified']
    if missing:
        notices.append('以下接驳地点未唯一定位，已按原文保留：' + '、'.join(dict.fromkeys(missing))
                       + '；请核对完整名称或地址，接驳时间未核实。')
    new_hotel_refs = {h.booking_id for h in trip.confirmed_hotels}
    if any(e.booking_ref and e.booking_ref not in new_hotel_refs for d in candidate.itinerary.days for e in d.expenses):
        notices.append('已移除酒店的原填费用仍保留在预算；系统不会据此假定已经退款。')
    if any(not b.arrival_place or not b.departure_place for b in body.booked_transports):
        notices.append('部分站点／机场未填写：已应用时刻和预留时间，但未核实缺失端点的接驳，请补全。')
    changes = [f'补录{len(body.confirmed_hotels)}段酒店入住、{len(body.booked_transports)}段已订交通；重新核对{len(affected)}天', *changes]
    if trip.start_time != previous_start:
        changes.insert(0, f'首日开始{previous_start}→{trip.start_time}，适配你填写的已订交通与出发前预留时间')
    candidate.adjustments = list(dict.fromkeys([*changes, *notices]))
    candidate.schedule_baseline = {s.id: (s.start, s.end) for d in candidate.itinerary.days for s in d.stops}
    candidate.routing_revision = 0
    response = {'status': 'conflict' if conflicts else 'ready', 'changes': changes, 'notices': notices,
                'conflicts': list(dict.fromkeys(conflicts)), 'candidate': candidate.model_dump(mode='json')}
    if conflicts:
        return response
    return storage.save_preview(plan, state, candidate, state['activities'], response, day_windows=state.get('day_windows', []))


def install_booking_routes(app, store_getter):
    def load(plan_id):
        store = store_getter()
        plan = store.get(plan_id)
        if not plan:
            raise HTTPException(404, '没有找到这份行程')
        if plan.request.study:
            raise HTTPException(422, '历史研学暂不使用普通酒店补录，请通过研学调整保留原文与现场任务')
        storage = ConstraintStore(store.path)
        return plan, storage, storage.state(plan)

    @app.get('/api/plans/{plan_id}/booking-context')
    async def booking_context(plan_id: str):
        plan, _, state = load(plan_id)
        return {'base_fingerprint': fingerprint(plan), 'expected_routing_revision': plan.routing_revision,
            'expected_state_revision': state['state_revision'], 'current_plan_id': state['current_plan_id'],
            'confirmed_hotels': [h.model_dump(mode='json') for h in confirmed_hotels(plan.request)],
            'booked_transports': [b.model_dump(mode='json') for b in plan.request.booked_transports]}

    async def serve_preview(plan_id, body, request, *, regenerate=False, seconds=90,
                            failure='本次酒店／接驳路线核对暂未完成；原行程未修改，请稍后重试'):
        global _active_previews
        if _active_previews >= 2:
            raise HTTPException(429, '已有两个补录核对正在运行，请稍后重试')
        plan, storage, state = load(plan_id)
        _active_previews += 1
        tasks = []
        try:
            async with asyncio.timeout(seconds):
                work = asyncio.create_task(preview_bookings(plan, state, body, storage, regenerate=regenerate))
                async def disconnected():
                    while not asyncio.current_task().cancelling():
                        if await request.is_disconnected():
                            return
                        if asyncio.current_task().cancelling():
                            return
                        await asyncio.sleep(0.2)
                watch = asyncio.create_task(disconnected())
                tasks = [work, watch]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if work in done:
                    return await work
                raise HTTPException(499, '补录核对已取消，原行程保持')
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except BookingMapUnavailable as error:
            raise HTTPException(503, str(error)) from None
        except MapServiceError as error:
            reason = {'map_quota': '地图服务配额或余额已用尽，请检查高德控制台后再试',
                      'map_auth': '地图服务鉴权失败，请检查服务配置',
                      'map_ip_limit': '地图服务IP访问受限，请检查服务配置',
                      'map_network': '地图服务网络暂不可用，请稍后重试',
                      'map_rate_limit': '地图接口暂时限流，请稍后重试'}.get(error.code, '地图服务暂不可用，请稍后核对路线')
            raise HTTPException(503, reason + '；未据此删除活动，原版本保留。') from None
        except (RuntimeError, TimeoutError):
            raise HTTPException(503, failure) from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            _active_previews -= 1

    @app.post('/api/plans/{plan_id}/booking-preview')
    async def booking_preview(plan_id: str, body: BookingPreviewRequest, request: Request):
        return await serve_preview(plan_id, body, request)

    @app.post('/api/plans/{plan_id}/booking-regenerate')
    async def booking_regenerate(plan_id: str, body: BookingPreviewRequest, request: Request):
        # A model run plus the map pass needs more than the 90s re-time budget.
        response = await serve_preview(plan_id, body, request, regenerate=True, seconds=300,
            failure='本次整份重新生成未完成（模型或地图超时）；原行程未修改，请稍后重试')
        # Full regeneration saves immediately: the user asked for a new plan,
        # not another confirmation step. Conflicts still come back untouched.
        if response.get('status') == 'ready' and response.get('preview_id'):
            _, storage, _ = load(plan_id)
            try:
                saved = storage.apply(plan_id, response['preview_id'])
            except ConstraintStale as error:
                raise HTTPException(409, str(error)) from None
            return {'status': 'applied', 'plan': saved.model_dump(mode='json'),
                    'changes': response.get('changes', []), 'notices': response.get('notices', [])}
        return response

    @app.post('/api/plans/{plan_id}/booking-apply')
    async def booking_apply(plan_id: str, body: ConstraintApplyRequest):
        _, storage, _ = load(plan_id)
        try:
            return {'plan': storage.apply(plan_id, body.preview_id)}
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
