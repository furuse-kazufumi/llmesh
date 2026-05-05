"""Server-side nonce store with TTL expiry — replay attack defence.

Thread-safe in-memory store. A (node_id, nonce) pair is accepted once within
the TTL window; subsequent submissions with the same pair are rejected as
replay attacks.
"""
from __future__ import annotations

import re
import threading
import time

_NONCE_RE = re.compile(r"^[a-f0-9]{32}$")


class NonceStore:
    """Thread-safe in-memory nonce store with TTL expiry.

    Rejects (node_id, nonce) pairs seen within the TTL window.
    """

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        # Maps (node_id, nonce) -> expiry_timestamp
        self._store: dict[tuple[str, str], float] = {}
        self._lock = threading.Lock()

    def check_and_store(self, node_id: str, nonce: str) -> bool:
        """Return True if nonce is fresh (not seen). False = replay attack.

        Raises ValueError for malformed nonce patterns.
        """
        if not _NONCE_RE.match(nonce):
            raise ValueError(f"invalid_nonce_pattern:{nonce!r}")

        now = time.monotonic()
        key = (node_id, nonce)

        with self._lock:
            # Remove expired entries opportunistically
            self._cleanup_expired_locked(now)

            if key in self._store:
                # Entry still within TTL window — replay detected
                return False

            self._store[key] = now + self._ttl
            return True

    def cleanup_expired(self) -> int:
        """Remove expired entries. Returns count removed."""
        now = time.monotonic()
        with self._lock:
            return self._cleanup_expired_locked(now)

    def _cleanup_expired_locked(self, now: float) -> int:
        """Internal cleanup — must be called with self._lock held."""
        expired = [k for k, exp in self._store.items() if exp <= now]
        for k in expired:
            del self._store[k]
        return len(expired)
