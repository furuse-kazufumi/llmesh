"""Research-orchestration core — Task DAG primitives.

`TaskGraph` represents an executable research workflow as a directed
acyclic graph of `TaskNode` references. Topological order is computed
on demand (Kahn's algorithm); cycles and dangling dependencies raise
`ValueError` early so execution never starts on an invalid graph.

The DAG is intentionally schema-light here: each node names a target
agent or tool by string, and the executor (Phase 0b/1) resolves and
runs them. This keeps `core` free of executor concerns.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

TaskKind = Literal["agent", "tool", "human", "evaluation"]


@dataclass
class TaskNode:
    """A single step in a research workflow DAG.

    Attributes:
        id: Unique identifier within the enclosing :class:`TaskGraph`.
        kind: What kind of executor handles this node.
        target: Name of the concrete agent / tool / human role.
        inputs: Static inputs / parameter overrides for this node.
        depends_on: IDs of nodes whose outputs must be available first.
    """

    id: str
    kind: TaskKind
    target: str
    inputs: dict[str, Any] = field(default_factory=dict)
    depends_on: tuple[str, ...] = ()


@dataclass
class TaskGraph:
    """An immutable-after-build DAG of `TaskNode`s."""

    nodes: list[TaskNode] = field(default_factory=list)

    def add(self, node: TaskNode) -> None:
        if any(n.id == node.id for n in self.nodes):
            raise ValueError(f"duplicate task id: {node.id!r}")
        self.nodes.append(node)

    def topo_order(self) -> list[TaskNode]:
        """Return nodes in a valid execution order.

        Raises :class:`ValueError` if the graph references an unknown
        dependency or contains a cycle.
        """
        by_id = {n.id: n for n in self.nodes}
        indeg: dict[str, int] = {n.id: 0 for n in self.nodes}
        for n in self.nodes:
            for dep in n.depends_on:
                if dep not in by_id:
                    raise ValueError(f"unknown dependency: {dep!r} (from {n.id!r})")
                indeg[n.id] += 1

        ready = [nid for nid, d in indeg.items() if d == 0]
        order: list[TaskNode] = []
        while ready:
            nid = ready.pop(0)
            order.append(by_id[nid])
            for n in self.nodes:
                if nid in n.depends_on:
                    indeg[n.id] -= 1
                    if indeg[n.id] == 0:
                        ready.append(n.id)

        if len(order) != len(self.nodes):
            unresolved = [nid for nid, d in indeg.items() if d > 0]
            raise ValueError(f"cycle detected in task graph (unresolved={unresolved!r})")
        return order


__all__ = ["TaskGraph", "TaskKind", "TaskNode"]
