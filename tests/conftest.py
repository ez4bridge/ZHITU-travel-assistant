"""Unit tests do not depend on a developer's optional downloaded models."""
import pytest

from backend.config import settings
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from backend.weather import SHANGHAI, unavailable


@pytest.fixture(autouse=True)
def local_retrieval_baseline(monkeypatch):
    monkeypatch.setattr(settings, 'rag_mode', 'bm25')


@pytest.fixture(autouse=True)
def no_live_weather_in_http_tests(monkeypatch):
    monkeypatch.setattr(settings, 'qweather_key', '')
    monkeypatch.setattr(settings, 'qweather_host', '')
    import backend.main as main
    async def offline(request, amap):
        return unavailable(request, datetime.now(SHANGHAI))
    monkeypatch.setattr(main, 'weather_service', SimpleNamespace(forecast=AsyncMock(side_effect=offline)))






@pytest.fixture(autouse=True)
def isolated_optional_railway(monkeypatch):
    # Normal regression tests must not depend on the developer's local service settings.
    monkeypatch.setattr(settings, 'rail_mcp_enabled', False)
