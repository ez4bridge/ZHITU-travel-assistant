"""Isolate regression tests from credentials, saved data and external services."""
import os
from pathlib import Path
import socket
import tempfile

import dotenv
import dotenv.main


for _name in tuple(os.environ):
    _upper = _name.upper()
    if (_upper.startswith(('LLM_', 'AMAP_', 'OPENAI_', 'LANGSMITH_', 'LANGCHAIN_'))
            or _upper.endswith(('_KEY', '_TOKEN', '_SECRET', '_PASSWORD'))):
        os.environ.pop(_name, None)
os.environ.update(PYTHON_DOTENV_DISABLED='1', LANGSMITH_TRACING='false', LANGCHAIN_TRACING_V2='false')


def _no_dotenv(*_args, **_kwargs):
    return False


dotenv.load_dotenv = dotenv.main.load_dotenv = _no_dotenv


def _local_address(address):
    # A string socket address is local IPC (AF_UNIX), rather than a TCP host.
    return isinstance(address, str) or address[0] in ('127.0.0.1', '::1', 'localhost')


_connect, _connect_ex = socket.socket.connect, socket.socket.connect_ex
_resolve, _gethostbyname = socket.getaddrinfo, socket.gethostbyname
_sendto = socket.socket.sendto


def _guarded_connect(self, address):
    if not _local_address(address):
        raise OSError('pytest isolation: external network is disabled')
    return _connect(self, address)


def _guarded_connect_ex(self, address):
    if not _local_address(address):
        raise OSError('pytest isolation: external network is disabled')
    return _connect_ex(self, address)


def _guarded_resolve(host, *args, **kwargs):
    if host not in ('127.0.0.1', '::1', 'localhost', None):
        raise OSError('pytest isolation: external DNS is disabled')
    return _resolve(host, *args, **kwargs)


def _guarded_gethostbyname(host):
    if host not in ('127.0.0.1', '::1', 'localhost'):
        raise OSError('pytest isolation: external DNS is disabled')
    return _gethostbyname(host)


def _guarded_sendto(self, *args):
    if not _local_address(args[-1]):
        raise OSError('pytest isolation: external network is disabled')
    return _sendto(self, *args)


socket.socket.connect, socket.socket.connect_ex = _guarded_connect, _guarded_connect_ex
socket.getaddrinfo, socket.gethostbyname = _guarded_resolve, _guarded_gethostbyname
socket.socket.sendto = _guarded_sendto


def pytest_configure(config):
    # Only configuration is imported here; main/storage modules have not yet
    # created their default databases. Credentials are already disabled above.
    from backend.config import settings
    runtime = tempfile.TemporaryDirectory(prefix='travel-pytest-runtime-', ignore_cleanup_errors=True)
    path = Path(runtime.name).resolve()
    expected_parent = Path(tempfile.gettempdir()).resolve()
    if path.parent != expected_parent or not path.name.startswith('travel-pytest-runtime-'):
        raise ValueError('pytest runtime must be an owned direct child of the system temporary directory')
    settings.runtime = path
    for field in ('llm_key', 'amap_key', 'amap_web_key', 'amap_js_sec', 'amap_sign_secret'):
        setattr(settings, field, '')
    config.add_cleanup(runtime.cleanup)


import pytest


@pytest.fixture(autouse=True)
def isolated_optional_railway(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, 'rail_mcp_enabled', False)


@pytest.fixture
def fixture_trip_clock(monkeypatch):
    """Keep fixed October21 API fixtures future dated without weakening the API.

    Opt in only in fixture generation tests. Past-date rejection tests continue
    to use their own clocks and the production rule remains unchanged.
    """
    from datetime import datetime, timezone
    import backend.main as main

    class FixtureClock(datetime):
        @classmethod
        def now(cls, tz=None):
            at = datetime(2026, 10, 20, 0, 0, tzinfo=timezone.utc)
            return at.astimezone(tz) if tz else at.replace(tzinfo=None)

    monkeypatch.setattr(main, 'datetime', FixtureClock)


@pytest.fixture(autouse=True)
def local_retrieval_baseline(monkeypatch):
    from backend.config import settings
    monkeypatch.setattr(settings, 'rag_mode', 'bm25')


@pytest.fixture(autouse=True)
def no_live_weather_in_http_tests(monkeypatch):
    from backend.config import settings
    from backend.weather import SHANGHAI, unavailable
    from datetime import datetime
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    import backend.main as main
    monkeypatch.setattr(settings, 'qweather_key', '')
    monkeypatch.setattr(settings, 'qweather_host', '')
    async def offline(request, amap):
        return unavailable(request, datetime.now(SHANGHAI))
    monkeypatch.setattr(main, 'weather_service', SimpleNamespace(forecast=AsyncMock(side_effect=offline)))
