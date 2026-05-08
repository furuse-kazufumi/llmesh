"""ContributionTracker — per-node EWMA contribution score.

A node's score reflects how often its response was included in the k-of-n
consensus.  High-score nodes are preferred in fanout selection, creating a
positive feedback loop where reliable nodes handle more traffic.

Score semantics:
  0.0  — never contributed (or consistently diverges from consensus)
  0.5  — neutral starting value for new nodes
  1.0  — always contributes to consensus

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Thread-safe: all mutations under _lock
"""
from __future__ import annotations

import threading
from dataclasses import dataclass

_ALPHA: float = 0.2           # slower EWMA for smoother reputation
_DEFAULT_SCORE: float = 0.5   # neutral prior for unseen nodes


@dataclass
class ContributionStats:
    score: float = _DEFAULT_SCORE
    total_invocations: int = 0
    total_contributions: int = 0


class ContributionTracker:
    """Track per-node contribution rate using EWMA. Thread-safe.

    Args:
        alpha: EWMA smoothing factor for score updates.
    """

    def __init__(self, alpha: float = _ALPHA) -> None:
        if not 0 < alpha <= 1:
            raise ValueError("alpha must be in (0, 1]")
        self._alpha = alpha
        self._stats: dict[str, ContributionStats] = {}
        self._lock = threading.Lock()

    def record_invocation(self, node_id: str, contributed: bool) -> None:
        """Update score for node_id after one fanout invocation.

        Args:
            node_id:     The node whose outcome is being recorded.
            contributed: True if this node's response was part of the consensus.
        """
        with self._lock:
            if node_id not in self._stats:
                self._stats[node_id] = ContributionStats()
            s = self._stats[node_id]
            s.total_invocations += 1
            if contributed:
                s.total_contributions += 1
            sample = 1.0 if contributed else 0.0
            s.score = self._alpha * sample + (1.0 - self._alpha) * s.score

    def get_score(self, node_id: str) -> float:
        """Return EWMA score for node_id, or the neutral default for unseen nodes."""
        with self._lock:
            return self._stats.get(node_id, ContributionStats()).score

    def select_by_score(self, node_ids: list[str], limit: int) -> list[str]:
        """Return up to `limit` node_ids sorted by contribution score (descending)."""
        with self._lock:
            scored = [
                (nid, self._stats.get(nid, ContributionStats()).score)
                for nid in node_ids
            ]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [nid for nid, _ in scored[:limit]]

    def all_scores(self) -> dict[str, float]:
        """Return a snapshot of all known scores."""
        with self._lock:
            return {nid: s.score for nid, s in self._stats.items()}

    def get_stats(self, node_id: str) -> ContributionStats | None:
        """Return full stats for node_id, or None if unseen."""
        with self._lock:
            return self._stats.get(node_id)
