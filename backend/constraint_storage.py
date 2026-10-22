"""Atomic preview application; immutable plans and monotonic confirmed facts."""
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
from uuid import uuid4

from .models import PlanRecord


class ConstraintStale(ValueError):
    pass


def fingerprint(plan):
    body = plan.model_dump(mode='json')
    # expense_totals is a derived read-time cache; the authoritative expense rows
    # are already covered. Recomputing that cache must not create a false race.
    body.pop('expense_totals', None)
    raw = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


def assert_legacy_editable(store, plan):
    """An ordinary AI/route refresh cannot undo an accepted execution ledger."""
    state = ConstraintStore(store.path).state(plan)
    if state['state_revision'] > 0:
        raise ConstraintStale('此行程已有已确认约束或途中事实，请通过“约束与途中调整”继续修改；旧自动修改会绕过保护。')


class ConstraintStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS constraint_chains (
                    root_id TEXT PRIMARY KEY, current_plan_id TEXT NOT NULL,
                    state_revision INTEGER NOT NULL, state_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS constraint_members (
                    plan_id TEXT PRIMARY KEY, root_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS constraint_previews (
                    preview_id TEXT PRIMARY KEY, base_id TEXT NOT NULL, root_id TEXT NOT NULL,
                    base_fingerprint TEXT NOT NULL, routing_revision INTEGER NOT NULL,
                    state_revision INTEGER NOT NULL, expires_at TEXT NOT NULL,
                    candidate_json TEXT NOT NULL, state_json TEXT NOT NULL,
                    response_json TEXT NOT NULL, applied_plan_id TEXT);
                CREATE TABLE IF NOT EXISTS constraint_plan_settings (
                    plan_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS constraint_preview_settings (
                    preview_id TEXT PRIMARY KEY, settings_json TEXT NOT NULL);
            ''')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    @staticmethod
    def _state(db, plan):
        cursor, visited = plan, set()
        # A pre-existing A->ordinary B->ordinary C branch must not acquire a fresh
        # confirmation chain after A has been accepted through constraints.
        for _ in range(256):
            if cursor.id in visited:
                raise ConstraintStale('行程历史存在循环引用，请停止修改并核对历史')
            visited.add(cursor.id)
            member = db.execute('SELECT root_id FROM constraint_members WHERE plan_id=?', (cursor.id,)).fetchone()
            if member:
                row = db.execute('SELECT current_plan_id,state_revision,state_json FROM constraint_chains WHERE root_id=?', member).fetchone()
                if not row:
                    raise ConstraintStale('约束事实链不完整，请核对本地历史')
                preferences = db.execute('SELECT settings_json FROM constraint_plan_settings WHERE plan_id=?', (row[0],)).fetchone()
                windows = json.loads(preferences[0]).get('day_windows', []) if preferences else []
                return {'root_id': member[0], 'current_plan_id': row[0], 'state_revision': row[1], 'activities': json.loads(row[2]), 'day_windows': windows}
            if not cursor.parent_id:
                break
            ancestor = db.execute('SELECT data FROM plans WHERE id=?', (cursor.parent_id,)).fetchone()
            if not ancestor:
                break
            cursor = PlanRecord.model_validate_json(ancestor[0], context={'legacy_record': True})
        else:
            raise ConstraintStale('行程历史超过256层，请核对后再修改')
        return {'root_id': plan.id, 'current_plan_id': plan.id, 'state_revision': 0, 'activities': {}, 'day_windows': []}

    def state(self, plan):
        with self.connect() as db:
            return self._state(db, plan)

    def save_preview(self, base, expected_state, candidate, activities, response, *, day_windows=None):
        preview_id = str(uuid4())
        response = {**response, 'preview_id': preview_id}
        with self.connect() as db:
            db.execute('INSERT INTO constraint_previews VALUES (?,?,?,?,?,?,?,?,?,?,NULL)', (
                preview_id, base.id, expected_state['root_id'], fingerprint(base), base.routing_revision,
                expected_state['state_revision'], (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(),
                candidate.model_dump_json(), json.dumps(activities, ensure_ascii=False),
                json.dumps(response, ensure_ascii=False)))
            if day_windows is not None:
                db.execute('INSERT INTO constraint_preview_settings VALUES (?,?)',
                           (preview_id, json.dumps({'day_windows': day_windows}, ensure_ascii=False)))
        return response

    def apply(self, base_id, preview_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT base_id,root_id,base_fingerprint,routing_revision,state_revision,expires_at,candidate_json,state_json,applied_plan_id FROM constraint_previews WHERE preview_id=?', (preview_id,)).fetchone()
            if not row or row[0] != base_id:
                raise ConstraintStale('预览不存在或不属于当前父行程，请重新预览')
            if row[8]:
                saved = db.execute('SELECT data FROM plans WHERE id=?', (row[8],)).fetchone()
                if not saved:
                    raise ConstraintStale('已应用的行程已被删除，请刷新历史')
                return PlanRecord.model_validate_json(saved[0], context={'legacy_record': True})
            if datetime.fromisoformat(row[5]) < datetime.now(timezone.utc):
                raise ConstraintStale('预览已超过30分钟，请重新计算')
            saved = db.execute('SELECT data FROM plans WHERE id=?', (base_id,)).fetchone()
            if not saved:
                raise ConstraintStale('父行程已删除，请刷新')
            base = PlanRecord.model_validate_json(saved[0], context={'legacy_record': True})
            state = self._state(db, base)
            if (fingerprint(base) != row[2] or base.routing_revision != row[3]
                    or state['state_revision'] != row[4] or state['current_plan_id'] != base.id
                    or state['root_id'] != row[1]):
                raise ConstraintStale('行程、交通或已确认事实已变化，请打开最新版本重新预览')
            plan = PlanRecord.model_validate_json(row[6], context={'legacy_record': True})
            prior, proposed = state['activities'], json.loads(row[7])
            if any(v.get('status') == 'completed' and proposed.get(k, {}).get('status') != 'completed' for k, v in prior.items()):
                raise ConstraintStale('不能从旧版本撤销已确认完成的事实')
            db.execute('INSERT INTO plans VALUES (?,?,?)', (plan.id, plan.created_at.isoformat(), plan.model_dump_json()))
            db.execute('INSERT INTO constraint_chains VALUES (?,?,?,?) ON CONFLICT(root_id) DO UPDATE SET current_plan_id=excluded.current_plan_id,state_revision=excluded.state_revision,state_json=excluded.state_json',
                       (state['root_id'], plan.id, state['state_revision'] + 1, row[7]))
            for member_id in (base.id, plan.id):
                db.execute('INSERT INTO constraint_members VALUES (?,?) ON CONFLICT(plan_id) DO NOTHING', (member_id, state['root_id']))
            db.execute('UPDATE constraint_previews SET applied_plan_id=? WHERE preview_id=?', (plan.id, preview_id))
            preferences = db.execute('SELECT settings_json FROM constraint_preview_settings WHERE preview_id=?', (preview_id,)).fetchone()
            if preferences:
                db.execute('INSERT INTO constraint_plan_settings VALUES (?,?)', (plan.id, preferences[0]))
            return plan
