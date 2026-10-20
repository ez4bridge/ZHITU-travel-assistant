import sqlite3
from .config import settings
from .models import PlanRecord


class Store:
    def __init__(self, path=None):
        self.path = path or settings.runtime / 'plans.sqlite3'
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, created_at TEXT NOT NULL, data TEXT NOT NULL)')

    def connect(self):
        return sqlite3.connect(self.path, timeout=10)

    def save(self, plan):
        with self.connect() as db:
            db.execute('INSERT INTO plans VALUES (?, ?, ?)', (plan.id, plan.created_at.isoformat(), plan.model_dump_json()))

    def get(self, plan_id):
        with self.connect() as db:
            row = db.execute('SELECT data FROM plans WHERE id = ?', (plan_id,)).fetchone()
        return PlanRecord.model_validate_json(row[0], context={'legacy_record': True}) if row else None

    def update_routes(self, plan, expected_revision):
        # One transaction prevents two browser tabs from overwriting each other.
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT data FROM plans WHERE id = ?', (plan.id,)).fetchone()
            if not row or PlanRecord.model_validate_json(row[0], context={'legacy_record': True}).routing_revision != expected_revision:
                raise ValueError('行程已被其他页面更新，请重新打开后选择交通方式')
            db.execute('UPDATE plans SET data = ? WHERE id = ?', (plan.model_dump_json(), plan.id))

    def delete(self, plan_id: str):
        with self.connect() as db:
            db.execute('DELETE FROM plans WHERE id = ?', (plan_id,))

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM plans ORDER BY created_at DESC LIMIT 50').fetchall()
        plans = [PlanRecord.model_validate_json(row[0], context={'legacy_record': True}) for row in rows]
        by_id = {p.id: p for p in plans}
        hidden = set()
        def structure(p):
            return (p.request.model_dump_json(), [[(s.name, s.kind, s.poi_id) for s in d.stops] for d in p.itinerary.days])
        for p in plans:
            parent = by_id.get(p.parent_id)
            if parent and p.revision_instruction == '更换单段交通方式并重新计算时间和交通费用' and structure(p) == structure(parent):
                hidden.add(parent.id)
        return [{'id': p.id, 'title': p.itinerary.title, 'destination': p.request.destination,
                 'start_date': str(p.request.start_date), 'revision': p.revision, 'created_at': p.created_at.isoformat(),
                 'status': p.validation.status} for p in plans if p.id not in hidden]
