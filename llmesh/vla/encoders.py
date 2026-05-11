"""VisionEncoder ABC + text-scene mock implementation (Phase 9).

A :class:`VisionEncoder` consumes a raw observation (currently a
text string; image bytes / depth / point cloud at later phases) and
produces a :class:`SceneFeatures` dict that the :class:`ActionDecoder`
uses to pick actions.

The Phase 9 mock pipes the observation through :func:`parse_scene_text`
and exposes the parsed objects plus a couple of derived features
(distance to nearest target, vector to it, wall proximity flag).
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from llmesh.vla.scene import SceneObject, SceneState, parse_scene_text


@dataclass(frozen=True)
class SceneFeatures:
    """Decoder-ready feature dict.

    ``state`` keeps the parsed scene so a debug pane can show what
    the encoder saw; ``features`` is the numeric / categorical
    payload the decoder consumes.
    """

    state: SceneState
    features: dict[str, Any] = field(default_factory=dict)


class VisionEncoder(ABC):
    """ABC: raw observation → :class:`SceneFeatures`."""

    @abstractmethod
    def encode(self, observation: str) -> SceneFeatures:
        ...


class MockTextSceneEncoder(VisionEncoder):
    """Text-only encoder used by the Phase 9 PoC.

    Extracts the agent's pose, distance + bearing to the nearest target,
    and whether a wall is within ``wall_proximity_m``.
    """

    def __init__(self, *, wall_proximity_m: float = 1.0) -> None:
        if wall_proximity_m <= 0:
            raise ValueError("wall_proximity_m must be > 0")
        self._wall_thresh = float(wall_proximity_m)

    def encode(self, observation: str) -> SceneFeatures:
        state = parse_scene_text(observation)
        features: dict[str, Any] = {
            "n_objects": len(state.objects),
            "n_walls": len(state.walls),
            "has_self": state.has_self,
        }
        if state.self_object is None:
            return SceneFeatures(state=state, features=features)
        # Distance + bearing to each named object (excluding walls)
        non_walls = [
            obj for obj in state.objects if not obj.name.lower().startswith("wall")
        ]
        if non_walls:
            nearest = min(non_walls, key=lambda o: _distance(state.self_object, o))
            features["nearest_target_name"] = nearest.name
            features["nearest_target_dist"] = _distance(state.self_object, nearest)
            features["nearest_target_bearing"] = _bearing(state.self_object, nearest)
        # Wall proximity flag
        nearest_wall_dist: float | None = None
        if state.walls:
            nearest_wall_dist = min(_distance(state.self_object, w) for w in state.walls)
            features["nearest_wall_dist"] = nearest_wall_dist
            features["wall_close"] = nearest_wall_dist <= self._wall_thresh
        else:
            features["wall_close"] = False
        return SceneFeatures(state=state, features=features)


def _distance(a: SceneObject, b: SceneObject) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _bearing(a: SceneObject, b: SceneObject) -> float:
    """Angle (radians) from ``a`` to ``b`` in the xy-plane."""
    return math.atan2(b.y - a.y, b.x - a.x)


__all__ = ["MockTextSceneEncoder", "SceneFeatures", "VisionEncoder"]
