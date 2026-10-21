"""Executable entry point for the application-local Python runtime."""
from pathlib import Path
import os
import subprocess
import sys


def main() -> int:
    root = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    python = root / 'runtime/python/python.exe'
    manager = root / 'app/portable_manager.py'
    if not python.is_file() or not manager.is_file():
        print('运行文件不完整，请先解压整个压缩包，再双击启动程序。')
        return 1
    env = dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for name in ('PYTHONHOME', 'PYTHONPATH', 'PYTHONSTARTUP', 'PYTHONUSERBASE'):
        env.pop(name, None)
    return subprocess.call([str(python), '-B', '-X', 'utf8', str(manager), *sys.argv[1:]], cwd=root, env=env)


if __name__ == '__main__':
    code = main()
    if code and sys.stdin.isatty():
        try:
            input('按回车关闭窗口。')
        except (EOFError, KeyboardInterrupt):
            pass
    raise SystemExit(code)
