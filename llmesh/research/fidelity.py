"""Multi-fidelity pipeline (Phase 17, D5).

The research-orchestration goal stated in
``project_research_orchestration.md`` is "failure cost ≈ 0". This
module implements the staged pipeline that gets us there: every
experiment starts at the cheapest fidelity tier (``mock``) and
*automatically promotes* to a higher tier only when the lower tier
has produced a passing result with sufficient confidence. A failure
at any tier halts promotion and feeds back into the failure-driven
hypothesis generator (Phase 13, D6) instead of bubbling up to a
real-world resource.

Tiers (lowest → highest):

- ``mock``       — pure-Python deterministic stub, no I/O
- ``simulator``  — physics simulator (Gazebo / MuJoCo / Drake mock)
- ``soft``       — low-damage real-world surrogate (paper, foam,
                   cardboard manipulanda)
- ``real``       — production hardware

Components plug in via :class:`FidelityRunner` — a callable
``(experiment_id) -> FidelityResult``. The pipeline calls each
runner in order and aborts as soon as one fails the promotion gate.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Callable


class FidelityTier(str, Enum):
    """Ordered fidelity tiers; comparison uses the ``rank`` property."""

    MOCK = "mock"
    SIMULATOR = "simulator"
    SOFT = "soft"
    REAL = "real"

    @property
    def rank(self) -> int:
        return _TIER_ORDER[self.value]


_TIER_ORDER: dict[str, int] = {
    FidelityTier.MOCK.value: 0,
    FidelityTier.SIMULATOR.value: 1,
    FidelityTier.SOFT.value: 2,
    FidelityTier.REAL.value: 3,
}


DEFAULT_TIER_ORDER: tuple[FidelityTier, ...] = (
    FidelityTier.MOCK,
    FidelityTier.SIMULATOR,
    FidelityTier.SOFT,
    FidelityTier.REAL,
)


@dataclass(frozen=True)
class FidelityResult:
    """Outcome from one tier of the pipeline."""

    tier: FidelityTier
    success: bool
    confidence: float = 0.0       # 0..1; gate uses this
    metric: float = 0.0           # primary observed metric
    cost_usd: float = 0.0
    notes: str = ""
    metadata: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(
                f"confidence must be in [0, 1] (got {self.confidence})"
            )


FidelityRunner = Callable[[str], FidelityResult]


@dataclass(frozen=True)
class PipelineConfig:
    """Knobs for the promotion gate.

    ``min_confidence`` is the threshold a tier's result must hit to
    promote to the next. ``budget_usd`` caps the total spend; the
    pipeline stops before crossing it. ``allow_skip`` lets a runner
    return a tier other than its assigned one (e.g. a mock that
    decides the question is unanswerable at mock fidelity).
    """

    min_confidence: float = 0.75
    budget_usd: float = float("inf")
    tier_order: tuple[FidelityTier, ...] = DEFAULT_TIER_ORDER
    allow_skip: bool = False

    def __post_init__(self) -> None:
        if not 0.0 < self.min_confidence < 1.0:
            raise ValueError(
                f"min_confidence must be in (0, 1) (got {self.min_confidence})"
            )
        if self.budget_usd <= 0:
            raise ValueError(
                f"budget_usd must be > 0 (got {self.budget_usd})"
            )
        if not self.tier_order:
            raise ValueError("tier_order must contain at least one tier")


@dataclass(frozen=True)
class PipelineRun:
    """Aggregate result across all tiers attempted."""

    experiment_id: str
    results: tuple[FidelityResult, ...]
    promoted_to: FidelityTier | None     # highest tier reached
    halted_reason: str                   # "success" | "low_confidence" | "failure" | "budget"
    total_cost_usd: float = 0.0


def _runner_for(
    tier: FidelityTier, runners: dict[FidelityTier, FidelityRunner]
) -> FidelityRunner | None:
    return runners.get(tier)


def run_pipeline(
    *,
    experiment_id: str,
    runners: dict[FidelityTier, FidelityRunner],
    config: PipelineConfig | None = None,
) -> PipelineRun:
    """Run an experiment through the fidelity pipeline.

    Walks ``tier_order`` and dispatches the runner for each. Halts on:
    1. ``success=False`` or ``confidence < min_confidence`` (gate)
    2. ``total_cost > budget_usd`` (budget)
    3. End of tier_order reached (full promotion).

    Tiers with no registered runner are skipped silently — partial
    pipelines (e.g. mock + sim only, no real device) are explicitly
    supported so a CI run doesn't fail for lack of hardware.
    """
    cfg = config or PipelineConfig()
    results: list[FidelityResult] = []
    total_cost = 0.0
    promoted: FidelityTier | None = None
    halted_reason = "success"

    for tier in cfg.tier_order:
        runner = _runner_for(tier, runners)
        if runner is None:
            continue
        # budget check *before* invoking the runner
        if total_cost > cfg.budget_usd:
            halted_reason = "budget"
            break
        result = runner(experiment_id)
        results.append(result)
        total_cost += result.cost_usd
        # tier was actually reached — record it before any halt check
        promoted = result.tier
        if total_cost > cfg.budget_usd:
            halted_reason = "budget"
            break
        if not result.success:
            halted_reason = "failure"
            break
        if result.confidence < cfg.min_confidence:
            halted_reason = "low_confidence"
            break

    return PipelineRun(
        experiment_id=experiment_id,
        results=tuple(results),
        promoted_to=promoted,
        halted_reason=halted_reason,
        total_cost_usd=total_cost,
    )


# ---------------------------------------------------------------------------
# Mock runners — useful for tests and pipeline-level integration smoke
# ---------------------------------------------------------------------------


def make_mock_runner(
    tier: FidelityTier,
    *,
    success: bool = True,
    confidence: float = 0.9,
    metric: float = 1.0,
    cost_usd: float = 0.0,
) -> FidelityRunner:
    """Construct a deterministic runner for a fixed outcome.

    Used in tests and as a placeholder when a real runner isn't wired
    in yet so the pipeline shape can still be exercised end-to-end.
    """

    def runner(experiment_id: str) -> FidelityResult:
        return FidelityResult(
            tier=tier,
            success=success,
            confidence=confidence,
            metric=metric,
            cost_usd=cost_usd,
            notes=f"mock runner for {experiment_id}",
        )

    return runner


__all__ = [
    "DEFAULT_TIER_ORDER",
    "FidelityResult",
    "FidelityRunner",
    "FidelityTier",
    "PipelineConfig",
    "PipelineRun",
    "make_mock_runner",
    "run_pipeline",
]
