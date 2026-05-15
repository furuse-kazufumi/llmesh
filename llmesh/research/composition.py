"""Task-specific component composition via Shapley value (Phase 15, D7).

"Cross-Component Interference" (CCI) research shows that stacking every
agent capability — planner + tools + RAG + reflection + critic — often
*hurts* task performance vs. a curated subset. D7 picks the subset
*per task* using Shapley value attribution: each component's marginal
contribution is averaged over all orderings, so a component that helps
on its own but redundantly with the rest scores low.

Implementation notes:

- Stdlib only. Exact Shapley enumeration is O(2^N) — fine for the
  small N (≤ 10) we expect at the orchestration tier. A
  Monte-Carlo permutation sampler is offered for N > 10.
- The value function is injected by the caller (``ValueFn``): given a
  subset of component IDs, return a scalar score (accuracy / reward /
  -loss / anything monotone-better). The selector is value-agnostic.
- Output is a ``CompositionPlan`` listing each component with its
  Shapley value and a recommended subset (those with value above a
  threshold *and* whose addition still net-positive given the subset
  size penalty).

The differentiator over LangGraph / AutoGen "full bundle" composition
is *measured* per-task subset selection rather than fixed pipeline,
backed by a well-known game-theoretic attribution method instead of
ad-hoc heuristics.
"""

from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from typing import Callable, Iterable


# A scalar score for a given subset of component IDs.
ValueFn = Callable[[frozenset[str]], float]


@dataclass(frozen=True)
class ComponentScore:
    """Shapley value of one component plus its marginal contribution."""

    component_id: str
    shapley_value: float
    marginal_alone: float       # value(coalition={c}) - value(coalition={})
    marginal_with_all: float    # value(all) - value(all \ {c})


@dataclass(frozen=True)
class CompositionPlan:
    """Recommended subset + per-component scores."""

    chosen: tuple[str, ...]              # recommended subset
    scores: tuple[ComponentScore, ...]   # all components, ranked desc by value
    method: str = "exact_shapley"        # "exact_shapley" | "monte_carlo"
    value_full: float = 0.0              # value(all components together)
    value_chosen: float = 0.0            # value(chosen subset)
    permutations_sampled: int = 0        # for MC; 0 for exact


# ---------------------------------------------------------------------------
# Exact Shapley
# ---------------------------------------------------------------------------


def _exact_shapley(
    components: tuple[str, ...], value_fn: ValueFn
) -> tuple[ComponentScore, ...]:
    """Compute exact Shapley for each component via brute-force enumeration."""
    n = len(components)
    if n == 0:
        return ()
    # Pre-tabulate value at every subset to avoid recomputation.
    subsets = list(_powerset(components))
    value_cache: dict[frozenset[str], float] = {
        s: value_fn(s) for s in subsets
    }
    # Shapley factorial weight: k! (n-k-1)! / n!
    n_fact = math.factorial(n)
    out: list[ComponentScore] = []
    full_set = frozenset(components)
    empty = frozenset()
    for c in components:
        sv = 0.0
        for subset in subsets:
            if c in subset:
                continue
            k = len(subset)
            weight = math.factorial(k) * math.factorial(n - k - 1) / n_fact
            sv += weight * (
                value_cache[subset | {c}] - value_cache[subset]
            )
        marginal_alone = value_cache[frozenset({c})] - value_cache[empty]
        marginal_with_all = value_cache[full_set] - value_cache[
            frozenset(x for x in components if x != c)
        ]
        out.append(
            ComponentScore(
                component_id=c,
                shapley_value=sv,
                marginal_alone=marginal_alone,
                marginal_with_all=marginal_with_all,
            )
        )
    out.sort(key=lambda s: s.shapley_value, reverse=True)
    return tuple(out)


def _powerset(items: tuple[str, ...]) -> Iterable[frozenset[str]]:
    for size in range(len(items) + 1):
        for combo in itertools.combinations(items, size):
            yield frozenset(combo)


# ---------------------------------------------------------------------------
# Monte-Carlo Shapley (for N > exact cap)
# ---------------------------------------------------------------------------


def _monte_carlo_shapley(
    components: tuple[str, ...],
    value_fn: ValueFn,
    *,
    n_permutations: int,
    seed: int | None,
) -> tuple[ComponentScore, ...]:
    rng = random.Random(seed)
    n = len(components)
    if n == 0:
        return ()
    sums: dict[str, float] = {c: 0.0 for c in components}
    perm_list = list(components)
    for _ in range(n_permutations):
        rng.shuffle(perm_list)
        prefix: list[str] = []
        prev_value = value_fn(frozenset())
        for c in perm_list:
            prefix.append(c)
            new_value = value_fn(frozenset(prefix))
            sums[c] += new_value - prev_value
            prev_value = new_value
    empty = frozenset()
    full = frozenset(components)
    return tuple(
        sorted(
            (
                ComponentScore(
                    component_id=c,
                    shapley_value=sums[c] / n_permutations,
                    marginal_alone=value_fn(frozenset({c})) - value_fn(empty),
                    marginal_with_all=(
                        value_fn(full)
                        - value_fn(frozenset(x for x in components if x != c))
                    ),
                )
                for c in components
            ),
            key=lambda s: s.shapley_value,
            reverse=True,
        )
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_EXACT_CAP = 10  # 2^10 = 1024 subsets — cheap enough


def compose(
    components: Iterable[str],
    value_fn: ValueFn,
    *,
    threshold: float = 0.0,
    n_permutations: int = 200,
    seed: int | None = 0,
) -> CompositionPlan:
    """Pick the best subset of ``components`` for one task.

    Steps:
    1. Score each component with Shapley value (exact for N ≤ 10, else MC).
    2. Greedily add components in descending Shapley order while the
       chosen subset's value strictly improves and the next component's
       shapley_value > ``threshold``.

    The greedy step matters: if two components have positive Shapley
    individually but are mutually redundant, adding the second one may
    not improve value over the first. Greedy stops there so the chosen
    subset is the *smallest* one that captures most of the upside.

    Returns a :class:`CompositionPlan`; ``chosen`` is empty when no
    component clears the threshold.
    """
    comps = tuple(dict.fromkeys(components))  # dedup, preserve order
    n = len(comps)
    if n == 0:
        return CompositionPlan(chosen=(), scores=(), value_full=value_fn(frozenset()))

    if n <= _EXACT_CAP:
        scores = _exact_shapley(comps, value_fn)
        method = "exact_shapley"
        permutations = 0
    else:
        scores = _monte_carlo_shapley(
            comps, value_fn, n_permutations=n_permutations, seed=seed
        )
        method = "monte_carlo"
        permutations = n_permutations

    full_value = value_fn(frozenset(comps))
    chosen: list[str] = []
    current_value = value_fn(frozenset())
    for s in scores:
        if s.shapley_value <= threshold:
            break
        trial = frozenset(chosen + [s.component_id])
        trial_value = value_fn(trial)
        if trial_value > current_value:
            chosen.append(s.component_id)
            current_value = trial_value
    return CompositionPlan(
        chosen=tuple(chosen),
        scores=scores,
        method=method,
        value_full=full_value,
        value_chosen=current_value,
        permutations_sampled=permutations,
    )


__all__ = [
    "ComponentScore",
    "CompositionPlan",
    "ValueFn",
    "compose",
]
