"""JointTrajectory decoder for arm scenarios (Phase 10).

Phase 9's :class:`MockTwistDecoder` emits a ``Twist`` stream suited
to a 2-D mobile base. Phase 10 introduces :class:`JointTrajectory` —
a list of joint-space waypoints for a serial-link arm (UR3 / Franka
Panda mock) — plus a deterministic decoder that produces a 3-step
pick&place trajectory (approach → grasp → place).

The decoder reads the caption colour hint produced by
:class:`MockImageEncoder` so the same approach pose differs when the
caption says ``blue cup`` vs ``red cup``. That's the Phase 10
acceptance: image observation conditions the joint sequence.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

from llmesh.vla.encoders import SceneFeatures
from llmesh.vla.vla import ActionStream


@dataclass(frozen=True)
class JointWaypoint:
    """One joint-space waypoint plus optional gripper state."""

    positions: tuple[float, ...]
    duration_s: float = 1.0
    gripper: float = 0.0  # 0=closed, 1=open


@dataclass(frozen=True)
class JointTrajectory:
    """Named joints + ordered waypoints (joint-space)."""

    joint_names: tuple[str, ...]
    waypoints: tuple[JointWaypoint, ...]
    frame_id: str = "base_link"
    metadata: dict = field(default_factory=dict)


# 6-DOF default joint set — matches UR3 ordering for convenience.
_DEFAULT_JOINTS: tuple[str, ...] = (
    "shoulder_pan",
    "shoulder_lift",
    "elbow",
    "wrist_1",
    "wrist_2",
    "wrist_3",
)


class JointTrajectoryDecoder(ABC):
    """ABC: ``(instruction, features) → JointTrajectory wrapped in ActionStream``."""

    @abstractmethod
    def decode(
        self, *, instruction: str, features: SceneFeatures
    ) -> ActionStream[JointWaypoint]:
        ...


class MockJointTrajectoryDecoder(JointTrajectoryDecoder):
    """Deterministic 3-waypoint pick&place plan.

    The plan is intentionally tiny but observation-conditional: the
    approach pose's first joint angle is derived from the target's
    in-image side hint (left / right / centre). The grasp waypoint
    closes the gripper; the place waypoint moves the wrist over to
    the destination indicated in the instruction (default: ``left``).
    """

    _LEFT_WORDS = ("left", "左")
    _RIGHT_WORDS = ("right", "右")

    def decode(
        self, *, instruction: str, features: SceneFeatures
    ) -> ActionStream[JointWaypoint]:
        instr = (instruction or "").lower()
        place_left = any(w in instr for w in self._LEFT_WORDS) or not any(
            w in instr for w in self._RIGHT_WORDS
        )
        # Colour cue from the encoder shifts the approach angle so two
        # different captions yield two different trajectories.
        colour = features.features.get("caption_colour", "")
        colour_offset = {
            "red": -0.20,
            "blue": 0.20,
            "green": 0.40,
            "yellow": -0.40,
            "white": 0.0,
            "black": 0.10,
        }.get(str(colour), 0.0)

        approach = JointWaypoint(
            positions=(0.0 + colour_offset, -0.8, 1.2, 0.0, 0.5, 0.0),
            duration_s=2.0,
            gripper=1.0,
        )
        grasp = JointWaypoint(
            positions=(0.0 + colour_offset, -1.0, 1.4, 0.0, 0.5, 0.0),
            duration_s=1.0,
            gripper=0.0,
        )
        place = JointWaypoint(
            positions=(
                (-1.2 if place_left else 1.2) + colour_offset,
                -0.8,
                1.2,
                0.0,
                0.5,
                0.0,
            ),
            duration_s=2.5,
            gripper=1.0,  # open at place
        )
        actions = (approach, grasp, place)
        return ActionStream(
            actions=actions,
            confidence=0.85,
            notes=(
                f"colour={colour or '-'} side={'left' if place_left else 'right'} "
                f"n_waypoints={len(actions)}"
            ),
        )


def waypoints_to_trajectory(
    waypoints: tuple[JointWaypoint, ...],
    *,
    joint_names: tuple[str, ...] = _DEFAULT_JOINTS,
    frame_id: str = "base_link",
    metadata: dict | None = None,
) -> JointTrajectory:
    """Glue helper: pack an :class:`ActionStream`'s waypoints into a JointTrajectory."""
    if waypoints and len(waypoints[0].positions) != len(joint_names):
        raise ValueError(
            f"waypoint dim ({len(waypoints[0].positions)}) "
            f"!= joint_names dim ({len(joint_names)})"
        )
    return JointTrajectory(
        joint_names=joint_names,
        waypoints=waypoints,
        frame_id=frame_id,
        metadata=dict(metadata or {}),
    )


__all__ = [
    "JointTrajectory",
    "JointTrajectoryDecoder",
    "JointWaypoint",
    "MockJointTrajectoryDecoder",
    "waypoints_to_trajectory",
]
