"""Bounded MCP stdio transport. The SDK and provider stay outside the app environment."""
import asyncio
import json
import os
from pathlib import Path

from .config import ROOT


class RailMcpClient:
    def __init__(self, python: Path, timeout: float = 25):
        self.python = python
        self.timeout = timeout
        self.process = None
        self.sequence = 0

    async def start(self) -> None:
        if self.process and self.process.returncode is None:
            return
        names = {'PATH', 'HOME', 'LANG', 'LC_ALL', 'TZ', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
                 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY',
                 'http_proxy', 'https_proxy', 'all_proxy', 'no_proxy', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP'}
        env = {name: value for name, value in os.environ.items() if name in names}
        env.update({'PYTHONUNBUFFERED': '1', 'TZ': 'Asia/Shanghai', 'DEBUG': 'false', 'LOG_LEVEL': 'ERROR'})
        self.process = await asyncio.create_subprocess_exec(str(self.python), str(ROOT / 'tools' / 'rail_mcp_stdio.py'),
            cwd=ROOT / '.runtime', env=env, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=2000000)
        result = await self.rpc('initialize', {'protocolVersion': '2025-11-25', 'capabilities': {},
                                              'clientInfo': {'name': 'travel-assistant', 'version': '0.1'}})
        if result.get('protocolVersion') != '2025-11-25':
            raise RuntimeError('铁路 MCP 协议版本不兼容')
        await self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})

    async def send(self, message: dict) -> None:
        if not self.process or self.process.returncode is not None:
            raise RuntimeError('铁路 MCP 服务未启动')
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode())
        await self.process.stdin.drain()

    async def rpc(self, method: str, params: dict) -> dict:
        self.sequence += 1
        request_id = self.sequence
        await self.send({'jsonrpc': '2.0', 'id': request_id, 'method': method, 'params': params})
        async with asyncio.timeout(self.timeout):
            for _ in range(100):
                line = await self.process.stdout.readline()
                if not line:
                    raise RuntimeError('铁路 MCP 连接中断')
                message = json.loads(line)
                if message.get('id') == request_id:
                    if 'error' in message:
                        raise RuntimeError('铁路 MCP 请求失败')
                    return message['result']
                if message.get('method') == 'ping' and 'id' in message:
                    await self.send({'jsonrpc': '2.0', 'id': message['id'], 'result': {}})
        raise RuntimeError('铁路 MCP 未返回有效结果')

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in ('search-stations', 'query-tickets', 'query-ticket-price'):
            raise ValueError('不支持的铁路查询工具')
        try:
            await self.start()
            result = await self.rpc('tools/call', {'name': name, 'arguments': arguments})
            if result.get('isError') or result.get('is_error'):
                raise RuntimeError('铁路查询工具执行失败')
            for content in result.get('content', []):
                if content.get('type') == 'text':
                    data = json.loads(content['text'])
                    if isinstance(data, dict):
                        return data
            raise RuntimeError('铁路查询返回格式错误')
        except BaseException:
            await self.close()
            raise

    async def close(self) -> None:
        if self.process and self.process.returncode is None:
            try:
                self.process.terminate()
                try:
                    await asyncio.wait_for(self.process.wait(), 2)
                except asyncio.TimeoutError:
                    self.process.kill()
                    await self.process.wait()
            except ProcessLookupError:
                await self.process.wait()
        self.process = None
