"""Tests for Phase 17 D5 — multi-fidelity pipeline."""

from __future__ import annotations

import pytest

from llmesh.research.fidelity import (
    DEFAULT_TIER_ORDER,
    FidelityResult,
    FidelityTier,
    PipelineConfig,
    PipelineRun,
    make_mock_runner,
    run_pipeline,
)


# ---------------------------------------------------------------------------
# FidelityTier
# ---------------------------------------------------------------------------


class TestFidelityTier:
    def test_rank_orders_tiers(self) -> None:
        assert FidelityTier.MOCK.rank < FidelityTier.SIMULATOR.rank
        assert FidelityTier.SIMULATOR.rank < FidelityTier.SOFT.rank
        assert FidelityTier.SOFT.rank < FidelityTier.REAL.rank

    def test_default_order_includes_all(self) -> None:
        assert set(DEFAULT_TIER_ORDER) == set(FidelityTier)


# ---------------------------------------------------------------------------
# FidelityResult / PipelineConfig validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_confidence_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError):
            FidelityResult(tier=FidelityTier.MOCK, success=True, confidence=1.5)
        with pytest.raises(ValueError):
            FidelityResult(tier=FidelityTier.MOCK, success=True, confidence=-0.1)

    def test_min_confidence_endpoints_rejected(self) -> None:
        with pytest.raises(ValueError):
            PipelineConfig(min_confidence=0.0)
        with pytest.raises(ValueError):
            PipelineConfig(min_confidence=1.0)

    def test_zero_budget_rejected(self) -> None:
        with pytest.raises(ValueError):
            PipelineConfig(budget_usd=0)

    def test_empty_tier_order_rejected(self) -> None:
        with pytest.raises(ValueError):
            PipelineConfig(tier_order=())


# ---------------------------------------------------------------------------
# Pipeline behaviour
# ---------------------------------------------------------------------------


class TestPipelineRun:
    def test_full_promotion_when_all_succeed(self) -> None:
        runners = {
            t: make_mock_runner(t, success=True, confidence=0.9, cost_usd=0.1)
            for t in FidelityTier
        }
        run = run_pipeline(experiment_id="exp1", runners=runners)
        assert run.promoted_to == FidelityTier.REAL
        assert run.halted_reason == "success"
        assert len(run.results) == 4

    def test_halts_on_failure(self) -> None:
        runners = {
            FidelityTier.MOCK: make_mock_runner(FidelityTier.MOCK, success=True),
            FidelityTier.SIMULATOR: make_mock_runner(
                FidelityTier.SIMULATOR, success=False
            ),
            FidelityTier.SOFT: make_mock_runner(FidelityTier.SOFT, success=True),
        }
        run = run_pipeline(experiment_id="exp1", runners=runners)
        assert run.halted_reason == "failure"
        assert run.promoted_to == FidelityTier.SIMULATOR
        # never reached SOFT
        assert all(r.tier != FidelityTier.SOFT for r in run.results)

    def test_halts_on_low_confidence(self) -> None:
        runners = {
            FidelityTier.MOCK: make_mock_runner(
                FidelityTier.MOCK, success=True, confidence=0.3
            ),
            FidelityTier.SIMULATOR: make_mock_runner(FidelityTier.SIMULATOR),
        }
        run = run_pipeline(experiment_id="exp1", runners=runners)
        assert run.halted_reason == "low_confidence"
        assert run.promoted_to == FidelityTier.MOCK

    def test_halts_on_budget(self) -> None:
        runners = {
            FidelityTier.MOCK: make_mock_runner(FidelityTier.MOCK, cost_usd=10.0),
            FidelityTier.SIMULATOR: make_mock_runner(
                FidelityTier.SIMULATOR, cost_usd=10.0
            ),
        }
        run = run_pipeline(
            experiment_id="exp1",
            runners=runners,
            config=PipelineConfig(budget_usd=5.0),
        )
        # mock alone exceeds budget after run -> halted by budget post-MOCK
        assert run.halted_reason == "budget"
        assert run.promoted_to == FidelityTier.MOCK

    def test_missing_runner_skipped_silently(self) -> None:
        # Only mock available; pipeline shouldn't crash
        runners = {
            FidelityTier.MOCK: make_mock_runner(FidelityTier.MOCK)
        }
        run = run_pipeline(experiment_id="exp1", runners=runners)
        assert run.promoted_to == FidelityTier.MOCK
        assert run.halted_reason == "success"

    def test_no_runners_yields_empty_run(self) -> None:
        run = run_pipeline(experiment_id="exp1", runners={})
        assert run.promoted_to is None
        assert run.results == ()
        assert run.total_cost_usd == 0.0

    def test_returns_pipeline_run(self) -> None:
        runners = {FidelityTier.MOCK: make_mock_runner(FidelityTier.MOCK)}
        run = run_pipeline(experiment_id="exp1", runners=runners)
        assert isinstance(run, PipelineRun)

    def test_total_cost_aggregates(self) -> None:
        runners = {
            FidelityTier.MOCK: make_mock_runner(FidelityTier.MOCK, cost_usd=0.05),
            FidelityTier.SIMULATOR: make_mock_runner(
                FidelityTier.SIMULATOR, cost_usd=0.50
            ),
            FidelityTier.SOFT: make_mock_runner(FidelityTier.SOFT, cost_usd=2.00),
        }
        run = run_pipeline(experiment_id="exp1", runners=runners)
        assert run.total_cost_usd == pytest.approx(2.55)
