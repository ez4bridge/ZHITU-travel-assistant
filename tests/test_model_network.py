import httpx
import pytest
from openai import PermissionDeniedError
from backend.agent import model_client
from backend.config import settings


@pytest.mark.asyncio
async def test_model_client_ignores_system_proxy_settings(monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:1')
    monkeypatch.setattr(settings, 'llm_key', 'test-key')
    async with model_client() as client:
        assert client._client.trust_env is False
        assert client.max_retries == 0


def test_model_forbidden_html_is_not_shown_as_raw_markup():
    response = httpx.Response(403, request=httpx.Request('POST', 'https://model.example/chat/completions'))
    error = PermissionDeniedError('<html><h1>403 Forbidden</h1>openresty</html>', response=response, body=None)
    message = settings.safe_error(error)
    assert 'HTTP 403' in message and '模型' in message
    assert '<html>' not in message and 'openresty' not in message


def test_deepseek_gateway_can_disable_thinking_without_changing_other_models(monkeypatch):
    monkeypatch.setattr(settings, 'llm_url', 'https://gateway.example/v1')
    monkeypatch.setattr(settings, 'llm_model', 'DeepSeek')
    assert settings.model_options() == {'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}}
    monkeypatch.setattr(settings, 'llm_model', 'other-model')
    assert settings.model_options() == {}


def test_official_deepseek_keeps_its_own_thinking_protocol(monkeypatch):
    monkeypatch.setattr(settings, 'llm_url', 'https://api.deepseek.com')
    assert settings.model_options() == {'extra_body': {'thinking': {'type': 'disabled'}}}
