"""Tests for hypothesis + planner + reviewer (Phase 2 skeleton).

All tests run mock-first: no real LLM calls.  The planner ↔ reviewer
closed loop is exercised with a stateful closure mock so the
revision path is observable without a real backend.
"""

from __future__ import annotations

import pytest

from llmesh.core.agent import AgentConfig
from llmesh.research import (
    ExperimentPlan,
    ExperimentStep,
    Hypothesis,
    HypothesisAgent,
    HypothesisRequest,
    HypothesisResponse,
    LiteratureResponse,
    LoopResult,
    PlannerAgent,
    PlannerRequest,
    ReviewerAgent,
    ReviewerRequest,
    Verdict,
    build_hypothesis_prompt,
    build_planner_prompt,
    build_reviewer_prompt,
    mock_hypothesis_extract,
    mock_planner_extract,
    mock_reviewer_extract,
    parse_hypothesis_result,
    parse_plan_result,
    parse_verdict_result,
    run_plan_review_loop,
)


def _digest() -> LiteratureResponse:
    return LiteratureResponse(
        research_question="Does X affect Y under Z?",
        constraints=("c1", "c2"),
        metrics=("accuracy", "latency_ms"),
        open_problems=("multilingual",),
    )


def _hypothesis() -> Hypothesis:
    return Hypothesis(
        statement="X has no effect on Y under Z.",
        independent_variable="X",
        dependent_variable="Y",
        expected_effect="negligible",
        falsifier="|effect| > epsilon",
    )


# ---------------------------------------------------------------------------
# Hypothesis
# ---------------------------------------------------------------------------


class TestHypothesisPrompt:
    def test_embeds_digest_fields(self) -> None:
        prompt = build_hypothesis_prompt(HypothesisRequest(digest=_digest()))
        assert "Does X affect Y under Z?" in prompt
        assert "accuracy" in prompt
        assert "multilingual" in prompt

    def test_max_candidates_inlined(self) -> None:
        prompt = build_hypothesis_prompt(
            HypothesisRequest(digest=_digest(), max_candidates=5)
        )
        assert "5 testable hypotheses" in prompt

    def test_focus_included_when_present(self) -> None:
        prompt = build_hypothesis_prompt(
            HypothesisRequest(digest=_digest(), focus="latency vs accuracy")
        )
        assert "Focus: latency vs accuracy" in prompt


class TestHypothesisParser:
    def test_happy_path(self) -> None:
        res = parse_hypothesis_result(
            {
                "hypotheses": [
                    {
                        "statement": "S1",
                        "independent_variable": "iv",
                        "dependent_variable": "dv",
                        "expected_effect": "increase",
                        "falsifier": "F",
                    }
                ]
            },
            max_candidates=3,
        )
        assert len(res.candidates) == 1
        h = res.candidates[0]
        assert h.statement == "S1"
        assert h.independent_variable == "iv"

    def test_drops_empty_statement(self) -> None:
        res = parse_hypothesis_result(
            {"hypotheses": [{"statement": ""}, {"statement": "ok"}]},
            max_candidates=3,
        )
        assert [c.statement for c in res.candidates] == ["ok"]

    def test_caps_at_max_candidates(self) -> None:
        res = parse_hypothesis_result(
            {"hypotheses": [{"statement": f"s{i}"} for i in range(10)]},
            max_candidates=3,
        )
        assert len(res.candidates) == 3

    def test_optional_fields_default_blank(self) -> None:
        res = parse_hypothesis_result(
            {"hypotheses": [{"statement": "s"}]}, max_candidates=3
        )
        h = res.candidates[0]
        assert h.independent_variable == ""
        assert h.falsifier == ""

    def test_missing_hypotheses_field_raises(self) -> None:
        with pytest.raises(ValueError, match="hypotheses"):
            parse_hypothesis_result({}, max_candidates=3)

    def test_non_dict_input_raises(self) -> None:
        with pytest.raises(ValueError, match="JSON object"):
            parse_hypothesis_result([], max_candidates=3)  # type: ignore[arg-type]


