import asyncio
import base64
import binascii
import time
import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from time import monotonic

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import settings, ROOT
from .models import TripRequest, PlanRecord, RevisionRequest, RouteSelection, StudyQuestion, PhotoQuestion, ConversationCreate, GuideQuery
from .amap import Amap
from .agent import generate_itinerary
from .storage import Store
from .validation import minutes, validator
from .cities import city_catalog
from .railway import Railway
from .planning import repair_schedule, recost_all
from .locality import prepare_accommodation
from .itinerary_evidence import bind_stop_expenses, audit_guides, normalize_ticket_quantities
from .meals import align_meals, meal_schedule_checks
from .arrival_flow import prepare_arrival_flow
from .build_info import source_fingerprint
from .conversation import ConversationStore, ConversationError, context_for, encoded, turn_memory
from .qa_usage import UsageCollector
from .photo_study import answer_photo, image_type
from .weather import WeatherService, forecast_trip, normalize_city
from .map_region import resolve_map_region
from .place_guide import GuideService, GuideStore
from .knowledge import KnowledgeBase
from .study_agent import answer_study, generate_study_plan, plan_places, update_study_request
from .constraint_bridge import constraint_planner
from .constraint_routes import active_constraint_previews, install_constraint_routes
from .booking_routes import install_booking_routes, active_booking_previews
from .planning_conversation_routes import install_conversation_routes, active_conversations
from .bookings import (apply_booked_transports, drop_last_optional_stop,
                       drop_movement_to_anchors, drop_return_movement_stops,
                       ease_conflicting_transfers, normalize_booking_start_time,
                       repair_booking_schedule)
from .transfer_timeline import prepare_transfer_anchors

BUILD_ID = source_fingerprint()
STARTED_AT = datetime.now(timezone.utc).isoformat()

store = Store()
knowledge = KnowledgeBase(settings.study_corpus_dir)
conversations = ConversationStore()
weather_service = WeatherService()
guide_service = GuideService()
guide_store = GuideStore()


@asynccontextmanager
async def lifespan(app):
    pid_file = settings.runtime / 'app.pid'
    manages_pid = os.getenv('TRAVEL_SERVER_PROCESS') == '1'
    if manages_pid:
        pid_file.write_text(str(os.getpid()))
    knowledge.retriever().start_background()
    try:
        yield
    finally:
        await knowledge.aclose()
        if manages_pid and pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
            pid_file.unlink()


app = FastAPI(title='漫游 Travel Assistant', version='0.1.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver', 'test'])
_active_tasks = 0
_metrics = {'completed': 0, 'failed': 0, 'cancelled': 0, 'last_duration_s': 0.0}
_last_failure = None
install_constraint_routes(app, lambda: store)
install_booking_routes(app, lambda: store)
install_conversation_routes(app, lambda: store)


def protect_confirmed_constraints(plan):
    from .constraint_storage import ConstraintStale, assert_legacy_editable
    try:
        assert_legacy_editable(store, plan)
    except ConstraintStale as error:
        raise HTTPException(409, str(error)) from None


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.url.netloc:
        return JSONResponse(status_code=403, content={'detail': '请从本机应用页面访问服务'})
    return await call_next(request)


@app.exception_handler(Exception)
async def safe_exception(request, error):
    return JSONResponse(status_code=500, content={'detail': settings.safe_error(error)})


@app.exception_handler(ConversationError)
async def conversation_error(request, error):
    return JSONResponse(status_code=error.status, content={'detail': str(error)})


@app.get('/api/health')
async def health():
    ready = await validator.ready()
    constraints_ready = await constraint_planner.ready()
    return {'app_id': 'travel-assistant', 'status': 'ok', 'model': settings.llm_model,
            'capabilities': {'planning': bool(settings.llm_key and settings.amap_key), 'map': bool(settings.amap_web_key and settings.amap_js_sec),
                             'railway': settings.rail_mcp_enabled and settings.rail_mcp_python.is_file(),
                             'routing': bool(settings.amap_key), 'validator': 'cangjie' if ready else 'python',
                             'constraint_replanning': constraints_ready},
            'missing': [name for name, value in [('LLM_API_KEY', settings.llm_key), ('AMAP_API_KEY', settings.amap_key),
                       ('AMAP_WEB_KEY', settings.amap_web_key), ('AMAP_JS_SEC', settings.amap_js_sec)] if not value],
            'validator_diagnostic': validator.diagnostic, 'constraint_diagnostic': constraint_planner.diagnostic,
            'tasks': {'active': _active_tasks + active_constraint_previews() + active_booking_previews() + active_conversations(),
                      'constraint_active': active_constraint_previews(), 'booking_active': active_booking_previews(),
                      'conversation_active': active_conversations(), **_metrics},
            'build_id': BUILD_ID, 'started_at': STARTED_AT,
            'rate_limit': {'qps_per_endpoint': settings.amap_qps, 'scope': 'server_process', 'max_retries': 2},
            'last_failure': _last_failure}


