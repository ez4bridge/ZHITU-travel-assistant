"""Async retrieval facade. Native inference stays in a disposable local process."""
import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

from .rag_common import MODES, digest, diverse, fuse, index_identity, read_json

logger = logging.getLogger(__name__)


class RetrievalService:
    def __init__(self, knowledge, directory: Path, mode: str = 'auto', timeout: float = 8):
        if mode not in MODES:
            raise ValueError('Unsupported retrieval mode')
        self.knowledge, self.directory, self.mode, self.timeout = knowledge, Path(directory), mode, timeout
        self.process = None
        self._startup, self._lock = None, asyncio.Lock()
        self._retry_after, self._sequence = 0.0, 0
        self.actual_mode, self.reason, self.last_seconds = 'bm25', '', 0.0
        self.worker_stats = {}

    def manifest(self) -> dict:
        value = read_json(self.directory / 'index.json')
        if value['identity'] != index_identity(self.knowledge.version):
            raise ValueError('资料或索引参数已变化，请重新构建RAG索引')
        if value['fingerprint'] != digest({k: v for k, v in value.items() if k != 'fingerprint'}):
            raise ValueError('RAG索引清单校验失败，请重新构建')
        return value

    def selected_mode(self) -> str:
        if self.mode != 'auto':
            return self.mode
        try:
            manifest = self.manifest()
            selection = read_json(self.directory / 'selection.json')
            if selection['fingerprint'] != manifest['fingerprint']:
                raise ValueError('评测选择与当前索引不一致，请重新评测')
            mode = selection['mode']
            if mode not in ('bm25', 'hybrid', 'hybrid_rerank'):
                raise ValueError('Invalid selection')
            return mode
        except (OSError, ValueError, KeyError, TypeError):
            return 'bm25'

    def diagnostic(self) -> dict:
        try:
            self.manifest()
            ready, reason = True, self.reason
        except (OSError, ValueError, KeyError, TypeError):
            ready, reason = False, '模型索引未准备好或与当前资料不一致，使用BM25'
        selected = self.selected_mode()
        if self.mode == 'auto' and selected == 'bm25' and ready and not reason:
            reason = 'auto按本地评测选择BM25；未评测时也默认BM25'
        return {'configured_mode': self.mode, 'selected_mode': selected, 'actual_mode': self.actual_mode,
                'index_ready': ready, 'worker_ready': bool(self.process and self.process.returncode is None
                    and self._startup and self._startup.done() and not self._startup.cancelled()
                    and self._startup.exception() is None),
                'fallback_reason': reason, 'last_seconds': round(self.last_seconds, 4)}

    def start_background(self):
        if self.selected_mode() != 'bm25' and self._startup is None:
            self._startup = asyncio.create_task(self._start())
            # A missing optional model must not produce an unhandled task exception.
            self._startup.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    async def _start(self):
        manifest = self.manifest()
        env = dict(os.environ, PYTHONUTF8='1', HF_HUB_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                   TOKENIZERS_PARALLELISM='false', DO_NOT_TRACK='1')
        command = [sys.executable, '-X', 'utf8', '-m', 'backend.rag_worker', '--rag-dir', str(self.directory)]
        if self.selected_mode() == 'hybrid_rerank':
            command.append('--rerank')
        self.directory.mkdir(parents=True, exist_ok=True)
        # Only the worker can write this local diagnostic; no request bodies are logged.
        with (self.directory / 'worker.log').open('ab') as errors:
            self.process = await asyncio.create_subprocess_exec(*command,
                cwd=Path(__file__).resolve().parents[1], env=env, stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=errors, limit=2_000_000,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            async with asyncio.timeout(60):
                line = await self.process.stdout.readline()
                reply = json.loads(line)
                if reply.get('status') != 'ready' or reply.get('fingerprint') != manifest['fingerprint']:
                    raise RuntimeError('RAG模型进程未就绪，请检查本地worker.log')
                self.worker_stats = reply
        except BaseException:
            await self._kill()
            raise

    async def warm(self):
        if self._startup is None and time.monotonic() < self._retry_after:
            raise RuntimeError('RAG模型暂不可用，稍后重试')
        if self._startup and self._startup.done() and (self._startup.cancelled() or self._startup.exception()):
            if time.monotonic() < self._retry_after:
                raise RuntimeError('RAG模型暂不可用，稍后重试')
            self._startup = None
        if self.process and self.process.returncode is not None:
            self._startup = None
        if self._startup is None:
            self._startup = asyncio.create_task(self._start())
        try:
            await asyncio.shield(self._startup)
        except Exception:
            self._retry_after = time.monotonic() + 30
            raise

    async def _rpc(self, operation: str, **arguments) -> dict:
        if self.process is None or self.process.returncode is not None:
            raise RuntimeError('RAG模型进程已退出')
        self._sequence += 1
        self.process.stdin.write((json.dumps({'id': self._sequence, 'op': operation, **arguments},
            ensure_ascii=False) + '\n').encode('utf-8'))
        await self.process.stdin.drain()
        line = await self.process.stdout.readline()
        if not line:
            raise RuntimeError('RAG模型进程异常退出')
        reply = json.loads(line)
        if reply.get('id') != self._sequence or reply.get('error'):
            raise RuntimeError('RAG模型请求失败，请检查本地worker.log')
        self.worker_stats.update(reply.get('stats', {}))
        return reply

    async def search(self, query: str, mode: str | None = None) -> list[dict]:
        started, fused, owns_worker = time.perf_counter(), None, False
        mode = mode or self.selected_mode()
        if mode not in MODES or mode == 'auto':
            raise ValueError('Explicit search mode required')
        bm25 = self.knowledge.rank_bm25(query)
        actual, reason, ids = 'bm25', '', bm25
        try:
            if mode != 'bm25' and query.strip():
                async with asyncio.timeout(self.timeout):
                    await self.warm()
                    async with self._lock:
                        owns_worker = True
                        try:
                            dense = (await self._rpc('dense', query=query))['ids']
                            ids, actual = dense, 'dense'
                            if mode in ('hybrid', 'hybrid_rerank'):
                                fused = fuse(bm25[:10], dense[:10])[:10]
                                ids, actual = fused, 'hybrid'
                            if mode == 'hybrid_rerank':
                                reply = await self._rpc('rerank', query=query, ids=fused)
                                if reply.get('rerank_error'):
                                    reason = '重排不可用，已使用混合检索'
                                else:
                                    ids, actual = reply['ids'], 'hybrid_rerank'
                        except BaseException:
                            # Dispose the interrupted protocol before releasing its single-owner lock.
                            await self._kill()
                            raise
                        finally:
                            owns_worker = False
        except asyncio.CancelledError:
            if owns_worker:
                await self._kill()
            raise
        except (Exception, TimeoutError) as error:
            if owns_worker:
                await self._kill()
            ids, actual = (fused, 'hybrid') if fused is not None else (bm25, 'bm25')
            reason = ('重排超时或失败，已使用混合检索' if fused is not None
                      else '向量检索超时或不可用，已使用BM25')
            logger.warning('retrieval fallback: %s (%s)', actual, type(error).__name__)
        self.actual_mode, self.reason = actual, reason
        self.last_seconds = time.perf_counter() - started
        return diverse(ids, self.knowledge.chunks)

    async def _kill(self):
        process, self.process = self.process, None
        if process and process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        self._startup = None
        self._retry_after = time.monotonic() + 30

    async def close(self):
        startup, self._startup = self._startup, None
        if startup and not startup.done():
            startup.cancel()
            await asyncio.gather(startup, return_exceptions=True)
        await self._kill()
