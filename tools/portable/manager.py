"""Start and stop only the server owned by this portable directory."""
import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser

from dotenv import dotenv_values, set_key
import psutil

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / 'app'
RUNTIME = APP / '.runtime'
PYTHON = ROOT / 'runtime/python/python.exe'
RAIL_PYTHON = ROOT / 'runtime/rail/python.exe'
RECORD = RUNTIME / 'portable-service.json'


@contextmanager
def launch_lock():
    import msvcrt
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / 'launcher.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0')
            lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise RuntimeError('另一个启动或停止操作正在进行，请稍后再试。') from None
        try:
            yield
        finally:
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


def fingerprint() -> str:
    value = hashlib.sha256()
    for path in sorted((APP / 'backend').glob('*.py')):
        value.update(path.name.encode('utf-8'))
        value.update(b'\0')
        value.update(path.read_bytes())
    return value.hexdigest()[:16]


def environment() -> dict:
    config = ROOT / '.env.local'
    if not config.exists():
        shutil.copyfile(ROOT / '.env.example', config)
    env = dict(os.environ)
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONUSERBASE'):
        env.pop(name, None)
    for name, value in dotenv_values(config).items():
        if value is not None:
            env[name] = value
    # Bundled railway dependencies must stay separate from the web runtime.
    env.update(PYTHONUTF8='1', PYTHONIOENCODING='utf-8', PYTHONDONTWRITEBYTECODE='1',
               RAIL_MCP_PYTHON=str(RAIL_PYTHON), HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1')
    return env


def import_configuration(source: Path):
    source = source.resolve()
    if not source.is_file() or source.stat().st_size > 65536:
        raise RuntimeError('请选择64KB以内、按模板填写的UTF-8配置文件。')
    try:
        incoming = dotenv_values(source, encoding='utf-8-sig', interpolate=False)
    except UnicodeError:
        raise RuntimeError('配置文件请使用UTF-8编码，字段格式为 NAME=value。') from None
    example = dotenv_values(ROOT / '.env.example')
    fields = {name: value for name, value in incoming.items() if name in example and value is not None}
    if not fields:
        raise RuntimeError('没有找到支持的配置字段，请使用随包的API配置模板。')
    destination = ROOT / '.env.local'
    if source != destination.resolve():
        temporary = ROOT / '.env.local.tmp'
        shutil.copyfile(destination if destination.exists() else ROOT / '.env.example', temporary)
        try:
            for name, value in fields.items():
                set_key(temporary, name, value, quote_mode='always')
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    print(f'已导入{len(fields)}项配置。')


def owned_process():
    if not RECORD.is_file():
        return None, None
    try:
        record = json.loads(RECORD.read_text(encoding='utf-8'))
        process = psutil.Process(int(record['pid']))
        command = process.cmdline()
        if (Path(process.exe()).resolve() != PYTHON.resolve()
                or Path(process.cwd()).resolve() != APP.resolve()
                or command[-2:] != ['-m', 'backend.main']
                or abs(process.create_time() - float(record['created'])) > 0.1):
            raise RuntimeError('记录的进程不属于此目录，已保持不变。')
        return process, record
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        RECORD.unlink(missing_ok=True)
        return None, None
    except (ValueError, KeyError, TypeError, OSError):
        raise RuntimeError('启动记录无法读取，请检查应用目录的写入权限。') from None


def health(port: int) -> dict:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f'http://127.0.0.1:{port}/api/health', timeout=2) as response:
        return json.load(response)


def stop(process=None, record=None):
    if process is None:
        process, record = owned_process()
    if process is None:
        print('此便携版尚未运行。')
        return
    # Capture descendants before terminating their parent so no model worker is left behind.
    try:
        children = process.children(recursive=True)
    except psutil.NoSuchProcess:
        children = []
    targets = [process, *children]
    for target in targets:
        try:
            target.terminate()
        except psutil.NoSuchProcess:
            pass
    _, pending = psutil.wait_procs(targets, timeout=5)
    for target in pending:
        try:
            target.kill()
        except psutil.NoSuchProcess:
            pass
    _, pending = psutil.wait_procs(pending, timeout=3)
    if pending:
        raise RuntimeError('服务仍在退出，请稍后再次停止。')
    RECORD.unlink(missing_ok=True)
    pid_file = RUNTIME / 'app.pid'
    if pid_file.exists() and pid_file.read_text().strip() == str(record['pid']):
        pid_file.unlink()
    print('漫游已停止，资料和已保存行程保留。')