@app.get('/api/map-config')
async def map_config():
    # The JS API key is public by design. The signing and LLM keys are never returned.
    return {'key': settings.amap_web_key, 'security_proxy': '/_AMapService'}


@app.get('/api/cities')
async def city_suggestions(query: str = Query(default='', max_length=40)):
    try:
        return await city_catalog.search(query)
    except RuntimeError:
        raise HTTPException(503, '城市建议暂不可用，可直接输入城市名称') from None


@app.get('/api/map-region')
async def map_region(city: str = Query(..., min_length=1, max_length=40)):
    if not city.strip():
        raise HTTPException(422, '请填写目的地行政区名称')
    return await resolve_map_region(city.strip())


@app.get('/_AMapService/{path:path}')
async def map_proxy(path: str, request: Request):
    import httpx
    # Only the documented map SDK service prefixes. Never accept a target URL.
    if not (path.startswith('v4/map/') or path.startswith('v3/')) or '..' in path or ':' in path:
        raise HTTPException(400, '不支持的地图路径')
    if not settings.amap_web_key or not settings.amap_js_sec:
        raise HTTPException(503, '地图 Web Key 或安全密钥未配置')
    params = [(k, v) for k, v in request.query_params.multi_items() if k not in ('jscode', 'key')]
    params.extend([('key', settings.amap_web_key), ('jscode', settings.amap_js_sec)])
    async with httpx.AsyncClient(timeout=15, trust_env=False) as client:
        upstream = 'https://webapi.amap.com/' if path == 'v4/map/styles' else 'https://restapi.amap.com/'
        result = await client.get(upstream + path, params=params)
    from fastapi.responses import Response
    return Response(result.content, status_code=result.status_code,
                    media_type=result.headers.get('content-type', 'application/json'))


@app.get('/api/plans')
async def plans():
    return store.list()


@app.get('/api/plans/{plan_id}')
async def get_plan(plan_id):
    plan = store.get(plan_id)
    if not plan:
        raise HTTPException(404, '没有找到这份行程；关联行程已删除的对话请新建会话')
    return plan


@app.get('/api/plans/{plan_id}/preparation')
async def preparation(plan_id: str):
    plan = await get_plan(plan_id)
    if plan.request.study:
        raise HTTPException(422, '历史研学使用独立的资料与研学准备流程')
    from .preparation import preparation_context
    return preparation_context(plan)


@app.post('/api/plans/{plan_id}/validate')
async def validate_plan(plan_id):
    plan = await get_plan(plan_id)
    return await validator.validate(plan.request, plan.itinerary, plan.routes, plan.study_context)


@app.post('/api/plans/{plan_id}/weather')
async def retry_weather(plan_id: str, city: str = Query(..., min_length=1, max_length=40)):
    plan = await get_plan(plan_id)
    selected = next((name for name in plan.request.city_route if normalize_city(name) == normalize_city(city)), None)
    if selected is None:
        raise HTTPException(422, '只能重新查询本行程目的地城市的天气')
    trip = plan.request.model_copy(update={'destination': selected, 'destinations': []})
    amap = Amap(selected)
    try:
        # A display-only result: do not rewrite the weather used by this plan's Agent.
        return await weather_service.forecast(trip, amap, force=True)
    finally:
        await amap.close()


@app.get('/api/plans/{plan_id}/place-guides')
async def place_guides(plan_id: str):
    await get_plan(plan_id)
    return guide_store.list(plan_id)


