"""Additive ordinary JSON routes; getters preserve existing test injection."""
import asyncio

from fastapi import HTTPException, Request

from . import constraint_replan
from .constraint_models import ConstraintApplyRequest, ConstraintPreviewRequest, ConstraintIntentRequest
from .constraint_storage import ConstraintStale, ConstraintStore, fingerprint

_active_previews = 0


def active_constraint_previews():
    return _active_previews


def install_constraint_routes(app, store_getter):
    def load(plan_id):
        store = store_getter()
        plan = store.get(plan_id)
        if not plan:
            raise HTTPException(404, '没有找到这份行程')
        if plan.request.study:
            raise HTTPException(422, '历史研学请使用研学调整入口，以保留原文引用与现场任务')
        storage = ConstraintStore(store.path)
        return plan, storage, storage.state(plan)

    @app.get('/api/plans/{plan_id}/constraint-context')
    async def constraint_context(plan_id: str):
        plan, storage, state = load(plan_id)
        return await constraint_replan.context(plan, state)

    @app.post('/api/plans/{plan_id}/constraint-intent')
    async def constraint_intent(plan_id: str, body: ConstraintIntentRequest):
        plan, storage, state = load(plan_id)
        try:
            if (fingerprint(plan) != body.base_fingerprint or plan.routing_revision != body.expected_routing_revision
                    or state['state_revision'] != body.expected_state_revision or state['current_plan_id'] != plan.id):
                raise ConstraintStale('行程或已确认事实已变化，请刷新最新版本后重新解析')
            if body.day_indexes[-1] >= len(plan.itinerary.days):
                raise ValueError('调整日期不属于此行程')
            from .constraint_intent import parse_intent
            async with asyncio.timeout(25):
                return await parse_intent(plan, state, body)
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except (RuntimeError, TimeoutError):
            raise HTTPException(503, '本次需求暂未解析成功，请保留原文或直接使用下面的具体选项；行程没有修改') from None

    @app.post('/api/plans/{plan_id}/constraint-preview')
    async def constraint_preview(plan_id: str, body: ConstraintPreviewRequest, request: Request):
        global _active_previews
        if _active_previews >= 2:
            raise HTTPException(429, '已有两个约束预览运行，请等待或取消后重试')
        plan, storage, state = load(plan_id)
        _active_previews += 1
        tasks = []
        try:
            async with asyncio.timeout(90):
                work = asyncio.create_task(constraint_replan.preview(plan, state, body, storage))
                async def disconnected():
                    # Starlette's probe uses an AnyIO CancelScope, which can consume
                    # cancellation delivered during receive(). Check the task's
                    # cancellation count as well so successful previews can finish.
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
                raise HTTPException(499, '预览已取消，原行程与确认事实保持')
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except (RuntimeError, TimeoutError):
            raise HTTPException(503, '仓颉重规划或有界路线查询暂不可用；旧行程未修改，请稍后重试') from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            _active_previews -= 1

    @app.post('/api/plans/{plan_id}/constraint-apply')
    async def constraint_apply(plan_id: str, body: ConstraintApplyRequest):
        plan, storage, state = load(plan_id)
        try:
            return {'plan': storage.apply(plan_id, body.preview_id)}
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
