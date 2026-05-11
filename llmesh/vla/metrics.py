"""Quantitative VLA evaluation metrics (Phase 9).

The Phase 9 acceptance asks for "scene-conditional behaviour change
that we can measure". This module ships the three headline metrics:

- ``success_rate``        — fraction of episodes where the agent
                             reached its target within step / time cap
- ``intervention_rate``   — fraction of episodes where a human had to
                             step in (recorded externally; we just
                             aggregate)
- ``mean_steps``          — average number of actions per episode
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EpisodeOutcome:
    """Single-episode result.

    Attributes:
        succeeded: True iff the agent completed the instruction.
        intervened: True iff a human / safety layer overrode the agent.
        n_steps: How many actions were dispatched.
        notes: Free-form trace for the human reviewer.
    """

    succeeded: bool
    intervened: bool
    n_steps: int
    notes: str = ""


@dataclass(frozen=True)
class EvaluationReport:
    """Aggregate across a batch of :class:`EpisodeOutcome`."""

    n_episodes: int
    success_rate: float
    intervention_rate: float
    mean_steps: float
    per_episode: tuple[EpisodeOutcome, ...]


def evaluate_trials(outcomes: list[EpisodeOutcome]) -> EvaluationReport:
    """Compute success / intervention / mean-steps over ``outcomes``.

    Returns a report with zeroes (and an empty per-episode tuple) if
    ``outcomes`` is empty so callers don't have to guard against
    division by zero.
    """
    n = len(outcomes)
    if n == 0:
        return EvaluationReport(
            n_episodes=0,
            success_rate=0.0,
            intervention_rate=0.0,
            mean_steps=0.0,
            per_episode=(),
        )
    succ = sum(1 for o in outcomes if o.succeeded)
    interv = sum(1 for o in outcomes if o.intervened)
    steps = sum(o.n_steps for o in outcomes)
    return EvaluationReport(
        n_episodes=n,
        success_rate=succ / n,
        intervention_rate=interv / n,
        mean_steps=steps / n,
        per_episode=tuple(outcomes),
    )


__all__ = ["EpisodeOutcome", "EvaluationReport", "evaluate_trials"]
