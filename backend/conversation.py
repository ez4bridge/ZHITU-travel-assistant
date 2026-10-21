"""Bounded, local study conversations, separate from immutable itinerary versions."""
from collections import OrderedDict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import time
from uuid import uuid4

from .config import settings

MAX_TURNS = 20
MEMORY_CHARS = 2400


def encoded(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationError(ValueError):
    def __init__(self, status: int, message: str):
        self.status = status
        super().__init__(message)


def excerpt_sentences(text: str, limit: int = 600) -> str:
    pieces, length = [], 0
    for sentence in re.split(r'(?<=[。！？!?\n])', text):
        if length + len(sentence) > limit:
            break
        pieces.append(sentence)
        length += len(sentence)
    return ''.join(pieces) + ('[较长内容已省略]' if length < len(text) else '')


def bounded_observation(value: dict | None) -> dict | None:
    if not value:
        return None
    return {'kind': value.get('kind', 'unclear'), **{
        key: [str(item)[:100] for item in value.get(key, [])[:count]]
        for key, count in [('objects', 3), ('visible_text', 4), ('search_terms', 3), ('uncertainties', 2)]}}


def context_for(turns: list[dict], use_photo: bool = True) -> dict:
    """Only complete recent turns are selected; this is a character, not token, budget."""
    context = {'recent_turns': []}
    if turns and use_photo:
        memory = turns[-1]['memory']
        observation = bounded_observation(memory.get('observation')) if memory.get('photo_active') else None
        if observation:
            context.update(photo_observation=observation, photo_place=memory.get('photo_place', '')[:120])
    for turn in reversed(turns[-3:]):
        memory = turn['memory']
        item = {'question': turn['question'], 'answer_excerpt': excerpt_sentences(memory.get('answer', ''), 400),
                'place': turn['place_name'], 'unknowns': memory.get('unknowns', [])[:3]}
        candidate = {**context, 'recent_turns': [item, *context['recent_turns']]}
        if len(encoded(candidate)) <= MEMORY_CHARS:
            context = candidate
        else:
            break
    return context if context['recent_turns'] or 'photo_observation' in context else {}


class ConversationStore:
    def __init__(self, path: Path | None = None):
        self.path = path or settings.runtime / 'study_conversations.sqlite3'
        self.active: set[str] = set()
        # Only temporary web excerpts live here, never in database records.
        self.cache: OrderedDict[str, tuple[float, list[dict]]] = OrderedDict()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY, plan_id TEXT, revision INTEGER NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    revision INTEGER NOT NULL, request_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    kind TEXT NOT NULL, question TEXT NOT NULL, place_name TEXT NOT NULL,
                    created_at TEXT NOT NULL, result TEXT NOT NULL, memory TEXT NOT NULL,
                    UNIQUE(conversation_id, request_id));
            ''')
        self.prune()

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys = ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    def prune(self):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        with self.connect() as db:
            db.execute('DELETE FROM conversations WHERE updated_at < ?', (cutoff,))

    def create(self, plan_id: str | None) -> dict:
        self.prune()
        cid, now = str(uuid4()), utc_now()
        with self.connect() as db:
            db.execute('INSERT INTO conversations VALUES (?, ?, 0, ?, ?)', (cid, plan_id, now, now))
        return self.get(cid)

    def get(self, cid: str, internal: bool = False) -> dict:
        with self.connect() as db:
            row = db.execute('SELECT * FROM conversations WHERE id = ?', (cid,)).fetchone()
            if not row or row['updated_at'] < (datetime.now(timezone.utc) - timedelta(days=7)).isoformat():
                raise ConversationError(404, '这段对话已不存在或已过期，请开始新对话')
            records = db.execute('SELECT * FROM conversation_turns WHERE conversation_id = ? ORDER BY revision', (cid,)).fetchall()
        turns = []
        for record in records:
            item = {key: record[key] for key in ('id', 'request_id', 'revision', 'kind', 'question', 'place_name', 'created_at')}
            item['result'] = json.loads(record['result'])
            if internal:
                item['memory'] = json.loads(record['memory'])
            turns.append(item)
        latest_memory = json.loads(records[-1]['memory']) if records else {}
        return {**dict(row), 'turns': turns, 'photo_context_available': bool(latest_memory.get('photo_active'))}

    def delete(self, cid: str):
        with self.connect() as db:
            db.execute('DELETE FROM conversations WHERE id = ?', (cid,))
        self.cache.pop(cid, None)

    def prepare(self, body) -> tuple[dict, str, dict | None]:
        cid = body.conversation_id
        session = self.get(cid, internal=True)
        if session['plan_id'] != body.plan_id:
            raise ConversationError(422, '请在当前行程对应的对话中提问')
        fingerprint = hashlib.sha256(encoded(body.model_dump(exclude={
            'request_id', 'expected_revision'})).encode()).hexdigest()
        with self.connect() as db:
            previous = db.execute('SELECT fingerprint, result FROM conversation_turns WHERE conversation_id = ? AND request_id = ?',
                                  (cid, body.request_id)).fetchone()
        if previous:
            if previous['fingerprint'] != fingerprint:
                raise ConversationError(409, '这个请求编号已用于其他内容，请重新发送')
            return session, fingerprint, json.loads(previous['result'])
        if session['revision'] != body.expected_revision or cid in self.active:
            raise ConversationError(409, '对话正在处理或已有更新，请刷新对话后重试')
        self.active.add(cid)
        return session, fingerprint, None

    def release(self, cid: str):
        self.active.discard(cid)

    def commit(self, body, fingerprint: str, kind: str, place: str, result: dict, memory: dict) -> dict:
        cid, now, tid = body.conversation_id, utc_now(), str(uuid4())
        result = {**result, 'conversation_id': cid, 'request_id': body.request_id,
                  'revision': body.expected_revision + 1, 'turn_id': tid, 'kind': kind}
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            changed = db.execute('UPDATE conversations SET revision = revision + 1, updated_at = ? WHERE id = ? AND revision = ?',
                                 (now, cid, body.expected_revision)).rowcount
            if not changed:
                raise ConversationError(409, '对话已清空或更新，本次回答未写入，请刷新对话')
            db.execute('INSERT INTO conversation_turns VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)',
                (tid, cid, result['revision'], body.request_id, fingerprint, kind, body.question, place[:120],
                 now, encoded(result), encoded(memory)))
            db.execute('DELETE FROM conversation_turns WHERE conversation_id = ? AND revision <= ?',
                       (cid, result['revision'] - MAX_TURNS))
        return result

    def web_cache(self, cid: str) -> list[dict]:
        item = self.cache.get(cid)
        if item and item[0] > time.monotonic():
            return item[1]
        self.cache.pop(cid, None)
        return []

    def cache_web(self, cid: str, sources: list[dict]):
        self.cache[cid] = (time.monotonic() + 600, [dict(s) for s in sources if s.get('kind') == 'web'][:2])
        self.cache.move_to_end(cid)
        while len(self.cache) > 64:
            self.cache.popitem(last=False)


def turn_memory(result: dict, context: dict, photo_active: bool = False, observation: dict | None = None,
                photo_place: str = '') -> dict:
    if 'answer' in result:
        answer = result['answer']
        text = '\n'.join([answer.get('visible_summary', ''), *[c['text'] for c in answer.get('history', [])]])
        unknowns = answer.get('unknowns', [])
    else:
        text = '\n'.join(p['text'] for p in result.get('paragraphs', [])) or result.get('message', '')
        unknowns = []
    return {'answer': text, 'unknowns': [str(s)[:160] for s in unknowns[:3]], 'photo_active': photo_active,
            'observation': bounded_observation(observation or context.get('photo_observation')) if photo_active else None,
            'photo_place': photo_place[:120] or context.get('photo_place', '')}
