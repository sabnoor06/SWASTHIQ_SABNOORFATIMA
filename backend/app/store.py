"""
store.py — SQLite persistence for computed reports.

Consistency on update: a report is keyed by clinic date and written with
INSERT ... ON CONFLICT DO UPDATE inside a single transaction, so re-ingesting
a day replaces it atomically rather than accumulating duplicates. A reader
either sees the whole previous report or the whole new one, never a mix.

Money is stored as the serialised report JSON with integer paise intact; we
never round-trip through float.
"""

import json
import sqlite3
import threading
from datetime import datetime, timezone

from .schemas import ReportResponse

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    date         TEXT PRIMARY KEY,
    clinic_id    TEXT,
    computed_at  TEXT NOT NULL,
    payload      TEXT NOT NULL
);
"""


class ReportStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(SCHEMA)
        self._conn.commit()

    def save(self, date: str, report: ReportResponse) -> None:
        payload = report.model_dump_json()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO reports (date, clinic_id, computed_at, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(date) DO UPDATE SET
                    clinic_id   = excluded.clinic_id,
                    computed_at = excluded.computed_at,
                    payload     = excluded.payload
                """,
                (
                    date,
                    report.reconciliation.clinic_id,
                    datetime.now(timezone.utc).isoformat(),
                    payload,
                ),
            )

    def get(self, date: str) -> ReportResponse | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM reports WHERE date = ?", (date,)
            ).fetchone()
        if row is None:
            return None
        return ReportResponse.model_validate(json.loads(row["payload"]))

    def dates(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT date FROM reports ORDER BY date DESC"
            ).fetchall()
        return [r["date"] for r in rows]
