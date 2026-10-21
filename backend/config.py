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
        self.guide_search_key = os.getenv('GUIDE_SEARCH_API_KEY', '').strip() or (
            self.llm_key if urlparse(self.llm_url).hostname == 'api.deepseek.com' else '')
        self.guide_official_domains = tuple(domain.strip().lower() for domain in
            os.getenv('GUIDE_OFFICIAL_DOMAINS', '').split(',') if domain.strip())
        self.amap_key = os.getenv('AMAP_API_KEY', '').strip()
        self.amap_web_key = os.getenv('AMAP_WEB_KEY', '').strip()
        self.amap_js_sec = os.getenv('AMAP_JS_SEC', '').strip()
        self.amap_sign_secret = os.getenv('AMAP_SIGN_SECRET', '').strip()
        self.qweather_key = os.getenv('QWEATHER_API_KEY', '').strip()
        self.qweather_host = os.getenv('QWEATHER_API_HOST', '').strip().lower()
        if self.qweather_host and not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.qweatherapi\.com', self.qweather_host):
            raise ValueError('QWEATHER_API_HOST 请填写控制台专属 qweatherapi.com 域名，不含协议、端口或路径')
        self.amap_qps = float(os.getenv('AMAP_QPS', '2'))
        if not 0 < self.amap_qps <= 100:
            raise ValueError('AMAP_QPS 必须大于 0 且不超过 100')
        self.port = int(os.getenv('PORT', '7862'))
        self.rail_mcp_enabled = os.getenv('RAIL_MCP_ENABLED', 'false').lower() in ('1', 'true', 'yes')
        default_rail_python = ROOT / '.tools' / 'rail-mcp' / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
        self.rail_mcp_python = Path(os.getenv('RAIL_MCP_PYTHON', '') or default_rail_python)
        self.runtime = ROOT / '.runtime'
        self.runtime.mkdir(exist_ok=True)
        corpus = Path(os.getenv('STUDY_CORPUS_DIR', '.runtime/study/shanghai_youth_v1').strip() or '.runtime/study/shanghai_youth_v1')
        self.study_corpus_dir = corpus if corpus.is_absolute() else ROOT / corpus
        self.rag_dir = self.runtime / 'rag'
        self.rag_mode = os.getenv('RAG_RETRIEVAL_MODE', 'auto').strip().lower()
        if self.rag_mode not in ('auto', 'bm25', 'dense', 'hybrid', 'hybrid_rerank'):
            raise ValueError('RAG_RETRIEVAL_MODE不在支持的检索模式中')

    def safe_error(self, error):
        message = str(error)
        for value in (self.llm_key, self.guide_search_key, self.amap_key, self.amap_web_key, self.amap_js_sec, self.amap_sign_secret, self.qweather_key):
            if value:
                message = message.replace(value, '[hidden]')
        message = re.sub(r'(?i)(sig=)[a-f0-9]{32}', r'\1[hidden]', message)
        return message[:600]

    def model_options(self):
        if urlparse(self.llm_url).hostname == 'api.deepseek.com':
            return {'extra_body': {'thinking': {'type': 'disabled'}}}
        return {}


settings = Settings()