@app.post('/api/plans/{plan_id}/stops/{stop_id}/guide')
async def place_guide(plan_id: str, stop_id: str, body: GuideQuery, request: Request):
    plan = await get_plan(plan_id)
    selected = [(day.date, stop) for day in plan.itinerary.days for stop in day.stops if stop.id == stop_id]
    if len(selected) != 1:
        raise HTTPException(404, '没有找到这份行程中的唯一地点')
    visit_date, stop = selected[0]
    if stop.kind != 'sight' or not stop.poi_id or stop.location_status != 'verified':
        raise HTTPException(422, '出行参考目前支持已核实地点的景点活动')
    saved = guide_store.get(plan_id, stop_id)
    if saved and not body.refresh:
        return saved
    key = (plan_id, stop_id)
    if key in guide_service.active:
        raise HTTPException(409, '这个地点正在查询，请等待完成')
    guide_service.active.add(key)
    task = asyncio.create_task(guide_service.generate(plan, stop, visit_date))
    try:
        while not task.done():
            await asyncio.wait({task}, timeout=.25)
            if await request.is_disconnected():
                raise HTTPException(499, '出行参考查询已取消')
        result = await task
        if await request.is_disconnected():
            raise HTTPException(499, '出行参考查询已取消')
        guide_store.save(result)
        return result
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        guide_service.active.discard(key)


@app.get('/api/study/catalog')
async def study_catalog():
    return knowledge.catalog()


@app.post('/api/study/ask')
async def study_ask(body: StudyQuestion, request: Request):
    plan = await get_plan(body.plan_id) if body.plan_id else None
    if body.place_id and body.place_id not in plan_places(plan):
        raise HTTPException(422, '请提供包含这个地点的行程版本')
    if body.conversation_id:
        return await conversation_qa(body, request, plan)
    if not knowledge.ready:
        raise HTTPException(503, knowledge.message)
    task = asyncio.create_task(answer_study(body, knowledge, plan))
    try:
        async with asyncio.timeout(90):
            while not task.done():
                await asyncio.wait({task}, timeout=.25)
                if await request.is_disconnected():
                    raise HTTPException(499, '资料问答已取消')
            return await task
    except TimeoutError:
        raise HTTPException(504, '资料问答超时，请稍后重试') from None
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, settings.safe_error(error)) from None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@app.post('/api/study/photo-ask')
async def study_photo_ask(request: Request):
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > 4_100_000:
            raise HTTPException(413, '照片过大，请选择3MB以内的PNG或JPG图片')
        chunks.append(chunk)
    try:
        body = PhotoQuestion.model_validate_json(b''.join(chunks))
        data = base64.b64decode(body.image_base64, validate=True)
        if not data or len(data) > 3_000_000:
            raise ValueError('size')
        image_type(data)
    except (ValueError, binascii.Error):
        raise HTTPException(422, '请提供有效的问题和3MB以内的PNG或JPG照片') from None
    plan = await get_plan(body.plan_id) if body.plan_id else None
    places = plan_places(plan)
    if body.place_id and body.place_id not in places:
        raise HTTPException(422, '请提供包含这个地点的行程版本')
    stop = places[body.place_id].name if body.place_id else body.stop_name.strip()
    if body.conversation_id:
        return await conversation_qa(body, request, plan, data)
    task = asyncio.create_task(answer_photo(data, stop, body.question, knowledge))
    try:
        async with asyncio.timeout(120):
            while not task.done():
                await asyncio.wait({task}, timeout=.25)
                if await request.is_disconnected():
                    raise HTTPException(499, '照片讲解已取消')
            return await task
    except TimeoutError:
        raise HTTPException(504, '照片讲解超时，请稍后重试') from None
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(503, settings.safe_error(error)) from None
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@app.post('/api/study/conversations')
async def create_conversation(body: ConversationCreate):
    if body.plan_id:
        plan = await get_plan(body.plan_id)
        if not plan.request.study:
            raise HTTPException(422, '请选择一份历史研学行程')
    return conversations.create(body.plan_id)


@app.get('/api/study/conversations/{conversation_id}')
async def get_conversation(conversation_id: str):
    return conversations.get(conversation_id)


@app.delete('/api/study/conversations/{conversation_id}', status_code=204)
async def delete_conversation(conversation_id: str):
    conversations.delete(conversation_id)
    return Response(status_code=204)


