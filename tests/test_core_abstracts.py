"""Tests for llmesh.core — research-orchestration primitives (Phase 0a)."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pytest

from llmesh.core import (
    Agent,
    AgentConfig,
    TaskGraph,
    TaskNode,
    Tool,
    ToolSpec,
    make_entry,
    write_trace_jsonl,
)

# ---------------------------------------------------------------------------
# Agent ABC
# ---------------------------------------------------------------------------


class TestAgent:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            Agent(AgentConfig(name="x"))  # type: ignore[abstract]

    def test_concrete_run(self) -> None:
        @dataclass
        class Req:
            text: str

        @dataclass
        class Res:
            text: str

        class Echo(Agent[Req, Res]):
            def run(self, request: Req) -> Res:
                return Res(text=request.text.upper())

        a = Echo(AgentConfig(name="echo", model="mock"))
        assert a.run(Req("hi")).text == "HI"
        assert a.name == "echo"
        assert a.config.model == "mock"

    def test_config_is_frozen(self) -> None:
        cfg = AgentConfig(name="x")
        with pytest.raises(Exception):  # FrozenInstanceError
            cfg.name = "y"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tool ABC
# ---------------------------------------------------------------------------


class TestTool:
    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            Tool(ToolSpec(name="t"))  # type: ignore[abstract]

    def test_concrete_call(self) -> None:
        class Doubler(Tool[int, int]):
            def call(self, request: int) -> int:
                return request * 2

        t = Doubler(ToolSpec(name="dbl", description="multiplies by 2"))
        assert t.call(3) == 6
        assert t.name == "dbl"
        assert t.spec.description == "multiplies by 2"


# ---------------------------------------------------------------------------
# TaskGraph
# ---------------------------------------------------------------------------


class TestTaskGraph:
    def test_topo_order_linear(self) -> None:
        g = TaskGraph()
        g.add(TaskNode(id="a", kind="agent", target="A"))
        g.add(TaskNode(id="b", kind="agent", target="B", depends_on=("a",)))
        g.add(TaskNode(id="c", kind="agent", target="C", depends_on=("b",)))
        assert [n.id for n in g.topo_order()] == ["a", "b", "c"]

    def test_topo_order_fan_out_in(self) -> None:
        g = TaskGraph()
        g.add(TaskNode(id="root", kind="agent", target="R"))
        g.add(TaskNode(id="l", kind="tool", target="L", depends_on=("root",)))
        g.add(TaskNode(id="r", kind="tool", target="R2", depends_on=("root",)))
        g.add(TaskNode(id="join", kind="agent", target="J", depends_on=("l", "r")))
        order = [n.id for n in g.topo_order()]
        assert order[0] == "root"
        assert order[-1] == "join"
        assert set(order[1:3]) == {"l", "r"}

    def test_duplicate_id_rejected(self) -> None:
        g = TaskGraph()
        g.add(TaskNode(id="a", kind="agent", target="A"))
        with pytest.raises(ValueError, match="duplicate"):
            g.add(TaskNode(id="a", kind="agent", target="B"))

    def test_unknown_dependency_rejected(self) -> None:
        g = TaskGraph()
        g.add(TaskNode(id="a", kind="agent", target="A", depends_on=("ghost",)))
        with pytest.raises(ValueError, match="unknown dependency"):
            g.topo_order()

    def test_cycle_detected(self) -> None:
        g = TaskGraph()
        g.add(TaskNode(id="a", kind="agent", target="A", depends_on=("b",)))
        g.add(TaskNode(id="b", kind="agent", target="B", depends_on=("a",)))
        with pytest.raises(ValueError, match="cycle"):
            g.topo_order()


# ---------------------------------------------------------------------------
# Trace JSONL
# ---------------------------------------------------------------------------


class TestTrace:
    def test_make_entry_fills_timestamp(self) -> None:
        e = make_entry("run-1", 0, "agent.foo", "agent.run", input_payload={"x": 1})
        assert e.run_id == "run-1"
        assert e.seq == 0
        assert e.actor == "agent.foo"
        assert e.kind == "agent.run"
        assert e.input_payload == {"x": 1}
        assert e.timestamp  # non-empty

    def test_writes_jsonl_append(self, tmp_path: Path) -> None:
        log = tmp_path / "research-trace.jsonl"
        write_trace_jsonl(log, make_entry("r", 0, "a", "agent.run", input_payload={"q": "hi"}))
        write_trace_jsonl(log, make_entry("r", 1, "tool.x", "tool.call", metrics={"ms": 42}))
        lines = log.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        rec0 = json.loads(lines[0])
        rec1 = json.loads(lines[1])
        assert rec0["seq"] == 0
        assert rec1["seq"] == 1
        assert rec1["metrics"]["ms"] == 42

    def test_creates_parent_dir(self, tmp_path: Path) -> None:
        log = tmp_path / "nested" / "deep" / "trace.jsonl"
        write_trace_jsonl(log, make_entry("r", 0, "a", "agent.run"))
        assert log.exists()
