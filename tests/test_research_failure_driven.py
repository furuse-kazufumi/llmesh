"""Tests for Phase 13 D6 — failure-driven hypothesis generation."""

from __future__ import annotations

import pytest

from llmesh.research import (
    DEFAULT_STRATEGIES,
    FailedExperiment,
    FailureDrivenGenerator,
    FailureDrivenRequest,
    Hypothesis,
    HypothesisResponse,
    invert_failures,
    mock_failure_driven_extract,
)
from llmesh.research.failure_driven import _negate_effect, _tighten_falsifier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _hyp(
    statement: str = "Activation checkpointing speeds up inference.",
    iv: str = "activation_checkpointing",
    dv: str = "forward_latency_ms",
    effect: str = "increase",
    falsifier: str = "latency increase < 5% across 1000 samples",
) -> Hypothesis:
    return Hypothesis(
        statement=statement,
        independent_variable=iv,
        dependent_variable=dv,
        expected_effect=effect,
        falsifier=falsifier,
    )


def _fail(
    mode: str = "timeout",
    delta: float | None = 0.03,
    hyp: Hypothesis | None = None,
) -> FailedExperiment:
    return FailedExperiment(
        original_hypothesis=hyp or _hyp(),
        failure_mode=mode,
        notes="exceeded budget",
        observed_metric_delta=delta,
    )


# ---------------------------------------------------------------------------
# Effect negation
# ---------------------------------------------------------------------------


class TestNegateEffect:
    def test_increase_to_decrease(self) -> None:
        assert _negate_effect("increase") == "decrease"
        assert _negate_effect("Significant rise") == "decrease"

    def test_decrease_to_increase(self) -> None:
        assert _negate_effect("decrease") == "increase"
        assert _negate_effect("pure slowdown") == "increase"  # only DECREASE word matches

    def test_increase_wins_when_both_present(self) -> None:
        # When a string contains both an INCREASE and a DECREASE word,
        # the check order returns "decrease" — documented behaviour, not a bug.
        assert _negate_effect("speedup slowdown") == "decrease"

    def test_negligible_to_non_trivial(self) -> None:
        assert _negate_effect("negligible") == "non-trivial"
        assert _negate_effect("no effect") == "non-trivial"

    def test_unknown_falls_back_to_negligible(self) -> None:
        assert _negate_effect("frobnicate") == "negligible"

    def test_empty_falls_back_to_negligible(self) -> None:
        assert _negate_effect("") == "negligible"

    def test_japanese_words(self) -> None:
        assert _negate_effect("向上") == "decrease"
        assert _negate_effect("低下") == "increase"


# ---------------------------------------------------------------------------
# Falsifier tightening
# ---------------------------------------------------------------------------


class TestTightenFalsifier:
    def test_replaces_first_numeric_token(self) -> None:
        out = _tighten_falsifier("latency increase < 5% across 1000 samples", 0.03)
        # 0.03/2 = 0.015 -> "0.015%" replaces the first numeric token "5%"
        assert "0.015" in out
        assert "across" in out  # rest preserved

    def test_no_delta_returns_input_unchanged(self) -> None:
        assert _tighten_falsifier("anything", None) == "anything"

    def test_empty_falsifier_with_delta_returns_empty(self) -> None:
        # No falsifier to tighten — observed_delta non-None but text empty
        assert _tighten_falsifier("", 0.05) == ""

    def test_no_numeric_token_appends_parenthetical(self) -> None:
        out = _tighten_falsifier("agent recovers cleanly", 0.10)
        assert "0.05" in out and "tightened" in out


# ---------------------------------------------------------------------------
# invert_failures
# ---------------------------------------------------------------------------


