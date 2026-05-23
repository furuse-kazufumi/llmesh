"""Push transport for predictive-coding output — typed diff-stream sink.

A *push frame* is what travels to a consumer when an alarm confirms. The whole
point of predictive coding is that, when the speculation was good, the frame is a
small **diff** (the prediction error) rather than the **full** representation.

This module keeps the transport abstract: :class:`PushSink` is the interface, and
:class:`InMemorySink` is the PoC implementation that just records frames. A real
deployment would back this with the existing MQTT / WebSocket / SSE adapters
(``llmesh.industrial.websocket_adapter`` etc.) — the frame shape is transport
agnostic on purpose.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PushFrame:
    """One unit pushed to consumers.

    kind:
        ``"diff"`` — ``ops`` carries the typed diff (prediction error) to apply
        against the consumer's already-held speculative document.
        ``"full"`` — ``document`` carries the complete llrepr document (cold path:
        an alarm with no prior speculation to diff against).
    """

    kind: str
    incident_id: str
    ops: list[dict[str, Any]] | None = None
    document: dict[str, Any] | None = None
    prediction_error: int = 0
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def is_diff(self) -> bool:
        return self.kind == "diff"


class PushSink(ABC):
    """Where confirmed frames are delivered."""

    @abstractmethod
    def push(self, frame: PushFrame) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class InMemorySink(PushSink):
    """Records frames in order — for tests, demos, and offline replay."""

    def __init__(self) -> None:
        self.frames: list[PushFrame] = []

    def push(self, frame: PushFrame) -> None:
        self.frames.append(frame)

    @property
    def bytes_estimate(self) -> int:
        """Rough payload-size proxy: diff ops vs full documents (smaller = win)."""
        import json

        total = 0
        for f in self.frames:
            blob = f.ops if f.is_diff else f.document
            total += len(json.dumps(blob, ensure_ascii=False).encode("utf-8")) if blob else 0
        return total
