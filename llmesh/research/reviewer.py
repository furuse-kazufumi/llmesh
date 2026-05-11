"""Reviewer agent + planner ↔ reviewer closed loop skeleton (Phase 2).

The :class:`ReviewerAgent` inspects a freshly-generated
:class:`ExperimentPlan` and emits a :class:`Verdict` of
``approve`` / ``revise`` / ``reject`` together with a short list of
concrete revision notes. The notes feed back into the planner via
:func:`run_plan_review_loop`, which iterates until the reviewer
approves (or a hard iteration cap is reached).

This is a *skeleton* — Phase 2 ships the loop control flow and dataclass
contract; concrete domain-aware review rubrics live downstream
(Phase 5+).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from llmesh.core.agent import Agent, AgentConfig
from llmesh.research.hypothesis import Hypothesis
from llmesh.research.literature import ExtractFn
from llmesh.research.planner import (
    ExperimentPlan,
    PlannerAgent,
    PlannerRequest,
)

REVIEWER_TOOL_NAME = "experiment_plan_review"

VerdictKind = Literal["approve", "revise", "reject"]
_VALID_VERDICTS: frozenset[str] = frozenset({"approve", "revise", "reject"})


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Verdict:
    """Outcome of one review pass.

    Attributes:
        kind: ``"approve"`` ends the loop; ``"revise"`` recycles into
            the planner with ``notes`` appended to its prompt;
            ``"reject"`` aborts.
        notes: Short revision suggestions. Treated as hints, not rules.
        score: Optional 0..1 quality estimate; ``None`` when the
            reviewer didn't quantify.
    """

    kind: VerdictKind
    notes: tuple[str, ...] = ()
    score: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ReviewerRequest:
    plan: ExperimentPlan


@dataclass(frozen=True)
class ReviewerResponse:
    verdict: Verdict


# ---------------------------------------------------------------------------
# prompt + parser
# ---------------------------------------------------------------------------


_PROMPT_HEADER = (
    "You are a research peer reviewer. Inspect the experiment plan below and "
    "reply with a strict JSON object: {\"verdict\": \"approve\"|\"revise\"|"
    "\"reject\", \"notes\": [string, ...], \"score\": number?}. "
    "Approve only if variables, metrics, success_criteria and steps form a "
    "coherent falsifiable test. Reply with JSON only."
)


def build_reviewer_prompt(req: ReviewerRequest) -> str:
    p = req.plan
    steps_block = "\n".join(
        f"  {s.order}. {s.action}" + (f" (notes: {s.notes})" if s.notes else "")
        for s in p.steps
    )
    return (
        f"{_PROMPT_HEADER}\n\n"
        f"Hypothesis: {p.hypothesis}\n"
        f"Variables: {list(p.variables)}\n"
        f"Metrics: {list(p.metrics)}\n"
        f"Success criteria: {list(p.success_criteria)}\n"
        f"Steps:\n{steps_block}\n"
    )


def parse_verdict_result(result: dict[str, Any]) -> Verdict:
    if not isinstance(result, dict):
        raise ValueError("review result must be a JSON object")
    kind = str(result.get("verdict", "")).strip().lower()
    if kind not in _VALID_VERDICTS:
        raise ValueError(f"verdict must be one of {sorted(_VALID_VERDICTS)}; got {kind!r}")
    raw_notes = result.get("notes")
    if raw_notes is None:
        notes: tuple[str, ...] = ()
    elif isinstance(raw_notes, str):
        notes = (raw_notes,) if raw_notes.strip() else ()
    elif isinstance(raw_notes, (list, tuple)):
        notes = tuple(s.strip() for s in (str(n) for n in raw_notes) if s.strip())
    else:
        notes = (str(raw_notes),)
    score_raw = result.get("score")
    score: float | None = None
    if isinstance(score_raw, (int, float)):
        # Clamp into [0, 1] — reviewers occasionally return percentages.
        score = max(0.0, min(1.0, float(score_raw) / (100.0 if score_raw > 1.0 else 1.0)))
    return Verdict(kind=kind, notes=notes, score=score, raw=dict(result))  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class ReviewerAgent(Agent[ReviewerRequest, ReviewerResponse]):
    """Single-pass plan reviewer."""

    def __init__(self, config: AgentConfig, extract_fn: ExtractFn) -> None:
        super().__init__(config)
        self._extract = extract_fn

    def run(self, request: ReviewerRequest) -> ReviewerResponse:
        prompt = build_reviewer_prompt(request)
        result = self._extract(prompt)
        return ReviewerResponse(verdict=parse_verdict_result(result))


# ---------------------------------------------------------------------------
# closed loop
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LoopResult:
    """Outcome of :func:`run_plan_review_loop`."""

    plan: ExperimentPlan
    verdict: Verdict
    iterations: int
    history: tuple[Verdict, ...]


def run_plan_review_loop(
    *,
    hypothesis: Hypothesis,
    planner: PlannerAgent,
    reviewer: ReviewerAgent,
    budget_notes: str = "",
    max_iterations: int = 3,
) -> LoopResult:
    """Iterate planner ↔ reviewer until ``approve`` or budget exhausted.

    On a ``revise`` verdict the reviewer's notes are appended to
    ``budget_notes`` so the next planner pass sees them as additional
    constraints. The loop terminates on the first ``approve`` or
    ``reject``, or after ``max_iterations`` revisions — whichever
    comes first.

    Returns the final plan, the terminating verdict (``"revise"`` only
    if the iteration cap was hit), iteration count and the per-pass
    verdict history. The plan is always returned, even on rejection,
    so callers can inspect what the reviewer rejected.
    """
    if max_iterations < 1:
        raise ValueError("max_iterations must be >= 1")
    history: list[Verdict] = []
    notes = budget_notes
    plan: ExperimentPlan | None = None
    last_verdict: Verdict | None = None
    for i in range(max_iterations):
        planner_resp = planner.run(PlannerRequest(hypothesis=hypothesis, budget_notes=notes))
        plan = planner_resp.plan
        verdict = reviewer.run(ReviewerRequest(plan=plan)).verdict
        history.append(verdict)
        last_verdict = verdict
        if verdict.kind in ("approve", "reject"):
            return LoopResult(plan=plan, verdict=verdict, iterations=i + 1, history=tuple(history))
        # revise — recycle with the reviewer's notes appended for context
        if verdict.notes:
            notes = (notes + "\n" if notes else "") + "Reviewer notes:\n- " + "\n- ".join(verdict.notes)
    assert plan is not None and last_verdict is not None  # max_iterations >= 1
    return LoopResult(
        plan=plan, verdict=last_verdict, iterations=max_iterations, history=tuple(history)
    )


def mock_reviewer_extract(prompt: str) -> dict[str, Any]:
    """Mock that approves any plan mentioning success_criteria.

    Used by tests to demonstrate a successful one-pass loop. For
    iteration tests, callers supply a stateful closure that returns
    ``"revise"`` on early calls and ``"approve"`` later.
    """
    if "success_criteria" in prompt or "Success criteria" in prompt:
        return {"verdict": "approve", "notes": [], "score": 0.85}
    return {
        "verdict": "revise",
        "notes": ["add explicit success criteria"],
        "score": 0.4,
    }


__all__ = [
    "REVIEWER_TOOL_NAME",
    "LoopResult",
    "ReviewerAgent",
    "ReviewerRequest",
    "ReviewerResponse",
    "Verdict",
    "VerdictKind",
    "build_reviewer_prompt",
    "mock_reviewer_extract",
    "parse_verdict_result",
    "run_plan_review_loop",
]
