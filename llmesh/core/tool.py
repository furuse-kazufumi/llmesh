"""Research-orchestration core — Tool abstract base for external resource adapters.

`Tool` wraps a side-effect-bearing capability (Python function, REST
endpoint, simulator, robot driver, optimiser). Agents call tools
through this single ABC so the trace logger can record every external
call uniformly.

Distinct from `llmesh.protocol.adapter.ProtocolAdapter` (network
transport) — `Tool` is the *semantic* call boundary for research
orchestration, while protocol adapters handle wire-level transport.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

I = TypeVar("I")
O = TypeVar("O")


@dataclass(frozen=True)
class ToolSpec:
    """Static metadata for a `Tool`. Description is consumed by LLM
    planner agents to decide tool selection."""

    name: str
    description: str = ""
    timeout_sec: float = 30.0
    extra: dict[str, Any] = field(default_factory=dict)


class Tool(ABC, Generic[I, O]):
    """Base class for research-orchestration tools."""

    def __init__(self, spec: ToolSpec) -> None:
        self.spec = spec

    @abstractmethod
    def call(self, request: I) -> O:
        """Invoke the tool. Implementations are responsible for honouring
        ``spec.timeout_sec`` and converting transport errors into
        domain-meaningful exceptions."""

    @property
    def name(self) -> str:
        return self.spec.name


__all__ = ["Tool", "ToolSpec"]
