import pytest
from backend.config import settings


@pytest.fixture(autouse=True)
def isolated_optional_railway(monkeypatch):
    # Normal regression tests must not depend on the developer's local service settings.
    monkeypatch.setattr(settings, 'rail_mcp_enabled', False)
