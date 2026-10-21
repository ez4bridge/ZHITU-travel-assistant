import asyncio
import json
import logging
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from time import monotonic

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .config import settings, ROOT
from .models import TripRequest, PlanRecord, RevisionRequest, RouteSelection
from .amap import Amap
from .agent import generate_itinerary
from .storage import Store
from .validation import validator
from .cities import city_catalog
from .railway import Railway
from .planning import repair_schedule, recost_all, same_activity_structure
from .locality import prepare_accommodation
from .itinerary_evidence import bind_stop_expenses, audit_guides
from .meals import align_meals
from .budget import reconcile_expenses, budget_instruction
from .build_info import source_fingerprint
from .generation_batches import task_limits

BUILD_ID = source_fingerprint()
STARTED_AT = datetime.now(timezone.utc).isoformat()

store = Store()


@asynccontextmanager
async def lifespan(app):
    pid_file = settings.runtime / 'app.pid'
    manages_pid = os.getenv('TRAVEL_SERVER_PROCESS') == '1'
    if manages_pid:
        pid_file.write_text(str(os.getpid()))
    try:
        yield
    finally:
        if manages_pid and pid_file.exists() and pid_file.read_text().strip() == str(os.getpid()):
            pid_file.unlink()


app = FastAPI(title='漫游 Travel Assistant', version='0.1.0', lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=['127.0.0.1', 'localhost', '[::1]', 'testserver', 'test'])
_active_tasks = 0
_metrics = {'completed': 0, 'failed': 0, 'cancelled': 0, 'last_duration_s': 0.0, 'last_phase_seconds': {}}
_last_failure = None


@app.middleware('http')
async def local_origin(request: Request, call_next):
    origin = request.headers.get('origin')
    if origin and urlsplit(origin).netloc != request.url.netloc:
        return JSONResponse(status_code=403, content={'detail': '请从本机应用页面访问服务'})
    return await call_next(request)


@app.exception_handler(Exception)
async def safe_exception(request, error):
    return JSONResponse(status_code=500, content={'detail': settings.safe_error(error)})