async def conversation_qa(body: StudyQuestion, request: Request, plan, data: bytes | None = None):
    session, fingerprint, completed = conversations.prepare(body)
    if completed is not None:
        return completed
    task = None
    started = time.monotonic()
    try:
        memory = context_for(session['turns'], body.use_photo_context)
        photo_path = data is not None or bool(memory.get('photo_observation'))
        kind = 'photo' if data is not None else 'photo_followup' if photo_path else 'text'
        selected = plan_places(plan).get(body.place_id)
        place = selected.name if selected else body.stop_name.strip() if isinstance(body, PhotoQuestion) else ''
        state, usage = {}, UsageCollector()
        if photo_path:
            task = asyncio.create_task(answer_photo(data, place or memory.get('photo_place', ''), body.question,
                knowledge, memory=memory, state=state,
                cached_sources=conversations.web_cache(body.conversation_id) if data is None else None))
        else:
            if not knowledge.ready:
                raise HTTPException(503, knowledge.message)
            task = asyncio.create_task(answer_study(body, knowledge, plan, memory=memory, usage=usage))
        async with asyncio.timeout(120 if photo_path else 90):
            while not task.done():
                await asyncio.wait({task}, timeout=.25)
                if await request.is_disconnected():
                    raise HTTPException(499, '本次问答已取消')
            answer = await task
            if await request.is_disconnected():
                raise HTTPException(499, '本次问答已取消')
        result = answer if isinstance(answer, dict) else answer.model_dump(mode='json')
        accounting = result.get('usage', usage.result())
        if accounting.get('usage_missing_calls'):
            accounting = {**accounting, 'reported_total_tokens': accounting.get('reported_total_tokens', accounting.get('total_tokens')),
                          'total_tokens': None}
        result['usage'] = {'search_calls': 0, 'fetch_calls': 0, 'web_cache_hits': 0, 'vision_calls': 0,
            **accounting, 'history_turns': len(memory.get('recent_turns', [])),
            'memory_chars': len(encoded(memory)) if memory else 0, 'seconds': round(time.monotonic() - started, 2)}
        photo_place = place if data is not None else memory.get('photo_place', '')
        saved = conversations.commit(body, fingerprint, kind, place, result,
            turn_memory(result, memory, photo_path, state.get('observation'), photo_place))
        if photo_path:
            conversations.cache_web(body.conversation_id, state.get('sources', []))
        return saved
    except TimeoutError:
        raise HTTPException(504, '问答超时，请稍后重试') from None
    except (HTTPException, ConversationError):
        raise
    except Exception as error:
        raise HTTPException(503, settings.safe_error(error)) from None
    finally:
        try:
            if task is not None:
                if not task.done():
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        finally:
            conversations.release(body.conversation_id)


