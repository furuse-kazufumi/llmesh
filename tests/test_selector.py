"""Tests for llmesh.routing.selector — SmartNodeSelector."""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from llmesh.routing.circuit_breaker import NodeCircuitBreakerMap
from llmesh.routing.contribution import ContributionTracker
from llmesh.routing.latency import NodeLatencyTracker
from llmesh.routing.selector import SmartNodeSelector


@dataclass
class _Node:
    node_id: str
    endpoint: str = "http://localhost:8080"


def _nodes(n: int) -> list[_Node]:
    return [_Node(node_id=f"node-{i}") for i in range(n)]


class TestSmartNodeSelectorInit:
    def test_invalid_multiplier(self):
        with pytest.raises(ValueError, match="candidate_multiplier"):
            SmartNodeSelector(candidate_multiplier=0)

    def test_defaults_created(self):
        sel = SmartNodeSelector()
        assert sel.latency is not None
        assert sel.breakers is not None
        assert sel.contribution is not None


class TestSmartNodeSelectorSelect:
    def test_returns_all_when_no_filters_hit(self):
        sel = SmartNodeSelector(candidate_multiplier=2)
        nodes = _nodes(4)
        result = sel.select(nodes, k=2)
        assert len(result) == 4   # k=2, multiplier=2 → 4 candidates

    def test_excludes_open_circuit_nodes(self):
        breakers = NodeCircuitBreakerMap(failure_threshold=1)
        breakers.record_failure("node-0")   # trips node-0
        sel = SmartNodeSelector(breakers=breakers)
        nodes = _nodes(3)
        result = sel.select(nodes, k=1)
        ids = [n.node_id for n in result]
        assert "node-0" not in ids

    def test_excludes_slow_nodes(self):
        latency = NodeLatencyTracker(alpha=1.0, max_latency_ms=100.0)
        latency.record("node-0", 200.0)   # too slow
        latency.record("node-1", 50.0)
        sel = SmartNodeSelector(latency_tracker=latency)
        nodes = _nodes(2)
        result = sel.select(nodes, k=1)
        ids = [n.node_id for n in result]
        assert "node-0" not in ids
        assert "node-1" in ids

    def test_high_score_node_first(self):
        contrib = ContributionTracker(alpha=1.0)
        contrib.record_invocation("node-0", contributed=False)
        contrib.record_invocation("node-1", contributed=True)
        sel = SmartNodeSelector(contribution=contrib)
        nodes = _nodes(2)
        result = sel.select(nodes, k=1)
        assert result[0].node_id == "node-1"

    def test_returns_empty_when_all_filtered(self):
        breakers = NodeCircuitBreakerMap(failure_threshold=1)
        for i in range(3):
            breakers.record_failure(f"node-{i}")
        sel = SmartNodeSelector(breakers=breakers)
        result = sel.select(_nodes(3), k=1)
        assert result == []

    def test_candidate_multiplier_caps_output(self):
        sel = SmartNodeSelector(candidate_multiplier=2)
        nodes = _nodes(10)
        result = sel.select(nodes, k=3)
        assert len(result) == 6   # k=3, multiplier=2


class TestSmartNodeSelectorRecordOutcome:
    def test_success_closes_breaker(self):
        sel = SmartNodeSelector()
        sel.record_outcome("n", rtt_ms=50.0, success=True, in_consensus=True)
        assert sel.breakers.allow_request("n")

    def test_failure_increments_breaker(self):
        sel = SmartNodeSelector()
        breakers = NodeCircuitBreakerMap(failure_threshold=1)
        sel2 = SmartNodeSelector(breakers=breakers)
        sel2.record_outcome("n", rtt_ms=50.0, success=False, in_consensus=False)
        assert sel2.breakers.is_open("n")

    def test_rtt_recorded_to_latency(self):
        sel = SmartNodeSelector()
        sel.record_outcome("n", rtt_ms=123.0, success=True, in_consensus=True)
        assert sel.latency.get_latency_ms("n") == pytest.approx(123.0)

    def test_consensus_updates_contribution(self):
        sel = SmartNodeSelector()
        sel.record_outcome("n", rtt_ms=10.0, success=True, in_consensus=True)
        assert sel.contribution.get_score("n") > 0.5   # above neutral