@app.get('/api/health')
async def health():
    ready = await validator.ready()
    return {'app_id': 'travel-assistant', 'status': 'ok', 'model': settings.llm_model,
            'capabilities': {'planning': bool(settings.llm_key and settings.amap_key), 'map': bool(settings.amap_web_key and settings.amap_js_sec),
                             'railway': settings.rail_mcp_enabled and settings.rail_mcp_python.is_file(),
                             'routing': bool(settings.amap_key), 'validator': 'cangjie' if ready else 'python'},
            'missing': [name for name, value in [('LLM_API_KEY', settings.llm_key), ('AMAP_API_KEY', settings.amap_key),
                       ('AMAP_WEB_KEY', settings.amap_web_key), ('AMAP_JS_SEC', settings.amap_js_sec)] if not value],
            'validator_diagnostic': validator.diagnostic, 'tasks': {'active': _active_tasks, **_metrics},
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
        raise HTTPException(404, '没有找到这份行程')
    return plan


@app.post('/api/plans/{plan_id}/validate')
async def validate_plan(plan_id):
    plan = await get_plan(plan_id)
    return await validator.validate(plan.request, plan.itinerary, plan.routes)


def stream_plan(request: Request, trip: TripRequest, previous=None, instruction='', refresh=False, day_index=None):
    if len(trip.city_route) > 5:
        raise HTTPException(422, '新规划最多 5 个目的地；旧的六城方案可查看，请调整城市后重新生成。')
    async def events():
        queue = asyncio.Queue(maxsize=256)
        progress = {'phase': ''}

        async def emit(kind, data):
            if kind == 'status' and data.get('phase'):
                progress['phase'] = data['phase']
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
            started = phase_started = monotonic()
            phase_times = {}
            def mark_phase(name):
                nonlocal phase_started
                now = monotonic()
                phase_times[name] = round(now - phase_started, 2)
                phase_started = now
            amap = Amap(trip.city_route, emit)
            rail = Railway(trip, emit)
            stage = 'refresh' if refresh else 'agent'
            limits = task_limits(trip, refresh)
            try:
                await emit('status', {'message': f'本次{trip.days}天、{len(trip.city_route)}个城市，处理上限约{(limits["total"] + 59) // 60}分钟；可随时取消'})
                async with asyncio.timeout(limits['total']):
                    async with asyncio.timeout(limits['model']):
                        itinerary = previous.itinerary.model_copy(deep=True) if refresh else await generate_itinerary(trip, amap, emit, previous, instruction, rail=rail)
                    mark_phase('model_and_research')
                    if previous and day_index is not None:
                        for index in range(len(itinerary.days)):
                            if index != day_index:
                                itinerary.days[index] = previous.itinerary.days[index].model_copy(deep=True)
                        itinerary.transfers = [t.model_copy(deep=True) for t in previous.itinerary.transfers]
                    if not refresh and day_index is None:
                        stage = 'rail_enrichment'
                        async with asyncio.timeout(limits['rail']):
                            await rail.enrich(itinerary)
                    mark_phase('rail_enrichment')
                    itinerary.guide_insights = [i for i in itinerary.guide_insights if i.source_index < len(trip.guides)]
                    prepare_accommodation(itinerary, trip, day_index)
                    stage = 'map_enrichment'
                    async with asyncio.timeout(limits['map']):
                        routes = await amap.enrich(itinerary, trip.mode, compare_modes=trip.compare_modes,
                                                   day_indices={day_index} if day_index is not None else None,
                                                   optimize_meals=True, protected_text=f'{trip.preferences} {instruction} ' + (' '.join(g.text for g in trip.guides) if trip.guide_mode == 'prefer' else ''), enrich_details=True)
                    mark_phase('map_enrichment')
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
                    bind_stop_expenses(itinerary, day_index)
                    cost_adjustments = reconcile_expenses(trip, itinerary, day_index)
                    audit_guides(trip, itinerary)
                    baseline = {s.id: (s.start, s.end) for d in itinerary.days for s in d.stops}
                    same_activities = refresh and same_activity_structure(previous.itinerary, itinerary)
                    if same_activities and previous.schedule_baseline:
                        baseline = previous.schedule_baseline.copy()
                        for day in itinerary.days:
                            for stop in day.stops:
                                if stop.id in baseline:
                                    stop.start, stop.end = baseline[stop.id]
                    adjustments = cost_adjustments + repair_schedule(itinerary, routes, trip.start_time, only_day=day_index)
                    adjustments += align_meals(trip, itinerary, routes, day_index, f'{trip.preferences} {instruction}')
                    await emit('status', {'message': '正在校验时间、真实通行时长和全员预算'})
                    stage = 'validation'
                    mark_phase('costs_and_schedule')
                    report = await validator.validate(trip, itinerary, routes)
                    mark_phase('validation')
                    plan = PlanRecord(id=str(uuid4()), parent_id=previous.id if previous else None,
                        revision=previous.revision + 1 if previous else 1, created_at=datetime.now(timezone.utc),
                        request=trip, itinerary=itinerary, routes=routes, validation=report, revision_instruction=instruction,
                        adjustments=adjustments, schedule_baseline=baseline)
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
                elapsed = round(monotonic() - started, 2)
                phase_times[f'{stage}_incomplete'] = round(monotonic() - phase_started, 2)
                _last_failure = {'stage': stage, 'type': 'TimeoutError', 'elapsed_s': elapsed,
                    'model_phase': progress['phase'] if stage == 'agent' else '', 'limits_s': limits}
                label = {'agent': '模型生成', 'rail_enrichment': '铁路查询', 'map_enrichment': '地图核实'}.get(stage, '行程处理')
                await emit('error', {'message': f'{label}达到本次处理时限（累计{int(elapsed)}秒），已停止。未保存不完整行程，已有方案保留；可稍后重试。'})
            except asyncio.CancelledError:
                _metrics['cancelled'] += 1
                raise
            except Exception as error:
                _metrics['failed'] += 1
                phase_times[f'{stage}_incomplete'] = round(monotonic() - phase_started, 2)
                _last_failure = {'stage': stage, 'type': type(error).__name__,
                    'provider_code': getattr(error, 'provider_code', ''), 'path': getattr(error, 'path', ''),
                    'category': getattr(error, 'code', ''), 'model_phase': getattr(error, 'phase', '') or progress['phase'],
                    'validation_issues': getattr(error, 'validation_issues', []), 'elapsed_s': round(monotonic() - started, 2)}
                logging.getLogger(__name__).warning('planning_failed type=%s category=%s provider_code=%s path=%s',
                    type(error).__name__, getattr(error, 'code', ''), getattr(error, 'provider_code', ''), getattr(error, 'path', ''))
                await emit('error', {'message': settings.safe_error(error)})
            finally:
                _active_tasks -= 1
                _metrics['last_duration_s'] = round(monotonic() - started, 2)
                _metrics['last_phase_seconds'] = phase_times
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
    if trip.start_date < datetime.now(timezone(timedelta(hours=8))).date():
        raise HTTPException(422, '新行程不能从过去日期开始，请更新出行日期')
    return stream_plan(request, trip)


@app.post('/api/plans/{plan_id}/revise')
async def revise(plan_id: str, body: RevisionRequest, request: Request):
    previous = await get_plan(plan_id)
    trip = body.trip or previous.request
    if trip.start_date < datetime.now(timezone(timedelta(hours=8))).date():
        raise HTTPException(422, '修改历史行程时请先将出行日期更新为今天或未来日期')
    if trip.destination != previous.request.destination:
        raise HTTPException(422, '更换目的地请重新生成行程')
    if body.day_index is not None and (trip.days != previous.request.days or trip.start_date != previous.request.start_date
                                       or body.day_index >= trip.days):
        raise HTTPException(422, '局部修改需保持日期与天数不变，且指定日期必须存在')
    instruction = body.instruction
    if previous.validation.total_cents > int(trip.budget * 100):
        instruction += '\n费用约束：' + budget_instruction(previous, trip.budget)
    return stream_plan(request, trip, previous, instruction, day_index=body.day_index)


@app.post('/api/plans/{plan_id}/refresh')
async def refresh_plan(plan_id: str, request: Request):
    previous = await get_plan(plan_id)
    trip = previous.request.model_copy(update={'mode': 'auto', 'compare_modes': True})
    return stream_plan(request, trip, previous, '刷新地点与路线，不调用模型', refresh=True)


@app.post('/api/plans/{plan_id}/route')
async def select_route(plan_id: str, body: RouteSelection):
    previous = await get_plan(plan_id)
    if body.expected_routing_revision is not None and body.expected_routing_revision != previous.routing_revision:
        raise HTTPException(409, '行程已更新，请重新打开后选择交通方式')
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
    bind_stop_expenses(plan.itinerary, body.day_index)
    cost_adjustments = reconcile_expenses(plan.request, plan.itinerary, body.day_index)
    try:
        recost_all(plan.request, plan.itinerary, plan.routes, body.day_index)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    plan.adjustments = cost_adjustments + repair_schedule(plan.itinerary, plan.routes, plan.request.start_time, only_day=body.day_index)
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


@app.post('/api/plans/{plan_id}/costs/reconcile')
async def reconcile_costs(plan_id: str):
    previous = await get_plan(plan_id)
    plan = previous.model_copy(deep=True)
    bind_stop_expenses(plan.itinerary)
    changes = reconcile_expenses(plan.request, plan.itinerary)
    recost_all(plan.request, plan.itinerary, plan.routes)
    plan.adjustments = list(dict.fromkeys([*plan.adjustments, *changes]))
    plan.validation = await validator.validate(plan.request, plan.itinerary, plan.routes)
    plan = PlanRecord.model_validate(plan.model_dump())
    if plan.model_dump(exclude={'routing_revision'}) != previous.model_dump(exclude={'routing_revision'}):
        plan.routing_revision = previous.routing_revision + 1
        try:
            store.update_routes(plan, previous.routing_revision)
        except ValueError as error:
            raise HTTPException(409, str(error)) from None
    return plan


@app.delete('/api/plans/{plan_id}')
async def delete_plan(plan_id: str):
    await get_plan(plan_id)
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
    return FileResponse(dist / 'index.html')


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
