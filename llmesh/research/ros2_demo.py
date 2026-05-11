"""ROS 2 e2e demo skeleton — research orchestration over ROS 2 (Phase 8).

Wraps the existing :mod:`llmesh.protocol.ros2_adapter` as a
:class:`llmesh.core.Tool` so the research-orchestration agents from
Phase 1–7 can drive a ROS 2 motion node (turtlesim today,
Gazebo / real hardware tomorrow). The e2e closed loop reuses the
Phase 3 robotics ABCs:

    PerceptionAgent → TaskPlannerAgent → ROS2MotionTool → ReviewerAgent

This module is **ros2-optional-extras**: it never imports ``rclpy`` at
top level, so package consumers without ROS 2 installed can still
``import llmesh.research``. The :class:`MockROS2MotionTool` provides
a deterministic stand-in so the e2e demo runs in CI without a live
ROS graph.

The headline function :func:`run_ros2_demo_loop` is mock-first; real
ROS 2 wiring lives in :func:`make_ros2_motion_tool` which raises a
clear :class:`ROS2Unavailable` if rclpy is not installed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from llmesh.core.tool import Tool, ToolSpec
from llmesh.research.robotics import (
    ContactEvent,
    MockMotionPlannerAgent,
    MockPerceptionAgent,
    MockTaskPlannerAgent,
    PerceptionAgent,
    PerceptionFrame,
    PerceptionRequest,
    PlanningRequest,
    PlanningResult,
    TaskPlan,
    TaskPlanRequest,
    TaskPlannerAgent,
    Trajectory,
    Waypoint,
)

if TYPE_CHECKING:  # pragma: no cover — type-only import
    pass  # placeholder for future rclpy.Node import

ROS2_MOTION_TOOL_NAME = "ros2_motion"


class ROS2Unavailable(RuntimeError):
    """Raised when rclpy / ROS 2 cannot be loaded but is required."""


# ---------------------------------------------------------------------------
# I/O dataclasses for the ROS2 motion tool
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ROS2MotionRequest:
    """Drive request: a Trajectory + scheduling hints.

    The trajectory comes from a MotionPlannerAgent. ``topic`` is the
    ROS topic to publish on; default ``/turtle1/cmd_vel`` matches the
    turtlesim convention so a fresh ROS 2 install can be driven without
    extra configuration.
    """

    trajectory: Trajectory
    topic: str = "/turtle1/cmd_vel"
    publish_hz: float = 10.0
    timeout_s: float = 5.0
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ROS2MotionResponse:
    success: bool
    n_published: int
    observed_contacts: tuple[ContactEvent, ...] = ()
    notes: str = ""


# ---------------------------------------------------------------------------
# Tool[I,O] wrappers
# ---------------------------------------------------------------------------


class MockROS2MotionTool(Tool[ROS2MotionRequest, ROS2MotionResponse]):
    """Deterministic stand-in for the real ROS 2 motion tool.

    Pretends to publish each waypoint and emits a synthetic
    ``contact`` event near the trajectory end. Useful for CI where
    rclpy is not installed and for design-time integration with the
    Phase 7 paper exporter.
    """

    def __init__(self) -> None:
        super().__init__(
            ToolSpec(
                name=ROS2_MOTION_TOOL_NAME,
                description=(
                    "Publishes a Trajectory to a ROS 2 topic as a sequence of "
                    "Twist messages. This Mock variant is deterministic and "
                    "does not require rclpy."
                ),
                timeout_sec=10.0,
            )
        )

    def call(self, request: ROS2MotionRequest) -> ROS2MotionResponse:
        wps = list(request.trajectory.waypoints)
        if not wps:
            return ROS2MotionResponse(
                success=False, n_published=0, notes="empty trajectory"
            )
        # Synthesise one expected grasp-like contact at the end of the run.
        final = wps[-1]
        contact = ContactEvent(
            body_a="turtle",
            body_b="goal_zone",
            location=(final.pose[0], final.pose[1], final.pose[2]),
            normal_force=0.0,
            t=final.t,
            is_expected=True,
        )
        return ROS2MotionResponse(
            success=True,
            n_published=len(wps),
            observed_contacts=(contact,),
            notes=f"mock-published {len(wps)} waypoints on {request.topic}",
        )


def make_ros2_motion_tool() -> Tool[ROS2MotionRequest, ROS2MotionResponse]:
    """Return a real (rclpy-backed) ROS 2 motion tool.

    Raises :class:`ROS2Unavailable` if rclpy is not installed so callers
    can fall back to :class:`MockROS2MotionTool` cleanly. Phase 8 only
    ships the failure path because the rclpy publisher logic is
    deferred to a real-hardware milestone — the goal of this phase is
    the *integration shape* (Tool[I,O] wrapper + e2e loop), not the
    publisher details.
    """
    try:
        import rclpy  # type: ignore[import-not-found]  # noqa: F401
    except ImportError as exc:  # pragma: no cover — depends on env
        raise ROS2Unavailable(
            "rclpy not installed; install ros-<distro>-rclpy or use MockROS2MotionTool"
        ) from exc
    # Real implementation deferred — Phase 8 contract is the wrapper
    # shape, not the rclpy publisher loop. Raise for now so callers do
    # not silently get a no-op tool.
    raise ROS2Unavailable(
        "real ROS 2 motion publisher not yet implemented (Phase 9+); "
        "use MockROS2MotionTool for the e2e demo"
    )


# ---------------------------------------------------------------------------
# E2E demo loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ROS2DemoResult:
    """Aggregate of one ROS 2 e2e demo run."""

    perception: PerceptionFrame
    task_plan: TaskPlan
    motion_plan: PlanningResult
    motion_response: ROS2MotionResponse


def run_ros2_demo_loop(
    *,
    instruction: str,
    sensors: dict[str, Any],
    motion_tool: Tool[ROS2MotionRequest, ROS2MotionResponse] | None = None,
    perception_agent: PerceptionAgent[PerceptionRequest, object] | None = None,
    task_planner: TaskPlannerAgent | None = None,
    publish_topic: str = "/turtle1/cmd_vel",
    publish_hz: float = 10.0,
) -> ROS2DemoResult:
    """Run perception → task → motion plan → ROS 2 publish in one call.

    All four agents default to their Phase 3 mocks so the demo runs
    without a live ROS graph. Real deployments replace
    ``motion_tool`` with :func:`make_ros2_motion_tool` and may inject
    real PerceptionAgent / TaskPlannerAgent instances tuned to the
    sensor / domain at hand.
    """
    p_agent: PerceptionAgent[PerceptionRequest, Any] = (
        perception_agent if perception_agent is not None else MockPerceptionAgent()
    )
    tp_agent = task_planner if task_planner is not None else MockTaskPlannerAgent()
    motion_planner = MockMotionPlannerAgent()
    tool = motion_tool if motion_tool is not None else MockROS2MotionTool()

    frame = p_agent.perceive(PerceptionRequest(sensors=sensors)).frame
    task_plan = tp_agent.plan_task(
        TaskPlanRequest(instruction=instruction, frame=frame)
    ).plan
    motion_plan = motion_planner.plan_motion(
        PlanningRequest(
            instruction=instruction, frame=frame, task_plan=task_plan, time_budget_s=5.0
        )
    )
    if motion_plan.trajectory is None:
        response = ROS2MotionResponse(
            success=False, n_published=0, notes="no trajectory to publish"
        )
        return ROS2DemoResult(
            perception=frame,
            task_plan=task_plan,
            motion_plan=motion_plan,
            motion_response=response,
        )
    response = tool.call(
        ROS2MotionRequest(
            trajectory=motion_plan.trajectory,
            topic=publish_topic,
            publish_hz=publish_hz,
        )
    )
    return ROS2DemoResult(
        perception=frame,
        task_plan=task_plan,
        motion_plan=motion_plan,
        motion_response=response,
    )


# Re-export to silence "unused" warnings; both are part of the Phase 8
# public API used by tests via __all__.
_ = Waypoint

__all__ = [
    "ROS2_MOTION_TOOL_NAME",
    "MockROS2MotionTool",
    "ROS2DemoResult",
    "ROS2MotionRequest",
    "ROS2MotionResponse",
    "ROS2Unavailable",
    "make_ros2_motion_tool",
    "run_ros2_demo_loop",
]
