"""Replanning loop + failure-mode handling for arm scenarios (Phase 10).

The Phase 10 scope calls out three failure modes: ``collision`` /
``grasp_fail`` / ``timeout``. This module names them, exposes a
:class:`FailureMode` enum, and ships a tiny :class:`ReplanController`
that decides ``retry`` / ``adapt`` / ``abort`` from a fault stream —
deliberately lightweight because the heavy lifting (continuous-time
trajectory rewrite, contact-aware planner) is a Phase 11+ concern.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class FailureMode(StrEnum):
    """Why a step failed during arm execution."""

    NONE = "none"
    COLLISION = "collision"
    GRASP_FAIL = "grasp_fail"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ExecutionFault:
    """One executor-side fault observation."""

    step_index: int
    mode: FailureMode
    detail: str = ""


@dataclass(frozen=True)
class ReplanDecision:
    """Replanner output for a single fault.

    ``action`` is the canonical decision (``"retry"`` / ``"adapt"`` /
    ``"abort"``) consumed by the executor. ``reason`` is the
    human-readable justification surfaced to the operator and the
    paper exporter.
    """

    action: str
    reason: str
    new_step_index: int | None = None  # only set on adapt


class ReplanController:
    """Trivial replanner with sensible defaults per failure mode.

    Each :class:`FailureMode` has an independent retry budget. Once
    exhausted, the controller switches to ``adapt`` (rewind two steps
    if possible) and finally ``abort``. The retry budgets are
    constructor-injectable so demos can run cheap-fail scenarios.
    """

    def __init__(
        self,
        *,
        collision_retries: int = 0,
        grasp_retries: int = 2,
        timeout_retries: int = 1,
    ) -> None:
        self._budget: dict[FailureMode, int] = {
            FailureMode.COLLISION: max(0, int(collision_retries)),
            FailureMode.GRASP_FAIL: max(0, int(grasp_retries)),
            FailureMode.TIMEOUT: max(0, int(timeout_retries)),
        }

    def decide(self, fault: ExecutionFault) -> ReplanDecision:
        if fault.mode in (FailureMode.NONE, FailureMode.UNKNOWN):
            return ReplanDecision(
                action="abort",
                reason=f"non-actionable mode={fault.mode}",
            )
        remaining = self._budget.get(fault.mode, 0)
        if remaining > 0:
            self._budget[fault.mode] = remaining - 1
            return ReplanDecision(
                action="retry",
                reason=f"retry budget left for {fault.mode}: {remaining - 1}",
            )
        # No retry budget left — try one adapt (rewind two steps if possible)
        if fault.step_index >= 2:
            new_idx = fault.step_index - 2
            return ReplanDecision(
                action="adapt",
                reason=f"adapt by rewinding two steps to index {new_idx}",
                new_step_index=new_idx,
            )
        return ReplanDecision(
            action="abort",
            reason=f"no retry budget and step_index={fault.step_index} too small to adapt",
        )

    @property
    def budgets(self) -> dict[FailureMode, int]:
        """Current remaining retry budgets (snapshot)."""
        return dict(self._budget)


__all__ = ["ExecutionFault", "FailureMode", "ReplanController", "ReplanDecision"]