def stream_plan(request: Request, trip: TripRequest, previous=None, instruction='', refresh=False, day_index=None, study_patch=None):
    if len(trip.city_route) > 5:
        raise HTTPException(422, '新规划最多 5 个目的地；旧的六城方案可查看，请调整城市后重新生成。')
    async def events():
        queue = asyncio.Queue(maxsize=256)

        async def emit(kind, data):
            await queue.put((kind, data))

        async def work():
            global _active_tasks, _last_failure
            if _active_tasks >= 2:
                await emit('error', {'message': '已有两个规划任务运行，请等待完成后重试。'})
                try:
                    queue.put_nowait(('done', {}))
                except asyncio.QueueFull:
                    pass
                return
            _active_tasks += 1
            started = monotonic()
            # Tickets typed on the first form may start earlier than the form's
            # "first day from" clock; the plan must begin at the real time.
            normalize_booking_start_time(trip)
            amap = Amap(trip.journey_cities, emit)
            rail = Railway(trip, emit)
            stage = 'refresh' if refresh else 'agent'
            try:
                async with asyncio.timeout(180):
                    effective_trip, study_context = trip, None
                    if trip.study and previous:
                        effective_trip = await update_study_request(trip, instruction, study_patch, knowledge, previous)
                    if refresh:
                        weather, weather_by_city = previous.weather, previous.weather_by_city
                    else:
                        await emit('status', {'message': '正在查询出行城市的天气'})
                        weather, weather_by_city = await forecast_trip(effective_trip, amap, weather_service)
                        values = weather_by_city or {effective_trip.destination: weather}
                        for city, value in values.items():
                            await emit('tool', {'name': 'get_weather', 'arguments': {'city': city, 'start_date': str(trip.start_date), 'days': trip.days}, 'summary': value.message})
                    if trip.study:
                        itinerary, routes, study_context, report = await generate_study_plan(
                            effective_trip, knowledge, amap, emit, validator, previous, instruction, weather=weather)
                        adjustments, baseline, same_activities = [], {}, False
                    else:
                        itinerary = previous.itinerary.model_copy(deep=True) if refresh else await generate_itinerary(trip, amap, emit, previous, instruction, rail=rail, weather=weather, weather_by_city=weather_by_city)
                        if not refresh:
                            # Models cannot declare that user bookings or payments were verified.
                            for generated_day in itinerary.days:
                                for generated_stop in generated_day.stops:
                                    generated_stop.booking_anchor = generated_stop.booking_ref = generated_stop.booking_address = ''
                                for generated_expense in generated_day.expenses:
                                    generated_expense.booking_ref = ''
                                    generated_expense.commitment = 'planned'
                            for generated_transfer in itinerary.transfers:
                                generated_transfer.booking_status = 'unconfirmed'
                                if generated_transfer.price_status == 'user_reported':
                                    generated_transfer.price_status = 'estimated'
                        # A “返回酒店” stop repeats the route's travel time. A partial
                        # day refresh keeps other days untouched, so only clean there
                        # when the whole route is being recomputed.
                        moved = (drop_return_movement_stops(itinerary)
                                 if not refresh or day_index is None else [])
                        if previous and day_index is not None:
                            for index in range(len(itinerary.days)):
                                if index != day_index:
                                    itinerary.days[index] = previous.itinerary.days[index].model_copy(deep=True)
                            itinerary.transfers = [t.model_copy(deep=True) for t in previous.itinerary.transfers]
                        if not refresh and day_index is None:
                            await rail.enrich(itinerary)
                        if trip.booked_transports:
                            apply_booked_transports(itinerary, trip)
                        # Model/queried legs yield to the day; user tickets never move.
                        eased = ease_conflicting_transfers(itinerary, trip)
                        itinerary.guide_insights = [i for i in itinerary.guide_insights if i.source_index < len(trip.guides)]
                        prepare_transfer_anchors(itinerary, trip, prefer_user_bookings=bool(trip.booked_transports))
                        prepare_accommodation(itinerary, trip, day_index)
                        moved += drop_movement_to_anchors(itinerary)
                        flow_notices = (prepare_arrival_flow(trip, itinerary, day_index, instruction)
                                        if not refresh else [])
                        if previous and day_index is not None:
                            # These helpers rebuild anchors globally. Other days are
                            # outside this map query and must retain their verified
                            # POIs, booking evidence, expenses and existing routes.
                            for index in range(len(itinerary.days)):
                                if index != day_index:
                                    itinerary.days[index] = previous.itinerary.days[index].model_copy(deep=True)
                        stage = 'map_enrichment'
                        routes = await amap.enrich(itinerary, trip.mode, compare_modes=trip.compare_modes,
                                                   day_indices={day_index} if day_index is not None else None,
                                                   optimize_meals=True, protected_text=f'{trip.preferences} {instruction} ' + (' '.join(g.text for g in trip.guides) if trip.guide_mode == 'prefer' else ''), enrich_details=True, people=trip.people)
                        if previous and day_index is not None:
                            routes.extend(r.model_copy(deep=True) for r in previous.routes if r.day_index != day_index)
                            routes.sort(key=lambda r: r.day_index)
                        if refresh:
                            for route in routes:
                                old = next((r for r in previous.routes if r.day_index == route.day_index
                                            and r.from_id == route.from_id and r.to_id == route.to_id), None)
                                option = next((o for o in route.alternatives if old and old.user_selected and o.mode == old.mode and o.available), None)
                                if option:
                                    for key, value in option.model_dump().items():
                                        setattr(route, key, value)
                                    route.user_selected = True
                                    route.selection_reason = '保留你手动选择的交通方式；本次已重新查询路线。'
                        recost_all(trip, itinerary, routes, day_index)
                        cost_adjustments = normalize_ticket_quantities(trip, itinerary, day_index) if not refresh else []
                        bind_stop_expenses(itinerary, day_index)
                        audit_guides(trip, itinerary)
                        baseline = {s.id: (s.start, s.end) for d in itinerary.days for s in d.stops}
                        same_activities = refresh and [
                            (d.date, [(s.id, s.name, s.kind, s.city, s.poi_id) for s in d.stops]) for d in itinerary.days
                        ] == [(d.date, [(s.id, s.name, s.kind, s.city, s.poi_id) for s in d.stops]) for d in previous.itinerary.days]
                        if same_activities and previous.schedule_baseline:
                            baseline = previous.schedule_baseline.copy()
                            for day in itinerary.days:
                                for stop in day.stops:
                                    if stop.id in baseline:
                                        stop.start, stop.end = baseline[stop.id]
                        if trip.booked_transports or trip.confirmed_hotels or any(s.booking_anchor.startswith('transport_') for d in itinerary.days for s in d.stops):
                            from types import SimpleNamespace
                            shell = SimpleNamespace(request=trip, itinerary=itinerary, routes=routes)
                            scope = {day_index} if day_index is not None else None
                            conflicts, adjustments = repair_booking_schedule(shell, only_days=scope)
                            trimmed = []
                            for _ in range(8):
                                if not conflicts:
                                    break
                                removed = []
                                for message in conflicts:
                                    found = re.match(r'第(\d+)天', message)
                                    if not found:
                                        continue
                                    number = int(found.group(1)) - 1
                                    if not 0 <= number < len(itinerary.days):
                                        continue
                                    dropped = drop_last_optional_stop(itinerary, number)
                                    if dropped:
                                        removed.append(dropped)
                                if not removed:
                                    break
                                trimmed += removed
                                conflicts, adjustments = repair_booking_schedule(shell, only_days=scope)
                            if conflicts:
                                raise ValueError('加入车站／机场接驳后无法排出可执行日程：' + '；'.join(conflicts) + '。请减少活动或调整时间；未保存本次方案，已有版本保留。')
                            if trimmed:
                                adjustments += ['当天时间不足，已移除模型建议的活动：'
                                                + '、'.join(dict.fromkeys(trimmed))
                                                + '；可在“调整行程”里另选时间补回']
                            # Fixed anchors also need the meal windows repaired.
                            adjustments += align_meals(trip, itinerary, routes, day_index,
                                f'{trip.preferences} {instruction}', respect_opening=True)
                            adjustments = cost_adjustments + adjustments
                        else:
                            adjustments = cost_adjustments + repair_schedule(itinerary, routes, trip.start_time, only_day=day_index)
                            adjustments += align_meals(trip, itinerary, routes, day_index, f'{trip.preferences} {instruction}', respect_opening=True)
                        meal_conflicts, meal_notices = meal_schedule_checks(trip, itinerary, routes, day_index,
                            f'{trip.preferences} {instruction}')
                        if meal_conflicts:
                            # A late or possibly closed meal is a quality warning, not a
                            # reason to discard a whole generated plan; it stays visible.
                            meal_notices += [f'用餐安排待调整：{item}' for item in meal_conflicts]
                        adjustments += [f'已移除模型生成的「{name}」移动活动；'
                                        '返程时间改由路线与酒店节点计算，不重复计时' for name in moved]
                        adjustments += [f'跨城班次调整：{item}' for item in eased]
                        adjustments += flow_notices + meal_notices
                        await emit('status', {'message': '正在校验时间、真实通行时长和全员预算'})
                        stage = 'validation'
                        report = await validator.validate(trip, itinerary, routes)
                        temporal_errors = [item for item in report.errors if item.code in {
                            'invalid_duration', 'time_overlap', 'transfer_time', 'before_start',
                            'intercity_overlap', 'intercity_activity_overlap',
                            'booking_departure_buffer', 'booking_arrival_buffer'}]
                        if temporal_errors:
                            raise ValueError('行程仍有时间冲突，无法当天完成可执行安排：' +
                                '；'.join(dict.fromkeys(item.message for item in temporal_errors)) +
                                '。请减少活动或调整交通／活动时间；未保存本次方案，已有版本保留。')
                    plan = PlanRecord(id=str(uuid4()), parent_id=previous.id if previous else None,
                        revision=previous.revision + 1 if previous else 1, created_at=datetime.now(timezone.utc),
                        request=effective_trip, itinerary=itinerary, routes=routes, validation=report, revision_instruction=instruction,
                        adjustments=adjustments, schedule_baseline=baseline, study_context=study_context,
                        weather=weather, weather_by_city=weather_by_city)
                    stage = 'storage'
                    if same_activities:
                        plan.id, plan.parent_id = previous.id, previous.parent_id
                        plan.revision, plan.created_at = previous.revision, previous.created_at
                        plan.revision_instruction = previous.revision_instruction
                        plan.routing_revision = previous.routing_revision + 1
                        store.update_routes(plan, previous.routing_revision)
                    else:
                        store.save(plan)
                    _metrics['completed'] += 1
                    await emit('result', plan.model_dump(mode='json'))
            except asyncio.TimeoutError:
                _metrics['failed'] += 1
                _last_failure = {'stage': stage, 'type': 'TimeoutError', 'elapsed_s': round(monotonic() - started, 2)}
                await emit('error', {'message': '本次规划超过 3 分钟，请减少天数后重试。旧方案仍然保留。'})
            except asyncio.CancelledError:
                _metrics['cancelled'] += 1
                raise
            except Exception as error:
                _metrics['failed'] += 1
                _last_failure = {'stage': stage, 'type': type(error).__name__,
                    'provider_code': getattr(error, 'provider_code', ''), 'path': getattr(error, 'path', ''),
                    'category': getattr(error, 'code', ''), 'elapsed_s': round(monotonic() - started, 2)}
                logging.getLogger(__name__).warning('planning_failed type=%s category=%s provider_code=%s path=%s',
                    type(error).__name__, getattr(error, 'code', ''), getattr(error, 'provider_code', ''), getattr(error, 'path', ''))
                await emit('error', {'message': settings.safe_error(error)})
            finally:
                _active_tasks -= 1
                _metrics['last_duration_s'] = round(monotonic() - started, 2)
                await asyncio.gather(rail.close(), amap.close(), return_exceptions=True)
                try:
                    queue.put_nowait(('done', {}))
                except asyncio.QueueFull:
                    pass

        task = asyncio.create_task(work())
        try:
            yield 'event: status\ndata: ' + json.dumps({'message': '已收到旅行需求，开始规划'}, ensure_ascii=False) + '\n\n'
            while True:
                if task.done() and queue.empty():
                    break
                if await request.is_disconnected():
                    break
                try:
                    kind, data = await asyncio.wait_for(queue.get(), timeout=1)
                except asyncio.TimeoutError:
                    yield ': keep-alive\n\n'
                    continue
                if kind == 'done':
                    break
                yield 'event: ' + kind + '\ndata: ' + json.dumps(data, ensure_ascii=False) + '\n\n'
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(events(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.post('/api/plans/generate')
async def generate(trip: TripRequest, request: Request):
    if trip.study and not knowledge.ready:
        raise HTTPException(503, knowledge.message)
    if trip.start_date < datetime.now(timezone(timedelta(hours=8))).date():
        raise HTTPException(422, '新行程不能从过去日期开始，请更新出行日期')
    return stream_plan(request, trip)


@app.post('/api/plans/{plan_id}/revise')
async def revise(plan_id: str, body: RevisionRequest, request: Request):
    previous = await get_plan(plan_id)
    protect_confirmed_constraints(previous)
    trip = body.trip or previous.request
    if bool(trip.study) != bool(previous.request.study):
        raise HTTPException(422, '切换普通旅行与研学请重新生成')
    if body.study_patch and not previous.request.study:
        raise HTTPException(422, '普通旅行不接受研学设置，请从研学入口重新规划')
    if trip.study:
        if not knowledge.ready:
            raise HTTPException(503, knowledge.message)
        if body.day_index is not None:
            raise HTTPException(422, '研学为单日行程，请使用整体调整')
    if trip.start_date < datetime.now(timezone(timedelta(hours=8))).date():
        raise HTTPException(422, '修改历史行程时请先将出行日期更新为今天或未来日期')
    if trip.destination != previous.request.destination:
        raise HTTPException(422, '更换目的地请重新生成行程')
    if body.day_index is not None and (trip.days != previous.request.days or trip.start_date != previous.request.start_date
                                       or body.day_index >= trip.days):
        raise HTTPException(422, '局部修改需保持日期与天数不变，且指定日期必须存在')
    return stream_plan(request, trip, previous, body.instruction, day_index=body.day_index, study_patch=body.study_patch)


@app.post('/api/plans/{plan_id}/refresh')
async def refresh_plan(plan_id: str, request: Request):
    previous = await get_plan(plan_id)
    protect_confirmed_constraints(previous)
    if previous.request.study:
        raise HTTPException(422, '研学请通过修改重新核对原文和路线，不能直接刷新地点')
    trip = previous.request.model_copy(update={'mode': 'auto', 'compare_modes': True})
    return stream_plan(request, trip, previous, '刷新地点与路线，不调用模型', refresh=True)


@app.post('/api/plans/{plan_id}/route')
async def select_route(plan_id: str, body: RouteSelection):
    previous = await get_plan(plan_id)
    protect_confirmed_constraints(previous)
    if body.expected_routing_revision is not None and body.expected_routing_revision != previous.routing_revision:
        raise HTTPException(409, '行程已更新，请重新打开后选择交通方式')
    if previous.request.study:
        raise HTTPException(422, '研学请使用步行或驾车重新调整，以核对结束时间与步行上限')
    plan = previous.model_copy(deep=True)
    route = next((r for r in plan.routes if r.day_index == body.day_index and r.from_id == body.from_id and r.to_id == body.to_id), None)
    if route is None:
        raise HTTPException(404, '没有找到该段路线')
    option = next((o for o in route.alternatives if o.mode == body.mode and o.available), None)
    if option is None:
        raise HTTPException(422, '该交通方式没有已查询的可用路线，请先刷新路线')
    for key, value in option.model_dump().items():
        setattr(route, key, value)
    route.user_selected = True
    route.selection_reason = '已按你的选择保存到当前行程；后续刷新会保留该选择（路线仍可用时）。'
    if not plan.schedule_baseline:
        plan.schedule_baseline = {s.id: (s.start, s.end) for d in plan.itinerary.days for s in d.stops}
    for stop in plan.itinerary.days[body.day_index].stops:
        if stop.id in plan.schedule_baseline:
            stop.start, stop.end = plan.schedule_baseline[stop.id]
    try:
        recost_all(plan.request, plan.itinerary, plan.routes, body.day_index)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    plan.adjustments = repair_schedule(plan.itinerary, plan.routes, plan.request.start_time, only_day=body.day_index)
    plan.adjustments += align_meals(plan.request, plan.itinerary, plan.routes, body.day_index, plan.request.preferences)
    plan.validation = await validator.validate(plan.request, plan.itinerary, plan.routes)
    plan.routing_revision = previous.routing_revision + 1
    # Revalidate to recompute authoritative expense category totals before storage.
    plan = PlanRecord.model_validate(plan.model_dump())
    try:
        store.update_routes(plan, previous.routing_revision)
    except ValueError as error:
        raise HTTPException(409, str(error)) from None
    return plan


@app.delete('/api/plans/{plan_id}')
async def delete_plan(plan_id: str):
    plan = await get_plan(plan_id)
    protect_confirmed_constraints(plan)
    store.delete(plan_id)
    return {'deleted': True}


dist = ROOT / 'frontend' / 'dist'
if (dist / 'assets').exists():
    app.mount('/assets', StaticFiles(directory=dist / 'assets'), name='assets')


@app.get('/favicon.svg', include_in_schema=False)
async def favicon():
    return FileResponse(ROOT / 'frontend' / 'public' / 'favicon.svg')


@app.get('/', include_in_schema=False)
async def index():
    if not (dist / 'index.html').exists():
        raise HTTPException(503, '请先运行 setup.ps1 构建前端')
    # Rebuilds must reach the browser: a cached shell keeps calling the old bundle.
    return FileResponse(dist / 'index.html', headers={'Cache-Control': 'no-cache, must-revalidate'})


if __name__ == '__main__':
    import uvicorn
    import socket
    # Check before lifespan writes the PID, so a duplicate launch preserves it.
    with socket.socket() as probe:
        try:
            probe.bind(('127.0.0.1', settings.port))
        except OSError:
            raise SystemExit(f'Port {settings.port} is already in use. Open the running app or use stop.bat first.')
    os.environ['TRAVEL_SERVER_PROCESS'] = '1'
    print(f'Travel Assistant: http://127.0.0.1:{settings.port} (Ctrl+C to stop)', flush=True)
    uvicorn.run(app, host='127.0.0.1', port=settings.port, log_level='warning', access_log=False)
