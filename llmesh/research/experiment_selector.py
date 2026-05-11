"""Bayesian / free-energy experiment selector (Phase 14, D2).

Phase 13 generates hypotheses; this module picks **which one to test
next** under the predictive-coding / Bayesian-brain principle:

> the experiment worth running is the one whose result will move our
> belief the most.

Concretely, for each candidate experiment we model what the *predicted*
posterior over its target hypothesis would look like under each possible
outcome, and compute the expected KL divergence to the *current* prior.
That's the **expected information gain (EIG)**. Higher EIG = the result
is more surprising in expectation = the experiment is more worth running.

The implementation is stdlib-only — beliefs are Beta-distribution
counts (alpha / beta), priors are derived from `alpha / (alpha+beta)`,
and KL divergence is computed in nats with `math.log`. No numpy. The
trade-off: this is a coarse approximation suitable for orchestration
(which experiment to dispatch next), not a precision Bayesian inference
tool. Heavier inference belongs in :mod:`llmesh.domains.materials` or a
dedicated downstream agent.

Hierarchical EIG: a candidate that predicts an outcome relevant to a
*parent* hypothesis as well gets a small bonus proportional to the
parent's prior uncertainty — this captures the intuition that experiments
hitting an under-explored branch of the hypothesis tree are more useful.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable


_EPS = 1e-12


# ---------------------------------------------------------------------------
# Beliefs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Belief:
    """Beta-distribution belief about a single hypothesis being supported.

    ``alpha`` ≈ pseudo-count of supporting evidence + 1,
    ``beta`` ≈ pseudo-count of contradicting evidence + 1. The prior
    probability the hypothesis is true is ``alpha / (alpha+beta)``;
    its uncertainty (variance proxy) is highest when alpha = beta.
    """

    alpha: float = 1.0
    beta: float = 1.0
    parent: str | None = None  # optional: ID of a parent hypothesis

    def __post_init__(self) -> None:
        if self.alpha <= 0 or self.beta <= 0:
            raise ValueError(
                f"alpha and beta must be > 0 (got {self.alpha}, {self.beta})"
            )

    @property
    def probability(self) -> float:
        """Posterior mean ``alpha / (alpha+beta)``."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def uncertainty(self) -> float:
        """Beta-distribution variance (peak at p=0.5, falls as evidence grows)."""
        a, b = self.alpha, self.beta
        total = a + b
        return (a * b) / (total * total * (total + 1.0))

    def updated(self, *, success: bool, strength: float = 1.0) -> "Belief":
        """Apply one observation. ``success=True`` bumps alpha, else beta.

        ``strength`` lets a high-confidence observation count more than
        a noisy one without changing the dataclass shape.
        """
        if strength <= 0:
            raise ValueError(f"strength must be > 0 (got {strength})")
        return Belief(
            alpha=self.alpha + (strength if success else 0.0),
            beta=self.beta + (0.0 if success else strength),
            parent=self.parent,
        )


# ---------------------------------------------------------------------------
# Belief store
# ---------------------------------------------------------------------------


class BeliefStore:
    """Mutable map from hypothesis_id → :class:`Belief`.

    The store owns the lifecycle of beliefs: ``set`` / ``update`` are
    explicit so a caller's mistake (e.g. updating a typo'd id) raises
    instead of silently creating a stray entry. Hierarchical lookups
    walk the ``parent`` chain to bound the EIG bonus to the deepest
    ancestor whose own uncertainty is still meaningful.
    """

    def __init__(self) -> None:
        self._beliefs: dict[str, Belief] = {}

    def set(self, hypothesis_id: str, belief: Belief) -> None:
        if not hypothesis_id:
            raise ValueError("hypothesis_id must be non-empty")
        self._beliefs[hypothesis_id] = belief

    def get(self, hypothesis_id: str) -> Belief:
        if hypothesis_id not in self._beliefs:
            raise KeyError(f"unknown hypothesis_id: {hypothesis_id!r}")
        return self._beliefs[hypothesis_id]

    def has(self, hypothesis_id: str) -> bool:
        return hypothesis_id in self._beliefs

    def update(
        self, hypothesis_id: str, *, success: bool, strength: float = 1.0
    ) -> Belief:
        cur = self.get(hypothesis_id)
        nxt = cur.updated(success=success, strength=strength)
        self._beliefs[hypothesis_id] = nxt
        return nxt

    def ancestor_uncertainty(self, hypothesis_id: str) -> float:
        """Sum of ancestor uncertainties (capped at 4 ancestors).

        Stops on missing parent or cycle. Used as the hierarchical-EIG
        bonus weight so an experiment touching an under-explored branch
        scores higher than one hitting a well-mapped subtree.
        """
        if hypothesis_id not in self._beliefs:
            return 0.0
        total = 0.0
        seen: set[str] = {hypothesis_id}
        current = self._beliefs[hypothesis_id].parent
        for _ in range(4):
            if not current or current in seen or current not in self._beliefs:
                break
            seen.add(current)
            total += self._beliefs[current].uncertainty
            current = self._beliefs[current].parent
        return total


