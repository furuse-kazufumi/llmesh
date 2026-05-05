"""Tests for llmesh.routing.latency — NodeLatencyTracker."""
from __future__ import annotations

import pytest
from llmesh.routing.latency import NodeLatencyTracker, _DEFAULT_LATENCY_MS


class TestNodeLatencyTrackerInit:
    def test_invalid_alpha_zero(self):
        with pytest.raises(ValueError, match="alpha"):
            NodeLatencyTracker(alpha=0)

    def test_invalid_alpha_above_one(self):
        with pytest.raises(ValueError, match="alpha"):
            NodeLatencyTracker(alpha=1.1)

    def test_invalid_max_latency(self):
        with pytest.raises(ValueError, match="max_latency_ms"):
            NodeLatencyTracker(max_latency_ms=0)

    def test_alpha_one_accepted(self):
        tracker = NodeLatencyTracker(alpha=1.0)
        assert tracker is not None


class TestNodeLatencyTrackerRecord:
    def setup_method(self):
        self.tracker = NodeLatencyTracker(alpha=1.0)  # alpha=1 → ewma = latest sample

    def test_first_record_sets_ewma(self):
        self.tracker.record("node-a", 200.0)
        assert self.tracker.get_latency_ms("node-a") == pytest.approx(200.0)

    def test_alpha_one_replaces_ewma(self):
        self.tracker.record("node-a", 100.0)
        self.tracker.record("node-a", 500.0)
        assert self.tracker.get_latency_ms("node-a") == pytest.approx(500.0)

    def test_ewma_smoothing(self):
        tracker = NodeLatencyTracker(alpha=0.5)
        tracker.record("n", 100.0)
        tracker.record("n", 200.0)
        # ewma = 0.5*200 + 0.5*100 = 150
        assert tracker.get_latency_ms("n") == pytest.approx(150.0)

    def test_unknown_node_returns_default(self):
        assert self.tracker.get_latency_ms("unknown") == pytest.approx(_DEFAULT_LATENCY_MS)

    def test_multiple_nodes_independent(self):
        self.tracker.record("a", 10.0)
        self.tracker.record("b", 500.0)
        assert self.tracker.get_latency_ms("a") == pytest.approx(10.0)
        assert self.tracker.get_latency_ms("b") == pytest.approx(500.0)


class TestNodeLatencyTrackerIsTooSlow:
    def test_below_ceiling_not_slow(self):
        tracker = NodeLatencyTracker(max_latency_ms=1000.0)
        tracker.record("n", 500.0)
        assert not tracker.is_too_slow("n")

    def test_above_ceiling_is_slow(self):
        tracker = NodeLatencyTracker(alpha=1.0, max_latency_ms=1000.0)
        tracker.record("n", 1001.0)
        assert tracker.is_too_slow("n")

    def test_unknown_node_not_slow_with_default_ceiling(self):
        tracker = NodeLatencyTracker(max_latency_ms=30_000.0)
        assert not tracker.is_too_slow("unknown")


class TestNodeLatencyTrackerSelectFastest:
    def test_returns_up_to_limit(self):
        tracker = NodeLatencyTracker(alpha=1.0)
        for i, rtt in enumerate([50.0, 30.0, 80.0, 20.0]):
            tracker.record(f"n{i}", rtt)
        result = tracker.select_fastest(["n0", "n1", "n2", "n3"], limit=2)
        assert result == ["n3", "n1"]   # 20, 30

    def test_excludes_too_slow_nodes(self):
        tracker = NodeLatencyTracker(alpha=1.0, max_latency_ms=100.0)
        tracker.record("fast", 50.0)
        tracker.record("slow", 200.0)
        result = tracker.select_fastest(["fast", "slow"], limit=10)
        assert "slow" not in result
        assert "fast" in result

    def test_unknown_nodes_included_by_default(self):
        tracker = NodeLatencyTracker(max_latency_ms=30_000.0)
        result = tracker.select_fastest(["new-node"], limit=5)
        assert "new-node" in result

    def test_empty_input_returns_empty(self):
        tracker = NodeLatencyTracker()
        assert tracker.select_fastest([], limit=5) == []

    def test_limit_zero_returns_empty(self):
        tracker = NodeLatencyTracker(alpha=1.0)
        tracker.record("n", 10.0)
        assert tracker.select_fastest(["n"], limit=0) == []

    def test_all_stats_snapshot(self):
        tracker = NodeLatencyTracker(alpha=1.0)
        tracker.record("x", 42.0)
        stats = tracker.all_stats()
        assert stats == {"x": pytest.approx(42.0)}
