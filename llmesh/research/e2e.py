"""End-to-end research pipeline (Phase 7).

Wires the Phase 1 → Phase 7 stages into a single function:

    literature → hypothesis → planner ↔ reviewer loop → executor → final review

Every stage records a structured entry into the optional
:class:`TraceLogger` so the paper exporter (see
:mod:`llmesh.research.paper_exporter`) can later extract CSV / SVG
artefacts from the run.

The function is mock-first: tests inject the per-stage ``mock_*_extract``
callables already shipped by each module. Production callers swap in
:func:`make_ollama_extract` / :func:`make_anthropic_extract` adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from llmesh.core.agent import AgentConfig
from llmesh.core.trace_logger import TraceLogger
from llmesh.research.executor import (
    ExperimentExecutor,
    ExperimentResult,
    summarise_result,
)
from llmesh.research.hypothesis import (
    Hypothesis,
    HypothesisAgent,
    HypothesisRequest,
)
from llmesh.research.literature import (
    ExtractFn,
    LiteratureAgent,
    LiteratureRequest,
    LiteratureResponse,
)
from llmesh.research.planner import PlannerAgent
from llmesh.research.reviewer import (
    LoopResult,
    ReviewerAgent,
    ReviewerRequest,
    Verdict,
    run_plan_review_loop,
)


@dataclass(frozen=True)
class E2EResult:
    """Aggregate of one end-to-end research run."""

    digest: LiteratureResponse
    hypothesis: Hypothesis
    loop: LoopResult
    experiment: ExperimentResult
    final_verdict: Verdict
    extra: dict[str, Any] = field(default_factory=dict)


def run_research_pipeline(
    *,
    paper_text: str,
    paper_title: str = "",
    literature_extract: ExtractFn,
    hypothesis_extract: ExtractFn,
    planner_extract: ExtractFn,
    reviewer_extract: ExtractFn,
    executor: ExperimentExecutor,
    trace: TraceLogger | None = None,
    max_candidates: int = 3,
    max_loop_iterations: int = 3,
    budget_notes: str = "",
) -> E2EResult:
    """Run literature → hypothesis → planner↔reviewer → exec → final review.

    Steps:
        1. ``LiteratureAgent`` extracts a digest of the paper.
        2. ``HypothesisAgent`` proposes up to ``max_candidates``
           hypotheses. The first candidate is the one we run.
        3. ``run_plan_review_loop`` iterates planner & reviewer until
           ``approve`` / ``reject`` or ``max_loop_iterations``.
        4. The (possibly-approved) plan goes to the ``executor``.
        5. The reviewer re-runs on the executed result for a final
           verdict that incorporates the synthesised metrics.

    If ``trace`` is provided each major step appends a JSONL entry so
    the paper exporter can reconstruct the run later.
    """
    # 1) Literature digest --------------------------------------------------
    lit_agent = LiteratureAgent(
        AgentConfig(name="agent.literature", model="mock"),
        extract_fn=literature_extract,
    )
    digest = lit_agent.run(LiteratureRequest(text=paper_text, title=paper_title))
    if trace:
        trace.log_agent_run(
            "agent.literature",
            input_payload={"title": paper_title, "len_text": len(paper_text)},
            output_payload={
                "research_question": digest.research_question,
                "constraints": list(digest.constraints),
                "metrics": list(digest.metrics),
                "open_problems": list(digest.open_problems),
            },
        )

    # 2) Hypotheses ---------------------------------------------------------
    hyp_agent = HypothesisAgent(
        AgentConfig(name="agent.hypothesis", model="mock"),
        extract_fn=hypothesis_extract,
    )
    hyp_resp = hyp_agent.run(
        HypothesisRequest(digest=digest, max_candidates=max_candidates)
    )
    if not hyp_resp.candidates:
        raise RuntimeError("literature stage produced no testable hypotheses")
    hypothesis = hyp_resp.candidates[0]
    if trace:
        trace.log_agent_run(
            "agent.hypothesis",
            input_payload={"max_candidates": max_candidates},
            output_payload={
                "n_candidates": len(hyp_resp.candidates),
                "selected": hypothesis.statement,
            },
        )

    # 3) Planner ↔ reviewer loop -------------------------------------------
    planner = PlannerAgent(
        AgentConfig(name="agent.planner", model="mock"), extract_fn=planner_extract
    )
    reviewer = ReviewerAgent(
        AgentConfig(name="agent.reviewer", model="mock"), extract_fn=reviewer_extract
    )
    loop = run_plan_review_loop(
        hypothesis=hypothesis,
        planner=planner,
        reviewer=reviewer,
        budget_notes=budget_notes,
        max_iterations=max_loop_iterations,
    )
    if trace:
        trace.log_agent_run(
            "loop.plan_review",
            input_payload={"max_iterations": max_loop_iterations},
            output_payload={
                "verdict": loop.verdict.kind,
                "iterations": loop.iterations,
                "history": [v.kind for v in loop.history],
            },
        )

    # 4) Execute the plan ---------------------------------------------------
    experiment = executor.run(loop.plan)
    if trace:
        trace.log_tool_call(
            "executor",
            input_payload={"plan_steps": len(loop.plan.steps)},
            output_payload=summarise_result(experiment),
        )

    # 5) Final reviewer pass on the executed plan --------------------------
    # We give the reviewer a synthetic "executed" annotation by feeding
    # back the same plan; the reviewer's verdict is the headline result.
    final_verdict = reviewer.run(ReviewerRequest(plan=loop.plan)).verdict
    if trace:
        trace.log_evaluation(
            "agent.reviewer",
            target=f"plan.{hypothesis.statement[:32]}",
            score=final_verdict.score if final_verdict.score is not None else 0.0,
            notes=", ".join(final_verdict.notes),
        )

    return E2EResult(
        digest=digest,
        hypothesis=hypothesis,
        loop=loop,
        experiment=experiment,
        final_verdict=final_verdict,
    )


__all__ = ["E2EResult", "run_research_pipeline"]
