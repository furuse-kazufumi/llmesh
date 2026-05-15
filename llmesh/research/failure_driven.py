"""Failure-driven hypothesis generation (Phase 13, D6).

The Phase 2 :class:`~llmesh.research.hypothesis.HypothesisAgent` turns
a literature digest into testable claims. D6 closes the loop on the
other side: when an experiment **fails**, instead of throwing the
result away, we invert the failed claim along well-defined axes and
emit fresh hypotheses that would have been hard to surface from the
literature alone.

This is the TRIZ "transformation / inversion" principle applied to
research planning. Competing frameworks (AutoGen, AI Scientist) tend
to be greedy / reward-driven and quietly drop failures; here failures
are a *first-class generator* of the next round's hypotheses.

The module is deliberately *not* LLM-bound. A pure stdlib rule set
covers the common inversion patterns (negate effect, relax falsifier,
swap IV / DV, surface failure-mode as a new IV). Optional LLM
augmentation is exposed via the same ``ExtractFn`` injection pattern
used by :class:`HypothesisAgent`, so a downstream caller can wire in
Anthropic / Ollama / a custom backend without depending on this
module's defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal

from llmesh.research.hypothesis import (
    Hypothesis,
    HypothesisResponse,
    parse_hypothesis_result,
)
from llmesh.research.literature import ExtractFn


# ---------------------------------------------------------------------------
# Failure record
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailedExperiment:
    """One observed failure to learn from.

    ``original_hypothesis`` is the claim that motivated the run; the
    ``failure_mode`` is a short tag (``"timeout"`` / ``"collision"`` /
    ``"grasp_fail"`` / ``"low_metric"`` / ``"unsafe"`` etc.) that the
    inverter uses to pick a strategy. ``observed_metric_delta`` lets
    a numeric-aware inverter relax a falsifier proportionally (e.g.
    if the threshold was 5% and we observed 3%, the new falsifier
    can be tightened to 2%).
    """

    original_hypothesis: Hypothesis
    failure_mode: str = "unknown"
    notes: str = ""
    observed_metric_delta: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Inversion strategies
# ---------------------------------------------------------------------------


InversionStrategy = Literal[
    "negate_expected_effect",   # increase -> decrease / negligible
    "relax_falsifier",          # tighten the falsifier given the observed delta
    "swap_iv_dv",               # treat the original DV as the next IV
    "promote_failure_mode",     # surface the failure mode itself as a new IV
    "anti_pattern",             # "under condition X, no positive effect occurs"
]


DEFAULT_STRATEGIES: tuple[InversionStrategy, ...] = (
    "negate_expected_effect",
    "promote_failure_mode",
    "anti_pattern",
)


# ---------------------------------------------------------------------------
# Rule-based inverter (stdlib only)
# ---------------------------------------------------------------------------


_INCREASE_WORDS: tuple[str, ...] = ("increase", "rise", "improve", "speedup", "上昇", "向上")
_DECREASE_WORDS: tuple[str, ...] = ("decrease", "drop", "degrade", "slowdown", "減少", "低下")
_NEGLIGIBLE_WORDS: tuple[str, ...] = ("negligible", "no effect", "no change", "変化なし", "ほぼゼロ")


def _negate_effect(effect: str) -> str:
    """Flip an effect direction; collapse to ``negligible`` for unknown phrasing."""
    e = (effect or "").strip().lower()
    if not e:
        return "negligible"
    if any(w in e for w in _INCREASE_WORDS):
        return "decrease"
    if any(w in e for w in _DECREASE_WORDS):
        return "increase"
    if any(w in e for w in _NEGLIGIBLE_WORDS):
        return "non-trivial"
    return "negligible"


def _tighten_falsifier(falsifier: str, observed_delta: float | None) -> str:
    """Tighten a numeric falsifier if we have an observed delta.

    Tries the cheap pattern ``"X% on Y"`` first; falls back to
    appending a parenthetical when no number can be extracted, so the
    function always returns a usable falsifier string.
    """
    f = falsifier or ""
    if observed_delta is None or not f:
        if observed_delta is not None:
            return (
                f"{f.strip()} (observed delta {abs(observed_delta):.3g} so "
                f"new test must beat half of that)"
            ).strip()
        return f
    # Tighten any first numeric token that looks like a percentage.
    new_threshold = max(0.0, abs(observed_delta) / 2.0)
    tokens = f.split()
    for i, t in enumerate(tokens):
        cleaned = t.rstrip("%,.;")
        try:
            float(cleaned)
        except ValueError:
            continue
        tokens[i] = f"{new_threshold:.3g}%"
        return " ".join(tokens)
    return (
        f"{f.strip()} (tightened: must beat {new_threshold:.3g} given prior observation)"
    ).strip()


def _invert_one(
    failure: FailedExperiment, strategy: InversionStrategy
) -> Hypothesis | None:
    """Apply one strategy. Returns ``None`` when the strategy doesn't fit."""
    orig = failure.original_hypothesis
    if strategy == "negate_expected_effect":
        new_effect = _negate_effect(orig.expected_effect)
        return Hypothesis(
            statement=(
                f"Under failure mode '{failure.failure_mode}', "
                f"{orig.independent_variable or 'the prior IV'} does NOT cause the "
                f"previously claimed effect on "
                f"{orig.dependent_variable or 'the prior DV'}."
            ),
            independent_variable=orig.independent_variable,
            dependent_variable=orig.dependent_variable,
            expected_effect=new_effect,
            falsifier=_tighten_falsifier(orig.falsifier, failure.observed_metric_delta),
        )
    if strategy == "relax_falsifier":
        return Hypothesis(
            statement=orig.statement,
            independent_variable=orig.independent_variable,
            dependent_variable=orig.dependent_variable,
            expected_effect=orig.expected_effect,
            falsifier=_tighten_falsifier(orig.falsifier, failure.observed_metric_delta),
        )
    if strategy == "swap_iv_dv":
        if not orig.dependent_variable or not orig.independent_variable:
            return None
        return Hypothesis(
            statement=(
                f"{orig.dependent_variable} causally influences "
                f"{orig.independent_variable}, reversing the prior assumption."
            ),
            independent_variable=orig.dependent_variable,
            dependent_variable=orig.independent_variable,
            expected_effect="non-trivial",
            falsifier=(
                "Correlation between swapped axes < 0.1 across replicates"
            ),
        )
    if strategy == "promote_failure_mode":
        # The failure mode itself becomes the next independent variable.
        if not failure.failure_mode or failure.failure_mode == "unknown":
            return None
        return Hypothesis(
            statement=(
                f"Avoiding failure mode '{failure.failure_mode}' is necessary "
                f"and possibly sufficient for the original outcome on "
                f"{orig.dependent_variable or 'the prior DV'}."
            ),
            independent_variable=f"avoid_{failure.failure_mode}",
            dependent_variable=orig.dependent_variable,
            expected_effect="enables",
            falsifier=(
                f"Outcome unchanged when '{failure.failure_mode}' is suppressed"
            ),
        )
    if strategy == "anti_pattern":
        return Hypothesis(
            statement=(
                f"Conditions producing '{failure.failure_mode}' constitute an "
                f"anti-pattern; no setting of "
                f"{orig.independent_variable or 'the prior IV'} recovers the "
                f"claimed effect once the anti-pattern triggers."
            ),
            independent_variable=orig.independent_variable,
            dependent_variable=f"recovery_under_{failure.failure_mode}",
            expected_effect="negligible",
            falsifier=(
                "Recovery rate > 50% with no intervention on the failure mode"
            ),
        )
    return None


