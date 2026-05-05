"""PerNodeRateLimiter — token bucket rate limiter, one bucket per node_id.

Prevents:
- Request flooding directed at a single node
- A compromised node triggering recursive request amplification
- Bandwidth exhaustion from fanout storms

The token bucket algorithm allows short bursts up to `burst` requests while
enforcing a long-term average of `rate` requests/second.

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Thread-safe: all mutations under _lock
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class RateLimitExceeded(Exception):
    """Raised when a node's rate limit is exceeded."""


@dataclass
class _Bucket:
    tokens: float
    last_refill: float = field(default_factory=time.monotonic)


class PerNodeRateLimiter:
    """Token bucket rate limiter with one bucket per node_id.

    Args:
        rate:  Tokens refilled per second (long-term average request rate).
        burst: Maximum token capacity (maximum short-term burst).
    """

    def __init__(self, rate: float = 10.0, burst: float = 20.0) -> None:
        if rate <= 0:
            raise ValueError("rate must be positive")
        if burst <= 0:
            raise ValueError("burst must be positive")
        self._rate = rate
        self._burst = burst
        self._buckets: dict[str, _Bucket] = {}
        self._lock = threading.Lock()

    def check(self, node_id: str) -> None:
        """Consume one token for node_id.

        Raises:
            RateLimitExceeded: If the token bucket is empty.
        """
        with self._lock:
            now = time.monotonic()
            if node_id not in self._buckets:
                self._buckets[node_id] = _Bucket(tokens=self._burst, last_refill=now)
            bucket = self._buckets[node_id]

            # Refill tokens proportional to elapsed time
            elapsed = now - bucket.last_refill
            bucket.tokens = min(self._burst, bucket.tokens + elapsed * self._rate)
            bucket.last_refill = now

            if bucket.tokens < 1.0:
                raise RateLimitExceeded(f"rate_limit_exceeded:node={node_id}")
            bucket.tokens -= 1.0

    def reset(self, node_id: str) -> None:
        """Reset the bucket for node_id to full capacity."""
        with self._lock:
            self._buckets.pop(node_id, None)

    def available_tokens(self, node_id: str) -> float:
        """Return the current token count for node_id (for diagnostics)."""
        with self._lock:
            now = time.monotonic()
            if node_id not in self._buckets:
                return self._burst
            bucket = self._buckets[node_id]
            elapsed = now - bucket.last_refill
            return min(self._burst, bucket.tokens + elapsed * self._rate)
