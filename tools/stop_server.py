"""Stop only the recorded Python service whose command names this project module."""
from pathlib import Path
import json
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
pid_file = root / '.runtime' / 'app.pid'
if not pid_file.exists():
    print('No recorded server.'); raise SystemExit(0)
pid = int(pid_file.read_text().strip())
command = f"Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}' | Select-Object ExecutablePath,CommandLine,ParentProcessId | ConvertTo-Json -Compress"
output = subprocess.run(['powershell', '-NoProfile', '-Command', command], capture_output=True, text=True).stdout.strip()
if not output:
    pid_file.unlink(); print('Server already stopped.'); raise SystemExit(0)
info = json.loads(output)
exe = Path(info.get('ExecutablePath') or '')
expected = root / '.venv' / 'Scripts' / 'python.exe'
target_pid = pid
if exe.resolve() != expected.resolve():
    # Windows venv launches a base-Python child. Only trust a parent whose path
    # is the exact venv executable of this project and runs the same module.
    parent_pid = int(info.get('ParentProcessId') or 0)
    query = f"Get-CimInstance Win32_Process -Filter 'ProcessId = {parent_pid}' | Select-Object ExecutablePath,CommandLine | ConvertTo-Json -Compress"
    parent_raw = subprocess.run(['powershell', '-NoProfile', '-Command', query], capture_output=True, text=True).stdout.strip()
    parent = json.loads(parent_raw) if parent_raw else {}
    if Path(parent.get('ExecutablePath') or '').resolve() != expected.resolve() or '-m backend.main' not in (parent.get('CommandLine') or ''):
        raise SystemExit('Recorded PID does not belong to this project; left unchanged.')
    target_pid = parent_pid
if '-m backend.main' not in (info.get('CommandLine') or ''):
    raise SystemExit('Recorded PID does not belong to this project; left unchanged.')
result = subprocess.run(['taskkill', '/PID', str(target_pid), '/T', '/F'], capture_output=True, text=True)
if result.returncode:
    raise SystemExit('Could not stop server.')
pid_file.unlink(missing_ok=True)
print('Travel Assistant stopped.')