def invert_failures(
    failures: Iterable[FailedExperiment],
    *,
    strategies: tuple[InversionStrategy, ...] = DEFAULT_STRATEGIES,
    max_candidates: int = 6,
    dedup: bool = True,
) -> tuple[Hypothesis, ...]:
    """Generate hypotheses from failures using rule-based inversion.

    Walks each failure × strategy pair, drops ``None`` results, and
    de-duplicates by statement when ``dedup=True`` so the same anti-
    pattern isn't surfaced once per failure record. Stops once
    ``max_candidates`` hypotheses are collected.
    """
    out: list[Hypothesis] = []
    seen: set[str] = set()
    for failure in failures:
        for strategy in strategies:
            cand = _invert_one(failure, strategy)
            if cand is None:
                continue
            key = cand.statement.strip().lower()
            if dedup and key in seen:
                continue
            seen.add(key)
            out.append(cand)
            if len(out) >= max_candidates:
                return tuple(out)
    return tuple(out)


# ---------------------------------------------------------------------------
# Agent-style wrapper for HypothesisResponse compatibility
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FailureDrivenRequest:
    """Bundle of failures plus generator knobs."""

    failures: tuple[FailedExperiment, ...]
    strategies: tuple[InversionStrategy, ...] = DEFAULT_STRATEGIES
    max_candidates: int = 6


