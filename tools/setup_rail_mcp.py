"""Install the bundled railway server's locked dependencies in a separate venv."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / 'third_party' / 'mcp-server-12306'
ENVIRONMENT = ROOT / '.tools' / 'rail-mcp' / '.venv'
REQUIREMENTS = ROOT / 'tools' / 'rail-mcp-requirements.txt'


def environment_python(environment: Path) -> Path:
    return environment / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')


def setup(environment: Path = ENVIRONMENT) -> dict:
    if environment.resolve() == (ROOT / '.venv').resolve():
        raise RuntimeError('The main application environment must not be used for railway dependencies.')
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        raise RuntimeError('Railway setup requires Python 3.11–3.13.')
    if not (VENDOR / 'src' / 'mcp_12306' / 'server.py').is_file():
        raise RuntimeError('Bundled railway source is missing. Check third_party/mcp-server-12306.')
    python = environment_python(environment)
    if environment.exists() and not (environment / 'pyvenv.cfg').is_file():
        raise RuntimeError('Target exists but is not a virtual environment; no files were overwritten.')
    if not python.is_file():
        subprocess.run([sys.executable, '-m', 'venv', str(environment)], check=True, timeout=120)
    print('Installing locked railway dependencies; bundled source needs no separate download.', flush=True)
    subprocess.run([str(python), '-m', 'pip', '--disable-pip-version-check',
                    '--cache-dir', str(ROOT / '.tools' / 'rail-pip-cache'), 'install',
                    '--require-hashes', '--only-binary=:all:', '--timeout', '25', '--retries', '1',
                    '-r', str(REQUIREMENTS)], check=True, timeout=360)
    subprocess.run([str(python), '-m', 'pip', 'check'], check=True, timeout=30)
    check = subprocess.run([str(python), str(ROOT / 'tools' / 'rail_mcp_stdio.py'), '--check-install'],
                           cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8',
                           env={**os.environ, 'PYTHONUTF8': '1'}, timeout=30)
    result = json.loads(check.stdout)
    if Path(result['source']) != (VENDOR / 'src' / 'mcp_12306' / 'server.py').resolve():
        raise RuntimeError('Railway server did not load from the bundled source.')
    metadata = json.loads((VENDOR / 'UPSTREAM.json').read_text(encoding='utf-8'))
    result.update({'commit': metadata['commit'], 'python': str(python)})
    (environment / 'rail-install.json').write_text(json.dumps(result, indent=2) + '\n', encoding='utf-8')
    print('Railway dependencies ready. Existing .env.local settings were preserved.', flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--environment', type=Path, default=ENVIRONMENT,
                        help='Alternative isolated venv path; the app default remains .tools/rail-mcp/.venv.')
    args = parser.parse_args()
    try:
        setup(args.environment.resolve())
    except (RuntimeError, OSError, ValueError, subprocess.SubprocessError) as error:
        reason = str(error) if isinstance(error, RuntimeError) else type(error).__name__
        print(f'Railway setup failed: {reason}. Existing app settings were not changed.', file=sys.stderr)
        raise SystemExit(1)


if __name__ == '__main__':
    main()
