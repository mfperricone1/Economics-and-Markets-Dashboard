"""SQLite cache of raw observations. History accumulates across runs.

Observations are keyed by (series_id, source, code, date) so a fallback source
(e.g. a monthly series standing in for a daily one) never mixes with the
primary's history.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS obs (
    series_id TEXT NOT NULL, source TEXT NOT NULL, code TEXT NOT NULL,
    date TEXT NOT NULL, value REAL NOT NULL,
    PRIMARY KEY (series_id, source, code, date)
);
CREATE TABLE IF NOT EXISTS fetch_log (
    series_id TEXT NOT NULL, source TEXT NOT NULL, code TEXT NOT NULL,
    last_attempt TEXT, last_success TEXT, last_error TEXT,
    PRIMARY KEY (series_id, source, code)
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.executescript(SCHEMA)
        self.lock = threading.Lock()  # one connection shared by the fetch threads

    def upsert(self, sid: str, source: str, code: str, s: pd.Series) -> None:
        rows = [(sid, source, code, d.strftime("%Y-%m-%d"), float(v)) for d, v in s.items()]
        with self.lock, self.db:
            self.db.executemany("INSERT OR REPLACE INTO obs VALUES (?,?,?,?,?)", rows)
            self.db.execute(
                "INSERT INTO fetch_log VALUES (?,?,?,?,?,NULL) ON CONFLICT DO UPDATE SET "
                "last_attempt=excluded.last_attempt, last_success=excluded.last_success, "
                "last_error=NULL", (sid, source, code, _now(), _now()))

    def log_error(self, sid: str, source: str, code: str, err: str) -> None:
        with self.lock, self.db:
            self.db.execute(
                "INSERT INTO fetch_log VALUES (?,?,?,?,NULL,?) ON CONFLICT DO UPDATE SET "
                "last_attempt=excluded.last_attempt, last_error=excluded.last_error",
                (sid, source, code, _now(), err[:500]))

    def load(self, sid: str, source: str, code: str) -> pd.Series:
        with self.lock:
            rows = self.db.execute(
                "SELECT date, value FROM obs WHERE series_id=? AND source=? AND code=? ORDER BY date",
                (sid, source, code)).fetchall()
        if not rows:
            return pd.Series(dtype=float)
        d, v = zip(*rows)
        return pd.Series(v, index=pd.DatetimeIndex(d), dtype=float)

    def last_date(self, sid: str, source: str, code: str) -> pd.Timestamp | None:
        with self.lock:
            r = self.db.execute("SELECT MAX(date) FROM obs WHERE series_id=? AND source=? AND code=?",
                                (sid, source, code)).fetchone()
        return pd.Timestamp(r[0]) if r and r[0] else None

    def best_cached(self, sid: str) -> tuple[str, str] | None:
        """(source, code) with the most recent successful fetch for this series."""
        with self.lock:
            r = self.db.execute(
                "SELECT source, code FROM fetch_log WHERE series_id=? AND last_success IS NOT NULL "
                "ORDER BY last_success DESC LIMIT 1", (sid,)).fetchone()
        return (r[0], r[1]) if r else None
