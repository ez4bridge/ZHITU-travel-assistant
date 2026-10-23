from fastapi import HTTPException
from .constraint_models import ConstraintApplyRequest
from .constraint_storage import ConstraintStale, ConstraintStore


def active_constraint_previews():
    return 0


def install_constraint_routes(app, store_getter):
    def load(plan_id):
        store = store_getter()
        plan = store.get(plan_id)
        if not plan:
            raise HTTPException(404, '没有找到这份行程')
        if plan.request.study:
            raise HTTPException(422, '历史研学请使用研学调整入口，以保留原文引用与现场任务')
        return ConstraintStore(store.path)

    async def retired(plan_id: str):
        load(plan_id)
        raise HTTPException(410, '旧约束重排接口已停用，请刷新页面使用 AI 调整行程')

    for path, method in [('constraint-context','GET'), ('constraint-intent','POST'), ('constraint-preview','POST'), ('conversation-preview','POST')]:
        app.add_api_route('/api/plans/{plan_id}/' + path, retired, methods=[method])

    @app.post('/api/plans/{plan_id}/constraint-apply')
    async def apply_preview(plan_id: str, body: ConstraintApplyRequest):
        storage = load(plan_id)
        try:
            return {'plan': storage.apply(plan_id, body.preview_id)}
        except ConstraintStale as error:
            raise HTTPException(409, str(error)) from None
