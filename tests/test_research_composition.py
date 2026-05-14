"""Tests for Phase 15 D7 — Shapley component composition."""

from __future__ import annotations

from typing import Callable

import pytest

from llmesh.research.composition import (
    CompositionPlan,
    compose,
)


# ---------------------------------------------------------------------------
# Helper: synthetic value functions
# ---------------------------------------------------------------------------


def _make_redundant_pair_vfn(
    redundant_pair: tuple[str, str],
) -> Callable[[frozenset[str]], float]:
    """Value = sum of individual contributions, minus penalty if redundant pair both included."""
    weights = {"a": 1.0, "b": 0.8, "c": 0.5, "d": 0.3}

    def vfn(subset: frozenset[str]) -> float:
        v = sum(weights.get(c, 0.0) for c in subset)
        if redundant_pair[0] in subset and redundant_pair[1] in subset:
            v -= 0.6  # redundancy penalty
        return v

    return vfn


def _useless_component_vfn(subset: frozenset[str]) -> float:
    # 'noise' adds nothing; 'core' adds 1.0
    return 1.0 if "core" in subset else 0.0


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


class TestComposeShape:
    def test_empty_components(self) -> None:
        plan = compose([], lambda s: 0.0)
        assert plan.chosen == ()
        assert plan.scores == ()

    def test_single_positive_component_chosen(self) -> None:
        plan = compose(["x"], lambda s: 1.0 if "x" in s else 0.0)
        assert plan.chosen == ("x",)
        assert plan.scores[0].shapley_value == pytest.approx(1.0)

    def test_returns_composition_plan(self) -> None:
        plan = compose(["a"], _useless_component_vfn)
        assert isinstance(plan, CompositionPlan)
        # 'a' is not 'core' — contributes 0, so chosen is empty
        assert plan.chosen == ()

    def test_scores_sorted_descending(self) -> None:
        plan = compose(
            ["a", "b", "c"], _make_redundant_pair_vfn(("x", "y"))
        )
        values = [s.shapley_value for s in plan.scores]
        assert values == sorted(values, reverse=True)

    def test_dedup_preserves_order(self) -> None:
        # duplicate components should be deduplicated, not double-counted
        plan = compose(
            ["a", "a", "b"], _make_redundant_pair_vfn(("x", "y"))
        )
        ids = [s.component_id for s in plan.scores]
        assert sorted(ids) == ["a", "b"]


# ---------------------------------------------------------------------------
# Cross-component interference (CCI) handling
# ---------------------------------------------------------------------------


class TestCCIHandling:
    def test_useless_component_gets_zero_shapley(self) -> None:
        plan = compose(["core", "noise"], _useless_component_vfn)
        noise = next(s for s in plan.scores if s.component_id == "noise")
        assert noise.shapley_value == pytest.approx(0.0)

    def test_useless_component_excluded_from_chosen(self) -> None:
        plan = compose(["core", "noise"], _useless_component_vfn)
        assert "core" in plan.chosen
        assert "noise" not in plan.chosen

    def test_redundant_pair_recovers_one_not_both(self) -> None:
        # 'a' and 'b' are individually useful but redundant when both present
        # weights: a=1.0, b=0.8, redundancy penalty -0.6 when both present
        # -> value({a})=1.0, value({b})=0.8, value({a,b})=1.2
        # so adding b after a improves value from 1.0 to 1.2 (positive)
        # but in the "anti-redundancy" version below we tighten it
        vfn = _make_redundant_pair_vfn(("a", "b"))
        plan = compose(["a", "b"], vfn)
        # both have positive shapley (still net-positive)
        assert all(s.shapley_value > 0 for s in plan.scores)

    def test_strong_redundancy_drops_one(self) -> None:
        # Strong redundancy: penalty exceeds weaker component
        def vfn(subset: frozenset[str]) -> float:
            v = (1.0 if "a" in subset else 0.0) + (0.8 if "b" in subset else 0.0)
            if "a" in subset and "b" in subset:
                v -= 0.9  # penalty larger than b's contribution
            return v

        plan = compose(["a", "b"], vfn)
        # 'b' adds value({a,b}) - value({a}) = (1.0+0.8-0.9) - 1.0 = -0.1
        # so greedy should stop after 'a'
        assert plan.chosen == ("a",)


# ---------------------------------------------------------------------------
# Marginal contribution fields
# ---------------------------------------------------------------------------


class TestMarginalFields:
    def test_marginal_alone_matches_singleton_value(self) -> None:
        plan = compose(["x"], lambda s: 1.0 if "x" in s else 0.0)
        x = plan.scores[0]
        assert x.marginal_alone == pytest.approx(1.0)
        assert x.marginal_with_all == pytest.approx(1.0)

    def test_marginal_with_all_for_redundant_component(self) -> None:
        # 'b' is redundant with 'a' — marginal_with_all should be lower than alone
        def vfn(subset: frozenset[str]) -> float:
            if "a" in subset:
                return 1.0
            if "b" in subset:
                return 1.0
            return 0.0

        plan = compose(["a", "b"], vfn)
        b = next(s for s in plan.scores if s.component_id == "b")
        assert b.marginal_alone == pytest.approx(1.0)
        assert b.marginal_with_all == pytest.approx(0.0)  # 'a' already covers


# ---------------------------------------------------------------------------
# Method selection (exact vs Monte Carlo)
# ---------------------------------------------------------------------------


class TestMethodSelection:
    def test_exact_method_used_for_small_n(self) -> None:
        plan = compose(
            list("abcd"), _make_redundant_pair_vfn(("x", "y"))
        )
        assert plan.method == "exact_shapley"
        assert plan.permutations_sampled == 0

    def test_monte_carlo_used_above_cap(self) -> None:
        # 12 components -> exceeds _EXACT_CAP=10
        comps = [f"c{i}" for i in range(12)]
        plan = compose(
            comps,
            lambda s: sum(1.0 for c in s if c in ("c0", "c1", "c2")),
            n_permutations=50,
            seed=42,
        )
        assert plan.method == "monte_carlo"
        assert plan.permutations_sampled == 50
        # the three "useful" components should top the ranking
        top3 = {s.component_id for s in plan.scores[:3]}
        assert top3 == {"c0", "c1", "c2"}


# ---------------------------------------------------------------------------
# Threshold
# ---------------------------------------------------------------------------


class TestThreshold:
    def test_threshold_filters_low_value_components(self) -> None:
        plan = compose(
            ["a", "b", "c", "d"],
            _make_redundant_pair_vfn(("x", "y")),
            threshold=0.6,
        )
        # only 'a' (1.0) and 'b' (0.8) clear threshold
        for s in plan.scores:
            if s.component_id in plan.chosen:
                assert s.shapley_value > 0.6


# ---------------------------------------------------------------------------
# Value tracking
# ---------------------------------------------------------------------------


class TestValueTracking:
    def test_value_full_recorded(self) -> None:
        plan = compose(["a", "b"], _make_redundant_pair_vfn(("x", "y")))
        # full set: a + b = 1.8 (no redundancy since pair is (x,y))
        assert plan.value_full == pytest.approx(1.8)

    def test_value_chosen_reflects_subset(self) -> None:
        plan = compose(
            ["a"], _make_redundant_pair_vfn(("x", "y"))
        )
        assert plan.value_chosen == pytest.approx(1.0)