class TestHypothesisAgent:
    def test_mock_e2e(self) -> None:
        agent = HypothesisAgent(
            AgentConfig(name="agent.hypothesis", model="mock"),
            extract_fn=mock_hypothesis_extract,
        )
        res = agent.run(HypothesisRequest(digest=_digest(), max_candidates=3))
        assert isinstance(res, HypothesisResponse)
        # mock returns 3 entries; one is malformed and dropped -> 2 candidates
        assert len(res.candidates) == 2
        assert "Activation checkpointing" in res.candidates[0].statement


# ---------------------------------------------------------------------------
# Planner
# ---------------------------------------------------------------------------


class TestPlannerPrompt:
    def test_embeds_hypothesis(self) -> None:
        prompt = build_planner_prompt(PlannerRequest(hypothesis=_hypothesis()))
        assert "X has no effect on Y under Z." in prompt
        assert "|effect| > epsilon" in prompt

    def test_budget_notes_included(self) -> None:
        prompt = build_planner_prompt(
            PlannerRequest(hypothesis=_hypothesis(), budget_notes="4 GPU-hours")
        )
        assert "4 GPU-hours" in prompt


class TestPlannerParser:
    def test_happy_path(self) -> None:
        plan = parse_plan_result(
            {
                "variables": ["X"],
                "metrics": ["accuracy"],
                "success_criteria": ["delta < 1%"],
                "steps": [
                    {"order": 2, "action": "evaluate"},
                    {"order": 1, "action": "train"},
                ],
            },
            hypothesis_statement="H",
        )
        assert plan.hypothesis == "H"
        assert plan.variables == ("X",)
        # steps are sorted by order
        assert [s.action for s in plan.steps] == ["train", "evaluate"]

    def test_step_without_action_dropped(self) -> None:
        plan = parse_plan_result(
            {"steps": [{"order": 0, "action": ""}, {"order": 1, "action": "go"}]},
            hypothesis_statement="H",
        )
        assert len(plan.steps) == 1

    def test_non_dict_inputs_become_empty(self) -> None:
        plan = parse_plan_result(
            {"steps": [{"action": "x", "inputs": "garbage"}]},
            hypothesis_statement="H",
        )
        assert plan.steps[0].inputs == {}

    def test_missing_steps_raises(self) -> None:
        with pytest.raises(ValueError, match="steps"):
            parse_plan_result({}, hypothesis_statement="H")

    def test_unparseable_order_falls_back_to_index(self) -> None:
        plan = parse_plan_result(
            {
                "steps": [
                    {"order": "bad", "action": "a"},
                    {"order": "worse", "action": "b"},
                ]
            },
            hypothesis_statement="H",
        )
        assert [s.order for s in plan.steps] == [0, 1]


class TestPlannerAgent:
    def test_mock_e2e(self) -> None:
        agent = PlannerAgent(
            AgentConfig(name="agent.planner", model="mock"),
            extract_fn=mock_planner_extract,
        )
        res = agent.run(PlannerRequest(hypothesis=_hypothesis()))
        plan = res.plan
        assert plan.hypothesis == _hypothesis().statement
        assert "GLUE_accuracy" in plan.metrics
        assert len(plan.steps) == 3
        assert plan.steps[0].action == "train_baseline"


# ---------------------------------------------------------------------------
# Reviewer
# ---------------------------------------------------------------------------


def _sample_plan() -> ExperimentPlan:
    return ExperimentPlan(
        hypothesis="H",
        variables=("X",),
        metrics=("accuracy",),
        success_criteria=("delta < 1%",),
        steps=(ExperimentStep(order=1, action="train"),),
    )


