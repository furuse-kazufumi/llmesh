"""Tests for Phase 7 — executor + e2e pipeline + paper exporter."""

from __future__ import annotations

from pathlib import Path

import pytest

from llmesh.core.trace_logger import TraceLogger
from llmesh.research import (
    E2EResult,
    ExperimentResult,
    MockExperimentExecutor,
    StepRun,
    export_paper_bundle,
    iter_trace,
    mock_extract,
    mock_hypothesis_extract,
    mock_planner_extract,
    mock_reviewer_extract,
    render_timing_svg,
    run_research_pipeline,
    summarise_result,
)
from llmesh.research.planner import ExperimentPlan, ExperimentStep


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


def _plan(metrics: tuple[str, ...] = ("accuracy", "latency_ms")) -> ExperimentPlan:
    return ExperimentPlan(
        hypothesis="X has no effect on Y.",
        variables=("X",),
        metrics=metrics,
        success_criteria=("delta < 1%",),
        steps=(
            ExperimentStep(order=1, action="train_baseline"),
            ExperimentStep(order=2, action="evaluate"),
        ),
    )


class TestMockExecutor:
    def test_runs_each_step(self) -> None:
        ex = MockExperimentExecutor()
        result = ex.run(_plan())
        assert isinstance(result, ExperimentResult)
        assert len(result.steps) == 2
        assert all(isinstance(s, StepRun) for s in result.steps)

    def test_metrics_emitted_per_step(self) -> None:
        ex = MockExperimentExecutor()
        result = ex.run(_plan())
        for step in result.steps:
            assert set(step.metrics.keys()) == {"accuracy", "latency_ms"}

    def test_deterministic_across_runs(self) -> None:
        a = MockExperimentExecutor().run(_plan())
        b = MockExperimentExecutor().run(_plan())
        assert a.metrics == b.metrics
        assert [(s.action, s.duration_ms) for s in a.steps] == [
            (s.action, s.duration_ms) for s in b.steps
        ]

    def test_value_bounds_respected(self) -> None:
        ex = MockExperimentExecutor(low=0.5, high=0.6)
        result = ex.run(_plan())
        for v in result.metrics.values():
            assert 0.5 <= v <= 0.6

    def test_success_flag_requires_metrics(self) -> None:
        ex = MockExperimentExecutor()
        plan = _plan(metrics=())  # no metrics → success False
        result = ex.run(plan)
        assert result.success is False

    def test_constructor_validates_bounds(self) -> None:
        with pytest.raises(ValueError):
            MockExperimentExecutor(low=1.0, high=0.5)
        with pytest.raises(ValueError):
            MockExperimentExecutor(baseline_duration_ms=0)

    def test_summarise_result_is_json_friendly(self) -> None:
        result = MockExperimentExecutor().run(_plan())
        s = summarise_result(result)
        assert isinstance(s["metrics"], dict)
        assert isinstance(s["total_duration_ms"], float)


# ---------------------------------------------------------------------------
# E2E pipeline
# ---------------------------------------------------------------------------


