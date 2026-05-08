"""SmartNodeSelector — combines latency, circuit breaker, contribution score, and overrides.

Selection pipeline (applied in order):
  1. Manual block filter: remove nodes in NodeOverrides.blocked
  2. Circuit breaker:     exclude OPEN nodes
  3. Fairness filter:     exclude BLOCKED/EXCLUDED nodes
                          (pinned nodes bypass this step)
  4. Latency filter:      exclude nodes above max_latency threshold
  5. Sort:                pinned nodes first, then by contribution score (desc)
  6. Trim to k * candidate_multiplier candidates

The executor then sends requests to all returned candidates; the first k that
succeed form the consensus.  Returning more than k provides resilience while
the filters prevent slow/unreliable nodes from dragging down overall latency.

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Thread-safe: delegates to thread-safe sub-trackers
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .circuit_breaker import NodeCircuitBreakerMap
from .contribution import ContributionTracker
from .latency import NodeLatencyTracker

if TYPE_CHECKING:
    from ..fairness.policy import FairnessPolicy
    from .node_overrides import NodeOverrides


class SmartNodeSelector:
    """Select the best candidate nodes for a fanout execution.

    Args:
        latency_tracker:      EWMA RTT tracker.
        breakers:             Per-node circuit breakers.
        contribution:         Per-node contribution scorer.
        candidate_multiplier: Pull k*multiplier candidates so the executor has
                              room to absorb failures without falling below k.
        fairness_policy:      Optional FairnessPolicy — nodes that are BLOCKED or
                              EXCLUDED are filtered out before latency/contribution
                              ordering.  None disables fairness filtering entirely.
        overrides:            Optional NodeOverrides — blocked nodes are always
                              removed; pinned nodes bypass fairness and sort first.
    """

    def __init__(
        self,
        latency_tracker: NodeLatencyTracker | None = None,
        breakers: NodeCircuitBreakerMap | None = None,
        contribution: ContributionTracker | None = None,
        candidate_multiplier: int = 3,
        fairness_policy: "FairnessPolicy | None" = None,
        overrides: "NodeOverrides | None" = None,
    ) -> None:
        if candidate_multiplier < 1:
            raise ValueError("candidate_multiplier must be >= 1")
        self._latency = latency_tracker or NodeLatencyTracker()
        self._breakers = breakers or NodeCircuitBreakerMap()
        self._contribution = contribution or ContributionTracker()
        self._multiplier = candidate_multiplier
        self._fairness_policy = fairness_policy
        self._overrides = overrides

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select(self, nodes: list[Any], k: int) -> list[Any]:
        """Select best candidates from nodes.

        Args:
            nodes: Objects with a .node_id attribute.
            k:     Minimum consensus quorum size.

        Returns:
            Up to k * candidate_multiplier nodes that pass all filters.
            Pinned nodes appear first, then remaining nodes sorted by
            contribution score descending.  May return fewer than
            k * multiplier if not enough nodes survive filtering.
        """
        # Step 1: manual block filter (highest priority — always applied first)
        if self._overrides is not None:
            available = [n for n in nodes if not self._overrides.is_blocked(n.node_id)]
        else:
            available = list(nodes)

        # Step 2: circuit breaker filter
        available = [n for n in available if self._breakers.allow_request(n.node_id)]

        # Step 3: fairness policy filter — pinned nodes bypass this step
        if self._fairness_policy is not None:
            available = [
                n for n in available
                if (
                    self._overrides is not None and self._overrides.is_pinned(n.node_id)
                ) or self._fairness_policy.is_allowed(n.node_id)
            ]

        # Step 4: latency filter
        available = [n for n in available if not self._latency.is_too_slow(n.node_id)]

        # Step 5: sort — pinned nodes first, then by contribution score (desc)
        def _sort_key(n: Any) -> tuple[int, float]:
            is_pinned = (
                self._overrides is not None and self._overrides.is_pinned(n.node_id)
            )
            return (
                0 if is_pinned else 1,                          # pinned = lower bucket
                -self._contribution.get_score(n.node_id),      # higher score = lower value
            )

        available.sort(key=_sort_key)

        # Step 6: trim to candidate pool
        return available[: k * self._multiplier]

    # ------------------------------------------------------------------
    # Outcome recording (called by FanoutExecutor after each node completes)
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        node_id: str,
        rtt_ms: float,
        success: bool,
        in_consensus: bool,
    ) -> None:
        """Update all sub-trackers after a node call completes.

        Args:
            node_id:      The node that was called.
            rtt_ms:       Round-trip time in milliseconds.
            success:      True if the call returned a valid, validated response.
            in_consensus: True if this node's response was included in the consensus.
        """
        self._latency.record(node_id, rtt_ms)
        if success:
            self._breakers.record_success(node_id)
        else:
            self._breakers.record_failure(node_id)
        self._contribution.record_invocation(node_id, in_consensus)

    # ------------------------------------------------------------------
    # Accessors (for diagnostics / admin endpoints)
    # ------------------------------------------------------------------

    @property
    def latency(self) -> NodeLatencyTracker:
        return self._latency

    @property
    def breakers(self) -> NodeCircuitBreakerMap:
        return self._breakers

    @property
    def contribution(self) -> ContributionTracker:
        return self._contribution

    @property
    def fairness_policy(self) -> "FairnessPolicy | None":
        return self._fairness_policy

    @property
    def overrides(self) -> "NodeOverrides | None":
        return self._overrides
