"""End-to-end research-orchestration demo (Phase 19).

Walks every differentiation item from D1 to D7 in a single mock-driven
script. No network, no API keys, no external services — the whole
pipeline runs on the stdlib so this script doubles as a smoke test
and a publication-ready demo material.

The narrative:

1. **Literature** (Phase 1) — a mock paper digest comes in.
2. **Hypothesis** (Phase 2) — three testable claims are generated.
3. **Bayesian Selector (D2)** — pick the hypothesis with the highest
   expected information gain under a uniform prior.
4. **Multi-Fidelity Pipeline (D5)** — run the chosen experiment
   through mock → simulator. Simulator deliberately fails to
   exercise the failure-driven loop.
5. **Failure-Driven Generator (D6)** — invert the failed claim into
   fresh hypotheses.
6. **Embodied Replay (D4)** — replay a VLA trajectory against
   physical constraints; surface any violations with attribution.
7. **Composition (D7)** — score which agent components (planner /
   tool / RAG / reflect / critic) earned their place in the run.
8. **Cost-Aware Trace (D1)** — every step is logged to JSONL with
   per-step USD/token + attribution chain, then summarised.
9. **Paper export (Phase 7)** — render a paper bundle to disk.

Run with::

    py -3.11 -m examples.research_pipeline_e2e

The script writes everything under ``./out/research_e2e_demo/`` and
prints a one-line summary per stage so the reader can follow along
in the terminal. Adjust the constants at the top to dial up the noise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Windows consoles default to cp932 which can't render em-dash / check marks;
# nudge stdout to utf-8 when the runtime supports it. Silently no-op elsewhere.
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, OSError):
        pass

from llmesh.core import (
    AttributionLink,
    CostBreakdown,
    TraceLogger,
    summarize_costs,
)
from llmesh.research import (
    Belief,
    BeliefStore,
    CandidateExperiment,
    FailedExperiment,
    FailureDrivenGenerator,
    FailureDrivenRequest,
    FidelityTier,
    HypothesisAgent,
    HypothesisRequest,
    LiteratureAgent,
    LiteratureRequest,
    PipelineConfig,
    compose,
    iter_trace,
    make_mock_runner,
    mock_extract,
    mock_hypothesis_extract,
    render_paper_md,
    run_pipeline,
    select_next,
)
from llmesh.research.executor import MockExperimentExecutor
from llmesh.research.fidelity import FidelityResult
from llmesh.core.agent import AgentConfig
from llmesh.vla import (
    JointTrajectory,
    JointWaypoint,
    joint_limit_checker,
    replay_episode,
    velocity_cap_checker,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUT_DIR = Path("out/research_e2e_demo")
TRACE_PATH = OUT_DIR / "trace.jsonl"
PAPER_DIR = OUT_DIR / "paper"


# ---------------------------------------------------------------------------
# Pretty printers
# ---------------------------------------------------------------------------


def _hr(title: str) -> None:
    line = "=" * 70
    print(f"\n{line}\n  {title}\n{line}")


def _ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def _info(msg: str) -> None:
    print(f"    {msg}")


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------


def stage_literature(trace: TraceLogger) -> "LiteratureResponse":  # type: ignore[name-defined]
    _hr("Stage 1 — Literature digest (Phase 1)")
    agent = LiteratureAgent(AgentConfig(name="lit"), mock_extract)
    req = LiteratureRequest(
        text="(synthetic paper body for the e2e demo)",
        title="Activation checkpointing trade-offs",
    )
    seq_before = trace.seq
    resp = agent.run(req)
    trace.log_agent_run(
        "literature",
        input_payload={"title": req.title},
        output_payload={"research_question": resp.research_question},
        cost=CostBreakdown(usd=0.002, input_tokens=120, output_tokens=80),
    )
    _ok(f"question: {resp.research_question}")
    _info(f"constraints: {len(resp.constraints)} / metrics: {len(resp.metrics)}")
    return resp


def stage_hypothesis(
    trace: TraceLogger, digest: "LiteratureResponse"  # type: ignore[name-defined]
) -> "HypothesisResponse":  # type: ignore[name-defined]
    _hr("Stage 2 — Hypothesis generation (Phase 2)")
    agent = HypothesisAgent(AgentConfig(name="hyp"), mock_hypothesis_extract)
    seq_before = trace.seq
    resp = agent.run(HypothesisRequest(digest=digest, max_candidates=3))
    trace.log_agent_run(
        "hypothesis",
        input_payload={"max_candidates": 3},
        output_payload={"n_candidates": len(resp.candidates)},
        cost=CostBreakdown(usd=0.004, input_tokens=200, output_tokens=160),
        attribution=[AttributionLink(seq=seq_before - 1, role="caused_by")],
    )
    for i, h in enumerate(resp.candidates):
        _info(f"H{i}: {h.statement}")
    return resp


def stage_bayesian_select(
    trace: TraceLogger, hypotheses
) -> CandidateExperiment:
    _hr("Stage 3 — Bayesian selector (D2)")
    store = BeliefStore()
    candidates: list[CandidateExperiment] = []
    for i, h in enumerate(hypotheses):
        hid = f"h{i}"
        store.set(hid, Belief())  # uniform prior
        candidates.append(
            CandidateExperiment(
                candidate_id=f"exp_{i}",
                hypothesis_id=hid,
                # synthetic likelihood model: diverge so EIG ranking is non-trivial
                p_success_if_true=0.85 - 0.05 * i,
                p_success_if_false=0.15 + 0.05 * i,
                cost_usd=0.01,
            )
        )
    report = select_next(candidates, store)
    chosen = report.chosen.candidate if report.chosen else candidates[0]
    trace.log_step(
        "bayesian_selector",
        kind="d2.select",
        output_payload={"chosen": chosen.candidate_id, "eig": report.chosen.eig},
        cost=CostBreakdown(usd=0.0),
        redundancy="novel",
    )
    _ok(f"picked {chosen.candidate_id} (eig={report.chosen.eig:.4f})")
    for r in report.ranked:
        _info(f"  {r.candidate.candidate_id}: score={r.score:.4f}")
    return chosen


def stage_multi_fidelity(
    trace: TraceLogger, chosen: CandidateExperiment
) -> bool:
    """Run the experiment through mock → simulator. Simulator fails on purpose."""
    _hr("Stage 4 — Multi-fidelity pipeline (D5)")
    runners = {
        FidelityTier.MOCK: make_mock_runner(
            FidelityTier.MOCK, success=True, confidence=0.92, cost_usd=0.001
        ),
        # simulator deliberately fails to drive the failure-driven loop
        FidelityTier.SIMULATOR: make_mock_runner(
            FidelityTier.SIMULATOR,
            success=False,
            confidence=0.10,
            cost_usd=0.05,
        ),
    }
    run = run_pipeline(
        experiment_id=chosen.candidate_id,
        runners=runners,
        config=PipelineConfig(min_confidence=0.6, budget_usd=1.0),
    )
    trace.log_step(
        "fidelity_pipeline",
        kind="d5.run",
        output_payload={
            "halted": run.halted_reason,
            "promoted_to": run.promoted_to.value if run.promoted_to else None,
        },
        cost=CostBreakdown(usd=run.total_cost_usd),
        redundancy="novel",
    )
    _ok(f"halted: {run.halted_reason} at tier {run.promoted_to}")
    _info(f"total cost: ${run.total_cost_usd:.4f}")
    return run.halted_reason == "success"


def stage_failure_driven(
    trace: TraceLogger, original_hypothesis
) -> list:
    _hr("Stage 5 — Failure-driven inversion (D6)")
    failures = [
        FailedExperiment(
            original_hypothesis=original_hypothesis,
            failure_mode="timeout",
            notes="simulator exceeded budget",
            observed_metric_delta=0.04,
        )
    ]
    gen = FailureDrivenGenerator()
    resp = gen.run(FailureDrivenRequest(failures=tuple(failures), max_candidates=4))
    seq_attr = trace.seq - 1
    trace.log_step(
        "failure_driven",
        kind="d6.invert",
        output_payload={"n_new": len(resp.candidates)},
        cost=CostBreakdown(usd=0.0),
        attribution=[AttributionLink(seq=seq_attr, role="reflection_of")],
        redundancy="novel",
    )
    _ok(f"{len(resp.candidates)} inverted hypotheses generated")
    for h in resp.candidates:
        _info(f"• {h.statement}")
    return list(resp.candidates)


def stage_replay(trace: TraceLogger) -> None:
    _hr("Stage 6 — Embodied replay + attribution (D4)")
    # Construct a plausible-but-broken trajectory: joint 0 walks out of range.
    traj = JointTrajectory(
        joint_names=("j1", "j2", "j3", "j4", "j5", "j6"),
        waypoints=(
            JointWaypoint(positions=(0.0, -0.8, 1.2, 0.0, 0.5, 0.0), duration_s=2.0, gripper=1.0),
            JointWaypoint(positions=(4.0, -1.0, 1.4, 0.0, 0.5, 0.0), duration_s=1.0, gripper=0.0),
            JointWaypoint(positions=(-1.2, -0.8, 1.2, 0.0, 0.5, 0.0), duration_s=2.5, gripper=1.0),
        ),
    )
    upstream = trace.seq - 1
    report = replay_episode(
        episode_id="ep_demo",
        trajectory=traj,
        constraints=[joint_limit_checker(), velocity_cap_checker(2.0)],
        upstream_seq=upstream,
    )
    trace.log_step(
        "replay",
        kind="d4.replay",
        output_payload={
            "passes": report.passes,
            "n_errors": report.n_errors,
            "n_violations": len(report.violations),
        },
        cost=CostBreakdown(usd=0.0),
        attribution=list(report.attribution),
        redundancy="novel",
    )
    _ok(
        f"{len(report.violations)} violations "
        f"(errors={report.n_errors}, passes={report.passes})"
    )
    for v in report.violations[:3]:
        _info(f"• wp{v.waypoint_index} {v.constraint_name}: {v.detail}")


def stage_composition(trace: TraceLogger) -> None:
    _hr("Stage 7 — Shapley component composition (D7)")
    # Synthetic value-fn: planner + tool useful alone, RAG redundant with tool.
    def value_fn(subset):
        v = 0.0
        if "planner" in subset:
            v += 0.5
        if "tool" in subset:
            v += 0.4
        if "rag" in subset:
            v += 0.3
            if "tool" in subset:
                v -= 0.25  # redundancy with tool
        if "reflect" in subset:
            v += 0.2
        if "critic" in subset:
            v += 0.1
        return v

    plan = compose(
        ["planner", "tool", "rag", "reflect", "critic"],
        value_fn,
    )
    trace.log_step(
        "composition",
        kind="d7.compose",
        output_payload={"chosen": list(plan.chosen), "value": plan.value_chosen},
        cost=CostBreakdown(usd=0.0),
        redundancy="novel",
    )
    _ok(f"chosen: {plan.chosen}  (value={plan.value_chosen:.3f})")
    for s in plan.scores:
        _info(f"  {s.component_id}: shapley={s.shapley_value:.3f}")


def stage_cost_summary(trace: TraceLogger) -> None:
    _hr("Stage 8 — Cost-aware trace summary (D1)")
    # Pull entries back from the JSONL via iter_trace (Phase 7 helper)
    entries = list(iter_trace(TRACE_PATH))
    cs = summarize_costs(entries)
    _ok(
        f"total USD: ${cs.total_usd:.6f}  "
        f"(input_tokens={cs.total_input_tokens}, output_tokens={cs.total_output_tokens})"
    )
    for actor, usd in cs.by_actor.items():
        if usd > 0:
            _info(f"  actor={actor}: ${usd:.6f}")


def stage_paper_export() -> None:
    _hr("Stage 9 — Paper bundle export (Phase 7)")
    PAPER_DIR.mkdir(parents=True, exist_ok=True)
    md = render_paper_md(
        title="Activation checkpointing — e2e demo bundle",
        run_metadata={"trace": str(TRACE_PATH)},
        sections={
            "Abstract": (
                "End-to-end mock pipeline exercising D1-D7 in a single run."
            ),
            "Method": (
                "Mock literature -> hypothesis -> Bayesian select -> "
                "multi-fidelity (sim fails on purpose) -> failure-driven inversion "
                "-> embodied replay -> Shapley composition. Cost-aware trace logged "
                "throughout."
            ),
        },
    )
    (PAPER_DIR / "paper.md").write_text(md, encoding="utf-8")
    _ok(f"wrote {PAPER_DIR / 'paper.md'}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if TRACE_PATH.exists():
        TRACE_PATH.unlink()
    with TraceLogger(
        TRACE_PATH, run_id="e2e_demo", seed=42, config={"variant": "phase19"}
    ) as trace:
        digest = stage_literature(trace)
        hyp_resp = stage_hypothesis(trace, digest)
        chosen = stage_bayesian_select(trace, hyp_resp.candidates)
        succeeded = stage_multi_fidelity(trace, chosen)
        if not succeeded:
            stage_failure_driven(trace, hyp_resp.candidates[0])
        stage_replay(trace)
        stage_composition(trace)
        stage_cost_summary(trace)
    stage_paper_export()
    _hr("E2E demo complete")
    print(f"  trace JSONL: {TRACE_PATH}")
    print(f"  paper bundle: {PAPER_DIR}")


if __name__ == "__main__":
    main()
