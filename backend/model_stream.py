"""Receive model output incrementally without exposing reasoning or partial tool calls."""
import logging
from time import monotonic
from openai.types.chat import ChatCompletion


class ModelOutputError(RuntimeError):
    def __init__(self, message, phase, reason='invalid_output'):
        super().__init__(message)
        self.phase = phase
        self.reason = reason


async def complete_model(client, emit, phase, **params):
    start = last_update = monotonic()
    parts, tools = [], {}
    finish = None
    received = False
    reasoning_chars = 0
    label = '资料查询' if phase == 'research' else ('全程框架生成' if phase == 'outline' else ('第' + phase[5:].replace('_', '–') + '天细化' if phase.startswith('days_') else '行程结构生成'))
    stream = None
    try:
        await emit('status', {'message': f'模型开始{label}', 'phase': phase})
        stream = await client.chat.completions.create(stream=True, **params)
        async for chunk in stream:
            for choice in chunk.choices:
                if choice.index != 0:
                    continue
                received = True
                delta = choice.delta
                reasoning_chars += len(getattr(delta, 'reasoning_content', '') or '') + len(getattr(delta, 'reasoning', '') or '')
                if delta.content:
                    parts.append(delta.content)
                # Only assemble public content and complete function arguments.
                # The provider's reasoning fields are intentionally ignored.
                for call in delta.tool_calls or []:
                    if not 0 <= call.index < 64:
                        raise ModelOutputError('模型返回过多工具请求，请减少行程复杂度。', phase)
                    tool = tools.setdefault(call.index, {'id': '', 'type': 'function', 'function': {'name': '', 'arguments': ''}})
                    if call.id:
                        tool['id'] = call.id
                    if call.function:
                        if call.function.name:
                            tool['function']['name'] += call.function.name
                        if call.function.arguments:
                            tool['function']['arguments'] += call.function.arguments
                finish = choice.finish_reason or finish
            now = monotonic()
            if now - last_update >= 8:
                await emit('status', {'message': f'模型正在{label}，已等待约{int(now - start)}秒；仍在接收响应', 'phase': phase})
                last_update = now
        logging.getLogger(__name__).info('model_response phase=%s seconds=%.1f content_chars=%s reasoning_chars=%s finish=%s',
            phase, monotonic() - start, sum(map(len, parts)), reasoning_chars, finish)
        if not received or finish is None:
            raise ModelOutputError(f'模型在{label}阶段连接提前结束，未收到完整结果。', phase)
        if finish == 'length':
            if reasoning_chars and sum(map(len, parts)) < reasoning_chars:
                raise ModelOutputError(f'模型服务在{label}阶段的推理内容耗尽输出额度，未生成完整行程；请检查网关关闭思考参数或更换模型。', phase)
            raise ModelOutputError(f'模型在{label}阶段达到输出长度上限，未生成完整结果。', phase, reason='length')
        if finish not in ('stop', 'tool_calls'):
            raise ModelOutputError(f'模型在{label}阶段未返回可用结果，请检查服务权限或内容限制。', phase)
        if not parts and not tools:
            raise ModelOutputError(f'模型在{label}阶段没有返回正文或工具调用。', phase)
        return ChatCompletion.model_validate({'id': 'request-local', 'object': 'chat.completion',
            'created': 0, 'model': params['model'], 'choices': [{'index': 0, 'finish_reason': finish,
            'message': {'role': 'assistant', 'content': ''.join(parts) or None,
                        'tool_calls': [tools[i] for i in sorted(tools)] or None}}]})
    except Exception as error:
        if not hasattr(error, 'phase'):
            error.phase = phase
        raise
    finally:
        if stream is not None:
            await stream.close()
