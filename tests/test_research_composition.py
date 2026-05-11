"""Tests for Phase 15 D7 — Shapley component composition."""

from __future__ import annotations

import pytest

from llmesh.research.composition import (
    ComponentScore,
    CompositionPlan,
    compose,
)


# ---------------------------------------------------------------------------
# Helper: synthetic value functions
# ---------------------------------------------------------------------------


def _additive_with_redundancy(redundant_pair: tuple[str, str]) -> callable:
    """Value = sum of individual contributions, minus penalty if redundant pair both included."""
    weights = {"a": 1.0, "b": 0.8, "c": 0.5, "d": 0.3}

    def vfn(subset: frozenset[str]) -> float:
        v = sum(weights.get(c, 0.0) for c in subset)
        if redundant_pair[0] in subset and redundant_pair[1] in subset:
            v -= 0.6  # redundancy penalty
        return v

    return vfn


def _useless_component_value_fn(subset: frozenset[str]) -> float:
    # 'noise' adds nothing; 'core' adds 1.0
    return (1.0 if "core" in subset else 0.0) + (0.0 if "noise" in subset else 0.0)


# ---------------------------------------------------------------------------
# Basic shape
# ---------------------------------------------------------------------------


class TestComposeShape:
    def test_empty_components(self) -> None:
        plan = compose([], lambda s: 0.0)
        assert plan.chosen == ()
        assert plan.scores == ()

    def test_single_component_picked_if_positive(self) -> None:
        plan = compose(["x"], lambda s: 1.0 if "x" in s else 0.0)
        assert plan.chosen == ("x",)
        assert plan.scores[0].shapley_value == pytest.approx(1.0)

    def test_returns_composition_plan(self) -> None:
        plan = compose(["a"], lambda s: 1.0 if a"" in s else 0.0)  # type: ignore[has-type]
        # falls through — replaced below
