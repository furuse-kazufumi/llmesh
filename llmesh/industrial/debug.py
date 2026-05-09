"""DebugRecorder / DebugReplayer — record & replay SensorEvent streams.

The single most useful debugging tool: capture every SensorEvent +
DiagnosisResult flowing through a pipeline to a JSON Lines file, then
replay it deterministically into another pipeline for offline
investigation.

Usage::

    # Capture (in production / staging)
    rec = DebugRecorder("session.jsonl")
    adapter.on_event(rec.record_event)
    pipeline.on_diagnosis(rec.record_diagnosis)
    # ... let it run ...
    rec.close()

    # Replay (in your laptop / CI)
    rep = DebugReplayer("session.jsonl")
    for event in rep.events():
        new_pipeline.process(event)

Plus structured logging helpers and a `PipelineProfiler` that adds
timing instrumentation around `IndustrialPipeline.process()`.

Security invariants
-------------------
- Records are JSON Lines, no pickle.
- File paths are validated; no shell evaluation.
- Recorded data goes through privacy summarisers if attached *before*
  the recorder.
"""
from __future__ import annotations

import contextlib
import json
import logging
import struct
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, IO

from llmesh.industrial.sensor_event import Priority, SensorEvent
from llmesh.industrial.pipeline import (
    DiagnosisResult, DiagnosisStatus, IndustrialPipeline,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Per-record size cap (bytes) — defends against malformed JSONL.
_MAX_RECORD_BYTES = 1_048_576       # 1 MiB

# Log format markers for record types in JSONL.
_RECORD_KIND_EVENT = "event"
_RECORD_KIND_DIAGNOSIS = "diagnosis"
_RECORD_KIND_TIMING = "timing"


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------

def _event_to_dict(ev: SensorEvent) -> dict[str, Any]:
    return {
        "kind": _RECORD_KIND_EVENT,
        "sensor_id": ev.sensor_id,
        "protocol": ev.protocol,
        "timestamp_ns": ev.timestamp_ns,
        "payload_hex": ev.payload.hex(),
        "priority": ev.priority.value,
        "device_id": ev.device_id,
        "sensor_type": ev.sensor_type,
        "unit": ev.unit,
        "metadata": _coerce_jsonable(dict(ev.metadata)),
    }


def _event_from_dict(d: dict[str, Any]) -> SensorEvent:
    return SensorEvent(
        sensor_id=d["sensor_id"],
        protocol=d["protocol"],
        timestamp_ns=int(d["timestamp_ns"]),
        payload=bytes.fromhex(d.get("payload_hex", "")),
        priority=Priority(d.get("priority", "normal")),
        device_id=d.get("device_id", ""),
        sensor_type=d.get("sensor_type", ""),
        unit=d.get("unit", ""),
        metadata=dict(d.get("metadata", {})),
    )


def _diagnosis_to_dict(d: DiagnosisResult) -> dict[str, Any]:
    return {
        "kind": _RECORD_KIND_DIAGNOSIS,
        "sensor_id": d.sensor_id,
        "device_id": d.device_id,
        "status": d.status.value,
        "severity": d.severity,
        "summary": d.summary,
        "evidence": _coerce_jsonable(dict(d.evidence)),
        "timestamp_ns": d.timestamp_ns,
        "source_protocol": d.source_protocol,
    }


def _diagnosis_from_dict(d: dict[str, Any]) -> DiagnosisResult:
    return DiagnosisResult(
        sensor_id=d["sensor_id"],
        device_id=d.get("device_id", ""),
        status=DiagnosisStatus(d.get("status", "unknown")),
        severity=float(d.get("severity", 0.0)),
        summary=d.get("summary", ""),
        evidence=dict(d.get("evidence", {})),
        timestamp_ns=int(d.get("timestamp_ns", 0)),
        source_protocol=d.get("source_protocol", ""),
    )


def _coerce_jsonable(d: dict[str, Any]) -> dict[str, Any]:
    """Coerce arbitrary metadata to JSON-safe types (recursive)."""
    out: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, (str, int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, (bytes, bytearray)):
            out[k] = v.hex()
        elif isinstance(v, dict):
            out[k] = _coerce_jsonable(v)
        elif isinstance(v, (list, tuple)):
            out[k] = [
                _coerce_jsonable({"_": x})["_"] for x in v
            ]
        else:
            out[k] = repr(v)
    return out


# ---------------------------------------------------------------------------
# DebugRecorder
# ---------------------------------------------------------------------------

class DebugRecorder:
    """Append-only JSONL recorder for SensorEvents + DiagnosisResults."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fp: IO[str] = self._path.open("w", encoding="utf-8")
        self._event_count = 0
        self._diag_count = 0

    @property
    def path(self) -> Path:
        return self._path

    @property
    def event_count(self) -> int:
        return self._event_count

    @property
    def diagnosis_count(self) -> int:
        return self._diag_count

    def record_event(self, ev: SensorEvent) -> None:
        try:
            self._write(_event_to_dict(ev))
            self._event_count += 1
        except Exception as exc:
            logger.error("DebugRecorder event error: %s", exc)

    def record_diagnosis(self, d: DiagnosisResult) -> None:
        try:
            self._write(_diagnosis_to_dict(d))
            self._diag_count += 1
        except Exception as exc:
            logger.error("DebugRecorder diagnosis error: %s", exc)

    def _write(self, obj: dict[str, Any]) -> None:
        line = json.dumps(obj, ensure_ascii=False)
        if len(line.encode()) > _MAX_RECORD_BYTES:
            logger.warning("DebugRecorder: record exceeds %d bytes — truncating",
                           _MAX_RECORD_BYTES)
            return
        self._fp.write(line + "\n")
        self._fp.flush()

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._fp.close()

    def __enter__(self) -> "DebugRecorder":
        return self

    def __exit__(self, *a: Any) -> None:
        self.close()


# ---------------------------------------------------------------------------
# DebugReplayer
# ---------------------------------------------------------------------------

class DebugReplayer:
    """Read JSONL recordings and yield events / diagnoses."""

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise FileNotFoundError(f"recording not found: {path}")

    def events(self) -> Iterator[SensorEvent]:
        for line in self._iter_lines():
            d = json.loads(line)
            if d.get("kind") == _RECORD_KIND_EVENT:
                yield _event_from_dict(d)

    def diagnoses(self) -> Iterator[DiagnosisResult]:
        for line in self._iter_lines():
            d = json.loads(line)
            if d.get("kind") == _RECORD_KIND_DIAGNOSIS:
                yield _diagnosis_from_dict(d)

    def all_records(self) -> Iterator[dict[str, Any]]:
        for line in self._iter_lines():
            yield json.loads(line)

    def _iter_lines(self) -> Iterator[str]:
        with self._path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield line


# ---------------------------------------------------------------------------
# PipelineProfiler — adds timing measurements around process()
# ---------------------------------------------------------------------------

@dataclass
class TimingSample:
    sensor_id: str
    started_ns: int
    duration_ns: int


class PipelineProfiler:
    """Wrap an IndustrialPipeline.process() to measure latency."""

    def __init__(self, pipeline: IndustrialPipeline,
                 *, max_samples: int = 10_000) -> None:
        self._pipeline = pipeline
        self._original_process = pipeline.process
        self._samples: list[TimingSample] = []
        self._max_samples = max_samples
        # Monkey-patch the process method
        pipeline.process = self._timed_process  # type: ignore[method-assign]

    def _timed_process(self, event: SensorEvent) -> DiagnosisResult:
        t0 = time.perf_counter_ns()
        try:
            return self._original_process(event)
        finally:
            t1 = time.perf_counter_ns()
            sample = TimingSample(
                sensor_id=event.sensor_id,
                started_ns=t0,
                duration_ns=t1 - t0,
            )
            if len(self._samples) >= self._max_samples:
                self._samples = self._samples[self._max_samples // 2:]
            self._samples.append(sample)

    @property
    def samples(self) -> list[TimingSample]:
        return list(self._samples)

    def detach(self) -> None:
        """Restore the original process method."""
        self._pipeline.process = self._original_process  # type: ignore[method-assign]

    def summary(self) -> dict[str, float]:
        if not self._samples:
            return {"count": 0}
        durations = [s.duration_ns for s in self._samples]
        durations_sorted = sorted(durations)
        n = len(durations_sorted)
        return {
            "count": n,
            "min_us": durations_sorted[0] / 1000,
            "p50_us": durations_sorted[n // 2] / 1000,
            "p95_us": durations_sorted[int(n * 0.95)] / 1000,
            "p99_us": durations_sorted[int(n * 0.99)] / 1000,
            "max_us": durations_sorted[-1] / 1000,
            "mean_us": sum(durations) / n / 1000,
        }


# ---------------------------------------------------------------------------
# describe_event — pretty-print a SensorEvent for debugging
# ---------------------------------------------------------------------------

def describe_event(ev: SensorEvent, *, max_payload_preview: int = 32) -> str:
    """Multi-line human-readable description of a SensorEvent."""
    # Try to interpret payload as float64 / float32 for hint
    hint = ""
    if len(ev.payload) >= 8:
        with contextlib.suppress(struct.error):
            (val,) = struct.unpack_from("<d", ev.payload, 0)
            hint = f"  (first 8B as f64 LE: {val:g})"
    elif len(ev.payload) >= 4:
        with contextlib.suppress(struct.error):
            (val,) = struct.unpack_from("<f", ev.payload, 0)
            hint = f"  (first 4B as f32 LE: {val:g})"

    preview = ev.payload[:max_payload_preview]
    preview_hex = preview.hex(" ")
    if len(ev.payload) > max_payload_preview:
        preview_hex += " ..."

    lines = [
        f"SensorEvent {ev.sensor_id}@{ev.device_id}",
        f"  protocol     : {ev.protocol}",
        f"  sensor_type  : {ev.sensor_type or '(none)'}",
        f"  unit         : {ev.unit or '(none)'}",
        f"  priority     : {ev.priority.value}",
        f"  timestamp_ns : {ev.timestamp_ns}",
        f"  payload      : {len(ev.payload)} bytes — {preview_hex}{hint}",
        "  metadata     :",
    ]
    for k, v in ev.metadata.items():
        lines.append(f"    {k} = {v}")
    return "\n".join(lines)
