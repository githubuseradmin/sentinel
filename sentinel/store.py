"""SQLite persistence: events, samples (for uptime %), and incidents.

A connection is opened and closed per call (like the rest of the project) so the
store is safe to use from a long-running loop without leaking handles or holding
a lock. The live per-target status lives in memory (the state machine); the store
is the durable history behind the status page and the uptime numbers.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from typing import Optional

from .models import Event


class Store:
    def __init__(self, path: str = "sentinel.db") -> None:
        self.path = path
        self._init()

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init(self) -> None:
        with self._db() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts       REAL NOT NULL,
                    kind     TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    source   TEXT NOT NULL,
                    title    TEXT NOT NULL,
                    detail   TEXT
                );
                CREATE TABLE IF NOT EXISTS samples (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts         REAL NOT NULL,
                    target     TEXT NOT NULL,
                    ok         INTEGER NOT NULL,
                    status     TEXT NOT NULL,
                    latency_ms REAL
                );
                CREATE INDEX IF NOT EXISTS ix_samples_target_ts ON samples(target, ts);
                CREATE TABLE IF NOT EXISTS incidents (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    target     TEXT NOT NULL,
                    severity   TEXT NOT NULL,
                    detail     TEXT,
                    started_ts REAL NOT NULL,
                    ended_ts   REAL
                );
                CREATE INDEX IF NOT EXISTS ix_incident_open ON incidents(target, ended_ts);
                """
            )

    # -- writes --------------------------------------------------------------
    def record_event(self, e: Event) -> None:
        with self._db() as c:
            c.execute(
                "INSERT INTO events (ts, kind, severity, source, title, detail) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (e.ts or time.time(), e.kind, e.severity.value, e.source, e.title, e.detail),
            )

    def record_sample(self, target: str, ok: bool, status: str,
                      latency_ms: Optional[float], ts: Optional[float] = None) -> None:
        with self._db() as c:
            c.execute(
                "INSERT INTO samples (ts, target, ok, status, latency_ms) VALUES (?, ?, ?, ?, ?)",
                (ts or time.time(), target, 1 if ok else 0, status, latency_ms),
            )

    def open_incident(self, target: str, severity: str, detail: str,
                      ts: Optional[float] = None) -> None:
        """Open an incident unless one is already ongoing for this target."""
        with self._db() as c:
            ongoing = c.execute(
                "SELECT 1 FROM incidents WHERE target=? AND ended_ts IS NULL", (target,)
            ).fetchone()
            if ongoing:
                return
            c.execute(
                "INSERT INTO incidents (target, severity, detail, started_ts) VALUES (?, ?, ?, ?)",
                (target, severity, detail, ts or time.time()),
            )

    def close_incident(self, target: str, ts: Optional[float] = None) -> None:
        with self._db() as c:
            c.execute(
                "UPDATE incidents SET ended_ts=? WHERE target=? AND ended_ts IS NULL",
                (ts or time.time(), target),
            )

    # -- reads ---------------------------------------------------------------
    def uptime(self, target: str, since_ts: float) -> Optional[float]:
        """Percentage of successful samples for ``target`` since ``since_ts``."""
        with self._db() as c:
            row = c.execute(
                "SELECT COUNT(*) total, COALESCE(SUM(ok), 0) oks "
                "FROM samples WHERE target=? AND ts>=?",
                (target, since_ts),
            ).fetchone()
        if not row or row["total"] == 0:
            return None
        return round(100.0 * row["oks"] / row["total"], 3)

    def recent_events(self, limit: int = 20) -> list[sqlite3.Row]:
        with self._db() as c:
            return c.execute(
                "SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,)
            ).fetchall()

    def open_incidents(self) -> list[sqlite3.Row]:
        with self._db() as c:
            return c.execute(
                "SELECT * FROM incidents WHERE ended_ts IS NULL ORDER BY started_ts"
            ).fetchall()

    # -- maintenance ---------------------------------------------------------
    def prune(self, older_than_ts: float) -> None:
        """Drop history older than ``older_than_ts`` so the DB stays small.

        Samples and events are time-series; closed incidents past the cutoff are
        removed too, while *open* incidents are always kept.
        """
        with self._db() as c:
            c.execute("DELETE FROM samples WHERE ts < ?", (older_than_ts,))
            c.execute("DELETE FROM events WHERE ts < ?", (older_than_ts,))
            c.execute(
                "DELETE FROM incidents WHERE ended_ts IS NOT NULL AND ended_ts < ?",
                (older_than_ts,),
            )
