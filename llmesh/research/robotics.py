"""Robotics planning interfaces — perception / task / motion / replanning (Phase 3).

Phase 3 ships **interfaces only**: ABCs for the four robotics planning
roles plus the dataclass contracts they share. Concrete adapters
(ROS 2, Gazebo, MuJoCo, real hardware) live in later phases (Phase 8+);
this module provides Mock* implementations so the rest of the
research-orchestration stack can be wired up against a deterministic
robotics layer.

The schema is dataclass-based to honour the llmesh "no pydantic
dependency" policy (see :mod:`llmesh.core.agent`). The shapes are still
JSON-Schema-emittable because every field is a primitive, tuple, or
nested dataclass.

The :class:`ContactEvent` dataclass is the Saguri-bot-inspired
representation of a discrete contact: bodies touching the environment
emit one event per contact, with normal force, location and a
timestamp so a replanner can decide whether to retract, push through,
or abort.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generic, Literal, TypeVar

# ---------------------------------------------------------------------------
# shared dataclasses
# ---------------------------------------------------------------------------


Pose6D = tuple[float, float, float, float, float, float]  # x, y, z, roll, pitch, yaw


@dataclass(frozen=True)
class Waypoint:
    """One point on a planned trajectory.

    ``t`` is seconds from trajectory start; absolute wall-clock
    scheduling is the executor's concern. ``gripper`` is a normalised
    [0,1] aperture (0=closed, 1=open) — ignored by non-gripper robots.
    """

    pose: Pose6D
    t: float
    gripper: float = 0.0


@dataclass(frozen=True)
class Trajectory:
    waypoints: tuple[Waypoint, ...]
    frame_id: str = "world"  # reference frame name (e.g. "world", "base_link")


@dataclass(frozen=True)
class ContactEvent:
    """One contact event observed during execution (Saguri-bot inspired).

    Attributes:
        body_a / body_b: Pair of bodies in contact (``"gripper"``,
            ``"cup"``, ``"table"``, ...).
        location: Contact point in :attr:`Trajectory.frame_id` coords.
        normal_force: Newtons along the contact normal.
        t: Seconds from trajectory start.
        is_expected: ``True`` for contacts the plan anticipated
            (e.g. grasp closure); ``False`` for surprises that warrant
            replanning.
    """

    body_a: str
    body_b: str
    location: tuple[float, float, float]
    normal_force: float
    t: float
    is_expected: bool = False


# ---------------------------------------------------------------------------
# perception
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PerceptionFrame:
    """One snapshot of the world state used by downstream planners."""

    timestamp: float
    objects: tuple[dict, ...] = ()  # detected objects: {name, pose, confidence}
    self_pose: Pose6D = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class PerceptionRequest:
    """Raw sensor bundle to be interpreted into a :class:`PerceptionFrame`.

    ``sensors`` is left free-form because adapters vary widely (RGB,
    depth, point cloud, joint state, tactile). The PerceptionAgent is
    expected to consume what it needs and ignore the rest.
    """

    sensors: dict = field(default_factory=dict)
    timestamp: float = 0.0


@dataclass(frozen=True)
class PerceptionResponse:
    frame: PerceptionFrame


I = TypeVar("I")
O = TypeVar("O")


class PerceptionAgent(ABC, Generic[I, O]):
    """ABC for raw-sensors → :class:`PerceptionFrame` interpreters.

    Generic over ``I``/``O`` so subclasses can refine the request and
    response types if they need richer payloads than the default
    :class:`PerceptionRequest` / :class:`PerceptionResponse`.
    """

    @abstractmethod
    def perceive(self, request: I) -> O:
        ...


# ---------------------------------------------------------------------------
# task planning
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TaskGoal:
    """One high-level goal (e.g. ``"pick(cup_blue)"``)."""

    action: str
    target: str = ""
    args: dict = field(default_factory=dict)


@dataclass(frozen=True)
class TaskPlan:
    goals: tuple[TaskGoal, ...]


@dataclass(frozen=True)
class TaskPlanRequest:
    instruction: str
    frame: PerceptionFrame


@dataclass(frozen=True)
class TaskPlanResponse:
    plan: TaskPlan


class TaskPlannerAgent(ABC):
    """ABC: natural-language instruction + perception → ordered TaskGoals."""

    @abstractmethod
    def plan_task(self, request: TaskPlanRequest) -> TaskPlanResponse:
        ...


# ---------------------------------------------------------------------------
# motion planning (the headline Phase 3 contract)
# ---------------------------------------------------------------------------


PlanStatus = Literal["ok", "infeasible", "timeout", "blocked"]


@dataclass(frozen=True)
class PlanningRequest:
    """Top-level request that drives a single robotics planning episode."""

    instruction: str
    frame: PerceptionFrame
    task_plan: TaskPlan | None = None  # optional: skip the task planner
    time_budget_s: float = 5.0
    safety_margin_m: float = 0.05


@dataclass(frozen=True)
class PlanningResult:
    """Top-level response from the motion planner."""

    status: PlanStatus
    trajectory: Trajectory | None
    expected_contacts: tuple[ContactEvent, ...] = ()
    notes: str = ""


class MotionPlannerAgent(ABC):
    """ABC: TaskPlan + PerceptionFrame → executable :class:`Trajectory`."""

    @abstractmethod
    def plan_motion(self, request: PlanningRequest) -> PlanningResult:
        ...


# ---------------------------------------------------------------------------
# replanning
# ---------------------------------------------------------------------------


ReplanDecision = Literal["retry", "adapt", "abort"]


@dataclass(frozen=True)
class ReplanRequest:
    """Replan input: original plan + observed contact events + current frame."""

    original: PlanningResult
    contacts: tuple[ContactEvent, ...]
    frame: PerceptionFrame
    elapsed_s: float = 0.0


@dataclass(frozen=True)
class ReplanResponse:
    decision: ReplanDecision
    new_plan: PlanningResult | None = None
    reason: str = ""


class ReplanningAgent(ABC):
    """ABC: react to surprises during execution.

    A :data:`ReplanDecision` of ``"retry"`` rewinds to the previous
    waypoint; ``"adapt"`` issues a fresh trajectory via ``new_plan``;
    ``"abort"`` surrenders to the operator with a ``reason``.
    """

    @abstractmethod
    def replan(self, request: ReplanRequest) -> ReplanResponse:
        ...


# ---------------------------------------------------------------------------
# Mock implementations — deterministic, dependency-free
# ---------------------------------------------------------------------------


class MockPerceptionAgent(PerceptionAgent[PerceptionRequest, PerceptionResponse]):
    """Returns a PerceptionFrame echoing any ``objects`` in the request sensors."""

    def perceive(self, request: PerceptionRequest) -> PerceptionResponse:
        sensors = request.sensors or {}
        objects = sensors.get("objects") if isinstance(sensors, dict) else None
        if not isinstance(objects, (list, tuple)):
            objects = ()
        return PerceptionResponse(
            frame=PerceptionFrame(
                timestamp=request.timestamp,
                objects=tuple(objects),
                self_pose=(0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
                extra={"backend": "mock"},
            )
        )


class MockTaskPlannerAgent(TaskPlannerAgent):
    """Two-line "pick(target) -> place(home)" plan, target inferred from instruction."""

    def plan_task(self, request: TaskPlanRequest) -> TaskPlanResponse:
        # Pull the last token of the instruction as the implicit target,
        # falling back to the first detected object's name.
        tokens = [t for t in request.instruction.strip().split() if t]
        target = ""
        if tokens:
            target = tokens[-1].strip(".,;:!?")
        if not target and request.frame.objects:
            obj0 = request.frame.objects[0]
            if isinstance(obj0, dict):
                target = str(obj0.get("name", ""))
        goals: tuple[TaskGoal, ...] = (
            TaskGoal(action="pick", target=target or "object"),
            TaskGoal(action="place", target="home"),
        )
        return TaskPlanResponse(plan=TaskPlan(goals=goals))


class MockMotionPlannerAgent(MotionPlannerAgent):
    """Linear trajectory from current pose to a synthetic target pose."""

    def plan_motion(self, request: PlanningRequest) -> PlanningResult:
        if request.time_budget_s <= 0:
            return PlanningResult(status="timeout", trajectory=None, notes="zero budget")
        # 4-step linear trajectory: identity ramp along +x by 0.1 m per step.
        start = request.frame.self_pose
        steps = 4
        wps: list[Waypoint] = []
        for i in range(steps + 1):
            frac = i / steps
            pose = (
                start[0] + 0.1 * i,
                start[1],
                start[2] + 0.05 * frac,
                start[3],
                start[4],
                start[5],
            )
            wps.append(Waypoint(pose=pose, t=0.5 * i, gripper=1.0 if i < steps else 0.0))
        # Expected grasp contact at the final waypoint.
        contacts = (
            ContactEvent(
                body_a="gripper",
                body_b="object",
                location=(start[0] + 0.4, start[1], start[2] + 0.05),
                normal_force=2.0,
                t=2.0,
                is_expected=True,
            ),
        )
        return PlanningResult(
            status="ok",
            trajectory=Trajectory(waypoints=tuple(wps), frame_id="world"),
            expected_contacts=contacts,
            notes="mock linear ramp",
        )


class MockReplanningAgent(ReplanningAgent):
    """Adapt on unexpected contact, retry on a single timeout, abort otherwise."""

    def replan(self, request: ReplanRequest) -> ReplanResponse:
        unexpected = [c for c in request.contacts if not c.is_expected]
        if unexpected:
            return ReplanResponse(
                decision="adapt",
                new_plan=PlanningResult(
                    status="ok",
                    trajectory=None,
                    notes="adapted: contact surprise",
                ),
                reason=f"{len(unexpected)} unexpected contact(s)",
            )
        if request.original.status == "timeout":
            return ReplanResponse(decision="retry", reason="prior timeout")
        return ReplanResponse(decision="abort", reason="no replan strategy")


# ---------------------------------------------------------------------------
# convenience: full pipeline against the mocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RoboticsPipelineResult:
    """Aggregate output of :func:`run_robotics_pipeline` for tests / demos."""

    perception: PerceptionFrame
    task_plan: TaskPlan
    motion_plan: PlanningResult


def run_robotics_pipeline(
    *,
    perception_agent: PerceptionAgent[PerceptionRequest, PerceptionResponse],
    task_planner: TaskPlannerAgent,
    motion_planner: MotionPlannerAgent,
    instruction: str,
    sensors: dict,
    time_budget_s: float = 5.0,
) -> RoboticsPipelineResult:
    """Wire perception → task plan → motion plan in one call.

    Skeleton-level convenience: the real executor will weave in
    :class:`ReplanningAgent` between motion plan and execution feedback
    in a later phase.
    """
    p = perception_agent.perceive(PerceptionRequest(sensors=sensors)).frame
    tp = task_planner.plan_task(TaskPlanRequest(instruction=instruction, frame=p)).plan
    mp = motion_planner.plan_motion(
        PlanningRequest(
            instruction=instruction,
            frame=p,
            task_plan=tp,
            time_budget_s=time_budget_s,
        )
    )
    return RoboticsPipelineResult(perception=p, task_plan=tp, motion_plan=mp)


__all__ = [
    "ContactEvent",
    "MockMotionPlannerAgent",
    "MockPerceptionAgent",
    "MockReplanningAgent",
    "MockTaskPlannerAgent",
    "MotionPlannerAgent",
    "PerceptionAgent",
    "PerceptionFrame",
    "PerceptionRequest",
    "PerceptionResponse",
    "PlanStatus",
    "PlanningRequest",
    "PlanningResult",
    "Pose6D",
    "ReplanDecision",
    "ReplanRequest",
    "ReplanResponse",
    "ReplanningAgent",
    "RoboticsPipelineResult",
    "TaskGoal",
    "TaskPlan",
    "TaskPlanRequest",
    "TaskPlanResponse",
    "TaskPlannerAgent",
    "Trajectory",
    "Waypoint",
    "run_robotics_pipeline",
]
