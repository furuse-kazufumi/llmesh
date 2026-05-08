"""IndustrialPipeline — unified SensorEvent → analysis → LLM-ready diagnosis (v2.0.0).

Single integration point that ties together all industrial Phase A–F components:

    SensorEvent  (from Modbus / OPC-UA / MQTT / EtherCAT / 3D adapters)
        │
        ▼
    Pre-processor   (optional payload decoder per protocol)
        │
        ▼
    Analyzer        (MTEngine / XbarRChart / CUSUMChart, opt-in per device)
        │
        ▼
    Diagnosis       (DiagnosisResult: status, severity, summary, evidence)
        │
        ▼
    Subscribers     (callbacks, e.g. forward to MCP stdio LLM diagnose_sensor tool)

Usage::

    pipeline = IndustrialPipeline()

    # MT法を device_id="smt01" に登録（事前訓練済み）
    pipeline.attach_mt(device_id="smt01", engine=trained_mt_engine, threshold=3.0)

    # CUSUMを sensor_id="pressure_01" に登録
    pipeline.attach_cusum(sensor_id="pressure_01",
                           target=101325.0, k=0.5, h=4.0, sigma=100.0)

    # 診断結果を購読
    pipeline.on_diagnosis(lambda d: print(d.status, d.summary))

    # SensorEventを投入（adapter callback から）
    diagnosis = pipeline.process(sensor_event)

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Per-spec analyzers are isolated; one failure does not crash the pipeline.
"""
from __future__ import annotations

import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from llmesh.industrial.sensor_event import SensorEvent

logger = logging.getLogger(__name__)


class DiagnosisStatus(Enum):
    NORMAL = "normal"
    WARNING = "warning"
    ANOMALY = "anomaly"
    CRITICAL = "critical"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class DiagnosisResult:
    """Result of running an industrial analysis on one SensorEvent."""

    sensor_id: str
    device_id: str
    status: DiagnosisStatus
    severity: float                # 0.0 (normal) → 1.0 (critical)
    summary: str                   # LLM-ready one-line description
    evidence: dict[str, Any] = field(default_factory=dict)
    timestamp_ns: int = 0
    source_protocol: str = ""

    def to_prompt_text(self) -> str:
        """Format as a single text block suitable for a privacy-pipeline prompt."""
        lines = [
            f"[{self.status.value.upper()}] {self.sensor_id}@{self.device_id}: {self.summary}",
            f"  protocol={self.source_protocol} severity={self.severity:.2f}",
        ]
        for k, v in self.evidence.items():
            lines.append(f"  {k}={v}")
        return "\n".join(lines)


DiagnosisCallback = Callable[[DiagnosisResult], None]


@dataclass
class _MtSpec:
    engine: Any                           # MTEngine
    threshold: float
    feature_extractor: Callable[[SensorEvent], list[float]] | None = None


@dataclass
class _CusumSpec:
    chart: Any                            # CUSUMChart
    value_extractor: Callable[[SensorEvent], float] | None = None


@dataclass
class _XbarRSpec:
    chart: Any                            # XbarRChart
    subgroup_size: int
    value_extractor: Callable[[SensorEvent], float] | None = None
    _buffer: list[float] = field(default_factory=list)


