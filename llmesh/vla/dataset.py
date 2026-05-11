"""Behavior-cloning trajectory dataset format (Phase 10).

A :class:`TrajectoryEpisode` captures everything a downstream BC model
needs to learn from one (observation, instruction, action sequence)
triplet plus an outcome label. Episodes serialise to JSONL — one
record per line — so a training pipeline can stream from disk
without loading the whole dataset.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from llmesh.vla.joint_decoder import JointTrajectory, JointWaypoint


@dataclass(frozen=True)
class TrajectoryEpisode:
    """One episode of a BC dataset.

    ``observation`` is an opaque dict so the format isn't pinned to a
    specific modality — a text caption, a base64-encoded thumbnail
    pointer, or a structured ``ImageObservation.hints`` payload all
    fit. ``outcome`` tags the episode with ``"success" / "collision"
    / "grasp_fail" / "timeout"`` for filtered training.
    """

    episode_id: str
    instruction: str
    observation: dict[str, Any]
    trajectory: JointTrajectory
    outcome: str = "success"
    notes: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


def _trajectory_to_jsonable(traj: JointTrajectory) -> dict[str, Any]:
    return {
        "joint_names": list(traj.joint_names),
        "frame_id": traj.frame_id,
        "metadata": dict(traj.metadata),
        "waypoints": [
            {
                "positions": list(wp.positions),
                "duration_s": wp.duration_s,
                "gripper": wp.gripper,
            }
            for wp in traj.waypoints
        ],
    }


def _trajectory_from_jsonable(obj: dict[str, Any]) -> JointTrajectory:
    wps = tuple(
        JointWaypoint(
            positions=tuple(float(x) for x in (w.get("positions") or ())),
            duration_s=float(w.get("duration_s", 1.0)),
            gripper=float(w.get("gripper", 0.0)),
        )
        for w in obj.get("waypoints") or []
    )
    return JointTrajectory(
        joint_names=tuple(obj.get("joint_names") or ()),
        waypoints=wps,
        frame_id=str(obj.get("frame_id", "base_link")),
        metadata=dict(obj.get("metadata") or {}),
    )


def episode_to_jsonl_line(ep: TrajectoryEpisode) -> str:
    """One-line JSON dump of an episode (no trailing newline)."""
    payload = {
        "episode_id": ep.episode_id,
        "instruction": ep.instruction,
        "observation": dict(ep.observation),
        "trajectory": _trajectory_to_jsonable(ep.trajectory),
        "outcome": ep.outcome,
        "notes": ep.notes,
        "metadata": dict(ep.metadata),
    }
    return json.dumps(payload, ensure_ascii=False)


def episode_from_jsonl_line(line: str) -> TrajectoryEpisode:
    raw = json.loads(line)
    return TrajectoryEpisode(
        episode_id=str(raw.get("episode_id", "")),
        instruction=str(raw.get("instruction", "")),
        observation=dict(raw.get("observation") or {}),
        trajectory=_trajectory_from_jsonable(raw.get("trajectory") or {}),
        outcome=str(raw.get("outcome", "")),
        notes=str(raw.get("notes", "")),
        metadata=dict(raw.get("metadata") or {}),
    )


def save_dataset(episodes: list[TrajectoryEpisode], path: Path) -> int:
    """Append episodes to a JSONL file; create parent dirs as needed."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for ep in episodes:
            f.write(episode_to_jsonl_line(ep) + "\n")
    return len(episodes)


def load_dataset(path: Path) -> list[TrajectoryEpisode]:
    """Read a JSONL file back into ``TrajectoryEpisode`` instances.

    Skips malformed lines so a half-flushed run can still be partly
    consumed — matches the trace logger's robustness contract.
    """
    out: list[TrajectoryEpisode] = []
    if not Path(path).exists():
        return out
    with Path(path).open("r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if not raw:
                continue
            try:
                out.append(episode_from_jsonl_line(raw))
            except (json.JSONDecodeError, TypeError, ValueError):
                continue
    return out


__all__ = [
    "TrajectoryEpisode",
    "episode_from_jsonl_line",
    "episode_to_jsonl_line",
    "load_dataset",
    "save_dataset",
]


# silence unused-import hint
_ = asdict
