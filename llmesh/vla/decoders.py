"""ActionDecoder ABC + Twist action stream (Phase 9).

The decoder turns the encoder's :class:`SceneFeatures` + an
instruction into a concrete :class:`ActionStream` of robot actions.
Phase 9's turtlesim case uses :class:`Twist` (linear_x + angular_z);
later phases will add :class:`JointTrajectory` and discrete macros.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from llmesh.vla.encoders import SceneFeatures
from llmesh.vla.vla import ActionStream


@dataclass(frozen=True)
class Twist:
    """ROS-style velocity command (subset used by turtlesim).

    ``linear_x`` is forward velocity in m/s; ``angular_z`` is yaw rate
    in rad/s. The actual ROS message has six DOF but turtlesim cares
    only about these two.
    """

    linear_x: float
    angular_z: float
    duration_s: float = 0.5


TwistActionStream = ActionStream  # alias used by tests for readability


class ActionDecoder(ABC):
    """ABC: ``(instruction, features) → ActionStream``."""

    @abstractmethod
    def decode(self, *, instruction: str, features: SceneFeatures) -> ActionStream:
        ...


class MockTwistDecoder(ActionDecoder):
    """Rule-based Twist decoder for the turtlesim PoC.

    The rules:

    - Recognise a target by matching common colour keywords in the
      instruction (``red`` / ``blue`` / ``green`` / ``flag`` etc.).
      Otherwise default to the encoder's nearest target.
    - If wall_close is True and the instruction mentions ``avoid`` /
      ``壁`` / ``wall``, emit a turn-in-place action.
    - Otherwise emit a 3-step plan: align (yaw) → advance (forward) →
      stop. The advance step's duration scales with distance.

    Determinism is the whole point: the same (instruction, features)
    pair always yields the same ActionStream, which lets the evaluator
    detect *scene-conditional* behaviour change.
    """

    _COLOUR_WORDS = ("red", "blue", "green", "yellow", "white", "black", "orange")
    _AVOID_WORDS = ("avoid", "壁", "wall", "dodge", "stay away")

    def decode(self, *, instruction: str, features: SceneFeatures) -> ActionStream:
        instr = (instruction or "").lower()
        feats = features.features

        # No self_object → can't act.
        if not feats.get("has_self"):
            return ActionStream(
                actions=(Twist(linear_x=0.0, angular_z=0.0),),
                confidence=0.0,
                notes="no self observed; idling",
            )

        # Wall-avoid mode
        wants_avoid = any(w in instr for w in self._AVOID_WORDS)
        if wants_avoid and feats.get("wall_close"):
            return ActionStream(
                actions=(
                    Twist(linear_x=0.0, angular_z=math.pi / 2, duration_s=0.5),
                    Twist(linear_x=0.0, angular_z=0.0, duration_s=0.2),
                ),
                confidence=0.6,
                notes="wall close & avoid intent — turning in place",
            )

        # Pick a target name from the instruction colour cues; fall
        # back to the encoder's nearest target.
        chosen_name = ""
        for colour in self._COLOUR_WORDS:
            if colour in instr:
                hit = features.state.find(colour)
                if hit is not None:
                    chosen_name = hit.name
                    break
        if not chosen_name:
            chosen_name = feats.get("nearest_target_name", "")

        # Use the encoder's distance/bearing when targeting nearest;
        # recompute for explicit colour targets.
        from llmesh.vla.encoders import _bearing as bearing_fn  # local import keeps cycle small
        from llmesh.vla.encoders import _distance as distance_fn

        target_obj = features.state.find(chosen_name) if chosen_name else None
        self_obj = features.state.self_object
        if target_obj is None or self_obj is None:
            return ActionStream(
                actions=(Twist(linear_x=0.0, angular_z=0.0),),
                confidence=0.1,
                notes=f"no target matched ({chosen_name or 'any'}); idling",
            )

        dist = distance_fn(self_obj, target_obj)
        yaw = bearing_fn(self_obj, target_obj)
        advance_duration = max(0.1, min(5.0, dist / 0.5))  # 0.5 m/s nominal
        actions: tuple[Twist, ...] = (
            Twist(linear_x=0.0, angular_z=yaw, duration_s=0.5),
            Twist(linear_x=0.5, angular_z=0.0, duration_s=advance_duration),
            Twist(linear_x=0.0, angular_z=0.0, duration_s=0.2),
        )
        return ActionStream(
            actions=actions,
            confidence=0.9,
            notes=f"target={target_obj.name} dist={dist:.2f}m bearing={yaw:.2f}rad",
        )


__all__ = ["ActionDecoder", "MockTwistDecoder", "Twist", "TwistActionStream"]
