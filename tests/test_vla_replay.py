"""Tests for Phase 16 D4 — embodied failure replay + attribution."""

from __future__ import annotations

import pytest

from llmesh.vla.joint_decoder import JointTrajectory, JointWaypoint
from llmesh.vla.replay import (
    ConstraintViolation,
    ReplayReport,
    gripper_monotonic_checker,
    joint_limit_checker,
    replay_batch,
    replay_episode,
    velocity_cap_checker,
)


def _traj(*positions_list: tuple[float, ...], gripper_seq: tuple[float, ...] = ()) -> JointTrajectory:
    wps = tuple(
        JointWaypoint(
            positions=positions,
            duration_s=1.0,
            gripper=gripper_seq[i] if i < len(gripper_seq) else 0.0,
        )
        for i, positions in enumerate(positions_list)
    )
    return JointTrajectory(
        joint_names=("j1", "j2", "j3", "j4", "j5", "j6"),
        waypoints=wps,
    )


# ---------------------------------------------------------------------------
# joint_limit_checker
# ---------------------------------------------------------------------------


class TestJointLimitChecker:
    def test_within_limits_no_violations(self) -> None:
        traj = _traj((0.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        out = joint_limit_checker()(traj)
        assert out == []

    def test_above_upper_violates(self) -> None:
        traj = _traj((5.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        out = joint_limit_checker()(traj)
        assert len(out) == 1
        assert out[0].constraint_name == "joint_limit"
        assert out[0].waypoint_index == 0
        assert "outside" in out[0].detail

    def test_below_lower_violates(self) -> None:
        traj = _traj((-5.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        out = joint_limit_checker()(traj)
        assert len(out) == 1

    def test_custom_limits(self) -> None:
        traj = _traj((1.5, 0.0, 0.0, 0.0, 0.0, 0.0))
        out = joint_limit_checker(lower=(-1.0,) * 6, upper=(1.0,) * 6)(traj)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# velocity_cap_checker
# ---------------------------------------------------------------------------


class TestVelocityCapChecker:
    def test_no_violations_for_slow_motion(self) -> None:
        traj = _traj(
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            (0.5, 0.0, 0.0, 0.0, 0.0, 0.0),  # delta 0.5 / 1.0s = 0.5/s
        )
        out = velocity_cap_checker(max_delta_per_second=2.0)(traj)
        assert out == []

    def test_violation_for_fast_motion(self) -> None:
        traj = _traj(
            (0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
            (3.0, 0.0, 0.0, 0.0, 0.0, 0.0),  # rate 3.0/s > cap
        )
        out = velocity_cap_checker(max_delta_per_second=2.0)(traj)
        assert len(out) == 1
        assert out[0].severity == "warning"

    def test_single_waypoint_no_check(self) -> None:
        traj = _traj((0.0,) * 6)
        out = velocity_cap_checker()(traj)
        assert out == []


# ---------------------------------------------------------------------------
# gripper_monotonic_checker
# ---------------------------------------------------------------------------


class TestGripperMonotonicChecker:
    def test_monotonic_gripper_no_violation(self) -> None:
        traj = _traj(
            (0.0,) * 6, (0.0,) * 6, (0.0,) * 6,
            gripper_seq=(1.0, 0.5, 0.0),  # smoothly closing
        )
        out = gripper_monotonic_checker()(traj)
        assert out == []

    def test_rapid_toggle_flagged(self) -> None:
        traj = _traj(
            (0.0,) * 6, (0.0,) * 6, (0.0,) * 6,
            gripper_seq=(1.0, 0.0, 1.0),  # open -> closed -> open
        )
        out = gripper_monotonic_checker()(traj)
        assert len(out) == 1
        assert out[0].waypoint_index == 2

    def test_under_3_waypoints_no_check(self) -> None:
        traj = _traj((0.0,) * 6, (0.0,) * 6, gripper_seq=(1.0, 0.0))
        assert gripper_monotonic_checker()(traj) == []


# ---------------------------------------------------------------------------
# replay_episode / replay_batch
# ---------------------------------------------------------------------------


class TestReplayEpisode:
    def test_passes_when_no_violations(self) -> None:
        traj = _traj((0.0,) * 6)
        report = replay_episode(
            episode_id="ep1",
            trajectory=traj,
            constraints=[joint_limit_checker()],
        )
        assert isinstance(report, ReplayReport)
        assert report.passes
        assert report.n_errors == 0

    def test_reports_violations(self) -> None:
        traj = _traj((5.0, 0.0, 0.0, 0.0, 0.0, 0.0))
        report = replay_episode(
            episode_id="ep1",
            trajectory=traj,
            constraints=[joint_limit_checker()],
        )
        assert not report.passes
        assert report.n_errors == 1
        assert report.violations[0].constraint_name == "joint_limit"

    def test_attribution_link_when_upstream_seq_given(self) -> None:
        traj = _traj((0.0,) * 6)
        report = replay_episode(
            episode_id="ep1",
            trajectory=traj,
            constraints=[joint_limit_checker()],
            upstream_seq=42,
        )
        assert len(report.attribution) == 1
        assert report.attribution[0].seq == 42
        assert report.attribution[0].role == "caused_by"

    def test_no_attribution_when_upstream_seq_missing(self) -> None:
        traj = _traj((0.0,) * 6)
        report = replay_episode(
            episode_id="ep1",
            trajectory=traj,
            constraints=[joint_limit_checker()],
        )
        assert report.attribution == ()

    def test_multiple_constraints_aggregate(self) -> None:
        traj = _traj(
            (5.0,) + (0.0,) * 5,  # joint_limit violation
            (10.0,) + (0.0,) * 5,  # velocity_cap violation
        )
        report = replay_episode(
            episode_id="ep1",
            trajectory=traj,
            constraints=[joint_limit_checker(), velocity_cap_checker()],
        )
        names = {v.constraint_name for v in report.violations}
        assert names == {"joint_limit", "velocity_cap"}


class TestReplayBatch:
    def test_returns_one_report_per_episode(self) -> None:
        traj = _traj((0.0,) * 6)
        reports = replay_batch(
            [("e1", traj, None), ("e2", traj, 7)],
            [joint_limit_checker()],
        )
        assert len(reports) == 2
        assert reports[0].episode_id == "e1"
        assert reports[1].episode_id == "e2"
        assert reports[1].attribution[0].seq == 7
