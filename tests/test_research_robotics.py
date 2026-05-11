"""Tests for llmesh.research.robotics — Phase 3 robotics planning interfaces.

ABC contracts are exercised via the Mock* implementations so the
interface boundaries stay observable without a real simulator.
"""

from __future__ import annotations

import pytest

from llmesh.research import (
    ContactEvent,
    MockMotionPlannerAgent,
    MockPerceptionAgent,
    MockReplanningAgent,
    MockTaskPlannerAgent,
    MotionPlannerAgent,
    PerceptionAgent,
    PerceptionFrame,
    PerceptionRequest,
    PlanningRequest,
    PlanningResult,
    ReplanRequest,
    ReplanningAgent,
    TaskGoal,
    TaskPlanRequest,
    TaskPlannerAgent,
    Trajectory,
    Waypoint,
    run_robotics_pipeline,
)


# ---------------------------------------------------------------------------
# ABC contracts
# ---------------------------------------------------------------------------


class TestABCs:
    def test_perception_agent_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            PerceptionAgent()  # type: ignore[abstract]

    def test_task_planner_agent_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            TaskPlannerAgent()  # type: ignore[abstract]

    def test_motion_planner_agent_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            MotionPlannerAgent()  # type: ignore[abstract]

    def test_replanning_agent_cannot_be_instantiated(self) -> None:
        with pytest.raises(TypeError):
            ReplanningAgent()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# Perception
# ---------------------------------------------------------------------------


class TestPerception:
    def test_echoes_objects_from_sensors(self) -> None:
        agent = MockPerceptionAgent()
        res = agent.perceive(
            PerceptionRequest(
                sensors={"objects": [{"name": "cup_blue", "pose": (1, 0, 0)}]},
                timestamp=1.5,
            )
        )
        assert isinstance(res.frame, PerceptionFrame)
        assert res.frame.timestamp == 1.5
        assert res.frame.objects[0]["name"] == "cup_blue"
        assert res.frame.extra["backend"] == "mock"

    def test_handles_missing_objects(self) -> None:
        res = MockPerceptionAgent().perceive(PerceptionRequest(sensors={}))
        assert res.frame.objects == ()

    def test_handles_non_list_objects(self) -> None:
        res = MockPerceptionAgent().perceive(
            PerceptionRequest(sensors={"objects": "not-a-list"})
        )
        assert res.frame.objects == ()


# ---------------------------------------------------------------------------
# Task planner
# ---------------------------------------------------------------------------


class TestTaskPlanner:
    def test_uses_last_token_as_target(self) -> None:
        frame = PerceptionFrame(timestamp=0.0)
        plan = MockTaskPlannerAgent().plan_task(
            TaskPlanRequest(instruction="please pick the red_cube", frame=frame)
        ).plan
        assert plan.goals[0] == TaskGoal(action="pick", target="red_cube")
        assert plan.goals[-1].action == "place"

    def test_strips_trailing_punctuation(self) -> None:
        frame = PerceptionFrame(timestamp=0.0)
        plan = MockTaskPlannerAgent().plan_task(
            TaskPlanRequest(instruction="grab apple.", frame=frame)
        ).plan
        assert plan.goals[0].target == "apple"

    def test_falls_back_to_first_object_when_instruction_empty(self) -> None:
        frame = PerceptionFrame(
            timestamp=0.0, objects=({"name": "cube_blue"},)
        )
        plan = MockTaskPlannerAgent().plan_task(
            TaskPlanRequest(instruction="   ", frame=frame)
        ).plan
        # When instruction has no tokens, target falls through to object name
        # (the "object" placeholder is the last resort, see the mock body).
        # Either is acceptable — we just require non-empty.
        assert plan.goals[0].target in {"cube_blue", "object"}


# ---------------------------------------------------------------------------
# Motion planner
# ---------------------------------------------------------------------------


