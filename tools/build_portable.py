"""Build a Windows x64 directory bundle from pinned dependencies and local resources."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import urllib.request
import zipfile

SOURCE = Path(__file__).resolve().parents[1]
PYTHON_VERSION = '3.11.9'
PYTHON_ARCHIVE = f'python-{PYTHON_VERSION}-embed-amd64.zip'
PYTHON_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ARCHIVE}'
PYTHON_SHA256 = '009d6bf7e3b2ddca3d784fa09f90fe54336d5b60f0e0f305c37f400bf83cfd3b'
PYINSTALLER_VERSION = '6.22.3'


def run(command, *, cwd=None, env=None):
    subprocess.run([str(value) for value in command], cwd=cwd, env=env, check=True)


def checksum(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as incoming:
        for block in iter(lambda: incoming.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def copy_native_runtime(app):
    native = SOURCE / 'cangjie'
    required = ('src/validator.cj', 'fixtures.json', 'journey_planner/src/main.cj',
                'journey_planner/fixtures/cases.json', 'bin/validator.exe',
                'bin/journey_planner.exe', 'bin/journey_planner.build.json',
                'bin/SDK-LICENSE', 'bin/Open_Source_Software_Notice.docx')
    for name in required:
        if not (native / name).is_file():
            raise RuntimeError('Native runtime is incomplete: cangjie/' + name)
    manifest = json.loads((native / 'bin/journey_planner.build.json').read_text(encoding='utf-8-sig'))
    if (manifest.get('schema_version') != '2.2'
            or manifest.get('source_sha256') != checksum(native / 'journey_planner/src/main.cj')
            or manifest.get('executable_sha256') != checksum(native / 'bin/journey_planner.exe')):
        raise RuntimeError('Native runtime does not match its source manifest.')
    files = [native / name for name in required] + sorted((native / 'bin').glob('*.dll'))
    for path in files:
        target = app / 'cangjie' / path.relative_to(native)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def archive_bundle(directory: Path) -> Path:
    directory = directory.resolve()
    public_configuration = (SOURCE / '.env.example').read_bytes()
    credentials = {'LLM_API_KEY', 'AMAP_API_KEY', 'AMAP_SIGN_SECRET', 'AMAP_WEB_KEY',
                   'AMAP_JS_SEC', 'QWEATHER_API_KEY', 'GUIDE_SEARCH_API_KEY'}
    for line in public_configuration.decode('utf-8-sig').splitlines():
        name, separator, value = line.strip().partition('=')
        if separator and name.strip() in credentials and value.strip():
            raise RuntimeError('The public configuration example must contain empty credentials.')
    example = directory / '.env.example'
    if example.exists() and example.read_bytes() != public_configuration:
        raise RuntimeError('The bundled configuration example must remain unmodified.')
    for configuration in directory.glob('.env.local*'):
        if configuration.name != '.env.local' or configuration.read_bytes() != public_configuration:
            raise RuntimeError('Only the unmodified empty configuration may be included in a public archive.')
    for path in (directory / 'app/.runtime').rglob('*'):
        if path.is_file() and path.relative_to(directory / 'app/.runtime').parts[0] not in ('study', 'rag'):
            raise RuntimeError('Remove user databases and service records before creating a public archive.')
    files = sorted(path for path in directory.rglob('*') if path.is_file()
                   and '__pycache__' not in path.parts and path.suffix != '.pyc' and path.name != 'MANIFEST.json')
    records = [{'path': path.relative_to(directory).as_posix(), 'bytes': path.stat().st_size,
                'sha256': checksum(path)} for path in files]
    manifest = directory / 'MANIFEST.json'
    manifest.write_text(json.dumps({'version': 1, 'files': records}, ensure_ascii=False, indent=2), encoding='utf-8')
    archive = directory.with_name(directory.name + '-x64.zip')
    if archive.exists():
        raise RuntimeError('Archive already exists; existing downloads were not overwritten.')
    with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=3) as package:
        for path in [*files, manifest]:
            package.write(path, (Path(directory.name) / path.relative_to(directory)).as_posix())
    print(f'Archive ready: {archive}', flush=True)
    return archive


def embed_runtime(archive, destination):
    destination.mkdir(parents=True)
    with zipfile.ZipFile(archive) as package:
        for name in package.namelist():
            target = (destination / name).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError('Invalid Python archive path')
        package.extractall(destination)
    (destination / 'python311._pth').write_text(
        'python311.zip\n.\nLib/site-packages\n../../app\nimport site\n', encoding='utf-8')


def build(output, resources, work, node_directory):
    if os.name != 'nt' or sys.version_info[:2] != (3, 11) or sys.maxsize <= 2**32:
        raise RuntimeError('Build with Windows x64 Python 3.11.')
    output, resources, work = output.resolve(), resources.resolve(), work.resolve()
    if output.exists():
        raise RuntimeError('Output already exists; use a new directory to preserve local configuration and data.')
    if not (resources / 'MANIFEST.json').is_file():
        raise RuntimeError('Extract the verified resource bundle first.')
    if not (node_directory / 'node.exe').is_file() or not (node_directory / 'npm.cmd').is_file():
        raise RuntimeError('Provide an installed Node 22 directory with --node-dir.')
    work.mkdir(parents=True, exist_ok=True)
    archive = work / PYTHON_ARCHIVE
    if not archive.exists():
        print('Downloading official embedded Python.', flush=True)
        with urllib.request.urlopen(PYTHON_URL, timeout=120) as response, archive.open('wb') as target:
            shutil.copyfileobj(response, target)
    if checksum(archive) != PYTHON_SHA256:
        raise RuntimeError('Embedded Python checksum mismatch.')
    output.mkdir(parents=True)
    app = output / 'app'
    app.mkdir()
    shutil.copytree(SOURCE / 'backend', app / 'backend', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    shutil.copytree(SOURCE / 'third_party', app / 'third_party', ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.git'))
    copy_native_runtime(app)
    (app / 'tools').mkdir()
    shutil.copy2(SOURCE / 'tools/rail_mcp_stdio.py', app / 'tools/rail_mcp_stdio.py')
    shutil.copy2(SOURCE / 'tools/portable/manager.py', app / 'portable_manager.py')
    shutil.copy2(SOURCE / '.env.example', output / '.env.example')
    shutil.copy2(SOURCE / '.env.example', output / '.env.local')
    shutil.copy2(SOURCE / 'LICENSE', output / 'LICENSE')
    shutil.copytree(SOURCE / 'licenses', output / 'licenses')
    (output / 'docs').mkdir()
    shutil.copy2(SOURCE / 'docs/GETTING_STARTED.md', output / 'docs/GETTING_STARTED.md')
    shutil.copytree(SOURCE / 'docs/images', output / 'docs/images')
    shutil.copytree(resources / 'LICENSES', output / 'licenses/models', dirs_exist_ok=True)
    shutil.copy2(resources / 'SOURCES.json', output / 'docs/STUDY_SOURCES.json')

    main_runtime, rail_runtime = output / 'runtime/python', output / 'runtime/rail'
    for directory in (main_runtime, rail_runtime):
        embed_runtime(archive, directory)
    print('Installing pinned application and local inference dependencies.', flush=True)
    run([sys.executable, '-m', 'pip', '--disable-pip-version-check', 'install', '--only-binary=:all:',
         '--no-compile', '--no-warn-script-location', '--target', main_runtime / 'Lib/site-packages',
         '-r', SOURCE / 'requirements.txt', '-r', SOURCE / 'requirements-rag.txt'])
    print('Installing the isolated railway dependencies.', flush=True)
    run([sys.executable, '-m', 'pip', '--disable-pip-version-check', 'install', '--only-binary=:all:',
         '--require-hashes', '--no-compile', '--no-warn-script-location',
         '--target', rail_runtime / 'Lib/site-packages', '-r', SOURCE / 'tools/rail-mcp-requirements.txt'])
    # Console wrappers point at the build interpreter; the app invokes modules directly.
    for runtime in (main_runtime, rail_runtime):
        for name in ('bin', 'Scripts'):
            wrappers = (runtime / 'Lib/site-packages' / name).resolve()
            if wrappers.is_dir() and wrappers.is_relative_to(runtime.resolve()):
                shutil.rmtree(wrappers)

    frontend = work / 'frontend'
    if frontend.exists():
        raise RuntimeError('Frontend work directory already exists; use a new work directory.')
    shutil.copytree(SOURCE / 'frontend', frontend, ignore=shutil.ignore_patterns('node_modules', 'dist', '*.tsbuildinfo'))
    env = dict(os.environ, PATH=str(node_directory) + os.pathsep + os.environ.get('PATH', ''),
               npm_config_cache=str(work / 'npm-cache'))
    run([node_directory / 'npm.cmd', 'ci'], cwd=frontend, env=env)
    run([node_directory / 'npm.cmd', 'run', 'build'], cwd=frontend, env=env)
    shutil.copytree(frontend / 'dist', app / 'frontend/dist')
    (app / 'frontend/public').mkdir(parents=True)
    shutil.copy2(SOURCE / 'frontend/public/favicon.svg', app / 'frontend/public/favicon.svg')

    sys.path.insert(0, str(SOURCE / 'tools'))
    from restore_resources import restore
    restored = restore(resources, app / '.runtime')
    print(f'Restored {restored} verified runtime resource files.', flush=True)

    launcher_env = work / 'launcher-env'
    launcher_python = launcher_env / 'Scripts/python.exe'
    if not launcher_python.exists():
        run([sys.executable, '-m', 'venv', launcher_env])
    run([launcher_python, '-m', 'pip', '--disable-pip-version-check', 'install', f'pyinstaller=={PYINSTALLER_VERSION}'])
    run([launcher_python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--console',
         '--name', '启动漫游', '--distpath', output, '--workpath', work / 'launcher-build',
         '--specpath', work, SOURCE / 'tools/portable/launcher.py'])
    script = ('from importlib.metadata import distribution; import pathlib,shutil; '
              f'target=pathlib.Path({str(output / "licenses/launcher")!r}); target.mkdir(parents=True,exist_ok=True); '
              'd=distribution("pyinstaller"); '
              '[shutil.copy2(d.locate_file(p),target/p.name) for p in d.files if "licenses" in p.parts and p.name.endswith((".txt",".rst"))]')
    run([launcher_python, '-c', script])
    for name, arguments in (('停止运行.bat', '--stop'), ('重启运行.bat', '--restart')):
        (output / name).write_bytes(('@echo off\r\nchcp 65001 >nul\r\n"%~dp0启动漫游.exe" ' + arguments + '\r\n').encode('utf-8'))
    (output / '配置API.bat').write_bytes(
        ('@echo off\r\ncd /d "%~dp0"\r\nif not exist ".env.local" copy /y ".env.example" ".env.local" >nul\r\n'
         'notepad.exe ".env.local"\r\n').encode('ascii'))
    shutil.copy2(SOURCE / 'README.md', output / 'README.md')
    metadata = {'format': 1, 'platform': 'windows-x64', 'python': PYTHON_VERSION,
                'python_source': PYTHON_URL, 'python_archive_sha256': PYTHON_SHA256,
                'launcher_builder': f'PyInstaller {PYINSTALLER_VERSION}',
                'resource_files': restored, 'configuration': '.env.local', 'application_data': 'app/.runtime'}
    (output / 'BUILD-INFO.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(f'Portable directory ready: {output}', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--resources', type=Path, required=True)
    parser.add_argument('--work', type=Path, required=True)
    parser.add_argument('--node-dir', type=Path, required=True)
    parser.add_argument('--archive', action='store_true', help='Create a public ZIP after the directory build')
    args = parser.parse_args()
    build(args.output, args.resources, args.work, args.node_dir.resolve())
    if args.archive:
        archive_bundle(args.output)


if __name__ == '__main__':
    main()
