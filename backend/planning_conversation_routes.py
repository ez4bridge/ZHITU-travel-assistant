import asyncio

from fastapi import HTTPException, Request

from .planning_conversation import ConversationRequest, conversation_preview
from .constraint_storage import ConstraintStore, ConstraintStale

_active = 0


def active_conversations():
    return _active


def install_conversation_routes(app, store_getter):
    @app.post('/api/plans/{plan_id}/conversation-preview')
    async def preview_conversation(plan_id: str, body: ConversationRequest, request: Request):
        global _active
        if _active >= 2:
            raise HTTPException(429, '已有两个对话规划正在运行，请稍后再试')
        store = store_getter()
        plan = store.get(plan_id)
        if plan is None:
            raise HTTPException(404, '没有找到这份行程')
        if plan.request.study:
            raise HTTPException(422, '历史研学请使用研学调整入口，以保留原文引用与现场任务')
        storage = ConstraintStore(store.path)
        state = storage.state(plan)
        _active += 1
        tasks = []
        try:
            async with asyncio.timeout(120):
                work = asyncio.create_task(conversation_preview(plan, state, body, storage))
                async def disconnected():
                    while not asyncio.current_task().cancelling():
                        if await request.is_disconnected():
                            return
                        if asyncio.current_task().cancelling():
                            return
                        await asyncio.sleep(.2)
                watch = asyncio.create_task(disconnected())
                tasks = [work, watch]
                done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                if work in done:
                    return await work
                raise HTTPException(499, '对话规划已取消；原方案保留')
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
        except (RuntimeError, TimeoutError):
            raise HTTPException(503, '本次模型、地点查询或仓颉规划未完成；未修改原方案，请稍后重试') from None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            _active -= 1
