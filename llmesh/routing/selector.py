"""SmartNodeSelector — combines latency, circuit breaker, and contribution score.

Selection pipeline (applied in order):
  1. Circuit breaker: exclude OPEN nodes
  2. Latency filter: exclude nodes above max_latency threshold
  3. Contribution sort: order remaining nodes by score (descending)
  4. Trim to k * candidate_multiplier candidates

The executor then sends requests to all returned candidates; the first k that
succeed form the consensus.  Returning more than k provides resilience while
the filters prevent slow/unreliable nodes from dragging down overall latency.

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Thread-safe: delegates to thread-safe sub-trackers
"""
from __future__ import annotations

from typing import Any

from .circuit_breaker import NodeCircuitBreakerMap
from .contribution import ContributionTracker
from .latency import NodeLatencyTracker


class SmartNodeSelector:
    """Select the best candidate nodes for a fanout execution.

    Args:
        latency_tracker:      EWMA RTT tracker.
        breakers:             Per-node circuit breakers.
        contribution:         Per-node contribution scorer.
        candidate_multiplier: Pull k*multiplier candidates so the executor has
                              room to absorb failures without falling below k.
    """

    def __init__(
        self,
        latency_tracker: NodeLatencyTracker | None = None,
        breakers: NodeCircuitBreakerMap | None = None,
        contribution: ContributionTracker | None = None,
        candidate_multiplier: int = 3,
    ) -> None:
        if candidate_multiplier < 1:
            raise ValueError("candidate_multiplier must be >= 1")
        self._latency = latency_tracker or NodeLatencyTracker()
        self._breakers = breakers or NodeCircuitBreakerMap()
        self._contribution = contribution or ContributionTracker()
        self._multiplier = candidate_multiplier

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def select(self, nodes: list[Any], k: int) -> list[Any]:
        """Select best candidates from nodes.

        Args:
            nodes: Objects with a .node_id attribute.
            k:     Minimum consensus quorum size.

        Returns:
            Up to k * candidate_multiplier nodes that pass all filters,
            sorted by contribution score descending.  May return fewer
            if not enough nodes survive filtering.
        """
        # Step 1: circuit breaker filter
        available = [n for n in nodes if self._breakers.allow_request(n.node_id)]

        # Step 2: latency filter
        available = [n for n in available if not self._latency.is_too_slow(n.node_id)]

        # Step 3: sort by contribution score (high contributors first)
        available.sort(
            key=lambda n: self._contribution.get_score(n.node_id),
            reverse=True,
        )

        # Step 4: trim to candidate pool
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
