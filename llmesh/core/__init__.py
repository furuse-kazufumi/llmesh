"""Research-orchestration core abstractions.

Phase 0a primitives shared by literature / hypothesis / planner /
executor / reviewer agents and by domain-specific tools (robotics /
materials / vision / knowledge). Intentionally tiny: only ABCs,
dataclasses and helpers — no concrete implementations live here so
``llmesh-mcp`` keeps its embedded-friendly dependency budget.
"""

from llmesh.core.agent import Agent, AgentConfig
from llmesh.core.task import TaskGraph, TaskKind, TaskNode
from llmesh.core.tool import Tool, ToolSpec
from llmesh.core.trace import TraceEntry, TraceKind, make_entry, write_trace_jsonl

__all__ = [
    "Agent",
    "AgentConfig",
    "TaskGraph",
    "TaskKind",
    "TaskNode",
    "Tool",
    "ToolSpec",
    "TraceEntry",
    "TraceKind",
    "make_entry",
    "write_trace_jsonl",
]
