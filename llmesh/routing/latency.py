"""NodeLatencyTracker — EWMA per-node RTT tracking with latency-aware selection.

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Thread-safe: all mutations under _lock
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

_ALPHA: float = 0.3          # EWMA smoothing factor (higher = more reactive to recent samples)
_DEFAULT_LATENCY_MS: float = 1_000.0   # assumed 1 s for nodes with no samples yet
_DEFAULT_MAX_MS: float = 30_000.0      # 30 s hard ceiling; nodes above this are excluded


@dataclass
class LatencyRecord:
    ewma_ms: float
    sample_count: int = 0
    last_updated: float = field(default_factory=time.monotonic)


class NodeLatencyTracker:
    """Track per-node RTT using EWMA. Thread-safe.

    Args:
        alpha:         EWMA smoothing factor in (0, 1]. Higher = reacts faster to changes.
        max_latency_ms: Nodes whose EWMA exceeds this value are excluded from selection.
    """

    def __init__(
        self,
        alpha: float = _ALPHA,
        max_latency_ms: float = _DEFAULT_MAX_MS,
    ) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        if max_latency_ms <= 0:
            raise ValueError("max_latency_ms must be positive")
        self._alpha = alpha
        self._max_ms = max_latency_ms
        self._records: dict[str, LatencyRecord] = {}
        self._lock = threading.Lock()

    def record(self, node_id: str, rtt_ms: float) -> None:
        """Record a new RTT sample for node_id (updates EWMA)."""
        with self._lock:
            if node_id not in self._records:
                self._records[node_id] = LatencyRecord(ewma_ms=rtt_ms)
            else:
                r = self._records[node_id]
                r.ewma_ms = self._alpha * rtt_ms + (1.0 - self._alpha) * r.ewma_ms
                r.sample_count += 1
                r.last_updated = time.monotonic()

    def get_latency_ms(self, node_id: str) -> float:
        """Return EWMA latency for node_id, or default if no samples yet."""
        with self._lock:
            rec = self._records.get(node_id)
            return rec.ewma_ms if rec else _DEFAULT_LATENCY_MS

    def is_too_slow(self, node_id: str) -> bool:
        """Return True if node exceeds the latency ceiling."""
        return self.get_latency_ms(node_id) > self._max_ms

    def select_fastest(self, node_ids: list[str], limit: int) -> list[str]:
        """Return up to `limit` node_ids sorted by EWMA latency, slowest excluded.

        Nodes with no samples yet are treated as having the default latency (1 s)
        and remain eligible unless the ceiling is < 1 s.
        """
        with self._lock:
            eligible = [
                nid for nid in node_ids
                if (self._records.get(nid, LatencyRecord(ewma_ms=_DEFAULT_LATENCY_MS)).ewma_ms
                    <= self._max_ms)
            ]
            eligible.sort(
                key=lambda nid: self._records.get(
                    nid, LatencyRecord(ewma_ms=_DEFAULT_LATENCY_MS)
                ).ewma_ms
            )
        return eligible[:limit]

    def all_stats(self) -> dict[str, float]:
        """Return a snapshot of all EWMA latencies (ms)."""
        with self._lock:
            return {nid: r.ewma_ms for nid, r in self._records.items()}
