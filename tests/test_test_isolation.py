"""The ordinary pytest command must not load keys, call APIs or use normal DB."""
import os
from pathlib import Path
import socket
import tempfile

from dotenv import load_dotenv
import pytest

from backend.config import ROOT, settings
from backend.storage import Store


def test_default_store_uses_owned_temporary_runtime():
    path = Path(settings.runtime).resolve()
    assert path.parent == Path(tempfile.gettempdir()).resolve()
    assert path.name.startswith('travel-pytest-runtime-')
    assert path != (ROOT / '.runtime').resolve()
    assert Path(Store().path).parent == path
    assert not any(getattr(settings, name) for name in (
        'llm_key', 'amap_key', 'amap_web_key', 'amap_js_sec', 'amap_sign_secret'))


def test_dotenv_loader_does_not_read_even_a_synthetic_file(tmp_path):
    path = tmp_path / '.env.local'
    path.write_text('PYTEST_DOTENV_SENTINEL=synthetic-only\n', encoding='utf-8')
    assert load_dotenv(path, override=True) is False
    assert 'PYTEST_DOTENV_SENTINEL' not in os.environ


def test_external_dns_tcp_and_udp_are_blocked_before_a_connection():
    with pytest.raises(OSError, match='external DNS is disabled'):
        socket.getaddrinfo('example.invalid', 443)
    with socket.socket() as stream:
        with pytest.raises(OSError, match='external network is disabled'):
            stream.connect(('192.0.2.1', 443))
        with pytest.raises(OSError, match='external network is disabled'):
            stream.connect_ex(('192.0.2.1', 443))
    with socket.socket(type=socket.SOCK_DGRAM) as datagram:
        with pytest.raises(OSError, match='external network is disabled'):
            datagram.sendto(b'synthetic-only', ('192.0.2.1', 443))


def test_loopback_remains_available_for_local_http_and_async_subprocesses():
    assert socket.getaddrinfo('127.0.0.1', 80)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(1)
        with socket.socket() as client:
            client.connect(listener.getsockname())
            connection, address = listener.accept()
            with connection:
                assert address[0] == '127.0.0.1'
