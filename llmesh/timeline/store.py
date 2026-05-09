"""Task Timeline Store — SQLite-backed time-series event log.

Records the lifecycle of every MCP task as a sequence of timestamped events:

  received -> firewall_allow|firewall_block|firewall_summarize
           -> [summarized]
           -> llm_invoked -> llm_responded
           -> validated
           -> completed | failed

A task with no terminal event (completed/failed) is **resumable**: the client
can retry using the same task_id with a fresh nonce.

Design decisions:
- WAL mode + threading.Lock: safe for concurrent requests in a single process.
- No prompt or output text stored: only task_id, timestamps, event types, and
  lightweight metadata (latency_ms, tool, reason, etc.).
- Env var LLMESH_TIMELINE_DB_PATH enables the store; absent = zero overhead.
- TTL pruning runs on each record() call to bound DB size.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_TERMINAL_EVENTS: frozenset[str] = frozenset({"completed", "failed"})
_DEFAULT_TTL_DAYS: int = 7
_PRUNE_EVERY_N: int = 500  # prune at most once every N records to amortise cost


def _now_utc() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(frozen=True)
class TimelineEvent:
    event_id: int
    task_id: str
    node_id: str
    event_type: str
    timestamp_utc: str
    metadata: dict[str, Any]

    @property
    def is_terminal(self) -> bool:
        return self.event_type in _TERMINAL_EVENTS

    def delta_ms(self, other: "TimelineEvent") -> int:
        """Milliseconds between this event and *other* (positive = self is later)."""
        def _parse(ts: str) -> datetime:
            return datetime.fromisoformat(ts)
        return int((_parse(self.timestamp_utc) - _parse(other.timestamp_utc)).total_seconds() * 1000)


_DDL = """
CREATE TABLE IF NOT EXISTS timeline_events (
    event_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       TEXT    NOT NULL,
    node_id       TEXT    NOT NULL DEFAULT '',
    event_type    TEXT    NOT NULL,
    timestamp_utc TEXT    NOT NULL,
    metadata_json TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_task_id   ON timeline_events(task_id);
CREATE INDEX IF NOT EXISTS idx_timestamp ON timeline_events(timestamp_utc);
CREATE INDEX IF NOT EXISTS idx_node      ON timeline_events(node_id, timestamp_utc);
"""


class TimelineStore:
    """Thread-safe SQLite time-series store for task lifecycle events."""

    def __init__(
        self,
        db_path: str | Path,
        ttl_days: int = _DEFAULT_TTL_DAYS,
    ) -> None:
        self._db_path = str(db_path)
        self._ttl_days = ttl_days
        self._lock = threading.Lock()
        self._record_count = 0
        self._conn = sqlite3.connect(
            self._db_path, check_same_thread=False, isolation_level=None
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                self._conn.execute(stmt)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def record(
        self,
        task_id: str,
        node_id: str,
        event_type: str,
        **metadata: Any,
    ) -> None:
        """Append one event. Thread-safe. Never raises — errors are silently dropped
        so timeline failures never break the request pipeline."""
        try:
            ts = _now_utc()
            meta_json = json.dumps(metadata, default=str)
            with self._lock:
                self._conn.execute(
                    "INSERT INTO timeline_events"
                    " (task_id, node_id, event_type, timestamp_utc, metadata_json)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (task_id, node_id, event_type, ts, meta_json),
                )
                self._record_count += 1
                if self._record_count % _PRUNE_EVERY_N == 0:
                    self._prune_locked()
        except Exception:
            pass  # timeline is observability, not a security gate — never block requests

    def _prune_locked(self) -> None:
        """Remove events older than ttl_days. Must be called with _lock held."""
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._ttl_days)).isoformat()
        self._conn.execute(
            "DELETE FROM timeline_events WHERE timestamp_utc < ?", (cutoff,)
        )

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_task_timeline(self, task_id: str) -> list[TimelineEvent]:
        """Return all events for *task_id* in chronological order."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, task_id, node_id, event_type, timestamp_utc, metadata_json"
                " FROM timeline_events WHERE task_id = ?"
                " ORDER BY event_id ASC",
                (task_id,),
            ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_recent_events(
        self,
        limit: int = 50,
        *,
        node_id: str = "",
        event_type: str = "",
    ) -> list[TimelineEvent]:
        """Return the most recent events, newest first."""
        with self._lock:
            if node_id and event_type:
                rows = self._conn.execute(
                    "SELECT event_id, task_id, node_id, event_type, timestamp_utc, metadata_json"
                    " FROM timeline_events WHERE node_id = ? AND event_type = ?"
                    " ORDER BY event_id DESC LIMIT ?",
                    (node_id, event_type, limit),
                ).fetchall()
            elif node_id:
                rows = self._conn.execute(
                    "SELECT event_id, task_id, node_id, event_type, timestamp_utc, metadata_json"
                    " FROM timeline_events WHERE node_id = ?"
                    " ORDER BY event_id DESC LIMIT ?",
                    (node_id, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT event_id, task_id, node_id, event_type, timestamp_utc, metadata_json"
                    " FROM timeline_events ORDER BY event_id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def get_resumable_tasks(self) -> list[dict[str, str]]:
        """Return tasks that have no terminal event (completed/failed).

        These are tasks the client may retry with a fresh nonce. Each entry is:
          {"task_id": ..., "node_id": ..., "last_event": ..., "last_ts": ..., "idle_sec": ...}
        """
        terminal_list = ", ".join(f"'{e}'" for e in _TERMINAL_EVENTS)
        with self._lock:
            rows = self._conn.execute(
                f"""  # nosec B608 - interpolated value is _TERMINAL_EVENTS literal tuple.
                SELECT task_id,
                       node_id,
                       event_type   AS last_event,
                       timestamp_utc AS last_ts
                FROM   timeline_events t1
                WHERE  event_id = (
                           SELECT MAX(event_id) FROM timeline_events t2
                           WHERE t2.task_id = t1.task_id
                       )
                AND    event_type NOT IN ({terminal_list})
                ORDER  BY last_ts DESC
                """
            ).fetchall()

        now = datetime.now(timezone.utc)
        result = []
        for task_id, node_id, last_event, last_ts in rows:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                idle_sec = int((now - last_dt).total_seconds())
            except Exception:
                idle_sec = -1
            result.append({
                "task_id":    task_id,
                "node_id":    node_id,
                "last_event": last_event,
                "last_ts":    last_ts,
                "idle_sec":   str(idle_sec),
            })
        return result

    def event_count(self) -> int:
        with self._lock:
            return self._conn.execute(
                "SELECT COUNT(*) FROM timeline_events"
            ).fetchone()[0]

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_event(row: tuple) -> TimelineEvent:
        event_id, task_id, node_id, event_type, ts, meta_json = row
        try:
            metadata = json.loads(meta_json)
        except Exception:
            metadata = {}
        return TimelineEvent(
            event_id=event_id,
            task_id=task_id,
            node_id=node_id,
            event_type=event_type,
            timestamp_utc=ts,
            metadata=metadata,
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
