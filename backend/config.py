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
        self.amap_qps = float(os.getenv('AMAP_QPS', '2'))
        if not 0 < self.amap_qps <= 100:
            raise ValueError('AMAP_QPS 必须大于 0 且不超过 100')
        self.validator = os.getenv('VALIDATOR_BACKEND', 'auto')
        self.port = int(os.getenv('PORT', '7862'))
        self.rail_mcp_enabled = os.getenv('RAIL_MCP_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        default_rail_python = ROOT / '.tools' / 'rail-mcp' / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.rail_mcp_python = Path(os.getenv('RAIL_MCP_PYTHON', '') or default_rail_python)
        self.runtime = ROOT / '.runtime'
        self.runtime.mkdir(exist_ok=True)

    def safe_error(self, error):
        if type(error).__module__.startswith('openai') and getattr(error, 'status_code', None) == 403:
            return '模型服务拒绝访问（HTTP 403）。请检查模型服务访问权限或网络代理；本次尚未完成模型调用，不能归因于地图或铁路。'
        if type(error).__module__.startswith('openai') and type(error).__name__ == 'APITimeoutError':
            phase = '资料查询' if getattr(error, 'phase', '') == 'research' else '行程结构生成'
            return f'模型服务在{phase}阶段超过60秒没有收到数据，连接超时；不是地图限额错误。请稍后重试或减少行程复杂度。'
        message = str(error)
        for value in (self.llm_key, self.amap_key, self.amap_web_key, self.amap_js_sec, self.amap_sign_secret):
            if value:
                message = message.replace(value, '[hidden]')
        message = re.sub(r'(?i)(sig=)[a-f0-9]{32}', r'\1[hidden]', message)
        return message[:600]

    def model_options(self):
        if urlparse(self.llm_url).hostname == 'api.deepseek.com':
            return {'extra_body': {'thinking': {'type': 'disabled'}}}
        if self.llm_model.lower().split('/')[-1].startswith(('deepseek', 'qwen')):
            return {'extra_body': {'chat_template_kwargs': {'enable_thinking': False}}}
        return {}



settings = Settings()
