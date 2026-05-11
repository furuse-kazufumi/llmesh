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
from llmesh.core.trace_logger import (
    KIND_AGENT_RUN,
    KIND_EVALUATION,
    KIND_PROMPT,
    KIND_RUN_END,
    KIND_RUN_START,
    KIND_TOOL_CALL,
    TraceLogger,
)

__all__ = [
    "KIND_AGENT_RUN",
    "KIND_EVALUATION",
    "KIND_PROMPT",
    "KIND_RUN_END",
    "KIND_RUN_START",
    "KIND_TOOL_CALL",
    "Agent",
    "AgentConfig",
    "TaskGraph",
    "TaskKind",
    "TaskNode",
    "Tool",
    "ToolSpec",
    "TraceEntry",
    "TraceKind",
    "TraceLogger",
    "make_entry",
    "write_trace_jsonl",
]
