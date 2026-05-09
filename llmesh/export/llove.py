"""LloveJSONLExporter — convert LLMesh data into JSON Lines that llove can read.

`llove` is a terminal Artifact for LLMesh data
(https://github.com/furuse-kazufumi/llove) that consumes a unified ``Event``
record. This module bridges LLMesh's domain types to that record so users can:

    from llmesh.export import LloveJSONLExporter

    with LloveJSONLExporter("snapshot.jsonl") as ex:
        for ev in modbus.stream():
            ex.feed_sensor_event(ev)
        ex.feed_incident_report(report)
        ex.feed_audit_entry(audit_dict)

Then on the consumer side:

    llove tail snapshot.jsonl

The exporter is intentionally tiny and dependency-free: only ``json`` from the
stdlib. It does not import llove at runtime; the wire format is the contract.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Wire format mirrors `llove.events.EventKind`. Keep these strings in sync
# with the llove side (PyPI: ``llove``).
KIND_SENSOR = "sensor"
KIND_SPC_ALARM = "spc_alarm"
KIND_AUDIT = "audit"
KIND_RAG_HIT = "rag_hit"
KIND_LLM_CALL = "llm_call"
KIND_TRACE_SPAN = "trace_span"
KIND_INFO = "info"

_SENSOR_SOURCE_DEFAULT = "llmesh.sensor"


def _ts_to_iso(ts: float | datetime | str | None) -> str:
    """Coerce a variety of timestamp inputs into an ISO-8601 string."""
    if ts is None:
        return datetime.now(tz=timezone.utc).isoformat()
    if isinstance(ts, str):
        return ts
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return ts.isoformat()
    # Float epoch seconds
    return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()


class LloveJSONLExporter:
    """Append LLMesh-derived events to a JSON Lines file readable by llove.

    Designed as a context manager so writes are flushed on exit and the file
    handle is closed deterministically. Each call to ``feed_*`` writes exactly
    one line; readers can stream the file with ``llove tail`` while it grows.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._fh = None

    def __enter__(self) -> LloveJSONLExporter:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh = self._path.open("a", encoding="utf-8", buffering=1)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None

    # ------------------------------------------------------------------ #
    # Feed methods — one per LLMesh domain type.
    # ------------------------------------------------------------------ #

    def feed_sensor_event(self, sensor_event: Any) -> None:
        """Convert an llmesh.industrial.SensorEvent (or duck type) and write."""
        payload = {
            "sensor_id": getattr(sensor_event, "sensor_id", None),
            "sensor_type": getattr(sensor_event, "sensor_type", None),
            "value": getattr(sensor_event, "value", None),
            "quality": getattr(sensor_event, "quality", None),
        }
        meta = getattr(sensor_event, "meta", None)
        if isinstance(meta, dict):
            payload["meta"] = meta
        self._write(
            kind=KIND_SENSOR,
            ts=getattr(sensor_event, "ts", None),
            source_id=getattr(sensor_event, "source_id", _SENSOR_SOURCE_DEFAULT),
            payload={k: v for k, v in payload.items() if v is not None},
        )

    def feed_spc_alarm(
        self,
        *,
        sensor_id: str,
        cusum: float,
        threshold: float | None = None,
        ts: float | datetime | None = None,
        source_id: str = "llmesh.spc",
    ) -> None:
        payload: dict[str, Any] = {"sensor_id": sensor_id, "cusum": cusum}
        if threshold is not None:
            payload["threshold"] = threshold
        self._write(kind=KIND_SPC_ALARM, ts=ts, source_id=source_id, payload=payload)

    def feed_incident_report(self, report: Any) -> None:
        """Convert llmesh.industrial.explainer.IncidentReport (duck-typed)."""
        # The IncidentReport has an attached SPC alarm + LLM hypothesis.
        sensor_id = getattr(report, "sensor_id", "?")
        cusum = getattr(report, "cusum", None)
        threshold = getattr(report, "threshold", None)
        ts = getattr(report, "ts", None)
        if cusum is not None:
            self.feed_spc_alarm(
                sensor_id=sensor_id, cusum=float(cusum), threshold=threshold, ts=ts,
                source_id="llmesh.incident",
            )
        hypothesis = getattr(report, "hypothesis", None) or getattr(report, "explanation", None)
        if hypothesis:
            self._write(
                kind=KIND_LLM_CALL,
                ts=ts,
                source_id="llmesh.incident",
                payload={
                    "kind": "incident_explanation",
                    "sensor_id": sensor_id,
                    "hypothesis": str(hypothesis),
                    "tokens": getattr(report, "tokens", None),
                    "latency_ms": getattr(report, "latency_ms", None),
                },
            )

    def feed_audit_entry(self, entry: dict[str, Any]) -> None:
        """Audit log entries are already dicts; pass them through verbatim."""
        ts = entry.get("ts") or entry.get("timestamp_utc")
        payload = {k: v for k, v in entry.items() if k not in {"ts", "timestamp_utc"}}
        self._write(
            kind=KIND_AUDIT,
            ts=ts,
            source_id=entry.get("source_id", "llmesh.audit"),
            payload=payload,
        )

    def feed_llm_call(
        self,
        *,
        tokens: int | None = None,
        latency_ms: int | None = None,
        model: str | None = None,
        ts: float | datetime | None = None,
        source_id: str = "llmesh.llm",
        **extra: Any,
    ) -> None:
        payload: dict[str, Any] = {}
        if tokens is not None:
            payload["tokens"] = tokens
        if latency_ms is not None:
            payload["latency_ms"] = latency_ms
        if model is not None:
            payload["model"] = model
        payload.update(extra)
        self._write(kind=KIND_LLM_CALL, ts=ts, source_id=source_id, payload=payload)

    def feed_rag_hit(
        self,
        *,
        text: str,
        score: float,
        doc_id: str | None = None,
        ts: float | datetime | None = None,
        source_id: str = "llmesh.rag",
    ) -> None:
        payload: dict[str, Any] = {"text": text, "score": score}
        if doc_id is not None:
            payload["doc_id"] = doc_id
        self._write(kind=KIND_RAG_HIT, ts=ts, source_id=source_id, payload=payload)

    # ------------------------------------------------------------------ #
    # Internal writer.
    # ------------------------------------------------------------------ #

    def _write(
        self,
        *,
        kind: str,
        ts: float | datetime | None,
        source_id: str,
        payload: dict[str, Any],
    ) -> None:
        if self._fh is None:
            raise RuntimeError("LloveJSONLExporter must be used as a context manager")
        record = {
            "kind": kind,
            "ts": _ts_to_iso(ts),
            "source_id": source_id,
            "payload": payload,
        }
        self._fh.write(json.dumps(record, separators=(",", ":")) + "\n")


def dump_llove_jsonl(
    path: str | Path,
    *,
    sensor_events: Iterable[Any] = (),
    audit_entries: Iterable[dict[str, Any]] = (),
    incident_reports: Iterable[Any] = (),
) -> Path:
    """One-shot helper: take iterables and write a complete JSONL snapshot.

    Returns the path written for chaining.
    """
    p = Path(path)
    with LloveJSONLExporter(p) as ex:
        for sev in sensor_events:
            ex.feed_sensor_event(sev)
        for entry in audit_entries:
            ex.feed_audit_entry(entry)
        for rep in incident_reports:
            ex.feed_incident_report(rep)
    return p
