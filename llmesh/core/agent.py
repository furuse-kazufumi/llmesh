"""Research-orchestration core — Agent abstract base for research orchestration.

`Agent` is the I/O-typed building block used by all research-orchestration
agents (literature / hypothesis / planner / executor / reviewer). I/O
schemas are concrete `@dataclass` types declared by each subclass so
that JSON-Schema and trace serialisation stay first-class.

llmesh upstream policy: **no pydantic dependency**. Standard library
`dataclasses` are sufficient and keep `llmesh-mcp` installable on
embedded Linux / RTOS targets without extra wheels.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

I = TypeVar("I")
O = TypeVar("O")


@dataclass(frozen=True)
class AgentConfig:
    """Static configuration for an `Agent`. Immutable so trace records
    can serialise it verbatim with the run-id for later replay."""

    name: str
    model: str = ""
    temperature: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)


class Agent(ABC, Generic[I, O]):
    """Base class for research-orchestration agents.

    Subclasses declare concrete request / response dataclasses and
    implement :meth:`run`. The framework treats the I/O types as the
    contract; everything else (LLM choice, tools, retries) is internal.
    """

    def __init__(self, config: AgentConfig) -> None:
        self.config = config

    @abstractmethod
    def run(self, request: I) -> O:
        """Execute the agent on a single request. Must be deterministic
        given identical (config, request) for replayability."""

    @property
    def name(self) -> str:
        return self.config.name


__all__ = ["Agent", "AgentConfig"]