# ---------------------------------------------------------------------------
# Candidate experiments + EIG
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CandidateExperiment:
    """One experiment we could run next.

    ``hypothesis_id`` is the target whose belief this experiment will
    update. ``p_success_if_true`` and ``p_success_if_false`` are the
    likelihood model — what we'd expect to see if the hypothesis is
    actually true vs. false. ``cost_usd`` is informational; a downstream
    selector can divide EIG by cost for an efficiency ranking.
    """

    candidate_id: str
    hypothesis_id: str
    p_success_if_true: float = 0.8
    p_success_if_false: float = 0.2
    cost_usd: float = 0.0
    notes: str = ""

    def __post_init__(self) -> None:
        for name, value in (
            ("p_success_if_true", self.p_success_if_true),
            ("p_success_if_false", self.p_success_if_false),
        ):
            if not 0.0 < value < 1.0:
                raise ValueError(
                    f"{name} must be strictly between 0 and 1 (got {value})"
                )


def _safe_log(x: float) -> float:
    return math.log(max(x, _EPS))


def _kl_bernoulli(p: float, q: float) -> float:
    """KL(P || Q) for two Bernoulli distributions, in nats."""
    p = min(max(p, _EPS), 1.0 - _EPS)
    q = min(max(q, _EPS), 1.0 - _EPS)
    return p * (_safe_log(p) - _safe_log(q)) + (1.0 - p) * (
        _safe_log(1.0 - p) - _safe_log(1.0 - q)
    )


def expected_information_gain(
    cand: CandidateExperiment, store: BeliefStore
) -> float:
    """Expected KL between predicted-posterior and current prior.

    Marginalises over the two possible outcomes (success / failure)
    weighted by their predictive probabilities under the current prior.
    Result is non-negative; near-zero means the experiment is unlikely
    to move belief no matter how it lands.
    """
    prior = store.get(cand.hypothesis_id)
    p = prior.probability
    p_t = cand.p_success_if_true
    p_f = cand.p_success_if_false
    # Predictive probability of success: P(s) = p * p_s|t + (1-p) * p_s|f
    p_success = p * p_t + (1.0 - p) * p_f
    if p_success <= _EPS or p_success >= 1.0 - _EPS:
        # Outcome essentially deterministic -> no information.
        return 0.0

    # Bayesian posterior over H using the candidate's actual likelihood model.
    #   P(H | success) = P(success | H) * P(H) / P(success)
    #   P(H | failure) = P(failure | H) * P(H) / P(failure)
    p_h_given_success = (p_t * p) / p_success
    p_h_given_failure = ((1.0 - p_t) * p) / (1.0 - p_success)

    # Expected KL(posterior || prior) — Bernoulli surrogate at orchestration tier.
    kl_succ = _kl_bernoulli(p_h_given_success, p)
    kl_fail = _kl_bernoulli(p_h_given_failure, p)
    return p_success * kl_succ + (1.0 - p_success) * kl_fail


@dataclass(frozen=True)
class RankedCandidate:
    """Output of :func:`rank_candidates`."""

    candidate: CandidateExperiment
    eig: float                # expected information gain (nats)
    parent_bonus: float       # ancestor-uncertainty bonus (nats-equivalent)
    score: float              # eig + parent_bonus
    eig_per_usd: float        # score / max(cost_usd, eps); for cost-aware rank


def rank_candidates(
    candidates: Iterable[CandidateExperiment],
    store: BeliefStore,
    *,
    parent_bonus_weight: float = 0.25,
) -> list[RankedCandidate]:
    """Score each candidate and return them in descending score order.

    Unknown hypothesis IDs are skipped silently so an upstream planner
    that proposes more than the store currently knows about doesn't
    blow up. Equal-score candidates keep their input order (stable sort).
    """
    out: list[RankedCandidate] = []
    for cand in candidates:
        if not store.has(cand.hypothesis_id):
            continue
        eig = expected_information_gain(cand, store)
        parent = parent_bonus_weight * store.ancestor_uncertainty(cand.hypothesis_id)
        score = eig + parent
        per_usd = score / max(cand.cost_usd, _EPS)
        out.append(
            RankedCandidate(
                candidate=cand,
                eig=eig,
                parent_bonus=parent,
                score=score,
                eig_per_usd=per_usd,
            )
        )
    out.sort(key=lambda r: r.score, reverse=True)
    return out


@dataclass(frozen=True)
class SelectionReport:
    """Bundle of :func:`select_next` output for the caller."""

    chosen: RankedCandidate | None
    ranked: tuple[RankedCandidate, ...]
    budget_remaining_usd: float


def select_next(
    candidates: Iterable[CandidateExperiment],
    store: BeliefStore,
    *,
    budget_usd: float = float("inf"),
    parent_bonus_weight: float = 0.25,
) -> SelectionReport:
    """Pick the top-scoring candidate that fits the remaining budget.

    Falls back to ``chosen=None`` when nothing scores positive or
    nothing fits the budget — the caller can react (relax budget,
    generate more hypotheses, stop).
    """
    ranked = rank_candidates(
        candidates, store, parent_bonus_weight=parent_bonus_weight
    )
    chosen: RankedCandidate | None = None
    for r in ranked:
        if r.score <= 0:
            continue
        if r.candidate.cost_usd > budget_usd:
            continue
        chosen = r
        break
    return SelectionReport(
        chosen=chosen,
        ranked=tuple(ranked),
        budget_remaining_usd=(
            budget_usd - chosen.candidate.cost_usd if chosen else budget_usd
        ),
    )


__all__ = [
    "Belief",
    "BeliefStore",
    "CandidateExperiment",
    "RankedCandidate",
    "SelectionReport",
    "expected_information_gain",
    "rank_candidates",
    "select_next",
]
