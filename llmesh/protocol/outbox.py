"""OutboxQueue — SQLite-backed store-and-forward queue for LLMesh messages.

Messages that fail to send are persisted here and replayed after reconnection.
Expired messages (deadline exceeded) are purged automatically.

DB path resolution order:
  1. db_path argument to __init__
  2. LLMESH_OUTBOX_PATH environment variable
  3. ":memory:" (in-process, suitable for tests)

Thread safety: uses a threading.Lock around all SQLite access.
Async callers should wrap operations with asyncio.to_thread().
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message import NodeAddress, UnifiedMessage

_DEFAULT_DB = ":memory:"
_ENV_VAR = "LLMESH_OUTBOX_PATH"

_DDL = """
CREATE TABLE IF NOT EXISTS outbox (
    id          TEXT    PRIMARY KEY,
    target_host TEXT    NOT NULL,
    target_port INTEGER NOT NULL,
    payload     BLOB    NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 0,
    deadline    REAL,
    queued_at   REAL    NOT NULL,
    attempts    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS outbox_priority ON outbox (priority DESC, queued_at ASC);
"""


class OutboxQueue:
    """Persistent store-and-forward queue backed by SQLite.

    Args:
        db_path: SQLite file path, or ":memory:" for an in-process store.
                 Defaults to LLMESH_OUTBOX_PATH env var, then ":memory:".
    """

    def __init__(self, db_path: str | None = None) -> None:
        path = db_path or os.environ.get(_ENV_VAR, _DEFAULT_DB)
        self._db_path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def enqueue(self, msg: "UnifiedMessage", target: "NodeAddress") -> None:
        """Persist *msg* for later delivery to *target*."""
        payload = msg.to_bytes()
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO outbox "
                "(id, target_host, target_port, payload, priority, deadline, queued_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    msg.id,
                    target.host,
                    target.port,
                    payload,
                    msg.priority,
                    msg.deadline,
                    time.time(),
                ),
            )
            self._conn.commit()

    def dequeue(self, n: int = 1) -> "list[tuple[UnifiedMessage, NodeAddress]]":
        """Return up to *n* pending messages ordered by priority (high first), then age.

        Does NOT remove them — call mark_sent() after successful delivery.
        Increments the attempts counter on each dequeue.
        """
        from .message import NodeAddress, UnifiedMessage

        with self._lock:
            rows = self._conn.execute(
                "SELECT id, target_host, target_port, payload "
                "FROM outbox "
                "ORDER BY priority DESC, queued_at ASC "
                "LIMIT ?",
                (n,),
            ).fetchall()

            if not rows:
                return []

            ids = [r[0] for r in rows]
            self._conn.executemany(
                "UPDATE outbox SET attempts = attempts + 1 WHERE id = ?",
                [(id_,) for id_ in ids],
            )
            self._conn.commit()

        results: list[tuple[UnifiedMessage, NodeAddress]] = []
        for msg_id, host, port, payload in rows:
            try:
                msg = UnifiedMessage.from_bytes(payload)
                target = NodeAddress(host=host, port=port)
                results.append((msg, target))
            except (ValueError, KeyError):
                self.mark_sent(msg_id)  # corrupt entry — discard
        return results

    def mark_sent(self, msg_id: str) -> None:
        """Remove *msg_id* from the queue after successful delivery."""
        with self._lock:
            self._conn.execute("DELETE FROM outbox WHERE id = ?", (msg_id,))
            self._conn.commit()

    def purge_expired(self) -> int:
        """Delete all messages whose deadline has passed. Returns count removed."""
        now = time.time()
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM outbox WHERE deadline IS NOT NULL AND deadline <= ?",
                (now,),
            )
            self._conn.commit()
            return cur.rowcount

    def pending_count(self) -> int:
        """Return the number of messages currently in the queue."""
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        with self._lock:
            self._conn.close()