class IndustrialPipeline:
    """Unified pipeline: SensorEvent → analysis → DiagnosisResult."""

    def __init__(self) -> None:
        self._mt: dict[str, _MtSpec] = {}                  # keyed by device_id
        self._cusum: dict[str, _CusumSpec] = {}            # keyed by sensor_id
        self._xbar_r: dict[str, _XbarRSpec] = {}           # keyed by sensor_id
        self._callbacks: list[DiagnosisCallback] = []

    # ------------------------------------------------------------------
    # Analyzer attachment
    # ------------------------------------------------------------------

    def attach_mt(
        self,
        device_id: str,
        engine: Any,
        *,
        threshold: float = 3.0,
        feature_extractor: Callable[[SensorEvent], list[float]] | None = None,
    ) -> None:
        """Register an MTEngine for *device_id*.

        ``feature_extractor`` returns the feature vector for one SensorEvent.
        If omitted, the event payload is parsed as little-endian float64 array.
        """
        self._mt[device_id] = _MtSpec(
            engine=engine, threshold=threshold,
            feature_extractor=feature_extractor,
        )

    def attach_cusum(
        self,
        sensor_id: str,
        *,
        target: float,
        k: float,
        h: float,
        sigma: float | None = None,
        value_extractor: Callable[[SensorEvent], float] | None = None,
    ) -> None:
        """Register a CUSUMChart for *sensor_id*.

        Lazily imported to avoid a hard dependency on numpy/scipy.
        """
        from llmesh.industrial.spc_engine import CUSUMChart
        chart = CUSUMChart(target=target, k=k, h=h, sigma=sigma)
        self._cusum[sensor_id] = _CusumSpec(
            chart=chart, value_extractor=value_extractor,
        )

    def attach_xbar_r(
        self,
        sensor_id: str,
        *,
        chart: Any,
        subgroup_size: int,
        value_extractor: Callable[[SensorEvent], float] | None = None,
    ) -> None:
        """Register a (pre-fitted) XbarRChart for *sensor_id*."""
        self._xbar_r[sensor_id] = _XbarRSpec(
            chart=chart, subgroup_size=subgroup_size,
            value_extractor=value_extractor,
        )

    def on_diagnosis(self, callback: DiagnosisCallback) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Public process entry
    # ------------------------------------------------------------------

    def process(self, event: SensorEvent) -> DiagnosisResult:
        """Run all attached analyzers; return the highest-severity diagnosis."""
        diagnoses: list[DiagnosisResult] = []

        # MT (device-level multivariate)
        spec_mt = self._mt.get(event.device_id)
        if spec_mt is not None:
            d = self._run_mt(event, spec_mt)
            if d is not None:
                diagnoses.append(d)

        # CUSUM (sensor-level univariate, drift detection)
        spec_cu = self._cusum.get(event.sensor_id)
        if spec_cu is not None:
            d = self._run_cusum(event, spec_cu)
            if d is not None:
                diagnoses.append(d)

        # Xbar-R (sensor-level subgroup control chart)
        spec_xr = self._xbar_r.get(event.sensor_id)
        if spec_xr is not None:
            d = self._run_xbar_r(event, spec_xr)
            if d is not None:
                diagnoses.append(d)

        if not diagnoses:
            result = DiagnosisResult(
                sensor_id=event.sensor_id,
                device_id=event.device_id,
                status=DiagnosisStatus.UNKNOWN,
                severity=0.0,
                summary="no analyzer attached",
                timestamp_ns=event.timestamp_ns,
                source_protocol=event.protocol,
            )
        else:
            result = max(diagnoses, key=lambda d: d.severity)

        self._emit(result)
        return result

    # ------------------------------------------------------------------
    # Internal — analyzers
    # ------------------------------------------------------------------

    def _run_mt(self, event: SensorEvent, spec: _MtSpec) -> DiagnosisResult | None:
        try:
            features = self._extract_features_mt(event, spec)
            md = float(spec.engine.md(features))
            is_anom = md >= spec.threshold
            status = DiagnosisStatus.ANOMALY if is_anom else DiagnosisStatus.NORMAL
            severity = min(md / max(spec.threshold * 2.0, 1e-9), 1.0)
            summary = (
                f"MT-method MD={md:.2f} (threshold={spec.threshold:.2f}); "
                f"{'anomalous' if is_anom else 'within unit space'}"
            )
            return DiagnosisResult(
                sensor_id=event.sensor_id,
                device_id=event.device_id,
                status=status,
                severity=severity,
                summary=summary,
                evidence={"md": md, "threshold": spec.threshold,
                          "features": features},
                timestamp_ns=event.timestamp_ns,
                source_protocol=event.protocol,
            )
        except Exception as exc:
            logger.error("IndustrialPipeline MT error for %s: %s", event.device_id, exc)
            return None

    def _run_cusum(self, event: SensorEvent, spec: _CusumSpec) -> DiagnosisResult | None:
        try:
            value = self._extract_value(event, spec.value_extractor)
            res = spec.chart.update(value)
            in_control = bool(res.in_control)
            status = DiagnosisStatus.NORMAL if in_control else DiagnosisStatus.WARNING
            severity = 0.0 if in_control else 0.6
            summary = (
                f"CUSUM value={value:.3f}; "
                f"{'in control' if in_control else 'out-of-control: ' + ', '.join(res.violations)}"
            )
            return DiagnosisResult(
                sensor_id=event.sensor_id,
                device_id=event.device_id,
                status=status,
                severity=severity,
                summary=summary,
                evidence={"value": value, "extra": dict(res.extra)},
                timestamp_ns=event.timestamp_ns,
                source_protocol=event.protocol,
            )
        except Exception as exc:
            logger.error("IndustrialPipeline CUSUM error for %s: %s", event.sensor_id, exc)
            return None

    def _run_xbar_r(self, event: SensorEvent, spec: _XbarRSpec) -> DiagnosisResult | None:
        try:
            value = self._extract_value(event, spec.value_extractor)
            spec._buffer.append(value)
            if len(spec._buffer) < spec.subgroup_size:
                return None
            subgroup = spec._buffer[: spec.subgroup_size]
            spec._buffer = spec._buffer[spec.subgroup_size:]
            res = spec.chart.check(subgroup)
            in_control = bool(res.in_control)
            status = DiagnosisStatus.NORMAL if in_control else DiagnosisStatus.WARNING
            severity = 0.0 if in_control else 0.5
            summary = (
                f"Xbar-R subgroup={subgroup}; "
                f"{'in control' if in_control else 'out-of-control: ' + ', '.join(res.violations)}"
            )
            return DiagnosisResult(
                sensor_id=event.sensor_id,
                device_id=event.device_id,
                status=status,
                severity=severity,
                summary=summary,
                evidence={"subgroup": subgroup, "extra": dict(res.extra)},
                timestamp_ns=event.timestamp_ns,
                source_protocol=event.protocol,
            )
        except Exception as exc:
            logger.error("IndustrialPipeline Xbar-R error for %s: %s", event.sensor_id, exc)
            return None

    # ------------------------------------------------------------------
    # Internal — feature extraction
    # ------------------------------------------------------------------

    def _extract_features_mt(self, event: SensorEvent, spec: _MtSpec) -> list[float]:
        if spec.feature_extractor is not None:
            return list(spec.feature_extractor(event))
        # Default: payload as little-endian float64 array
        n = len(event.payload) // 8
        return [struct.unpack_from("<d", event.payload, i * 8)[0] for i in range(n)]

    def _extract_value(
        self,
        event: SensorEvent,
        extractor: Callable[[SensorEvent], float] | None,
    ) -> float:
        if extractor is not None:
            return float(extractor(event))
        # Default: try metadata["physical_value"] (EtherCAT), then first float64 of payload
        if "physical_value" in event.metadata:
            return float(event.metadata["physical_value"])
        if len(event.payload) >= 8:
            return float(struct.unpack_from("<d", event.payload, 0)[0])
        if len(event.payload) >= 4:
            return float(struct.unpack_from("<f", event.payload, 0)[0])
        raise ValueError(
            f"cannot extract scalar value from event {event.sensor_id} "
            f"(payload {len(event.payload)} bytes, no physical_value metadata)"
        )

    def _emit(self, diagnosis: DiagnosisResult) -> None:
        for cb in self._callbacks:
            try:
                cb(diagnosis)
            except Exception as exc:
                logger.error("IndustrialPipeline diagnosis callback error: %s", exc)