class TestE2EPipeline:
    def test_returns_e2e_result(self) -> None:
        result = run_research_pipeline(
            paper_text="A test paper body.",
            paper_title="Test",
            literature_extract=mock_extract,
            hypothesis_extract=mock_hypothesis_extract,
            planner_extract=mock_planner_extract,
            reviewer_extract=mock_reviewer_extract,
            executor=MockExperimentExecutor(),
        )
        assert isinstance(result, E2EResult)
        assert result.digest.research_question
        assert result.hypothesis.statement
        assert result.experiment.metrics
        assert result.final_verdict.kind == "approve"

    def test_no_hypotheses_raises(self) -> None:
        def empty_hypothesis(prompt: str) -> dict[str, object]:
            return {"hypotheses": []}

        with pytest.raises(RuntimeError, match="hypotheses"):
            run_research_pipeline(
                paper_text="anything",
                literature_extract=mock_extract,
                hypothesis_extract=empty_hypothesis,
                planner_extract=mock_planner_extract,
                reviewer_extract=mock_reviewer_extract,
                executor=MockExperimentExecutor(),
            )

    def test_pipeline_emits_trace_entries(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="pipe-1") as tl:
            run_research_pipeline(
                paper_text="abc",
                paper_title="t",
                literature_extract=mock_extract,
                hypothesis_extract=mock_hypothesis_extract,
                planner_extract=mock_planner_extract,
                reviewer_extract=mock_reviewer_extract,
                executor=MockExperimentExecutor(),
                trace=tl,
            )
        entries = list(iter_trace(log_path))
        kinds = [e["kind"] for e in entries]
        # The pipeline should log one of each major stage
        assert "run.start" in kinds
        assert "agent.run" in kinds
        assert "tool.call" in kinds
        assert "evaluation" in kinds
        assert "run.end" in kinds


# ---------------------------------------------------------------------------
# Paper exporter
# ---------------------------------------------------------------------------


class TestPaperExporter:
    def test_export_bundle_paths_exist(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r") as tl:
            run_research_pipeline(
                paper_text="abc",
                literature_extract=mock_extract,
                hypothesis_extract=mock_hypothesis_extract,
                planner_extract=mock_planner_extract,
                reviewer_extract=mock_reviewer_extract,
                executor=MockExperimentExecutor(),
                trace=tl,
            )
        out_dir = tmp_path / "bundle"
        bundle = export_paper_bundle(trace_path=log_path, out_dir=out_dir)
        assert bundle.runs_csv.exists()
        assert bundle.metrics_csv.exists()
        assert bundle.timing_svg.exists()
        assert bundle.paper_md.exists()
        assert bundle.n_entries > 0

    def test_csv_files_have_headers(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r") as tl:
            tl.log_tool_call("t", input_payload={}, output_payload={"metrics": {"acc": 0.9}})
        bundle = export_paper_bundle(trace_path=log_path, out_dir=tmp_path / "out")
        runs_lines = bundle.runs_csv.read_text(encoding="utf-8").splitlines()
        assert runs_lines[0].startswith("run_id,seq,timestamp,actor,kind")
        metrics_lines = bundle.metrics_csv.read_text(encoding="utf-8").splitlines()
        assert metrics_lines[0] == "run_id,seq,metric,value"
        # the 'acc' metric should appear
        assert any("acc" in line and "0.9" in line for line in metrics_lines[1:])

    def test_paper_md_lists_metrics(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r") as tl:
            tl.log_tool_call(
                "t",
                input_payload={},
                output_payload={"metrics": {"acc": 0.91, "lat_ms": 12.0}},
            )
        bundle = export_paper_bundle(trace_path=log_path, out_dir=tmp_path / "o")
        md = bundle.paper_md.read_text(encoding="utf-8")
        assert "# Research run bundle" in md
        assert "acc" in md or "lat_ms" in md

    def test_empty_trace_produces_empty_svg(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        log_path.write_text("", encoding="utf-8")
        bundle = export_paper_bundle(trace_path=log_path, out_dir=tmp_path / "o")
        assert bundle.timing_svg.exists()
        svg = bundle.timing_svg.read_text(encoding="utf-8")
        assert "no timing data" in svg

    def test_iter_trace_skips_malformed_lines(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        log_path.write_text(
            '{"run_id":"r","seq":0}\n'
            "this-is-not-json\n"
            '{"run_id":"r","seq":1}\n',
            encoding="utf-8",
        )
        entries = list(iter_trace(log_path))
        assert len(entries) == 2

    def test_render_timing_svg_with_no_duration_rows(self, tmp_path: Path) -> None:
        out = tmp_path / "timing.svg"
        n = render_timing_svg(
            [{"actor": "x", "kind": "agent.run"}],  # no metrics.duration_ms
            out,
        )
        assert n == 0
        assert out.exists()
