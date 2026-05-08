"""IndustrialTracing — lightweight span-tree tracing (v3 — C-13.1).

A pure-stdlib W3C trace-context-compatible tracer for the Industrial
adapters and pipeline.  Each operation is wrapped in a *Span* — a
finite-duration record with attributes and a parent-child relationship.
Spans can be exported in OTLP-JSON-compatible format for ingestion by
Jaeger / Zipkin / Tempo / any OpenTelemetry-aware backend.

Usage::

    from llmesh.industrial.tracing import IndustrialTracer

    tracer = IndustrialTracer()

    with tracer.span("modbus.poll", attributes={"slave_id": 1}) as span:
        value = read_register(...)
        span.set_attribute("value", value)

        with tracer.span("pipeline.process") as inner:
            diagnosis = pipeline.process(event)
            inner.set_attribute("status", diagnosis.status.value)

    # Export accumulated spans
    print(tracer.export_jsonl())

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Trace / span IDs are generated via `secrets.token_hex` (cryptographically
  strong), never `random`.
- Span attribute values are coerced to JSON-safe types before export.
- Per-span attribute count and per-tracer span count are capped to defend
  against unbounded memory growth.
"""
from __future__ import annotations

import contextvars
import json
import logging
import secrets
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# W3C Trace Context: 16-byte trace_id, 8-byte span_id (hex strings).
_TRACE_ID_HEX_LEN = 32        # 16 bytes
_SPAN_ID_HEX_LEN = 16         # 8 bytes

# Per-span attribute cap — prevents unbounded growth on malicious input.
_MAX_ATTRIBUTES_PER_SPAN = 64

# Per-tracer span cap — once exceeded, oldest spans are evicted (FIFO).
_MAX_SPANS_RETAINED = 10_000

# Status codes (compatible with OpenTelemetry SDK).
SPAN_STATUS_OK = "OK"
SPAN_STATUS_ERROR = "ERROR"
SPAN_STATUS_UNSET = "UNSET"


def _new_trace_id() -> str:
    return secrets.token_hex(_TRACE_ID_HEX_LEN // 2)


def _new_span_id() -> str:
    return secrets.token_hex(_SPAN_ID_HEX_LEN // 2)


def _coerce_attribute(value: Any) -> Any:
    """Coerce an arbitrary attribute value to a JSON-safe scalar / list."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if isinstance(value, (list, tuple)):
        return [_coerce_attribute(v) for v in value]
    return str(value)


# ---------------------------------------------------------------------------
# Span
# ---------------------------------------------------------------------------

@dataclass
class Span:
    """One unit of work — finite duration, parent-child relationship."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str = ""
    start_ns: int = 0
    end_ns: int = 0
    status: str = SPAN_STATUS_UNSET
    attributes: dict[str, Any] = field(default_factory=dict)
    error_message: str = ""

    # ------------------------------------------------------------------
    # Mutators (called inside the context manager)
    # ------------------------------------------------------------------

    def set_attribute(self, key: str, value: Any) -> None:
        if len(self.attributes) >= _MAX_ATTRIBUTES_PER_SPAN and key not in self.attributes:
            logger.debug("Span %s: attribute cap reached, dropping %s", self.name, key)
            return
        self.attributes[key] = _coerce_attribute(value)

    def set_status(self, status: str, error_message: str = "") -> None:
        self.status = status
        if error_message:
            self.error_message = error_message

    @property
    def duration_ns(self) -> int:
        return max(0, self.end_ns - self.start_ns)

    # ------------------------------------------------------------------
    # Serialisation — OTLP-JSON compatible subset
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "traceId": self.trace_id,
            "spanId": self.span_id,
            "parentSpanId": self.parent_span_id,
            "startTimeUnixNano": str(self.start_ns),
            "endTimeUnixNano": str(self.end_ns),
            "status": {"code": self.status, "message": self.error_message},
            "attributes": [
                {"key": k, "value": v} for k, v in self.attributes.items()
            ],
        }


# ---------------------------------------------------------------------------
# IndustrialTracer
# ---------------------------------------------------------------------------

# ContextVar so async tasks can inherit the current span automatically.
_current_span_var: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "llmesh_current_span", default=None,
)


class IndustrialTracer:
    """W3C-compatible tracer that retains spans for later export."""

    def __init__(self) -> None:
        self._spans: list[Span] = []
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    # Span creation
    # ------------------------------------------------------------------

    @contextmanager
    def span(
        self,
        name: str,
        *,
        attributes: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> Iterator[Span]:
        """Open a new span as the current one for the duration of the block."""
        parent = _current_span_var.get()
        s = Span(
            name=name,
            trace_id=trace_id or (parent.trace_id if parent else _new_trace_id()),
            span_id=_new_span_id(),
            parent_span_id=parent.span_id if parent else "",
            start_ns=time.time_ns(),
            attributes={k: _coerce_attribute(v) for k, v in (attributes or {}).items()},
        )
        token = _current_span_var.set(s)
        try:
            yield s
            if s.status == SPAN_STATUS_UNSET:
                s.status = SPAN_STATUS_OK
        except Exception as exc:
            s.set_status(SPAN_STATUS_ERROR, error_message=str(exc))
            raise
        finally:
            s.end_ns = time.time_ns()
            _current_span_var.reset(token)
            self._record(s)

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    def _record(self, span: Span) -> None:
        with self._lock:
            self._spans.append(span)
            if len(self._spans) > _MAX_SPANS_RETAINED:
                # Drop oldest half (FIFO eviction)
                self._spans = self._spans[_MAX_SPANS_RETAINED // 2:]

    def collected_spans(self) -> list[Span]:
        with self._lock:
            return list(self._spans)

    def clear(self) -> None:
        with self._lock:
            self._spans.clear()

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export_jsonl(self) -> str:
        """Return one OTLP-JSON span per line (newline-delimited)."""
        with self._lock:
            return "\n".join(json.dumps(s.to_dict()) for s in self._spans)

    def export_otlp_payload(self) -> dict[str, Any]:
        """Return a dict shaped roughly like OTLP/HTTP-JSON exporter expects."""
        with self._lock:
            return {
                "resourceSpans": [{
                    "resource": {"attributes": [
                        {"key": "service.name", "value": "llmesh.industrial"},
                    ]},
                    "scopeSpans": [{
                        "scope": {"name": "llmesh.industrial.tracing", "version": "2.1.0"},
                        "spans": [s.to_dict() for s in self._spans],
                    }],
                }],
            }


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def current_span() -> Span | None:
    """Return the active Span in this asyncio task / thread, if any."""
    return _current_span_var.get()
