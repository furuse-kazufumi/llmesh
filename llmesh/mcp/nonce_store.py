"""Server-side nonce store with TTL expiry — replay attack defence.

Two backends:
- ``NonceStore``: thread-safe in-memory store. Test/dev only (not restart-safe).
- ``SqliteNonceStore``: durable SQLite backend (v0.2.0+). Survives restarts.
  Uses a single shared connection + threading.Lock for thread safety.
  UNIQUE(node_id, nonce) prevents double-acceptance under concurrent writes.

Security invariants:
- Nonce is persisted *before* the tool handler executes.
- Prompt bodies are never stored in the nonce database.
- DB failures fail closed (request rejected).
"""
from __future__ import annotations

import re
import sqlite3
import threading
import time
from pathlib import Path

_NONCE_RE = re.compile(r"^[a-f0-9]{32}$")
_SQLITE_BUSY_TIMEOUT_MS = 2_000


class NonceStore:
    """Thread-safe in-memory nonce store. NOT restart-safe — use SqliteNonceStore in production."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._store: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def check_and_store(self, node_id: str, nonce: str) -> bool:
        if not _NONCE_RE.match(nonce):
            raise ValueError(f"invalid_nonce_pattern:{nonce!r}")
        now = time.monotonic()
        key = (node_id, nonce)
        with self._lock:
            self._cleanup_expired_locked(now)
            if key in self._store:
                return False
            self._store[key] = now + self._ttl
            return True

    def cleanup_expired(self) -> int:
        now = time.monotonic()
        with self._lock:
            return self._cleanup_expired_locked(now)

    def _cleanup_expired_locked(self, now: float) -> int:
        expired = [k for k, exp in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]
        return len(expired)


class SqliteNonceStore:
    """Durable nonce store backed by SQLite (v0.2.0+).

    Uses a single shared connection + threading.Lock so the UNIQUE constraint
    on (node_id, nonce) is effective across threads.  For `:memory:` databases
    this is required because each connection gets a separate in-memory DB.
    For file-backed databases it adds an extra safety layer on top of SQLite's
    own WAL locking.

    Failure modes — all fail closed:
    - DB locked beyond busy-timeout → returns False.
    - Malformed nonce → ValueError (same as NonceStore).
    """

    _CREATE_SQL = """
        CREATE TABLE IF NOT EXISTS nonces (
            node_id    TEXT NOT NULL,
            nonce      TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            UNIQUE(node_id, nonce)
        );
        CREATE INDEX IF NOT EXISTS idx_nonces_expires ON nonces(expires_at);
    """

    def __init__(self, db_path: str | Path = ":memory:", ttl_seconds: int = 300) -> None:
        self._path = str(db_path)
        self._ttl = ttl_seconds
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(
            self._path,
            check_same_thread=False,
            timeout=_SQLITE_BUSY_TIMEOUT_MS / 1000,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(self._CREATE_SQL)

    def check_and_store(self, node_id: str, nonce: str) -> bool:
        if not _NONCE_RE.match(nonce):
            raise ValueError(f"invalid_nonce_pattern:{nonce!r}")
        now = time.time()
        expires = now + self._ttl
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO nonces(node_id, nonce, created_at, expires_at)"
                    " VALUES (?, ?, ?, ?)",
                    (node_id, nonce, now, expires),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                return False
            except sqlite3.OperationalError:
                self._conn.rollback()
                return False

    def cleanup_expired(self) -> int:
        now = time.time()
        with self._lock:
            cur = self._conn.execute("DELETE FROM nonces WHERE expires_at <= ?", (now,))
            self._conn.commit()
            return cur.rowcount
