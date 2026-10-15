import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import settings, ROOT
from .models import TripRequest, PlanRecord, RevisionRequest
from .amap import Amap
from .agent import generate_itinerary
from .storage import Store
from .validation import validator

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


@app.exception_handler(Exception)
async def safe_exception(request, error):
    return JSONResponse(status_code=500, content={'detail': settings.safe_error(error)})


@app.get('/api/health')
async def health():
    ready = await validator.ready()
    return {'app_id': 'travel-assistant', 'status': 'ok', 'model': settings.llm_model,
            'capabilities': {'planning': bool(settings.llm_key and settings.amap_key), 'map': bool(settings.amap_web_key and settings.amap_js_sec),
                             'routing': bool(settings.amap_key), 'validator': 'cangjie' if ready else 'python'},
            'missing': [name for name, value in [('LLM_API_KEY', settings.llm_key), ('AMAP_API_KEY', settings.amap_key),
                       ('AMAP_WEB_KEY', settings.amap_web_key), ('AMAP_JS_SEC', settings.amap_js_sec)] if not value],
            'validator_diagnostic': validator.diagnostic}


@app.get('/api/map-config')
async def map_config():
    # The JS API key is public by design. The signing and LLM keys are never returned.
    return {'key': settings.amap_web_key, 'security_proxy': '/_AMapService'}


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


def stream_plan(request: Request, trip: TripRequest, previous=None, instruction=''):
    async def events():
        queue = asyncio.Queue()

        async def emit(kind, data):
            await queue.put((kind, data))

        async def work():
            amap = Amap(trip.destination, emit)
            try:
                async with asyncio.timeout(180):
                    itinerary = await generate_itinerary(trip, amap, emit, previous, instruction)
                    routes = await amap.enrich(itinerary, trip.mode)
                    await emit('status', {'message': '正在校验时间、真实通行时长和全员预算'})
                    report = await validator.validate(trip, itinerary, routes)
                    plan = PlanRecord(id=str(uuid4()), parent_id=previous.id if previous else None,
                        revision=previous.revision + 1 if previous else 1, created_at=datetime.now(timezone.utc),
                        request=trip, itinerary=itinerary, routes=routes, validation=report, revision_instruction=instruction)
                    store.save(plan)
                    await emit('result', plan.model_dump(mode='json'))
            except asyncio.TimeoutError:
                await emit('error', {'message': '本次规划超过 3 分钟，请减少天数后重试。旧方案仍然保留。'})
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await emit('error', {'message': settings.safe_error(error)})
            finally:
                await amap.close()
                await queue.put(('done', {}))

        task = asyncio.create_task(work())
        try:
            yield 'event: status\ndata: ' + json.dumps({'message': '已收到旅行需求，开始规划'}, ensure_ascii=False) + '\n\n'
            while True:
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
    return stream_plan(request, trip)


@app.post('/api/plans/{plan_id}/revise')
async def revise(plan_id: str, body: RevisionRequest, request: Request):
    previous = await get_plan(plan_id)
    return stream_plan(request, previous.request, previous, body.instruction)


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
