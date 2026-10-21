"""Explicit model download and offline index build. Never called by normal startup."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings
from backend.rag_common import file_hash, read_json, write_json

MODELS = {
    'embedding': ('Qdrant/bge-small-zh-v1.5', '46fbe35fd4374a00fee7de77dfddaeb6dd6a2c59',
                  ['model_optimized.onnx', 'config.json', 'tokenizer.json', 'tokenizer_config.json',
                   'special_tokens_map.json', 'vocab.txt']),
    'reranker': ('onnx-community/bge-reranker-v2-m3-ONNX', '6f5ff65298512715a1e669753bc754d2bc8f367b',
                 ['onnx/model_int8.onnx', 'config.json', 'tokenizer.json', 'tokenizer_config.json',
                  'special_tokens_map.json']),
}


def download(directory: Path) -> None:
    import httpx
    previous = {}
    try:
        previous = read_json(directory / 'models.json')
    except (OSError, ValueError):
        pass
    manifest = {}
    # Respect the user's network proxy; never attach application API credentials.
    with httpx.Client(timeout=120, follow_redirects=True) as client:
        for kind, (repository, revision, files) in MODELS.items():
            records = {}
            for name in files:
                path = directory / 'models' / kind / name
                old = previous.get(kind, {}).get('files', {}).get(name)
                if (previous.get(kind, {}).get('revision') == revision and path.is_file()
                        and old and file_hash(path) == old['sha256']):
                    records[name] = old
                    continue
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(path.suffix + '.download')
                print(f'Downloading {kind}/{name}', flush=True)
                with client.stream('GET', f'https://huggingface.co/{repository}/resolve/{revision}/{name}',
                                   params={'download': 'true'}) as response:
                    response.raise_for_status()
                    with temporary.open('wb') as output:
                        for block in response.iter_bytes(1024 * 1024):
                            output.write(block)
                temporary.replace(path)
                records[name] = {'sha256': file_hash(path), 'bytes': path.stat().st_size}
            manifest[kind] = {'repository': repository, 'revision': revision, 'files': records}
            write_json(directory / 'models.json', {**previous, **manifest})
    print('Models downloaded and checksummed. No LLM API was called.', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='Explicitly download both pinned CPU models')
    parser.add_argument('--download-only', action='store_true')
    args = parser.parse_args()
    if args.download:
        download(settings.rag_dir)
    if not args.download_only:
        command = [sys.executable, '-X', 'utf8', '-m', 'backend.rag_worker', '--build',
                   '--rag-dir', str(settings.rag_dir)]
        result = subprocess.run(command, cwd=ROOT, capture_output=True, encoding='utf-8',
            env=dict(os.environ, HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false'),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        if result.returncode:
            print(result.stderr[-4000:], file=sys.stderr)
            raise SystemExit('Index build failed; the existing BM25 application remains usable.')
        print(result.stdout, end='')


if __name__ == '__main__':
    main()
