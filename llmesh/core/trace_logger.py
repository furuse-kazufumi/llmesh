"""Research-orchestration core — append-only JSONL trace logger (Phase 0b).

A :class:`TraceLogger` captures every artefact needed to replay or audit a
single research run: prompts (with model + model_version), tool I/O,
agent runs and evaluations. Each :class:`~llmesh.core.trace.TraceEntry`
is appended to a JSONL file under a stable ``run_id``, with a
``run.start`` entry recording ``seed`` and ``config`` up front so the
trace is fully self-describing.

The logger pairs with the lower-level :func:`write_trace_jsonl` and
:func:`make_entry` helpers in :mod:`llmesh.core.trace`. It owns the
``seq`` counter, threading lock and run lifecycle so callers can focus
on what to log, not how.

Like its sibling :mod:`llmesh.audit.trace`, this logger is operational —
it does **not** HMAC-chain entries. Use ``llmesh.audit.trace`` when
compliance-grade tamper evidence is required.
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from types import TracebackType
from typing import Any, Iterable

from llmesh.core.cost_attribution import (
    AttributionLink,
    CostBreakdown,
    RedundancyFlag,
    attribution_to_extra,
    cost_to_metrics,
)
from llmesh.core.trace import TraceEntry, TraceKind, make_entry, write_trace_jsonl

# Standard trace kinds emitted by this logger. Custom kinds are accepted
# via :meth:`TraceLogger.log` but these four cover the Phase 0b contract.
KIND_RUN_START: TraceKind = "run.start"
KIND_RUN_END: TraceKind = "run.end"
KIND_PROMPT: TraceKind = "llm.prompt"
KIND_TOOL_CALL: TraceKind = "tool.call"
KIND_AGENT_RUN: TraceKind = "agent.run"
KIND_EVALUATION: TraceKind = "evaluation"


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


class TraceLogger:
    """Append-only JSONL trace logger for a single research run.

    The first appended entry is always ``run.start`` with ``seed`` and
    ``config`` in :attr:`TraceEntry.extra`; the last (on :meth:`close`
    or context-manager exit) is ``run.end``. ``seq`` increases
    monotonically, which makes the JSONL replayable without sorting.

    Thread-safe: a per-instance lock serialises ``seq`` increments and
    file appends so concurrent agents can share one logger.
    """

    def __init__(
        self,
        path: Path | str,
        *,
        run_id: str | None = None,
        seed: int | None = None,
        config: dict[str, Any] | None = None,
        actor: str = "run",
    ) -> None:
        self.path = Path(path)
        self.run_id = run_id or _new_run_id()
        self.seed = seed
        self.config: dict[str, Any] = dict(config or {})
        self._actor = actor
        self._seq = 0
        self._lock = threading.Lock()
        self._closed = False
        # Emit run.start immediately so even an aborted run is identifiable.
        self.log(
            actor=self._actor,
            kind=KIND_RUN_START,
            extra={"seed": self.seed, "config": self.config},
        )

    # ------------------------------------------------------------------
    # context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> TraceLogger:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        # Surface exceptions as part of run.end so a crashed run leaves a
        # diagnosable trace tail instead of silently truncating.
        extra: dict[str, Any] = {}
        if exc is not None:
            extra["error"] = {"type": exc_type.__name__ if exc_type else "Exception", "message": str(exc)}
        self.close(extra=extra)

    # ------------------------------------------------------------------
    # low-level
    # ------------------------------------------------------------------

    def log(
        self,
        actor: str,
        kind: TraceKind,
        *,
        input_payload: dict[str, Any] | None = None,
        output_payload: dict[str, Any] | None = None,
        metrics: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """Append one entry. Returns the assigned ``seq`` value.

        Callers prefer the typed helpers (:meth:`log_prompt` etc.) but
        this method is the escape hatch for non-standard ``kind`` values.
        """
        if self._closed:
            raise RuntimeError("TraceLogger is closed")
        with self._lock:
            seq = self._seq
            self._seq += 1
            entry: TraceEntry = make_entry(
                self.run_id,
                seq,
                actor,
                kind,
                input_payload=input_payload,
                output_payload=output_payload,
                metrics=metrics,
                extra=extra,
            )
            write_trace_jsonl(self.path, entry)
            return seq

    # ------------------------------------------------------------------
    # typed helpers
    # ------------------------------------------------------------------

    def log_prompt(
        self,
        actor: str,
        *,
        prompt: str,
        response: str,
        model: str,
        model_version: str = "",
        metrics: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """Record one LLM prompt/response pair.

        ``model`` is the logical name (e.g. ``"claude-haiku-4-5"``);
        ``model_version`` pins the actual served revision when the
        backend exposes one. Both are stored in :attr:`TraceEntry.extra`
        so trace replay can diff across model upgrades.
        """
        merged_extra: dict[str, Any] = {"model": model, "model_version": model_version}
        if extra:
            merged_extra.update(extra)
        return self.log(
            actor=actor,
            kind=KIND_PROMPT,
            input_payload={"prompt": prompt},
            output_payload={"response": response},
            metrics=metrics,
            extra=merged_extra,
        )

    def log_tool_call(
        self,
        tool: str,
        *,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        metrics: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        return self.log(
            actor=tool,
            kind=KIND_TOOL_CALL,
            input_payload=input_payload,
            output_payload=output_payload,
            metrics=metrics,
            extra=extra,
        )

    def log_agent_run(
        self,
        agent: str,
        *,
        input_payload: dict[str, Any],
        output_payload: dict[str, Any],
        metrics: dict[str, Any] | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        return self.log(
            actor=agent,
            kind=KIND_AGENT_RUN,
            input_payload=input_payload,
            output_payload=output_payload,
            metrics=metrics,
            extra=extra,
        )

    def log_evaluation(
        self,
        evaluator: str,
        *,
        target: str,
        score: float | dict[str, float],
        notes: str = "",
        extra: dict[str, Any] | None = None,
    ) -> int:
        """Record one evaluation result.

        ``score`` may be a scalar (single rubric) or a dict (multi-rubric).
        ``target`` identifies what was evaluated, typically a previous
        ``seq`` reference like ``"agent.literature#7"``.
        """
        merged_extra: dict[str, Any] = {"target": target, "notes": notes}
        if extra:
            merged_extra.update(extra)
        return self.log(
            actor=evaluator,
            kind=KIND_EVALUATION,
            output_payload={"score": score},
            extra=merged_extra,
        )

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    @property
    def seq(self) -> int:
        """Next seq value to be assigned. Snapshot under :attr:`_lock`."""
        with self._lock:
            return self._seq

    @property
    def closed(self) -> bool:
        return self._closed

    def close(self, *, extra: dict[str, Any] | None = None) -> None:
        """Append ``run.end`` and refuse subsequent writes.

        Idempotent: calling twice is a no-op so context-manager exit on
        an already-closed logger does not raise.
        """
        if self._closed:
            return
        # Emit run.end *before* flipping the flag so the entry actually lands.
        merged = {"total_entries": self._seq + 1}
        if extra:
            merged.update(extra)
        with self._lock:
            seq = self._seq
            self._seq += 1
            entry = make_entry(
                self.run_id,
                seq,
                self._actor,
                KIND_RUN_END,
                extra=merged,
            )
            write_trace_jsonl(self.path, entry)
            self._closed = True


__all__ = [
    "KIND_AGENT_RUN",
    "KIND_EVALUATION",
    "KIND_PROMPT",
    "KIND_RUN_END",
    "KIND_RUN_START",
    "KIND_TOOL_CALL",
    "TraceLogger",
]
