"""ExperimentExecutor — turn an ExperimentPlan into measured results (Phase 7).

Phase 7 introduces the missing link between Phase 2's
:class:`ExperimentPlan` and Phase 7's reviewer feedback: a step-by-step
**executor** that pretends to run each :class:`ExperimentStep` and
records a per-metric value. Real implementations swap in genuine
simulators / lab equipment / training jobs via the :class:`ExperimentExecutor`
ABC; the :class:`MockExperimentExecutor` shipped here is deterministic
and stdlib-only so the e2e demo pipeline runs without external
dependencies.
"""

from __future__ import annotations

import hashlib
import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from llmesh.research.planner import ExperimentPlan, ExperimentStep


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StepRun:
    """Output of executing one ExperimentStep."""

    order: int
    action: str
    duration_ms: float
    metrics: dict[str, float] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class ExperimentResult:
    """Aggregate of every StepRun for one plan.

    ``metrics`` is the union of every step's per-metric value, with
    duplicates resolved by taking the last value seen — useful for
    plotters that want a single number per metric per run.
    """

    plan: ExperimentPlan
    steps: tuple[StepRun, ...]
    metrics: dict[str, float] = field(default_factory=dict)
    success: bool = True
    notes: str = ""


# ---------------------------------------------------------------------------
# ABC + mock
# ---------------------------------------------------------------------------


class ExperimentExecutor(ABC):
    """ABC: ``run(plan) → ExperimentResult``."""

    @abstractmethod
    def run(self, plan: ExperimentPlan) -> ExperimentResult:
        ...


def _stable_float(seed: str, lo: float, hi: float) -> float:
    """Deterministic float in ``[lo, hi]`` derived from ``seed``."""
    if hi <= lo:
        raise ValueError("hi must be > lo")
    digest = hashlib.sha1(seed.encode("utf-8"), usedforsecurity=False)
    frac = int.from_bytes(digest.digest()[:4], "big") / 0xFFFFFFFF
    return lo + frac * (hi - lo)


class MockExperimentExecutor(ExperimentExecutor):
    """Deterministic mock — runs each step in ``order`` and synthesises metrics.

    For each step the executor:

    1. Records a synthetic ``duration_ms`` derived from the step hash.
    2. Emits one synthetic value per metric named in ``plan.metrics``.
       The value is bounded to ``[low, high]`` (defaults 0..1) and is
       reproducible across runs given the same plan.

    The aggregate :class:`ExperimentResult` merges step-level metrics
    by taking the **last** observed value, which gives a stable single
    number per metric for downstream paper-export figures.
    """

    def __init__(
        self,
        *,
        low: float = 0.0,
        high: float = 1.0,
        baseline_duration_ms: float = 50.0,
    ) -> None:
        if high <= low:
            raise ValueError("high must be > low")
        if baseline_duration_ms <= 0:
            raise ValueError("baseline_duration_ms must be > 0")
        self._low = float(low)
        self._high = float(high)
        self._baseline = float(baseline_duration_ms)

    def run(self, plan: ExperimentPlan) -> ExperimentResult:
        step_runs: list[StepRun] = []
        agg: dict[str, float] = {}
        for step in plan.steps:
            duration = self._baseline * (
                1.0 + _stable_float(f"dur|{plan.hypothesis}|{step.action}", 0.0, 1.5)
            )
            metrics: dict[str, float] = {}
            for metric_name in plan.metrics:
                seed = f"{plan.hypothesis}|{step.action}|{metric_name}"
                value = _stable_float(seed, self._low, self._high)
                metrics[metric_name] = value
                agg[metric_name] = value
            step_runs.append(
                StepRun(
                    order=step.order,
                    action=step.action,
                    duration_ms=duration,
                    metrics=metrics,
                    notes=step.notes,
                )
            )
        # mark success if every requested metric was observed at least once
        success = bool(plan.metrics) and all(m in agg for m in plan.metrics)
        return ExperimentResult(
            plan=plan,
            steps=tuple(step_runs),
            metrics=agg,
            success=success,
            notes=f"mock-exec n_steps={len(step_runs)}",
        )


def _is_finite(x: float) -> bool:
    return not math.isnan(x) and not math.isinf(x)


def summarise_result(result: ExperimentResult) -> dict[str, Any]:
    """Compact JSON-friendly summary for trace logging."""
    return {
        "success": result.success,
        "n_steps": len(result.steps),
        "total_duration_ms": sum(s.duration_ms for s in result.steps),
        "metrics": {k: v for k, v in result.metrics.items() if _is_finite(v)},
    }


__all__ = [
    "ExperimentExecutor",
    "ExperimentResult",
    "MockExperimentExecutor",
    "StepRun",
    "summarise_result",
]


# silence "unused" hint if ExperimentStep is removed in a future refactor
_ = ExperimentStep
