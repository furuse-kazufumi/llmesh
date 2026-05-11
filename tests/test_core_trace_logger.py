"""Tests for llmesh.core.trace_logger — Phase 0b research-orchestration logger."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from llmesh.core import (
    KIND_AGENT_RUN,
    KIND_EVALUATION,
    KIND_PROMPT,
    KIND_RUN_END,
    KIND_RUN_START,
    KIND_TOOL_CALL,
    TraceLogger,
)


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").strip().split("\n")]


class TestRunLifecycle:
    def test_emits_run_start_with_seed_and_config(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        tl = TraceLogger(log_path, run_id="r-1", seed=42, config={"k": "v"})
        tl.close()

        lines = _read_lines(log_path)
        assert lines[0]["kind"] == KIND_RUN_START
        assert lines[0]["run_id"] == "r-1"
        assert lines[0]["seq"] == 0
        assert lines[0]["extra"]["seed"] == 42
        assert lines[0]["extra"]["config"] == {"k": "v"}

    def test_emits_run_end_on_close(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        tl = TraceLogger(log_path, run_id="r-2")
        tl.log_tool_call("t", input_payload={"x": 1}, output_payload={"y": 2})
        tl.close()

        lines = _read_lines(log_path)
        assert lines[-1]["kind"] == KIND_RUN_END
        assert lines[-1]["extra"]["total_entries"] == 3  # start + tool + end

    def test_auto_run_id_assigned(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        tl = TraceLogger(log_path)
        try:
            assert tl.run_id  # non-empty
            assert len(tl.run_id) == 12  # uuid4 hex prefix
        finally:
            tl.close()

    def test_context_manager(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r-3") as tl:
            tl.log_agent_run("a", input_payload={}, output_payload={"ok": True})
        lines = _read_lines(log_path)
        assert lines[0]["kind"] == KIND_RUN_START
        assert lines[-1]["kind"] == KIND_RUN_END

    def test_context_manager_records_exception_in_run_end(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with pytest.raises(RuntimeError, match="boom"):
            with TraceLogger(log_path, run_id="r-4") as tl:
                tl.log_agent_run("a", input_payload={}, output_payload={})
                raise RuntimeError("boom")
        lines = _read_lines(log_path)
        assert lines[-1]["kind"] == KIND_RUN_END
        assert lines[-1]["extra"]["error"] == {"type": "RuntimeError", "message": "boom"}

    def test_close_is_idempotent(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        tl = TraceLogger(log_path, run_id="r-5")
        tl.close()
        tl.close()  # no-op, must not raise
        lines = _read_lines(log_path)
        # exactly one run.end entry
        assert sum(1 for line in lines if line["kind"] == KIND_RUN_END) == 1

    def test_log_after_close_raises(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        tl = TraceLogger(log_path, run_id="r-6")
        tl.close()
        with pytest.raises(RuntimeError, match="closed"):
            tl.log_tool_call("t", input_payload={}, output_payload={})


class TestTypedHelpers:
    def test_log_prompt_records_model_metadata(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r-p") as tl:
            tl.log_prompt(
                "agent.literature",
                prompt="summarise X",
                response="X is ...",
                model="claude-haiku-4-5",
                model_version="20251001",
                metrics={"tokens_in": 12, "tokens_out": 34},
            )

        lines = _read_lines(log_path)
        prompt_entry = next(line for line in lines if line["kind"] == KIND_PROMPT)
        assert prompt_entry["actor"] == "agent.literature"
        assert prompt_entry["input_payload"]["prompt"] == "summarise X"
        assert prompt_entry["output_payload"]["response"] == "X is ..."
        assert prompt_entry["extra"]["model"] == "claude-haiku-4-5"
        assert prompt_entry["extra"]["model_version"] == "20251001"
        assert prompt_entry["metrics"]["tokens_in"] == 12

    def test_log_tool_call(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r-t") as tl:
            tl.log_tool_call(
                "search",
                input_payload={"q": "AGI"},
                output_payload={"hits": 3},
                metrics={"ms": 87},
            )

        lines = _read_lines(log_path)
        tool_entry = next(line for line in lines if line["kind"] == KIND_TOOL_CALL)
        assert tool_entry["actor"] == "search"
        assert tool_entry["input_payload"] == {"q": "AGI"}
        assert tool_entry["output_payload"] == {"hits": 3}
        assert tool_entry["metrics"]["ms"] == 87

    def test_log_agent_run(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r-a") as tl:
            tl.log_agent_run(
                "agent.planner",
                input_payload={"goal": "..."},
                output_payload={"plan": [1, 2, 3]},
            )

        lines = _read_lines(log_path)
        a = next(line for line in lines if line["kind"] == KIND_AGENT_RUN)
        assert a["output_payload"]["plan"] == [1, 2, 3]

    def test_log_evaluation_scalar_and_rubric(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r-e") as tl:
            tl.log_evaluation("reviewer", target="agent.literature#1", score=0.8, notes="ok")
            tl.log_evaluation(
                "reviewer",
                target="agent.planner#2",
                score={"clarity": 0.9, "feasibility": 0.7},
            )

        lines = _read_lines(log_path)
        evals = [line for line in lines if line["kind"] == KIND_EVALUATION]
        assert evals[0]["output_payload"]["score"] == 0.8
        assert evals[0]["extra"]["target"] == "agent.literature#1"
        assert evals[0]["extra"]["notes"] == "ok"
        assert evals[1]["output_payload"]["score"] == {"clarity": 0.9, "feasibility": 0.7}


class TestSeqAndOrdering:
    def test_seq_monotonic_within_run(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="r") as tl:
            for i in range(5):
                tl.log_tool_call(f"t{i}", input_payload={"i": i}, output_payload={})
        lines = _read_lines(log_path)
        seqs = [line["seq"] for line in lines]
        assert seqs == list(range(len(seqs)))

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        log_path = tmp_path / "nested" / "deep" / "trace.jsonl"
        with TraceLogger(log_path, run_id="r"):
            pass
        assert log_path.exists()

    def test_run_id_stamped_on_every_entry(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        with TraceLogger(log_path, run_id="run-xyz") as tl:
            tl.log_agent_run("a", input_payload={}, output_payload={})
            tl.log_tool_call("t", input_payload={}, output_payload={})
        assert all(line["run_id"] == "run-xyz" for line in _read_lines(log_path))


class TestThreadSafety:
    def test_concurrent_writes_dont_corrupt_jsonl(self, tmp_path: Path) -> None:
        log_path = tmp_path / "trace.jsonl"
        tl = TraceLogger(log_path, run_id="r-mt")
        n_threads = 8
        per_thread = 25

        def worker(tid: int) -> None:
            for i in range(per_thread):
                tl.log_tool_call(f"t-{tid}", input_payload={"i": i}, output_payload={"tid": tid})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        tl.close()

        lines = _read_lines(log_path)
        # 1 run.start + n_threads*per_thread tool.call + 1 run.end
        assert len(lines) == 1 + n_threads * per_thread + 1
        seqs = [line["seq"] for line in lines]
        assert sorted(seqs) == list(range(len(seqs)))  # all unique, no gaps