class TestInvertFailures:
    def test_default_strategies_emit_three_kinds(self) -> None:
        out = invert_failures([_fail()])
        assert len(out) >= 3
        # at least one negation-of-effect candidate
        assert any("does NOT cause" in h.statement for h in out)
        # at least one promote-failure-mode candidate
        assert any("Avoiding failure mode" in h.statement for h in out)
        # at least one anti-pattern candidate
        assert any("anti-pattern" in h.statement for h in out)

    def test_max_candidates_cap(self) -> None:
        out = invert_failures([_fail()] * 5, max_candidates=2)
        assert len(out) == 2

    def test_dedup_removes_identical_statements(self) -> None:
        # Two identical failures -> default dedup yields no duplicates
        out = invert_failures([_fail(), _fail()])
        assert len(out) == len(set(h.statement for h in out))

    def test_dedup_off_keeps_duplicates(self) -> None:
        out = invert_failures([_fail(), _fail()], dedup=False)
        # 2 failures × 3 default strategies = 6 candidates
        assert len(out) == 6

    def test_unknown_failure_mode_skips_promote(self) -> None:
        out = invert_failures([_fail(mode="unknown")])
        assert not any("Avoiding failure mode" in h.statement for h in out)

    def test_swap_iv_dv_strategy(self) -> None:
        out = invert_failures([_fail()], strategies=("swap_iv_dv",))
        assert len(out) == 1
        assert out[0].independent_variable == "forward_latency_ms"
        assert out[0].dependent_variable == "activation_checkpointing"

    def test_swap_iv_dv_skipped_when_axes_missing(self) -> None:
        bare = _hyp(iv="", dv="")
        out = invert_failures(
            [_fail(hyp=bare)], strategies=("swap_iv_dv",)
        )
        assert out == ()

    def test_relax_falsifier_keeps_statement(self) -> None:
        out = invert_failures([_fail()], strategies=("relax_falsifier",))
        assert len(out) == 1
        assert (
            out[0].statement
            == "Activation checkpointing speeds up inference."
        )
        # tightened threshold present
        assert "0.015" in out[0].falsifier or "tightened" in out[0].falsifier

    def test_empty_failures_returns_empty(self) -> None:
        assert invert_failures([]) == ()


# ---------------------------------------------------------------------------
# FailureDrivenGenerator
# ---------------------------------------------------------------------------


class TestFailureDrivenGenerator:
    def test_rule_based_default_returns_hypothesis_response(self) -> None:
        gen = FailureDrivenGenerator()
        resp = gen.run(FailureDrivenRequest(failures=(_fail(),)))
        assert isinstance(resp, HypothesisResponse)
        assert len(resp.candidates) >= 1
        assert resp.raw["strategy"] == "rule_based"

    def test_rule_based_with_no_failures(self) -> None:
        gen = FailureDrivenGenerator()
        resp = gen.run(FailureDrivenRequest(failures=()))
        assert resp.candidates == ()

    def test_extract_fn_path_parses_llm_response(self) -> None:
        gen = FailureDrivenGenerator(extract_fn=mock_failure_driven_extract)
        resp = gen.run(FailureDrivenRequest(failures=(_fail(),), max_candidates=3))
        assert len(resp.candidates) == 1
        assert "Mock-inverted" in resp.candidates[0].statement

    def test_extract_fn_receives_failures_and_seeds(self) -> None:
        captured: list[str] = []

        def capture(prompt: str) -> dict:
            captured.append(prompt)
            return {"hypotheses": []}

        gen = FailureDrivenGenerator(extract_fn=capture)
        gen.run(FailureDrivenRequest(failures=(_fail(),)))
        assert captured
        # Both rule-based seeds and failure descriptions appear in the prompt
        assert "Failures:" in captured[0]
        assert "Seeds:" in captured[0]
        assert "timeout" in captured[0]

    def test_default_strategies_are_used_when_request_omits(self) -> None:
        req = FailureDrivenRequest(failures=(_fail(),))
        assert req.strategies == DEFAULT_STRATEGIES

    def test_custom_strategies_override(self) -> None:
        req = FailureDrivenRequest(
            failures=(_fail(),),
            strategies=("anti_pattern",),
            max_candidates=10,
        )
        gen = FailureDrivenGenerator()
        resp = gen.run(req)
        # only one strategy active so we get exactly one candidate per failure
        assert len(resp.candidates) == 1
        assert "anti-pattern" in resp.candidates[0].statement
