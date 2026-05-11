"""Planner agent — turn one hypothesis into an executable experiment plan (Phase 2).

The :class:`PlannerAgent` takes a single :class:`Hypothesis` from the
hypothesis stage and asks an LLM to produce a step-by-step
:class:`ExperimentPlan` that names the variables to manipulate, the
metrics to record, success criteria, and an ordered list of concrete
:class:`ExperimentStep` operations.

The plan is intentionally schema-light at Phase 2: the reviewer agent
(in :mod:`llmesh.research.reviewer`) closes the loop by accepting,
revising, or rejecting the plan in a separate stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llmesh.core.agent import Agent, AgentConfig
from llmesh.research.hypothesis import Hypothesis
from llmesh.research.literature import ExtractFn

PLANNER_TOOL_NAME = "experiment_plan"


# ---------------------------------------------------------------------------
# dataclasses (JSON-Schema-ready)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ExperimentStep:
    """One operation in an experiment plan."""

    order: int
    action: str
    inputs: dict[str, Any] = field(default_factory=dict)
    notes: str = ""


@dataclass(frozen=True)
class ExperimentPlan:
    """Self-contained plan for testing a single hypothesis."""

    hypothesis: str  # the originating Hypothesis.statement
    variables: tuple[str, ...]
    metrics: tuple[str, ...]
    success_criteria: tuple[str, ...]
    steps: tuple[ExperimentStep, ...]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PlannerRequest:
    hypothesis: Hypothesis
    budget_notes: str = ""  # free-form constraints (compute, time, data)


@dataclass(frozen=True)
class PlannerResponse:
    plan: ExperimentPlan


# ---------------------------------------------------------------------------
# prompt + parser
# ---------------------------------------------------------------------------


_PROMPT_HEADER = (
    "You are a research experiment planner. Given the hypothesis below, "
    "produce a strict JSON object with: \"variables\" (array of strings), "
    "\"metrics\" (array of strings), \"success_criteria\" (array of "
    "strings), \"steps\" (array of objects with integer \"order\", string "
    "\"action\", optional object \"inputs\", optional string \"notes\"). "
    "Reply with the JSON object only — no prose."
)


def build_planner_prompt(req: PlannerRequest) -> str:
    h = req.hypothesis
    budget_line = (
        f"Budget / constraints: {req.budget_notes.strip()}\n\n"
        if req.budget_notes.strip()
        else ""
    )
    h_block = (
        f"Hypothesis: {h.statement}\n"
        f"Independent variable: {h.independent_variable or '(unspecified)'}\n"
        f"Dependent variable: {h.dependent_variable or '(unspecified)'}\n"
        f"Expected effect: {h.expected_effect or '(unspecified)'}\n"
        f"Falsifier: {h.falsifier or '(unspecified)'}\n"
    )
    return f"{_PROMPT_HEADER}\n\n{budget_line}{h_block}"


def _coerce_str_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        return (value,) if value.strip() else ()
    if isinstance(value, (list, tuple)):
        return tuple(s.strip() for s in (str(v) for v in value) if s.strip())
    return (str(value),)


def parse_plan_result(
    result: dict[str, Any], *, hypothesis_statement: str
) -> ExperimentPlan:
    """Validate plan dict; preserve order from the input, dedupe nothing.

    Step ``order`` is honoured if present and parseable as int, otherwise
    falls back to the index in the input list. Out-of-bound or malformed
    ``inputs`` collapse to an empty dict so a partial plan is still
    runnable. Missing ``action`` drops the step entirely.
    """
    if not isinstance(result, dict):
        raise ValueError("plan result must be a JSON object")
    raw_steps = result.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError("'steps' field must be a list")
    steps: list[ExperimentStep] = []
    for idx, item in enumerate(raw_steps):
        if not isinstance(item, dict):
            continue
        action = str(item.get("action", "")).strip()
        if not action:
            continue
        try:
            order = int(item.get("order", idx))
        except (TypeError, ValueError):
            order = idx
        inputs_raw = item.get("inputs")
        inputs: dict[str, Any] = inputs_raw if isinstance(inputs_raw, dict) else {}
        notes = str(item.get("notes", "")).strip()
        steps.append(ExperimentStep(order=order, action=action, inputs=inputs, notes=notes))
    return ExperimentPlan(
        hypothesis=hypothesis_statement,
        variables=_coerce_str_tuple(result.get("variables")),
        metrics=_coerce_str_tuple(result.get("metrics")),
        success_criteria=_coerce_str_tuple(result.get("success_criteria")),
        steps=tuple(sorted(steps, key=lambda s: s.order)),
        raw=dict(result),
    )


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class PlannerAgent(Agent[PlannerRequest, PlannerResponse]):
    """Generate a single ExperimentPlan from a Hypothesis."""

    def __init__(self, config: AgentConfig, extract_fn: ExtractFn) -> None:
        super().__init__(config)
        self._extract = extract_fn

    def run(self, request: PlannerRequest) -> PlannerResponse:
        prompt = build_planner_prompt(request)
        result = self._extract(prompt)
        plan = parse_plan_result(result, hypothesis_statement=request.hypothesis.statement)
        return PlannerResponse(plan=plan)


def mock_planner_extract(prompt: str) -> dict[str, Any]:
    """Deterministic planner mock used by tests."""
    return {
        "variables": ["activation_checkpointing"],
        "metrics": ["GLUE_accuracy", "forward_latency_ms"],
        "success_criteria": [
            "GLUE accuracy delta <= 1.0%",
            "Latency increase reported with 95% CI",
        ],
        "steps": [
            {"order": 1, "action": "train_baseline", "inputs": {"steps": 10_000}},
            {"order": 2, "action": "train_with_checkpointing", "inputs": {"steps": 10_000}},
            {
                "order": 3,
                "action": "evaluate_glue",
                "inputs": {"split": "dev"},
                "notes": "average across tasks",
            },
        ],
        "_mock": True,
    }


__all__ = [
    "PLANNER_TOOL_NAME",
    "ExperimentPlan",
    "ExperimentStep",
    "PlannerAgent",
    "PlannerRequest",
    "PlannerResponse",
    "build_planner_prompt",
    "mock_planner_extract",
    "parse_plan_result",
]
