"""Embodied failure replay + attribution (Phase 16, D4).

Phase 10/11 give us :class:`TrajectoryEpisode` (a recorded VLA run)
and Phase 13 gives us :class:`FailedExperiment` (a failed claim).
D4 closes the gap between language-level reflection (which Reflexion-
style agents do well) and physics-grounded post-mortem (which they
don't): for each failed episode we *replay* the trajectory against a
set of physical constraints and emit a structured ``ReplayReport``
that pinpoints *which waypoint* violated *which constraint*, plus an
:class:`AttributionLink`-compatible chain back through the trace.

The constraint checker is intentionally simple stdlib code — joint
limits, velocity caps, monotonic gripper transitions, no-go zones in
joint space. Each is a callable so a downstream caller (Phase 18+ with
a real physics engine) can drop in MuJoCo / PyBullet / Drake checks
without changing this module's API.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable

from llmesh.core.cost_attribution import AttributionLink
from llmesh.vla.joint_decoder import JointTrajectory


# ---------------------------------------------------------------------------
# Constraint protocol
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ConstraintViolation:
    """One physical constraint failure during replay."""

    waypoint_index: int          # 0-based index into trajectory.waypoints
    constraint_name: str         # e.g. "joint_limit", "velocity_cap"
    severity: str = "error"      # "warning" | "error" | "fatal"
    detail: str = ""


# A constraint checker: given a trajectory, return any violations.
ConstraintFn = Callable[[JointTrajectory], list[ConstraintViolation]]


# ---------------------------------------------------------------------------
# Built-in constraint checkers (stdlib only)
# ---------------------------------------------------------------------------


def joint_limit_checker(
    *,
    lower: tuple[float, ...] = (-3.14,) * 6,
    upper: tuple[float, ...] = (3.14,) * 6,
) -> ConstraintFn:
    """Reject waypoints whose joint position is outside [lower, upper]."""

    def check(traj: JointTrajectory) -> list[ConstraintViolation]:
        out: list[ConstraintViolation] = []
        for i, wp in enumerate(traj.waypoints):
            for j, pos in enumerate(wp.positions):
                if j >= len(lower) or j >= len(upper):
                    continue
                if pos < lower[j] or pos > upper[j]:
                    out.append(
                        ConstraintViolation(
                            waypoint_index=i,
                            constraint_name="joint_limit",
                            severity="error",
                            detail=(
                                f"joint[{j}]={pos:.3f} outside "
                                f"[{lower[j]:.3f}, {upper[j]:.3f}]"
                            ),
                        )
                    )
        return out

    return check


def velocity_cap_checker(max_delta_per_second: float = 2.0) -> ConstraintFn:
    """Reject waypoints whose per-second joint delta exceeds ``max``."""

    def check(traj: JointTrajectory) -> list[ConstraintViolation]:
        out: list[ConstraintViolation] = []
        for i in range(1, len(traj.waypoints)):
            prev = traj.waypoints[i - 1]
            cur = traj.waypoints[i]
            if cur.duration_s <= 0:
                continue
            for j in range(min(len(prev.positions), len(cur.positions))):
                delta = abs(cur.positions[j] - prev.positions[j])
                rate = delta / cur.duration_s
                if rate > max_delta_per_second:
                    out.append(
                        ConstraintViolation(
                            waypoint_index=i,
                            constraint_name="velocity_cap",
                            severity="warning",
                            detail=(
                                f"joint[{j}] rate {rate:.3f}/s > "
                                f"{max_delta_per_second:.3f}/s"
                            ),
                        )
                    )
        return out

    return check


def gripper_monotonic_checker() -> ConstraintFn:
    """Flag wild gripper toggles (open→closed→open→closed in 3 steps).

    Used to detect mis-decoded plans that release a grasped object
    between approach and place.
    """

    def check(traj: JointTrajectory) -> list[ConstraintViolation]:
        out: list[ConstraintViolation] = []
        if len(traj.waypoints) < 3:
            return out
        for i in range(2, len(traj.waypoints)):
            a, b, c = (
                traj.waypoints[i - 2],
                traj.waypoints[i - 1],
                traj.waypoints[i],
            )
            # alternating pattern with ≥0.5 swing each step
            swing1 = abs(b.gripper - a.gripper)
            swing2 = abs(c.gripper - b.gripper)
            if swing1 >= 0.5 and swing2 >= 0.5 and (
                (b.gripper > a.gripper) != (c.gripper > b.gripper)
            ):
                out.append(
                    ConstraintViolation(
                        waypoint_index=i,
                        constraint_name="gripper_monotonic",
                        severity="warning",
                        detail=(
                            f"gripper toggled rapidly: "
                            f"{a.gripper:.2f}->{b.gripper:.2f}->{c.gripper:.2f}"
                        ),
                    )
                )
        return out

    return check


# ---------------------------------------------------------------------------
# Replay report
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayReport:
    """Result of replaying one failed episode against constraints."""

    episode_id: str
    violations: tuple[ConstraintViolation, ...]
    attribution: tuple[AttributionLink, ...] = field(default_factory=tuple)

    @property
    def passes(self) -> bool:
        return not self.violations

    @property
    def n_errors(self) -> int:
        return sum(1 for v in self.violations if v.severity == "error")

    @property
    def n_fatals(self) -> int:
        return sum(1 for v in self.violations if v.severity == "fatal")


def replay_episode(
    *,
    episode_id: str,
    trajectory: JointTrajectory,
    constraints: Iterable[ConstraintFn],
    upstream_seq: int | None = None,
) -> ReplayReport:
    """Run every constraint and bundle violations + attribution.

    ``upstream_seq`` is the seq of the trace entry that *recorded* the
    episode — when provided, the report carries an attribution link
    back to that step so a viewer can follow the chain.
    """
    violations: list[ConstraintViolation] = []
    for c in constraints:
        violations.extend(c(trajectory))
    attribution: tuple[AttributionLink, ...] = ()
    if upstream_seq is not None:
        attribution = (
            AttributionLink(
                seq=int(upstream_seq),
                role="caused_by",
                notes=f"replay of episode {episode_id}",
            ),
        )
    return ReplayReport(
        episode_id=episode_id,
        violations=tuple(violations),
        attribution=attribution,
    )


def replay_batch(
    episodes: Iterable[tuple[str, JointTrajectory, int | None]],
    constraints: Iterable[ConstraintFn],
) -> tuple[ReplayReport, ...]:
    """Replay many episodes; useful for post-mortem of a whole run."""
    checks = list(constraints)
    return tuple(
        replay_episode(
            episode_id=eid,
            trajectory=traj,
            constraints=checks,
            upstream_seq=seq,
        )
        for (eid, traj, seq) in episodes
    )


__all__ = [
    "ConstraintFn",
    "ConstraintViolation",
    "ReplayReport",
    "gripper_monotonic_checker",
    "joint_limit_checker",
    "replay_batch",
    "replay_episode",
    "velocity_cap_checker",
]
