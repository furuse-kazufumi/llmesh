"""Image-aware VisionEncoder for Gazebo arm scenarios (Phase 10).

Extends the Phase 9 text-only :class:`VisionEncoder` to consume an RGB
image plus an optional caption (e.g. produced by LLaVA / Anthropic
Vision). The mock implementation does not actually parse pixels —
that would require Pillow / a real vision model — but it gives the
rest of the Phase 10 pipeline a runnable encoder by deriving features
from the image *size* and a structured ``hints`` dict the caller
passes alongside the bytes.

Real backends are wired by subclassing :class:`ImageEncoder` and
overriding :meth:`encode`. The Phase 10 acceptance is the API
shape; production captioning is a Phase 11+ concern.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from llmesh.vla.encoders import SceneFeatures
from llmesh.vla.scene import SceneObject, SceneState


@dataclass(frozen=True)
class ImageObservation:
    """Image + optional caption + structured hints."""

    image_bytes: bytes
    caption: str = ""
    hints: dict[str, Any] = field(default_factory=dict)


class ImageEncoder(ABC):
    """ABC: ``ImageObservation → SceneFeatures``."""

    @abstractmethod
    def encode(self, observation: ImageObservation) -> SceneFeatures:
        ...


class MockImageEncoder(ImageEncoder):
    """Deterministic mock: derive features from caption + hints.

    The caller is expected to pre-pack the scene into ``hints``
    (``self_pose``, ``objects`` list) — this is the same shape a real
    caption parser would produce in a later phase. Image bytes are not
    pixel-parsed; only their size is surfaced as a feature so a
    pipeline test can detect a payload change.
    """

    def encode(self, observation: ImageObservation) -> SceneFeatures:
        hints = observation.hints or {}
        self_pose = hints.get("self_pose")
        self_obj: SceneObject | None = None
        if isinstance(self_pose, (list, tuple)) and len(self_pose) >= 2:
            try:
                self_obj = SceneObject(name="arm", x=float(self_pose[0]), y=float(self_pose[1]))
            except (TypeError, ValueError):
                self_obj = None
        objects_raw = hints.get("objects") or []
        objects: list[SceneObject] = []
        if isinstance(objects_raw, (list, tuple)):
            for entry in objects_raw:
                if not isinstance(entry, dict):
                    continue
                name = str(entry.get("name", ""))
                try:
                    x = float(entry.get("x", 0))
                    y = float(entry.get("y", 0))
                except (TypeError, ValueError):
                    continue
                if name:
                    objects.append(SceneObject(name=name, x=x, y=y))
        state = SceneState(
            self_object=self_obj,
            objects=tuple(objects),
            walls=(),  # arm scene tracks obstacles via hints, not wall regex
            raw=observation.caption or "",
        )
        features: dict[str, Any] = {
            "image_bytes_len": len(observation.image_bytes),
            "caption_len": len(observation.caption or ""),
            "n_objects": len(objects),
            "has_self": self_obj is not None,
        }
        # surface a "target_colour" hint if the caption mentions a common one
        for colour in ("red", "blue", "green", "yellow", "white", "black"):
            if colour in (observation.caption or "").lower():
                features["caption_colour"] = colour
                break
        return SceneFeatures(state=state, features=features)


__all__ = ["ImageEncoder", "ImageObservation", "MockImageEncoder"]
