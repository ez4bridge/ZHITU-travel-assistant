import os
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
import pytest

from backend.build_info import source_fingerprint

ROOT = Path(__file__).resolve().parents[1]


def test_health_identifies_loaded_build_and_active_rate_limiter(monkeypatch):
    import backend.main as main
    monkeypatch.setattr(main.validator, 'ready', AsyncMock(return_value=False))
    with TestClient(main.app) as client:
        health = client.get('/api/health').json()
    assert health['build_id'] == main.BUILD_ID == source_fingerprint()
    assert health['started_at']
    assert health['rate_limit']['max_retries'] == 2
    assert health['rate_limit']['qps_per_endpoint'] > 0


@pytest.mark.skipif(os.name != 'nt', reason='Windows PowerShell startup entry point')
@pytest.mark.parametrize('up_to_date', [False, True])
def test_start_script_distinguishes_old_running_server(up_to_date):
    env = {**os.environ, 'TEST_CURRENT_BUILD': source_fingerprint() if up_to_date else 'old-server'}
    command = "function Invoke-RestMethod { return @{app_id='travel-assistant'; build_id=$env:TEST_CURRENT_BUILD} }; & ./start.ps1; exit $LASTEXITCODE"
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == (0 if up_to_date else 2), result.stderr
    assert ('already running' if up_to_date else 'restart.bat') in result.stdout
