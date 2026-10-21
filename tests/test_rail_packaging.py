import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('rail_setup', ROOT / 'tools' / 'setup_rail_mcp.py')
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_bundled_snapshot_preserves_upstream_sources_license_and_lock():
    vendor = ROOT / 'third_party' / 'mcp-server-12306'
    provenance = json.loads((vendor / 'UPSTREAM.json').read_text())
    assert provenance['commit'] == 'f6865c077cfd51eb90021cc75aff33fad75ac3d5'
    assert not provenance['modified_files']
    assert not (vendor / '.git').exists()
    for name in ['LICENSE', 'uv.lock', 'pyproject.toml', 'src/mcp_12306/server.py',
                 'src/mcp_12306/resources/station_name.js']:
        assert name in provenance['files_sha256']
    for name, expected in provenance['files_sha256'].items():
        assert hashlib.sha256((vendor / name).read_bytes()).hexdigest() == expected, name
    assert 'Copyright (c) 2026 drfccv' in (vendor / 'LICENSE').read_text()
    # Pin the dependency's requested extras too for the Python 3.11 bundled pip resolver.
    assert 'pyjwt[crypto]==2.14.0' in (ROOT / 'tools' / 'rail-mcp-requirements.txt').read_text()


def fake_install(monkeypatch, tmp_path, *, foreign_source=False, fail_pip=False):
    environment = tmp_path / 'rail-venv'
    calls = []
    local_settings = tmp_path / '.env.local'
    local_settings.write_text('RAIL_MCP_ENABLED=false\n')
    monkeypatch.setattr(installer, 'ROOT', tmp_path)
    def run(command, **kwargs):
        calls.append(command)
        if command[1:3] == ['-m', 'venv']:
            python = installer.environment_python(environment)
            python.parent.mkdir(parents=True)
            python.write_text('fake executable')
            (environment / 'pyvenv.cfg').write_text('fake test environment')
        if 'install' in command and fail_pip:
            raise subprocess.CalledProcessError(1, command)
        source = tmp_path / 'other/server.py' if foreign_source else installer.VENDOR / 'src/mcp_12306/server.py'
        return SimpleNamespace(stdout=json.dumps({'source': str(source.resolve()), 'version': '0.5.0.post20260930'}))
    monkeypatch.setattr(installer.subprocess, 'run', run)
    return environment, calls, local_settings


def test_setup_uses_hash_locked_dependencies_preserves_settings_and_checks_source(monkeypatch, tmp_path):
    environment, calls, settings = fake_install(monkeypatch, tmp_path)
    result = installer.setup(environment)
    assert len(calls) == 4
    pip = next(command for command in calls if 'install' in command)
    assert '--require-hashes' in pip and '--only-binary=:all:' in pip
    assert not any('git' in command or 'clone' in command for command in calls)
    assert result['source'].startswith(str(installer.VENDOR))
    assert settings.read_text() == 'RAIL_MCP_ENABLED=false\n'
    assert (environment / 'rail-install.json').is_file()


def test_setup_rejects_accidental_use_of_another_source(monkeypatch, tmp_path):
    environment, _, _ = fake_install(monkeypatch, tmp_path, foreign_source=True)
    with pytest.raises(RuntimeError, match='bundled source'):
        installer.setup(environment)
    assert not (environment / 'rail-install.json').exists()


def test_setup_failure_keeps_user_settings_and_does_not_claim_ready(monkeypatch, tmp_path):
    environment, _, settings = fake_install(monkeypatch, tmp_path, fail_pip=True)
    with pytest.raises(subprocess.CalledProcessError):
        installer.setup(environment)
    assert settings.read_text() == 'RAIL_MCP_ENABLED=false\n'
    assert not (environment / 'rail-install.json').exists()


def test_setup_refuses_to_overwrite_an_unrelated_directory(tmp_path):
    environment = tmp_path / 'existing'
    environment.mkdir()
    (environment / 'keep.txt').write_text('user data')
    with pytest.raises(RuntimeError, match='no files were overwritten'):
        installer.setup(environment)
    assert (environment / 'keep.txt').read_text() == 'user data'


def test_setup_never_installs_railway_into_main_app_environment():
    with pytest.raises(RuntimeError, match='main application environment'):
        installer.setup(ROOT / '.venv')
