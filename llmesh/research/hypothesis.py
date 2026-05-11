"""Hypothesis agent — turn a literature digest into testable claims (Phase 2).

A :class:`HypothesisAgent` consumes the :class:`LiteratureResponse`
produced by :class:`~llmesh.research.literature.LiteratureAgent` and
asks an LLM to generate a small list of testable :class:`Hypothesis`
candidates. Each candidate names the variable being claimed, the
predicted effect, the comparator, and the falsifiability condition —
the minimum that a downstream planner agent needs to design an
experiment.

The agent uses the same :data:`ExtractFn` injection pattern as
:class:`LiteratureAgent` so the mock-first PoC test path stays
self-contained.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llmesh.core.agent import Agent, AgentConfig
from llmesh.research.literature import ExtractFn, LiteratureResponse

# Tool name forwarded to the underlying LLMBackend.invoke when an
# adapter (make_*_extract) routes prompts via TOOL_SCHEMAS.
HYPOTHESIS_TOOL_NAME = "hypothesis_generate"


# ---------------------------------------------------------------------------
# dataclasses (JSON-Schema-ready: every field is a primitive or list)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Hypothesis:
    """One testable claim.

    Mirrors a minimal JSON schema with required ``statement`` plus four
    nullable string fields. The strictness lives in
    :func:`parse_hypothesis_result`, not on the dataclass, so a missing
    optional field is preserved as an empty string rather than ``None``.
    """

    statement: str
    independent_variable: str = ""
    dependent_variable: str = ""
    expected_effect: str = ""
    falsifier: str = ""


@dataclass(frozen=True)
class HypothesisRequest:
    """Generate ``max_candidates`` hypotheses grounded in the digest.

    ``focus`` is an optional steering hint (e.g. ``"latency vs accuracy"``).
    """

    digest: LiteratureResponse
    max_candidates: int = 3
    focus: str = ""


@dataclass(frozen=True)
class HypothesisResponse:
    candidates: tuple[Hypothesis, ...]
    raw: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# prompt + parser
# ---------------------------------------------------------------------------


_PROMPT_HEADER = (
    "You are a research hypothesis generator. From the structured literature "
    "digest below, propose UP TO {max} testable hypotheses. Reply with a "
    "strict JSON object: {{\"hypotheses\": [{{\"statement\": ..., "
    "\"independent_variable\": ..., \"dependent_variable\": ..., "
    "\"expected_effect\": ..., \"falsifier\": ...}}, ...]}}. "
    "Each statement must be a single sentence, falsifiable, and grounded "
    "in the digest's constraints / metrics / open_problems. "
    "Reply with the JSON object only — no surrounding prose."
)


def build_hypothesis_prompt(req: HypothesisRequest) -> str:
    """Render the hypothesis prompt with the literature digest embedded."""
    header = _PROMPT_HEADER.format(max=req.max_candidates)
    focus_line = f"Focus: {req.focus.strip()}\n\n" if req.focus.strip() else ""
    d = req.digest
    digest_block = (
        f"research_question: {d.research_question}\n"
        f"constraints: {list(d.constraints)}\n"
        f"metrics: {list(d.metrics)}\n"
        f"open_problems: {list(d.open_problems)}\n"
    )
    return f"{header}\n\n{focus_line}{digest_block}"


def parse_hypothesis_result(
    result: dict[str, Any], *, max_candidates: int
) -> HypothesisResponse:
    """Validate a backend payload, drop malformed candidates silently.

    A single empty ``statement`` is the only reason a candidate is
    discarded — any other missing field becomes an empty string so a
    partially-formed hypothesis still propagates downstream for the
    reviewer to flag.
    """
    if not isinstance(result, dict):
        raise ValueError("hypothesis result must be a JSON object")
    items = result.get("hypotheses")
    if not isinstance(items, list):
        raise ValueError("'hypotheses' field must be a list")
    out: list[Hypothesis] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        statement = str(item.get("statement", "")).strip()
        if not statement:
            continue
        out.append(
            Hypothesis(
                statement=statement,
                independent_variable=str(item.get("independent_variable", "")).strip(),
                dependent_variable=str(item.get("dependent_variable", "")).strip(),
                expected_effect=str(item.get("expected_effect", "")).strip(),
                falsifier=str(item.get("falsifier", "")).strip(),
            )
        )
        if len(out) >= max_candidates:
            break
    return HypothesisResponse(candidates=tuple(out), raw=dict(result))


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class HypothesisAgent(Agent[HypothesisRequest, HypothesisResponse]):
    """Turn a literature digest into a short list of testable hypotheses."""

    def __init__(self, config: AgentConfig, extract_fn: ExtractFn) -> None:
        super().__init__(config)
        self._extract = extract_fn

    def run(self, request: HypothesisRequest) -> HypothesisResponse:
        prompt = build_hypothesis_prompt(request)
        result = self._extract(prompt)
        return parse_hypothesis_result(result, max_candidates=request.max_candidates)


def mock_hypothesis_extract(prompt: str) -> dict[str, Any]:
    """Deterministic mock for tests / smoke runs.

    Returns two well-formed hypotheses plus a malformed one to exercise
    the parser's silent-drop path.
    """
    return {
        "hypotheses": [
            {
                "statement": "Activation checkpointing does not change GLUE accuracy at fixed budget.",
                "independent_variable": "activation_checkpointing",
                "dependent_variable": "GLUE_accuracy",
                "expected_effect": "negligible",
                "falsifier": "GLUE accuracy delta > 1.0% on dev set",
            },
            {
                "statement": "Wall-clock latency increases by >10% under activation checkpointing.",
                "independent_variable": "activation_checkpointing",
                "dependent_variable": "forward_latency_ms",
                "expected_effect": "increase",
                "falsifier": "latency increase < 5% across 1000 samples",
            },
            # malformed — gets dropped silently
            {"statement": ""},
        ],
        "_mock": True,
    }


__all__ = [
    "HYPOTHESIS_TOOL_NAME",
    "Hypothesis",
    "HypothesisAgent",
    "HypothesisRequest",
    "HypothesisResponse",
    "build_hypothesis_prompt",
    "mock_hypothesis_extract",
    "parse_hypothesis_result",
]