class FailureDrivenGenerator:
    """Turn :class:`FailedExperiment` records into :class:`HypothesisResponse`.

    Exposes two paths:

    - Default: pure rule-based inversion (:func:`invert_failures`).
      No external dependencies, fully deterministic, easy to unit test.
    - Optional: an ``extract_fn`` (same signature as
      :class:`HypothesisAgent`) for LLM-augmented inversion. When
      supplied, the rule-based hypotheses are used to seed a prompt
      and the LLM's response is parsed back with
      :func:`parse_hypothesis_result`.
    """

    def __init__(self, *, extract_fn: ExtractFn | None = None) -> None:
        self._extract = extract_fn

    def run(self, request: FailureDrivenRequest) -> HypothesisResponse:
        rule_based = invert_failures(
            request.failures,
            strategies=request.strategies,
            max_candidates=request.max_candidates,
        )
        if self._extract is None:
            return HypothesisResponse(
                candidates=rule_based,
                raw={"strategy": "rule_based", "n_failures": len(request.failures)},
            )
        prompt = self._build_llm_prompt(request, rule_based)
        result = self._extract(prompt)
        return parse_hypothesis_result(result, max_candidates=request.max_candidates)

    @staticmethod
    def _build_llm_prompt(
        request: FailureDrivenRequest, seeds: tuple[Hypothesis, ...]
    ) -> str:
        seed_block = "\n".join(
            f"- {h.statement} (IV={h.independent_variable}, DV={h.dependent_variable})"
            for h in seeds
        ) or "(no rule-based seeds — invent fresh)"
        failure_block = "\n".join(
            f"- mode={f.failure_mode}, original={f.original_hypothesis.statement!r}, "
            f"notes={f.notes!r}"
            for f in request.failures
        ) or "(no failures)"
        return (
            "You are a research hypothesis inverter. Given the failed "
            "experiments below and a list of rule-based seed hypotheses, "
            "produce UP TO {max} stronger, more falsifiable hypotheses. "
            "Reply strict JSON: {{\"hypotheses\": [{{\"statement\": ..., "
            "\"independent_variable\": ..., \"dependent_variable\": ..., "
            "\"expected_effect\": ..., \"falsifier\": ...}}, ...]}}.\n\n"
            "Failures:\n{fail}\n\nSeeds:\n{seed}\n"
        ).format(max=request.max_candidates, fail=failure_block, seed=seed_block)


# ---------------------------------------------------------------------------
# Mock for tests / smoke runs
# ---------------------------------------------------------------------------


def mock_failure_driven_extract(prompt: str) -> dict[str, Any]:
    """Deterministic mock matching the prompt's ``hypotheses`` schema."""
    return {
        "hypotheses": [
            {
                "statement": "Mock-inverted: latency does not drop under checkpointing.",
                "independent_variable": "activation_checkpointing",
                "dependent_variable": "forward_latency_ms",
                "expected_effect": "negligible",
                "falsifier": "latency drop > 5% across replicates",
            },
        ],
        "_mock": True,
    }


__all__ = [
    "DEFAULT_STRATEGIES",
    "FailedExperiment",
    "FailureDrivenGenerator",
    "FailureDrivenRequest",
    "InversionStrategy",
    "invert_failures",
    "mock_failure_driven_extract",
]
