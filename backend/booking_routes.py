"""Bounded user-booking preview/apply with existing CAS and confirmed-fact ledger."""
import asyncio
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import HTTPException, Request
from pydantic import Field

from .models import StrictModel, ConfirmedHotel, BookedTransport, TripRequest
from .amap import Amap
from .bookings import (confirmed_hotels, validate_bookings, apply_hotel_anchors,
                       apply_booked_transports, apply_transport_anchors, repair_booking_schedule)
from .constraint_storage import ConstraintStore, ConstraintStale, fingerprint
from .constraint_models import ConstraintApplyRequest
from .planning import recost_all
from .validation import validator

_active_previews = 0


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


async def preview_bookings(plan, state, body, storage, *, amap_factory=Amap):
    if plan.request.study:
        raise ValueError('历史研学暂不使用普通酒店补录，请通过研学调整保留原文与现场任务')
    assert_current(plan, state, body)
    trip_data = plan.request.model_dump()
    trip_data.update(confirmed_hotel='', confirmed_hotels=body.confirmed_hotels, booked_transports=body.booked_transports)
    trip = TripRequest.model_validate(trip_data)
    previous_start = trip.start_time
    from .validation import minutes
    from .planning import clock
    for booking in trip.booked_transports:
        if booking.departure_date == trip.start_date:
            buffer = booking.departure_buffer_min if booking.departure_buffer_min is not None else (120 if booking.mode == 'flight' else 60)
            trip.start_time = clock(min(minutes(trip.start_time), max(0, minutes(booking.departure_time) - buffer)))
    for hotel in trip.confirmed_hotels:
        if not hotel.booking_id:
            from .bookings import hotel_ref
            hotel.booking_id = hotel_ref(hotel)
    validate_bookings(trip)
    from .models import city_key
    new_pairs = {(city_key(b.from_city), city_key(b.to_city), b.transfer_index) for b in body.booked_transports}
    if any((city_key(b.from_city), city_key(b.to_city), b.transfer_index) not in new_pairs for b in plan.request.booked_transports):
        raise ValueError('不能通过删除补录项撤销已有预订或假定退款；请保留原段，按实际改签信息修改时刻与费用')
    candidate = plan.model_copy(deep=True)
    candidate.id, candidate.parent_id = str(uuid4()), plan.id
    candidate.revision, candidate.created_at = plan.revision + 1, datetime.now(timezone.utc)
    candidate.request = trip
    candidate.revision_instruction = '确认补录酒店与已订交通；按住宿起点与固定交通时刻核对接驳'
    candidate.itinerary.summary = ('已按你填写的酒店位置与已订交通核对当天起点、接驳和费用，保留原景点顺序。'
        '本版时间与预算以跨城卡片、每日时间轴和预算明细为准，原方案保留在历史版本中。'
        '预订信息来自用户，酒店入住／寄存时间、班次变更、营业与预约仍需向官方确认。')
    if plan.request.confirmed_hotel:
        for day in candidate.itinerary.days:
            day.stops = [s for s in day.stops if s.kind != 'hotel' or s.booking_anchor]
    apply_booked_transports(candidate.itinerary, trip)
    from .transfer_timeline import prepare_transfer_anchors
    prepare_transfer_anchors(candidate.itinerary, trip)
    apply_hotel_anchors(candidate.itinerary, trip)
    affected = {i for i, (old, new) in enumerate(zip(plan.itinerary.days, candidate.itinerary.days)) if old != new}
    if body.booked_transports != plan.request.booked_transports:
        affected.update(i for i, day in enumerate(candidate.itinerary.days) if any(
            day.date in (b.departure_date, b.arrival_date) for b in [*body.booked_transports, *plan.request.booked_transports]))
    if not affected and trip.confirmed_hotels == plan.request.confirmed_hotels and trip.booked_transports == plan.request.booked_transports:
        raise ValueError('补录内容没有变化，无需生成新版本')
    amap = amap_factory(trip.journey_cities)
    try:
        async with asyncio.timeout(75):
            queried = await amap.enrich(candidate.itinerary, trip.mode, compare_modes=True,
                day_indices=affected, optimize_meals=False, people=trip.people)
    finally:
        await amap.close()
    candidate.routes = [r.model_copy(deep=True) for r in plan.routes if r.day_index not in affected] + queried
    candidate.routes.sort(key=lambda r: r.day_index)
    from .constraint_replan import select_route_mode
    for route in candidate.routes:
        previous = next((r for r in plan.routes if (r.day_index, r.from_id, r.to_id) == (route.day_index, route.from_id, route.to_id) and r.user_selected), None)
        if previous:
            try:
                selected = select_route_mode(route, previous.mode)
                route_index = candidate.routes.index(route)
                candidate.routes[route_index] = selected
                selected.user_selected = True
                selected.selection_reason = '保留你明确选择的交通方式；本次按新住宿／交通核对路线。'
            except ValueError:
                # The explicitly selected mode must not be silently replaced.
                return {'status': 'conflict', 'changes': [], 'notices': [], 'conflicts': ['原来明确选择的交通方式本次未查到可用路线，请先在调整行程中确认替代方式。']}
    for day_index in affected:
        recost_all(trip, candidate.itinerary, candidate.routes, day_index)
    conflicts, changes = repair_booking_schedule(candidate, state, affected)
    # Another endpoint must not turn a completed fact back into a planning suggestion.
    original = {s.id: s for d in plan.itinerary.days for s in d.stops}
    proposed = {s.id: s for d in candidate.itinerary.days for s in d.stops}
    for sid, fact in state['activities'].items():
        if (fact.get('status') == 'completed' or fact.get('locked')) and sid in original and proposed.get(sid) != original[sid]:
            conflicts.append(f'「{original[sid].name}」已有完成／锁定事实；本次补录影响该项，请只修改未开始的日期')
    for index in affected:
        for window in (w for w in state.get('day_windows', []) if w['day_index'] == index):
            for stop in candidate.itinerary.days[index].stops:
                if window.get('scope_city') and stop.city.removesuffix('市') != window['scope_city'].removesuffix('市'):
                    continue
                from .validation import minutes
                if minutes(stop.start) < window['start_min'] or minutes(stop.end) > window['end_min']:
                    conflicts.append(f'第{index + 1}天「{stop.name}」超出已确认的每日活动时段，请先调整时段')
    candidate.validation = await validator.validate(trip, candidate.itinerary, candidate.routes)
    # Retain pre-existing issues as visible validation, but block newly introduced failures.
    old_errors = {(e.code, e.day_index, e.stop_id) for e in plan.validation.errors}
    conflicts.extend(e.message for e in candidate.validation.errors if (e.code, e.day_index, e.stop_id) not in old_errors)
    notices = ['酒店、班次与成交金额来自用户填写，未查验预订订单；地图只核实地点和道路。',
        '保留现有活动顺序，按实际路线顺延时间；需要删减、换天或补游时再使用“调整行程”。']
    missing = [s.name for d in candidate.itinerary.days for s in d.stops if s.booking_anchor and s.location_status != 'verified']
    if missing:
        conflicts.append('以下接驳地点未唯一定位，请补充分店／车站／机场全称或酒店地址：' + '、'.join(dict.fromkeys(missing)))
    new_hotel_refs = {h.booking_id for h in trip.confirmed_hotels}
    if any(e.booking_ref and e.booking_ref not in new_hotel_refs for d in candidate.itinerary.days for e in d.expenses):
        notices.append('已移除酒店的原填费用仍保留在预算；系统不会据此假定已经退款。')
    if any(not b.arrival_place or not b.departure_place for b in body.booked_transports):
        notices.append('部分站点／机场未填写：已应用时刻和预留时间，但未核实缺失端点的接驳，请补全。')
    changes = [f'补录{len(body.confirmed_hotels)}段酒店入住、{len(body.booked_transports)}段已订交通；重新核对{len(affected)}天', *changes]
    if trip.start_time != previous_start:
        changes.insert(0, f'首日开始{previous_start}→{trip.start_time}，适配你填写的已订交通与出发前预留时间')
    candidate.adjustments = changes
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

    @app.post('/api/plans/{plan_id}/booking-preview')
    async def booking_preview(plan_id: str, body: BookingPreviewRequest, request: Request):
        global _active_previews
        if _active_previews >= 2:
            raise HTTPException(429, '已有两个补录核对正在运行，请稍后重试')
        plan, storage, state = load(plan_id)
        _active_previews += 1
        tasks = []
        try:
            async with asyncio.timeout(90):
                work = asyncio.create_task(preview_bookings(plan, state, body, storage))
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
        except (RuntimeError, TimeoutError):
            raise HTTPException(503, '本次酒店／接驳路线核对暂未完成；原行程未修改，请稍后重试') from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            _active_previews -= 1

    @app.post('/api/plans/{plan_id}/booking-apply')
    async def booking_apply(plan_id: str, body: ConstraintApplyRequest):
        _, storage, _ = load(plan_id)
        try:
            return {'plan': storage.apply(plan_id, body.preview_id)}
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
