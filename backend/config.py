from pathlib import Path
from urllib.parse import urlparse
import os
import re

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env.local')
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'


class Settings:
    def __init__(self):
        self.llm_key = os.getenv('LLM_API_KEY', '').strip()
        self.llm_url = os.getenv('LLM_BASE_URL', 'https://api.deepseek.com').strip()
        self.llm_model = os.getenv('LLM_MODEL', 'deepseek-flash').strip()
        self.amap_key = os.getenv('AMAP_API_KEY', '').strip()
        self.amap_web_key = os.getenv('AMAP_WEB_KEY', '').strip()
        self.amap_js_sec = os.getenv('AMAP_JS_SEC', '').strip()
        self.amap_sign_secret = os.getenv('AMAP_SIGN_SECRET', '').strip()
        self.validator = os.getenv('VALIDATOR_BACKEND', 'auto')
        self.port = int(os.getenv('PORT', '7862'))
        self.rail_mcp_enabled = os.getenv('RAIL_MCP_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        default_rail_python = ROOT / '.tools' / 'rail-mcp' / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.rail_mcp_python = Path(os.getenv('RAIL_MCP_PYTHON', '') or default_rail_python)
        self.runtime = ROOT / '.runtime'
        self.runtime.mkdir(exist_ok=True)

    def safe_error(self, error):
        message = str(error)
        for value in (self.llm_key, self.amap_key, self.amap_web_key, self.amap_js_sec, self.amap_sign_secret):
            if value:
                message = message.replace(value, '[hidden]')
        message = re.sub(r'(?i)(sig=)[a-f0-9]{32}', r'\1[hidden]', message)
        return message[:600]

    def model_options(self):
        if urlparse(self.llm_url).hostname == 'api.deepseek.com':
            return {'extra_body': {'thinking': {'type': 'disabled'}}}
        return {}


settings = Settings()