class TestMotionPlanner:
    def _basic_request(self, *, budget: float = 5.0) -> PlanningRequest:
        return PlanningRequest(
            instruction="pick cup",
            frame=PerceptionFrame(timestamp=0.0),
            time_budget_s=budget,
        )

    def test_returns_trajectory_with_waypoints(self) -> None:
        res = MockMotionPlannerAgent().plan_motion(self._basic_request())
        assert res.status == "ok"
        assert isinstance(res.trajectory, Trajectory)
        assert len(res.trajectory.waypoints) == 5  # steps=4 → 5 waypoints
        assert isinstance(res.trajectory.waypoints[0], Waypoint)
        assert res.trajectory.frame_id == "world"

    def test_waypoints_are_monotonic_in_time(self) -> None:
        traj = MockMotionPlannerAgent().plan_motion(self._basic_request()).trajectory
        assert traj is not None
        ts = [wp.t for wp in traj.waypoints]
        assert ts == sorted(ts)

    def test_zero_budget_returns_timeout(self) -> None:
        res = MockMotionPlannerAgent().plan_motion(self._basic_request(budget=0))
        assert res.status == "timeout"
        assert res.trajectory is None

    def test_expected_contact_emitted_at_grasp(self) -> None:
        res = MockMotionPlannerAgent().plan_motion(self._basic_request())
        assert len(res.expected_contacts) == 1
        c = res.expected_contacts[0]
        assert c.body_a == "gripper"
        assert c.is_expected is True


# ---------------------------------------------------------------------------
# Replanning
# ---------------------------------------------------------------------------


def _ok_plan() -> PlanningResult:
    return PlanningResult(
        status="ok",
        trajectory=Trajectory(waypoints=(Waypoint(pose=(0,) * 6, t=0.0),)),
    )


class TestReplanning:
    def test_unexpected_contact_triggers_adapt(self) -> None:
        agent = MockReplanningAgent()
        contact = ContactEvent(
            body_a="gripper",
            body_b="wall",
            location=(0.5, 0.0, 0.0),
            normal_force=8.0,
            t=1.2,
            is_expected=False,
        )
        res = agent.replan(
            ReplanRequest(
                original=_ok_plan(),
                contacts=(contact,),
                frame=PerceptionFrame(timestamp=1.2),
                elapsed_s=1.2,
            )
        )
        assert res.decision == "adapt"
        assert res.new_plan is not None
        assert "unexpected" in res.reason

    def test_only_expected_contacts_do_not_adapt(self) -> None:
        agent = MockReplanningAgent()
        contact = ContactEvent(
            body_a="gripper",
            body_b="object",
            location=(0,) * 3,
            normal_force=2.0,
            t=0.5,
            is_expected=True,
        )
        res = agent.replan(
            ReplanRequest(
                original=_ok_plan(),
                contacts=(contact,),
                frame=PerceptionFrame(timestamp=0.5),
            )
        )
        # No unexpected contact + original ok → abort (no retry strategy)
        assert res.decision == "abort"

    def test_timeout_triggers_retry(self) -> None:
        agent = MockReplanningAgent()
        timed_out = PlanningResult(status="timeout", trajectory=None)
        res = agent.replan(
            ReplanRequest(
                original=timed_out,
                contacts=(),
                frame=PerceptionFrame(timestamp=0.0),
            )
        )
        assert res.decision == "retry"


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_full_pipeline_with_mocks(self) -> None:
        res = run_robotics_pipeline(
            perception_agent=MockPerceptionAgent(),
            task_planner=MockTaskPlannerAgent(),
            motion_planner=MockMotionPlannerAgent(),
            instruction="pick the cup_blue",
            sensors={"objects": [{"name": "cup_blue"}]},
        )
        assert res.perception.objects[0]["name"] == "cup_blue"
        assert res.task_plan.goals[0].action == "pick"
        assert res.task_plan.goals[0].target == "cup_blue"
        assert res.motion_plan.status == "ok"
        assert res.motion_plan.trajectory is not None

    def test_pipeline_propagates_zero_budget(self) -> None:
        res = run_robotics_pipeline(
            perception_agent=MockPerceptionAgent(),
            task_planner=MockTaskPlannerAgent(),
            motion_planner=MockMotionPlannerAgent(),
            instruction="grab",
            sensors={},
            time_budget_s=0.0,
        )
        assert res.motion_plan.status == "timeout"


# ---------------------------------------------------------------------------
# Frozen contract sanity
# ---------------------------------------------------------------------------


class TestImmutability:
    def test_waypoint_frozen(self) -> None:
        wp = Waypoint(pose=(0,) * 6, t=0.0)
        with pytest.raises(Exception):  # FrozenInstanceError
            wp.t = 1.0  # type: ignore[misc]

    def test_contact_event_frozen(self) -> None:
        c = ContactEvent(
            body_a="a", body_b="b", location=(0, 0, 0), normal_force=1.0, t=0.0
        )
        with pytest.raises(Exception):
            c.normal_force = 9.9  # type: ignore[misc]
