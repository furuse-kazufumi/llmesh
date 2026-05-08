"""FairnessPolicy — thresholds and penalty decisions based on contribution_ratio.

Server-side opt-out:
    A node that serves requests may disable fairness enforcement for its own
    clients by calling policy.disable() or by constructing with enabled=False.
    When disabled, evaluate() always returns PenaltyLevel.NORMAL and
    is_allowed() always returns True.  This lets server operators opt out of
    the fairness system entirely (e.g. public nodes, trusted environments).

Long-running operation safety:
    _excluded uses an insertion-ordered dict (as an ordered set) so that when
    max_excluded_size is exceeded, the oldest entries are evicted first.
    _blocked_counts entries are removed as soon as a node stops being BLOCKED,
    so that dict is bounded by the number of currently-blocked nodes.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from enum import Enum

from .ledger import ContributionLedger


class PenaltyLevel(str, Enum):
    NORMAL       = "normal"        # ratio >= 0.5
    LOW_PRIORITY = "low_priority"  # 0.3 <= ratio < 0.5
    RATE_LIMITED = "rate_limited"  # 0.1 <= ratio < 0.3
    BLOCKED      = "blocked"       # ratio < 0.1
    EXCLUDED     = "excluded"      # long-term zero (set via exclude())


@dataclass
class FairnessPolicyConfig:
    """Threshold configuration for penalty escalation."""
    normal_threshold:       float = 0.5
    low_priority_threshold: float = 0.3
    rate_limited_threshold: float = 0.1
    # Consecutive BLOCKED evaluations before automatic EXCLUDED
    exclude_after: int = 10
    # Maximum number of simultaneously excluded nodes; oldest are evicted when
    # exceeded.  Set to 0 to disable the cap (unbounded, not recommended for
    # public-facing nodes that may see many transient bad actors).
    max_excluded_size: int = 10_000


class FairnessPolicy:
    """Evaluate contribution_ratio and assign a PenaltyLevel.

    Args:
        ledger:  ContributionLedger to query ratios from.
        config:  Threshold configuration. Uses defaults when omitted.
        enabled: Start enabled (True) or disabled (False). Can be toggled later
                 via enable() / disable(). When disabled, all nodes are NORMAL.
    """

    def __init__(
        self,
        ledger: ContributionLedger,
        config: FairnessPolicyConfig | None = None,
        *,
        enabled: bool = True,
    ) -> None:
        self._ledger = ledger
        self._cfg = config or FairnessPolicyConfig()
        self._enabled = enabled
        self._blocked_counts: dict[str, int] = {}
        # Insertion-ordered dict used as an ordered set for bounded eviction.
        self._excluded: dict[str, None] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # On/off switch (server-side opt-out)
    # ------------------------------------------------------------------

    def enable(self) -> None:
        """Enable fairness enforcement (default state)."""
        with self._lock:
            self._enabled = True

    def disable(self) -> None:
        """Disable fairness enforcement — all nodes treated as NORMAL.

        Intended for server operators who want to offer unrestricted access
        (e.g. public nodes, internal trusted clusters).
        """
        with self._lock:
            self._enabled = False

    @property
    def is_enabled(self) -> bool:
        with self._lock:
            return self._enabled

    # ------------------------------------------------------------------
    # Evaluation
    # ------------------------------------------------------------------

    def evaluate(self, node_id: str, window: float | None = None) -> PenaltyLevel:
        """Return current PenaltyLevel for node_id.

        Returns NORMAL immediately when fairness is disabled.
        """
        with self._lock:
            if not self._enabled:
                return PenaltyLevel.NORMAL

            if node_id in self._excluded:
                return PenaltyLevel.EXCLUDED

        ratio = self._ledger.get_ratio(node_id, window)
        level = self._ratio_to_level(ratio)

        with self._lock:
            if level == PenaltyLevel.BLOCKED:
                self._blocked_counts[node_id] = self._blocked_counts.get(node_id, 0) + 1
                if self._blocked_counts[node_id] >= self._cfg.exclude_after:
                    self._add_excluded(node_id)
                    return PenaltyLevel.EXCLUDED
            else:
                self._blocked_counts.pop(node_id, None)

        return level

    def is_allowed(self, node_id: str, window: float | None = None) -> bool:
        """Return False only if node is BLOCKED or EXCLUDED (and policy is enabled)."""
        level = self.evaluate(node_id, window)
        return level not in (PenaltyLevel.BLOCKED, PenaltyLevel.EXCLUDED)

    def get_queue_priority(self, node_id: str, window: float | None = None) -> int:
        """Return scheduling priority: NORMAL=3, LOW_PRIORITY=2, RATE_LIMITED=1, rest=0."""
        level = self.evaluate(node_id, window)
        return {
            PenaltyLevel.NORMAL:       3,
            PenaltyLevel.LOW_PRIORITY: 2,
            PenaltyLevel.RATE_LIMITED: 1,
            PenaltyLevel.BLOCKED:      0,
            PenaltyLevel.EXCLUDED:     0,
        }[level]

    # ------------------------------------------------------------------
    # Manual override
    # ------------------------------------------------------------------

    def exclude(self, node_id: str) -> None:
        """Manually exclude a node regardless of ratio."""
        with self._lock:
            self._add_excluded(node_id)

    def pardon(self, node_id: str) -> None:
        """Remove exclusion and reset consecutive-blocked counter."""
        with self._lock:
            self._excluded.pop(node_id, None)
            self._blocked_counts.pop(node_id, None)

    def excluded_count(self) -> int:
        """Return current number of excluded nodes."""
        with self._lock:
            return len(self._excluded)

    # ------------------------------------------------------------------
    # Internal (caller must hold self._lock)
    # ------------------------------------------------------------------

    def _add_excluded(self, node_id: str) -> None:
        """Add node_id to excluded set, evicting oldest entry if at capacity."""
        self._excluded[node_id] = None  # dict preserves insertion order (Py 3.7+)
        cap = self._cfg.max_excluded_size
        if cap > 0 and len(self._excluded) > cap:
            # Evict the oldest inserted node_id (first key in ordered dict)
            oldest = next(iter(self._excluded))
            del self._excluded[oldest]

    def _ratio_to_level(self, ratio: float) -> PenaltyLevel:
        if ratio >= self._cfg.normal_threshold:
            return PenaltyLevel.NORMAL
        if ratio >= self._cfg.low_priority_threshold:
            return PenaltyLevel.LOW_PRIORITY
        if ratio >= self._cfg.rate_limited_threshold:
            return PenaltyLevel.RATE_LIMITED
        return PenaltyLevel.BLOCKED