class TestReviewerParser:
    def test_approve(self) -> None:
        v = parse_verdict_result({"verdict": "approve", "score": 0.9})
        assert v.kind == "approve"
        assert v.score == 0.9
        assert v.notes == ()

    def test_revise_with_notes(self) -> None:
        v = parse_verdict_result(
            {"verdict": "revise", "notes": ["add metric A", "tighten criterion B"]}
        )
        assert v.kind == "revise"
        assert v.notes == ("add metric A", "tighten criterion B")

    def test_invalid_kind_raises(self) -> None:
        with pytest.raises(ValueError, match="verdict"):
            parse_verdict_result({"verdict": "maybe"})

    def test_percentage_score_clamped(self) -> None:
        v = parse_verdict_result({"verdict": "approve", "score": 85})
        assert v.score == 0.85

    def test_score_clamped_above_one(self) -> None:
        v = parse_verdict_result({"verdict": "approve", "score": 99})
        assert v.score == 0.99

    def test_score_optional(self) -> None:
        v = parse_verdict_result({"verdict": "reject"})
        assert v.score is None

    def test_case_insensitive_verdict(self) -> None:
        v = parse_verdict_result({"verdict": "APPROVE"})
        assert v.kind == "approve"


class TestReviewerAgent:
    def test_approves_when_success_criteria_present(self) -> None:
        agent = ReviewerAgent(
            AgentConfig(name="agent.reviewer", model="mock"),
            extract_fn=mock_reviewer_extract,
        )
        res = agent.run(ReviewerRequest(plan=_sample_plan()))
        assert res.verdict.kind == "approve"

    def test_revises_when_no_criteria(self) -> None:
        # build a prompt that lacks success criteria — we synthesise the
        # extract path directly because the prompt builder always emits
        # the "Success criteria" label.
        agent = ReviewerAgent(
            AgentConfig(name="agent.reviewer", model="mock"),
            # closure that emulates a prompt without success criteria
            extract_fn=lambda prompt: mock_reviewer_extract(
                prompt.replace("Success criteria", "Foo")
            ),
        )
        res = agent.run(ReviewerRequest(plan=_sample_plan()))
        assert res.verdict.kind == "revise"
        assert "add explicit success criteria" in res.verdict.notes


# ---------------------------------------------------------------------------
# Closed loop
# ---------------------------------------------------------------------------


