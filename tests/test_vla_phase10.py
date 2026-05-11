"""Tests for Phase 10 — Gazebo-style arm VLA + replanning + dataset format."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmesh.vla import (
    ActionStream,
    ExecutionFault,
    FailureMode,
    ImageEncoder,
    ImageObservation,
    JointTrajectory,
    JointTrajectoryDecoder,
    JointWaypoint,
    MockImageEncoder,
    MockJointTrajectoryDecoder,
    ReplanController,
    TrajectoryEpisode,
    episode_from_jsonl_line,
    episode_to_jsonl_line,
    load_dataset,
    save_dataset,
    waypoints_to_trajectory,
)


# ---------------------------------------------------------------------------
# ImageEncoder
# ---------------------------------------------------------------------------


def _img_obs(caption: str = "", **hints) -> ImageObservation:
    return ImageObservation(image_bytes=b"\x89PNG\r\n", caption=caption, hints=hints)


class TestImageEncoder:
    def test_abc_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            ImageEncoder()  # type: ignore[abstract]

    def test_mock_features_include_image_size(self) -> None:
        feats = MockImageEncoder().encode(_img_obs(caption="blue cup"))
        assert feats.features["image_bytes_len"] > 0
        assert feats.features["caption_len"] > 0
        assert feats.features["caption_colour"] == "blue"

    def test_hints_populate_scene_state(self) -> None:
        feats = MockImageEncoder().encode(
            _img_obs(
                caption="a red cup",
                self_pose=(0.1, 0.2),
                objects=[{"name": "red_cup", "x": 0.5, "y": 0.3}],
            )
        )
        assert feats.state.has_self
        assert feats.state.objects[0].name == "red_cup"

    def test_missing_hints_safe(self) -> None:
        feats = MockImageEncoder().encode(_img_obs())
        assert feats.features["has_self"] is False
        assert feats.features["n_objects"] == 0

    def test_invalid_object_entries_silently_dropped(self) -> None:
        feats = MockImageEncoder().encode(
            _img_obs(
                objects=[
                    {"name": "ok", "x": 1, "y": 2},
                    {"name": "", "x": 0, "y": 0},  # empty name
                    {"name": "bad", "x": "nope", "y": 0},  # bad x
                    "not-a-dict",  # noqa: F601  - intentionally not a dict
                ],
            )
        )
        assert [o.name for o in feats.state.objects] == ["ok"]


# ---------------------------------------------------------------------------
# JointTrajectoryDecoder
# ---------------------------------------------------------------------------


class TestJointDecoder:
    def test_abc(self) -> None:
        with pytest.raises(TypeError):
            JointTrajectoryDecoder()  # type: ignore[abstract]

    def test_three_waypoint_plan(self) -> None:
        feats = MockImageEncoder().encode(_img_obs(caption="blue cup"))
        out = MockJointTrajectoryDecoder().decode(
            instruction="place the blue cup on the left", features=feats
        )
        assert isinstance(out, ActionStream)
        assert len(out.actions) == 3
        assert all(isinstance(a, JointWaypoint) for a in out.actions)

    def test_place_left_vs_right(self) -> None:
        feats = MockImageEncoder().encode(_img_obs(caption="cup"))
        left = MockJointTrajectoryDecoder().decode(
            instruction="put the cup on the left", features=feats
        )
        right = MockJointTrajectoryDecoder().decode(
            instruction="put the cup on the right", features=feats
        )
        # Last waypoint's first joint differs between left/right placements
        assert left.actions[-1].positions[0] != right.actions[-1].positions[0]

    def test_colour_changes_trajectory(self) -> None:
        red = MockImageEncoder().encode(_img_obs(caption="red cup"))
        blue = MockImageEncoder().encode(_img_obs(caption="blue cup"))
        out_red = MockJointTrajectoryDecoder().decode(
            instruction="grab it", features=red
        )
        out_blue = MockJointTrajectoryDecoder().decode(
            instruction="grab it", features=blue
        )
        # First-joint approach angle differs by colour offset
        assert out_red.actions[0].positions[0] != out_blue.actions[0].positions[0]

    def test_grasp_step_closes_gripper(self) -> None:
        feats = MockImageEncoder().encode(_img_obs(caption="cup"))
        out = MockJointTrajectoryDecoder().decode(
            instruction="pick the cup", features=feats
        )
        # waypoints: approach (gripper=1), grasp (gripper=0), place (gripper=1)
        assert out.actions[0].gripper == 1.0
        assert out.actions[1].gripper == 0.0
        assert out.actions[2].gripper == 1.0


# ---------------------------------------------------------------------------
# waypoints_to_trajectory helper
# ---------------------------------------------------------------------------


class TestTrajectoryHelper:
    def test_packs_waypoints(self) -> None:
        wp = JointWaypoint(positions=(0.0, -0.8, 1.2, 0.0, 0.5, 0.0))
        traj = waypoints_to_trajectory((wp,))
        assert isinstance(traj, JointTrajectory)
        assert len(traj.waypoints) == 1
        assert traj.frame_id == "base_link"

    def test_dimension_mismatch_raises(self) -> None:
        wp = JointWaypoint(positions=(0.0, 1.0))  # only 2 joints
        with pytest.raises(ValueError, match="dim"):
            waypoints_to_trajectory((wp,))  # default joint_names has 6


# ---------------------------------------------------------------------------
# Replanning
# ---------------------------------------------------------------------------


class TestReplan:
    def test_collision_default_zero_retry_then_adapt(self) -> None:
        r = ReplanController()
        # collision at step 0 → no retry budget, step_index too small → abort
        d0 = r.decide(ExecutionFault(step_index=0, mode=FailureMode.COLLISION))
        assert d0.action == "abort"
        # at step 5 → can adapt by rewinding
        d5 = r.decide(ExecutionFault(step_index=5, mode=FailureMode.COLLISION))
        assert d5.action == "adapt"
        assert d5.new_step_index == 3

    def test_grasp_retry_budget(self) -> None:
        r = ReplanController(grasp_retries=2)
        # two retries
        a = r.decide(ExecutionFault(step_index=4, mode=FailureMode.GRASP_FAIL))
        b = r.decide(ExecutionFault(step_index=4, mode=FailureMode.GRASP_FAIL))
        c = r.decide(ExecutionFault(step_index=4, mode=FailureMode.GRASP_FAIL))
        assert a.action == "retry"
        assert b.action == "retry"
        # third hit → out of budget; falls through to adapt
        assert c.action == "adapt"

    def test_timeout_one_retry_then_adapt(self) -> None:
        r = ReplanController(timeout_retries=1)
        first = r.decide(ExecutionFault(step_index=5, mode=FailureMode.TIMEOUT))
        second = r.decide(ExecutionFault(step_index=5, mode=FailureMode.TIMEOUT))
        assert first.action == "retry"
        assert second.action == "adapt"

    def test_unknown_mode_aborts(self) -> None:
        r = ReplanController()
        d = r.decide(ExecutionFault(step_index=2, mode=FailureMode.UNKNOWN))
        assert d.action == "abort"

    def test_none_mode_aborts(self) -> None:
        r = ReplanController()
        d = r.decide(ExecutionFault(step_index=2, mode=FailureMode.NONE))
        assert d.action == "abort"

    def test_budgets_property_snapshot(self) -> None:
        r = ReplanController(collision_retries=1, grasp_retries=2)
        snap = r.budgets
        assert snap[FailureMode.COLLISION] == 1
        assert snap[FailureMode.GRASP_FAIL] == 2


# ---------------------------------------------------------------------------
# Trajectory dataset
# ---------------------------------------------------------------------------


def _episode(eid: str = "ep1", outcome: str = "success") -> TrajectoryEpisode:
    wp = JointWaypoint(positions=(0.1, 0.2, 0.3, 0.4, 0.5, 0.6), gripper=1.0)
    traj = waypoints_to_trajectory((wp,))
    return TrajectoryEpisode(
        episode_id=eid,
        instruction="put the blue cup on the left",
        observation={"image_bytes_len": 4096, "caption": "blue cup"},
        trajectory=traj,
        outcome=outcome,
        notes="mock-recorded",
    )


class TestDataset:
    def test_round_trip_single_line(self) -> None:
        ep = _episode()
        line = episode_to_jsonl_line(ep)
        ep2 = episode_from_jsonl_line(line)
        assert ep2.episode_id == ep.episode_id
        assert ep2.instruction == ep.instruction
        assert ep2.trajectory.joint_names == ep.trajectory.joint_names
        assert ep2.trajectory.waypoints[0].positions == ep.trajectory.waypoints[0].positions

    def test_save_and_load_roundtrip(self, tmp_path: Path) -> None:
        path = tmp_path / "bc.jsonl"
        eps = [_episode("e1"), _episode("e2", outcome="collision")]
        n = save_dataset(eps, path)
        assert n == 2
        loaded = load_dataset(path)
        assert [e.episode_id for e in loaded] == ["e1", "e2"]
        assert loaded[1].outcome == "collision"

    def test_load_nonexistent_returns_empty(self, tmp_path: Path) -> None:
        loaded = load_dataset(tmp_path / "missing.jsonl")
        assert loaded == []

    def test_load_skips_malformed_lines(self, tmp_path: Path) -> None:
        path = tmp_path / "bc.jsonl"
        save_dataset([_episode("e1")], path)
        # Append a garbage line
        path.open("a", encoding="utf-8").write("not-json\n")
        loaded = load_dataset(path)
        assert len(loaded) == 1

    def test_outcome_labels_supported(self, tmp_path: Path) -> None:
        path = tmp_path / "bc.jsonl"
        for label in ("success", "collision", "grasp_fail", "timeout"):
            save_dataset([_episode(label, outcome=label)], path)
        loaded = load_dataset(path)
        assert {e.outcome for e in loaded} == {"success", "collision", "grasp_fail", "timeout"}
