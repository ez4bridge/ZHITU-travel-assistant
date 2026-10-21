from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import pytest
from openai.types.chat import ChatCompletionChunk
from backend.model_stream import complete_model, ModelOutputError


def chunk(delta=None, finish=None, choices=True):
    return ChatCompletionChunk.model_validate({'id':'test', 'created':0, 'model':'test', 'object':'chat.completion.chunk',
        'choices':[{'index':0, 'delta':delta or {}, 'finish_reason':finish}] if choices else []})


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
    async def __aiter__(self):
        for item in self.chunks:
            if isinstance(item, BaseException): raise item
            yield item
    async def close(self): self.closed = True


def client_for(stream):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=stream))))


@pytest.mark.asyncio
async def test_stream_assembles_text_and_tool_fragments_without_reasoning():
    stream = Stream([chunk({'role':'assistant','reasoning_content':'private thought','reasoning':'private thought'}),
        chunk({'content':'hello '}), chunk({'content':'world', 'tool_calls':[{'index':0,'id':'call_one','type':'function',
            'function':{'name':'search_places','arguments':'{"keywords":'}}]}),
        chunk({'tool_calls':[{'index':0,'function':{'arguments':'"park"}'}}]}),chunk(finish='tool_calls'),chunk(choices=False)])
    client = client_for(stream)
    response = await complete_model(client, AsyncMock(), 'research', model='test')
    message = response.choices[0].message
    assert message.content == 'hello world'
    assert message.tool_calls[0].function.arguments == '{"keywords":"park"}'
    assert 'private thought' not in message.model_dump_json()
    assert stream.closed and client.chat.completions.create.await_args.kwargs['stream'] is True


@pytest.mark.asyncio
@pytest.mark.parametrize('chunks', [[chunk({'content':'partial'})], [chunk({'content':'partial'}, 'length')], [chunk(finish='stop')]])
async def test_partial_or_truncated_stream_never_becomes_success(chunks):
    stream = Stream(chunks)
    with pytest.raises(ModelOutputError):
        await complete_model(client_for(stream), AsyncMock(), 'itinerary', model='test')
    assert stream.closed


@pytest.mark.asyncio
async def test_cancellation_closes_provider_stream():
    stream = Stream([asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        await complete_model(client_for(stream), AsyncMock(), 'research', model='test')
    assert stream.closed


@pytest.mark.asyncio
async def test_hidden_reasoning_exhaustion_has_specific_error_without_thought_text():
    stream = Stream([chunk({'reasoning':'private thought'}, 'length')])
    with pytest.raises(ModelOutputError) as failure:
        await complete_model(client_for(stream), AsyncMock(), 'itinerary', model='test')
    assert '推理内容耗尽' in str(failure.value)
    assert 'private thought' not in str(failure.value)
    assert stream.closed


@pytest.mark.asyncio
async def test_large_tool_response_is_received_for_bounded_research_selection():
    calls = [{'index': i, 'id': f'call{i}', 'type': 'function',
        'function': {'name': 'search_places', 'arguments': '{"keywords":"site","city":"上海"}'}} for i in range(20)]
    stream = Stream([chunk({'tool_calls': calls}), chunk(finish='tool_calls')])
    result = await complete_model(client_for(stream), AsyncMock(), 'research', model='test')
    assert len(result.choices[0].message.tool_calls) == 20 and stream.closed