class TestLoop:
    def test_one_pass_approve(self) -> None:
        planner = PlannerAgent(
            AgentConfig(name="p", model="mock"), extract_fn=mock_planner_extract
        )
        reviewer = ReviewerAgent(
            AgentConfig(name="r", model="mock"), extract_fn=mock_reviewer_extract
        )
        result = run_plan_review_loop(
            hypothesis=_hypothesis(), planner=planner, reviewer=reviewer, max_iterations=3
        )
        assert isinstance(result, LoopResult)
        assert result.verdict.kind == "approve"
        assert result.iterations == 1
        assert len(result.history) == 1

    def test_revise_then_approve(self) -> None:
        planner = PlannerAgent(
            AgentConfig(name="p", model="mock"), extract_fn=mock_planner_extract
        )

        # Reviewer that says "revise" the first time and "approve" thereafter.
        calls = {"n": 0}

        def stateful_reviewer(prompt: str) -> dict[str, object]:
            calls["n"] += 1
            if calls["n"] == 1:
                return {"verdict": "revise", "notes": ["add an ablation step"]}
            return {"verdict": "approve", "notes": [], "score": 0.9}

        reviewer = ReviewerAgent(
            AgentConfig(name="r", model="mock"), extract_fn=stateful_reviewer
        )
        result = run_plan_review_loop(
            hypothesis=_hypothesis(),
            planner=planner,
            reviewer=reviewer,
            max_iterations=3,
        )
        assert result.verdict.kind == "approve"
        assert result.iterations == 2
        assert [v.kind for v in result.history] == ["revise", "approve"]

    def test_planner_sees_revise_notes_on_second_pass(self) -> None:
        seen_prompts: list[str] = []

        def recording_planner(prompt: str) -> dict[str, object]:
            seen_prompts.append(prompt)
            return mock_planner_extract(prompt)

        calls = {"n": 0}

        def stateful_reviewer(prompt: str) -> dict[str, object]:
            calls["n"] += 1
            if calls["n"] == 1:
                return {"verdict": "revise", "notes": ["add an ablation step"]}
            return {"verdict": "approve"}

        planner = PlannerAgent(AgentConfig(name="p", model="mock"), extract_fn=recording_planner)
        reviewer = ReviewerAgent(AgentConfig(name="r", model="mock"), extract_fn=stateful_reviewer)
        run_plan_review_loop(
            hypothesis=_hypothesis(),
            planner=planner,
            reviewer=reviewer,
            max_iterations=3,
        )
        # the second planner prompt picks up the reviewer's note as
        # appended budget_notes
        assert len(seen_prompts) == 2
        assert "add an ablation step" in seen_prompts[1]

    def test_reject_terminates_immediately(self) -> None:
        planner = PlannerAgent(
            AgentConfig(name="p", model="mock"), extract_fn=mock_planner_extract
        )
        reviewer = ReviewerAgent(
            AgentConfig(name="r", model="mock"),
            extract_fn=lambda prompt: {
                "verdict": "reject",
                "notes": ["hypothesis untestable in budget"],
            },
        )
        result = run_plan_review_loop(
            hypothesis=_hypothesis(),
            planner=planner,
            reviewer=reviewer,
            max_iterations=5,
        )
        assert result.verdict.kind == "reject"
        assert result.iterations == 1

    def test_iteration_cap_returns_last_verdict(self) -> None:
        planner = PlannerAgent(
            AgentConfig(name="p", model="mock"), extract_fn=mock_planner_extract
        )
        # always revise — should hit the iteration cap
        reviewer = ReviewerAgent(
            AgentConfig(name="r", model="mock"),
            extract_fn=lambda p: {"verdict": "revise", "notes": ["more"]},
        )
        result = run_plan_review_loop(
            hypothesis=_hypothesis(),
            planner=planner,
            reviewer=reviewer,
            max_iterations=2,
        )
        assert result.iterations == 2
        assert result.verdict.kind == "revise"
        assert len(result.history) == 2

    def test_max_iterations_must_be_positive(self) -> None:
        planner = PlannerAgent(
            AgentConfig(name="p", model="mock"), extract_fn=mock_planner_extract
        )
        reviewer = ReviewerAgent(
            AgentConfig(name="r", model="mock"), extract_fn=mock_reviewer_extract
        )
        with pytest.raises(ValueError, match="max_iterations"):
            run_plan_review_loop(
                hypothesis=_hypothesis(),
                planner=planner,
                reviewer=reviewer,
                max_iterations=0,
            )


# ---------------------------------------------------------------------------
# End-to-end Phase 1 -> Phase 2 chain
# ---------------------------------------------------------------------------


class TestPhase1ToPhase2Chain:
    def test_digest_to_hypothesis_to_plan_to_review(self) -> None:
        digest = _digest()
        hyp_agent = HypothesisAgent(
            AgentConfig(name="agent.hypothesis", model="mock"),
            extract_fn=mock_hypothesis_extract,
        )
        plan_agent = PlannerAgent(
            AgentConfig(name="agent.planner", model="mock"),
            extract_fn=mock_planner_extract,
        )
        rev_agent = ReviewerAgent(
            AgentConfig(name="agent.reviewer", model="mock"),
            extract_fn=mock_reviewer_extract,
        )
        # 1) digest → candidates
        hyps = hyp_agent.run(HypothesisRequest(digest=digest, max_candidates=2)).candidates
        assert len(hyps) >= 1
        # 2) one candidate → plan
        plan_resp = plan_agent.run(PlannerRequest(hypothesis=hyps[0]))
        plan = plan_resp.plan
        assert plan.hypothesis == hyps[0].statement
        # 3) plan → verdict
        verdict = rev_agent.run(ReviewerRequest(plan=plan)).verdict
        assert verdict.kind == "approve"
        # 4) The combined chain mirrors run_plan_review_loop's contract
        loop = run_plan_review_loop(
            hypothesis=hyps[0],
            planner=plan_agent,
            reviewer=rev_agent,
        )
        assert isinstance(loop.verdict, Verdict)
        assert loop.verdict.kind == "approve"
