"""Tests for llmesh.routing.contribution — ContributionTracker."""
from __future__ import annotations

import pytest
from llmesh.routing.contribution import ContributionTracker, _DEFAULT_SCORE


class TestContributionTrackerInit:
    def test_invalid_alpha(self):
        with pytest.raises(ValueError, match="alpha"):
            ContributionTracker(alpha=0)

    def test_alpha_one_accepted(self):
        assert ContributionTracker(alpha=1.0) is not None


class TestContributionTrackerScore:
    def test_unknown_node_returns_default(self):
        ct = ContributionTracker()
        assert ct.get_score("unknown") == pytest.approx(_DEFAULT_SCORE)

    def test_always_contributes_score_rises(self):
        ct = ContributionTracker(alpha=1.0)
        ct.record_invocation("n", contributed=True)
        assert ct.get_score("n") == pytest.approx(1.0)

    def test_never_contributes_score_falls(self):
        ct = ContributionTracker(alpha=1.0)
        ct.record_invocation("n", contributed=False)
        assert ct.get_score("n") == pytest.approx(0.0)

    def test_ewma_smoothing(self):
        ct = ContributionTracker(alpha=0.5)
        # After first invocation (contributed=True): score = 0.5*1 + 0.5*0.5 = 0.75
        ct.record_invocation("n", contributed=True)
        assert ct.get_score("n") == pytest.approx(0.75)

    def test_multiple_nodes_independent(self):
        ct = ContributionTracker(alpha=1.0)
        ct.record_invocation("good", contributed=True)
        ct.record_invocation("bad", contributed=False)
        assert ct.get_score("good") > ct.get_score("bad")

    def test_stats_increment_counters(self):
        ct = ContributionTracker()
        ct.record_invocation("n", contributed=True)
        ct.record_invocation("n", contributed=False)
        stats = ct.get_stats("n")
        assert stats is not None
        assert stats.total_invocations == 2
        assert stats.total_contributions == 1

    def test_get_stats_unknown_returns_none(self):
        ct = ContributionTracker()
        assert ct.get_stats("missing") is None


class TestContributionTrackerSelectByScore:
    def test_returns_highest_score_first(self):
        ct = ContributionTracker(alpha=1.0)
        ct.record_invocation("low", contributed=False)
        ct.record_invocation("high", contributed=True)
        result = ct.select_by_score(["low", "high"], limit=2)
        assert result[0] == "high"
        assert result[1] == "low"

    def test_limit_respected(self):
        ct = ContributionTracker()
        result = ct.select_by_score(["a", "b", "c"], limit=2)
        assert len(result) == 2

    def test_empty_returns_empty(self):
        ct = ContributionTracker()
        assert ct.select_by_score([], limit=5) == []

    def test_all_scores_snapshot(self):
        ct = ContributionTracker(alpha=1.0)
        ct.record_invocation("x", contributed=True)
        scores = ct.all_scores()
        assert "x" in scores
        assert scores["x"] == pytest.approx(1.0)
