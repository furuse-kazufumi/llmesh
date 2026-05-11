"""Research-orchestration core — trace entries written as append-only JSONL.

A `TraceEntry` is the unit of evidence for replaying a research run:
every agent invocation, tool call, human input and evaluation event
appends one entry. JSONL was chosen over a database so traces can be
diffed, grep'd and concatenated without tooling.

Pairs with :mod:`llmesh.audit.trace` (which provides tamper-evident
HMAC chaining) — research traces are operational, audit traces are
compliance-grade. They are intentionally separate to avoid coupling
research iteration speed to audit-chain rotation.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TraceKind = str  # "agent.run" | "tool.call" | "human.input" | "evaluation" | ...


@dataclass(frozen=True)
class TraceEntry:
    """One immutable trace record."""

    run_id: str
    seq: int
    timestamp: str
    actor: str          # agent or tool name
    kind: TraceKind
    input_payload: dict[str, Any] = field(default_factory=dict)
    output_payload: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)


def make_entry(
    run_id: str,
    seq: int,
    actor: str,
    kind: TraceKind,
    *,
    input_payload: dict[str, Any] | None = None,
    output_payload: dict[str, Any] | None = None,
    metrics: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> TraceEntry:
    """Build a `TraceEntry` with `timestamp` filled to local time.

    `seq` should be monotonically increasing within a `run_id` so the
    JSONL can be replayed deterministically without sorting.
    """
    return TraceEntry(
        run_id=run_id,
        seq=seq,
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
        actor=actor,
        kind=kind,
        input_payload=input_payload or {},
        output_payload=output_payload or {},
        metrics=metrics or {},
        extra=extra or {},
    )


def write_trace_jsonl(path: Path, entry: TraceEntry) -> None:
    """Append one entry to `path` as a single JSON line.

    Parent directory is created on demand. The write is line-buffered
    open/append/close so a process crash leaves at most one half-line
    behind (which a replayer can detect by JSON parse failure on the
    last line and ignore).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")


__all__ = ["TraceEntry", "TraceKind", "make_entry", "write_trace_jsonl"]
