"""Vision-Language-Action (VLA) agents — Phase 9 PoC.

The :class:`VLAAgent` is the top-level contract: given a
:class:`VisionLanguageRequest` (instruction + observation) it produces
an :class:`ActionStream` of robot actions. Concrete agents plug a
:class:`VisionEncoder` (text / image / point-cloud → feature) and an
:class:`ActionDecoder` (feature + instruction → action sequence).

Phase 9 ships a mock-first turtlesim-style PoC:
:class:`MockVLAAgent` is rule-based, takes a text observation
(``turtle at (x,y), target at (x,y), walls at [...]``), and emits
:class:`Twist` actions. The mock is deterministic and demonstrably
**scene-conditional** — the same instruction yields different action
sequences when the observation changes.
"""

from __future__ import annotations

from llmesh.vla.dataset import (
    TrajectoryEpisode,
    episode_from_jsonl_line,
    episode_to_jsonl_line,
    load_dataset,
    save_dataset,
)
from llmesh.vla.decoders import (
    ActionDecoder,
    MockTwistDecoder,
    Twist,
    TwistActionStream,
)
from llmesh.vla.image_encoder import (
    ImageEncoder,
    ImageObservation,
    MockImageEncoder,
)
from llmesh.vla.joint_decoder import (
    JointTrajectory,
    JointTrajectoryDecoder,
    JointWaypoint,
    MockJointTrajectoryDecoder,
    waypoints_to_trajectory,
)
from llmesh.vla.replan import (
    ExecutionFault,
    FailureMode,
    ReplanController,
    ReplanDecision,
)
from llmesh.vla.encoders import (
    MockTextSceneEncoder,
    SceneFeatures,
    VisionEncoder,
)
from llmesh.vla.metrics import EpisodeOutcome, EvaluationReport, evaluate_trials
from llmesh.vla.mock_agent import MockVLAAgent
from llmesh.vla.scene import SceneObject, SceneState, parse_scene_text
from llmesh.vla.vla import (
    ActionStream,
    VisionLanguageRequest,
    VLAAgent,
)

__all__ = [
    "ActionDecoder",
    "ActionStream",
    "EpisodeOutcome",
    "EvaluationReport",
    "ExecutionFault",
    "FailureMode",
    "ImageEncoder",
    "ImageObservation",
    "JointTrajectory",
    "JointTrajectoryDecoder",
    "JointWaypoint",
    "MockImageEncoder",
    "MockJointTrajectoryDecoder",
    "MockTextSceneEncoder",
    "MockTwistDecoder",
    "MockVLAAgent",
    "ReplanController",
    "ReplanDecision",
    "SceneFeatures",
    "SceneObject",
    "SceneState",
    "TrajectoryEpisode",
    "Twist",
    "TwistActionStream",
    "VLAAgent",
    "VisionEncoder",
    "VisionLanguageRequest",
    "episode_from_jsonl_line",
    "episode_to_jsonl_line",
    "evaluate_trials",
    "load_dataset",
    "parse_scene_text",
    "save_dataset",
    "waypoints_to_trajectory",
]
