"""Behavior-cloning trainer (Phase 11).

The Phase 10 dataset (:class:`TrajectoryEpisode`) gives us a stream of
``(instruction, observation, trajectory, outcome)`` records. Phase 11
closes the VLA learning loop with a *minimal* behavior-cloning policy
that can be trained from those records and then asked to imitate the
demonstrated trajectories.

Design constraints:

- **stdlib only** — no numpy / torch. The policy is a 1-nearest-
  neighbour lookup over a small dense feature vector. That keeps the
  module side-effect-free and unit-testable in CI without extra deps.
- **mock-first** — feature extraction is deliberately simple (a 1-of-K
  colour code, side hint, instruction-token presence). Real systems
  would replace :class:`Featurizer` with a learned encoder; the
  trainer contract is what matters for downstream Phase 12+ work.
- **observation-conditional** — the acceptance bar mirrors Phase 9 /
  10: the same instruction with two different scenes must produce two
  different predicted trajectories.

Public surface::

    Featurizer        # instruction + observation -> feature vector
    BCPolicy          # fitted policy (1-NN over features)
    train_bc_policy   # build a policy from a dataset
    evaluate_bc_policy  # MSE + outcome-recall on held-out episodes
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import sqrt
from typing import Any, Iterable

from llmesh.vla.dataset import TrajectoryEpisode
from llmesh.vla.joint_decoder import JointTrajectory, JointWaypoint


_COLOUR_VOCAB: tuple[str, ...] = (
    "red",
    "blue",
    "green",
    "yellow",
    "white",
    "black",
)
_LEFT_WORDS: tuple[str, ...] = ("left", "左")
_RIGHT_WORDS: tuple[str, ...] = ("right", "右")
_PICK_WORDS: tuple[str, ...] = ("pick", "grasp", "take", "つかむ", "拾う")
_PLACE_WORDS: tuple[str, ...] = ("place", "put", "drop", "置く", "戻す")


@dataclass(frozen=True)
class Featurizer:
    """Deterministic ``(instruction, observation) -> feature vector``.

    The vector is a fixed-length tuple of floats so a 1-NN lookup is
    well-defined. Layout (length = ``len(_COLOUR_VOCAB) + 4``):

    - one-hot over :data:`_COLOUR_VOCAB` for ``observation['caption_colour']``
    - ``side_left`` (1.0 if instruction mentions left, else 0.0)
    - ``side_right`` (1.0 if instruction mentions right, else 0.0)
    - ``has_pick`` (1.0 if any pick-verb token in instruction)
    - ``has_place`` (1.0 if any place-verb token in instruction)
    """

    colour_vocab: tuple[str, ...] = _COLOUR_VOCAB

    @property
    def dim(self) -> int:
        return len(self.colour_vocab) + 4

    def transform(
        self, *, instruction: str, observation: dict[str, Any]
    ) -> tuple[float, ...]:
        instr = (instruction or "").lower()
        colour = str((observation or {}).get("caption_colour", "")).lower()
        one_hot = tuple(
            1.0 if c == colour else 0.0 for c in self.colour_vocab
        )
        side_left = 1.0 if any(w in instr for w in _LEFT_WORDS) else 0.0
        side_right = 1.0 if any(w in instr for w in _RIGHT_WORDS) else 0.0
        has_pick = 1.0 if any(w in instr for w in _PICK_WORDS) else 0.0
        has_place = 1.0 if any(w in instr for w in _PLACE_WORDS) else 0.0
        return one_hot + (side_left, side_right, has_pick, has_place)


def _l2(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    return sqrt(sum((x - y) * (x - y) for x, y in zip(a, b)))


@dataclass(frozen=True)
class BCPolicy:
    """1-nearest-neighbour behavior-cloning policy.

    Stores the training features and the trajectories they map to.
    :meth:`predict` returns the trajectory of the closest training
    example by L2 distance over the featurizer's output. Ties are
    broken by insertion order (stable, deterministic).
    """

    featurizer: Featurizer
    features: tuple[tuple[float, ...], ...]
    trajectories: tuple[JointTrajectory, ...]
    source_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if len(self.features) != len(self.trajectories):
            raise ValueError(
                f"features ({len(self.features)}) and trajectories "
                f"({len(self.trajectories)}) must have equal length"
            )
        if len(self.features) != len(self.source_ids):
            raise ValueError(
                f"features ({len(self.features)}) and source_ids "
                f"({len(self.source_ids)}) must have equal length"
            )

    @property
    def n_examples(self) -> int:
        return len(self.features)

    def predict(
        self, *, instruction: str, observation: dict[str, Any]
    ) -> JointTrajectory:
        if not self.features:
            raise ValueError("BCPolicy has no training examples")
        query = self.featurizer.transform(
            instruction=instruction, observation=observation
        )
        best_idx = 0
        best_d = _l2(query, self.features[0])
        for i in range(1, len(self.features)):
            d = _l2(query, self.features[i])
            if d < best_d:
                best_d = d
                best_idx = i
        return self.trajectories[best_idx]

    def nearest_source(
        self, *, instruction: str, observation: dict[str, Any]
    ) -> str:
        """Return the ``episode_id`` whose trajectory was picked.

        Useful for traceability / replay logs.
        """
        if not self.features:
            raise ValueError("BCPolicy has no training examples")
        query = self.featurizer.transform(
            instruction=instruction, observation=observation
        )
        best_idx = 0
        best_d = _l2(query, self.features[0])
        for i in range(1, len(self.features)):
            d = _l2(query, self.features[i])
            if d < best_d:
                best_d = d
                best_idx = i
        return self.source_ids[best_idx]


def train_bc_policy(
    episodes: Iterable[TrajectoryEpisode],
    *,
    featurizer: Featurizer | None = None,
    success_only: bool = True,
) -> BCPolicy:
    """Build a :class:`BCPolicy` from ``episodes``.

    By default only ``outcome == "success"`` episodes are kept so the
    policy doesn't imitate known-bad demonstrations. Pass
    ``success_only=False`` to train on everything (useful for ablation).
    Raises ``ValueError`` if no usable episodes remain.
    """
    fz = featurizer or Featurizer()
    feats: list[tuple[float, ...]] = []
    trajs: list[JointTrajectory] = []
    ids: list[str] = []
    for ep in episodes:
        if success_only and ep.outcome != "success":
            continue
        feats.append(
            fz.transform(
                instruction=ep.instruction, observation=ep.observation
            )
        )
        trajs.append(ep.trajectory)
        ids.append(ep.episode_id)
    if not feats:
        raise ValueError(
            "no training episodes after filtering "
            f"(success_only={success_only})"
        )
    return BCPolicy(
        featurizer=fz,
        features=tuple(feats),
        trajectories=tuple(trajs),
        source_ids=tuple(ids),
    )


def _waypoint_mse(a: JointWaypoint, b: JointWaypoint) -> float:
    """Mean squared error between two waypoints' joint positions.

    Returns ``float('inf')`` when the dimensionalities disagree — a
    structural mismatch is not a small distance.
    """
    if len(a.positions) != len(b.positions):
        return float("inf")
    if not a.positions:
        return 0.0
    return sum(
        (x - y) * (x - y) for x, y in zip(a.positions, b.positions)
    ) / len(a.positions)


def _trajectory_mse(pred: JointTrajectory, ref: JointTrajectory) -> float:
    """Per-waypoint MSE averaged over min(len(pred), len(ref)).

    Length mismatch is penalised by adding the squared length delta
    so a 1-waypoint plan vs a 3-waypoint reference scores worse than
    same-length trajectories with the same joint error.
    """
    n = min(len(pred.waypoints), len(ref.waypoints))
    if n == 0:
        return float("inf")
    waypoint_errors = sum(
        _waypoint_mse(pred.waypoints[i], ref.waypoints[i]) for i in range(n)
    ) / n
    length_penalty = float(
        (len(pred.waypoints) - len(ref.waypoints)) ** 2
    )
    return waypoint_errors + length_penalty


@dataclass(frozen=True)
class BCEvalReport:
    """Held-out evaluation summary."""

    n_episodes: int
    mean_trajectory_mse: float
    perfect_recall_rate: float  # fraction with MSE == 0.0
    per_episode_mse: tuple[float, ...] = field(default_factory=tuple)


def evaluate_bc_policy(
    policy: BCPolicy, val_episodes: Iterable[TrajectoryEpisode]
) -> BCEvalReport:
    """Score ``policy`` on a held-out set.

    Returns zeroes (and an empty MSE tuple) when ``val_episodes`` is
    empty so callers don't have to guard against div-by-zero.
    """
    mses: list[float] = []
    for ep in val_episodes:
        pred = policy.predict(
            instruction=ep.instruction, observation=ep.observation
        )
        mses.append(_trajectory_mse(pred, ep.trajectory))
    if not mses:
        return BCEvalReport(
            n_episodes=0,
            mean_trajectory_mse=0.0,
            perfect_recall_rate=0.0,
            per_episode_mse=(),
        )
    n = len(mses)
    perfect = sum(1 for m in mses if m == 0.0)
    return BCEvalReport(
        n_episodes=n,
        mean_trajectory_mse=sum(mses) / n,
        perfect_recall_rate=perfect / n,
        per_episode_mse=tuple(mses),
    )


__all__ = [
    "BCEvalReport",
    "BCPolicy",
    "Featurizer",
    "evaluate_bc_policy",
    "train_bc_policy",
]