def choose_port(preferred: int) -> int:
    for port in dict.fromkeys([preferred, *range(7868, 7880)]):
        with socket.socket() as probe:
            try:
                probe.bind(('127.0.0.1', port))
                return port
            except OSError:
                continue
    raise RuntimeError('本地可用端口不足，请关闭占用端口的程序后重试。')


def announce(port: int, open_browser: bool, configured: bool):
    url = f'http://127.0.0.1:{port}/'
    print(f'漫游已启动：{url}')
    if not configured:
        print('首次使用：双击“配置API.bat”填写密钥，然后双击“重启运行.bat”。')
    print('关闭此窗口不会停止服务；退出时双击“停止运行.bat”。')
    if open_browser:
        webbrowser.open(url)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config_file', nargs='?', type=Path, help='导入按模板填写的配置文件并启动')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--stop', action='store_true')
    action.add_argument('--restart', action='store_true')
    action.add_argument('--check', action='store_true')
    parser.add_argument('--no-browser', action='store_true')
    parser.add_argument('--port', type=int)
    args = parser.parse_args()
    if not PYTHON.is_file() or not RAIL_PYTHON.is_file() or not (APP / 'frontend/dist/index.html').is_file():
        raise RuntimeError('运行文件不完整，请重新解压便携包。')
    if args.check:
        if args.config_file:
            with launch_lock():
                import_configuration(args.config_file)
        env = environment()
        import fastapi
        import onnxruntime
        import qdrant_client
        import fastembed
        print(json.dumps({'python': sys.version.split()[0], 'frontend': True, 'local_inference': True,
                          'railway_runtime': RAIL_PYTHON.is_file(), 'validator': 'python',
                          'model': env.get('LLM_MODEL', '')}, ensure_ascii=False))
        return 0
    with launch_lock():
        if args.config_file and args.stop:
            raise RuntimeError('停止运行时无需导入配置文件。')
        if args.config_file:
            import_configuration(args.config_file)
        env = environment()
        process, record = owned_process()
        if args.stop:
            stop(process, record)
            return 0
        if (args.restart or args.config_file) and process is not None:
            stop(process, record)
            process, record = None, None
        if process is not None:
            result = health(record['port'])
            if result.get('app_id') != 'travel-assistant' or result.get('build_id') != fingerprint():
                raise RuntimeError('此目录的服务版本已变化，请使用“重启运行.bat”。')
            announce(record['port'], not args.no_browser, bool(result['capabilities']['planning']))
            return 0
        try:
            preferred = args.port if args.port is not None else int(env.get('PORT', '7862'))
            if not 1024 <= preferred <= 65535:
                raise ValueError
        except ValueError:
            raise RuntimeError('PORT 请填写1024至65535之间的端口号。') from None
        port = choose_port(preferred)
        env['PORT'] = str(port)
        with (RUNTIME / 'server.log').open('ab') as log:
            child = subprocess.Popen([str(PYTHON), '-B', '-X', 'utf8', '-m', 'backend.main'], cwd=APP, env=env,
                                     stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     creationflags=subprocess.CREATE_NO_WINDOW)
        process = psutil.Process(child.pid)
        record = {'pid': process.pid, 'created': process.create_time(), 'port': port}
        RECORD.write_text(json.dumps(record), encoding='utf-8')
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline and child.poll() is None:
            try:
                result = health(port)
                if result.get('app_id') == 'travel-assistant' and result.get('build_id') == fingerprint():
                    announce(port, not args.no_browser, bool(result['capabilities']['planning']))
                    return 0
            except (OSError, ValueError, urllib.error.URLError):
                pass
            time.sleep(0.25)
        stop(process, record)
        raise RuntimeError('启动未完成，请查看 app/.runtime/server.log 或重新解压便携包。')


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (RuntimeError, OSError, psutil.Error) as error:
        print(str(error) if isinstance(error, RuntimeError) else '运行失败，请检查文件完整性和目录权限。', file=sys.stderr)
        raise SystemExit(1)
