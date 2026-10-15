"""Explicit one-time migration. Never prints secret values."""
from pathlib import Path
import argparse
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('source', type=Path)
args = parser.parse_args()
target = root / '.env.local'
if target.exists():
    raise SystemExit('Existing .env.local kept unchanged.')
source = dotenv_values(args.source)
mapping = {'LLM_API_KEY': 'DASHSCOPE_API_KEY', 'LLM_BASE_URL': 'QWEN_BASE_URL', 'LLM_MODEL': 'QWEN_MODEL',
           **{key: key for key in ('AMAP_API_KEY', 'AMAP_WEB_KEY', 'AMAP_JS_SEC', 'AMAP_SIGN_SECRET')}}
values = {key: source.get(old, '') or '' for key, old in mapping.items()}
with target.open('w', encoding='utf-8') as stream:
    for key, value in values.items():
        stream.write(f'{key}={value}\n')
    stream.write('VALIDATOR_BACKEND=auto\nPORT=7862\n')
print('Migrated settings:', ', '.join(f'{key}={"configured" if value else "missing"}' for key, value in values.items()))
