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

    def list(self):
        with self.connect() as db:
            rows = db.execute('SELECT data FROM plans ORDER BY created_at DESC LIMIT 50').fetchall()
        return [{'id': p.id, 'title': p.itinerary.title, 'destination': p.request.destination,
                 'start_date': str(p.request.start_date), 'revision': p.revision, 'created_at': p.created_at.isoformat(),
                 'status': p.validation.status} for p in (PlanRecord.model_validate_json(row[0], context={'legacy_record': True}) for row in rows)]
