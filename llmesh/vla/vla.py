"""VLAAgent ABC + I/O contracts (Phase 9).

Subclasses combine a :class:`VisionEncoder` and an
:class:`ActionDecoder` to turn an instruction + observation into a
typed action stream. The shape is deliberately generic so the same
contract spans turtlesim Twist commands today, Gazebo joint
trajectories at Phase 10, and real-hardware behaviour-cloning in the
roadmap's further reaches.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from llmesh.core.agent import Agent, AgentConfig

_A = TypeVar("_A")  # action type


@dataclass(frozen=True)
class VisionLanguageRequest:
    """Top-level VLA input.

    ``observation`` is intentionally typed as :class:`str` to keep the
    Phase 9 PoC focused on the text-only ``turtlesim`` scenario.
    Phase 10 swaps in raw image bytes via an encoder-specific subclass
    or richer observation dataclass — :class:`VisionEncoder` is the
    polymorphism point.
    """

    instruction: str
    observation: str
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ActionStream(Generic[_A]):
    """Ordered list of robot actions to dispatch.

    ``confidence`` is an optional 0..1 self-estimate from the decoder
    (None when not quantified). ``notes`` is a free-form trace string
    that the llove visualisation pane can render alongside the actions.
    """

    actions: tuple[_A, ...]
    confidence: float | None = None
    notes: str = ""


class VLAAgent(Agent[VisionLanguageRequest, ActionStream]):
    """Vision-Language-Action agent — instruction × observation → actions.

    Concrete classes implement :meth:`run` and typically delegate to a
    :class:`VisionEncoder` (observation → feature dict) and an
    :class:`ActionDecoder` (feature dict + instruction → action stream).
    The split lets researchers swap the perception backend (text /
    image / point-cloud) and the action space (Twist / JointTrajectory
    / discrete macro) independently.
    """

    def __init__(self, config: AgentConfig) -> None:
        super().__init__(config)

    @abstractmethod
    def run(self, request: VisionLanguageRequest) -> ActionStream:
        ...


__all__ = ["ActionStream", "VLAAgent", "VisionLanguageRequest"]
