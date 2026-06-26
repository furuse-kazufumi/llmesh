"""PredictivePush — predictive-coding coordinator over a CUSUM control chart.

Neuroscience's *predictive coding* says the brain generates a prediction first and
propagates only the **prediction error**. This coordinator applies that to
industrial alarm explanation:

1. **Warning zone** → *speculatively* generate the incident explanation for the
   anticipated alarm, render it as an llrepr document, and cache it. The
   expensive generation happens **before** the alarm confirms (negative latency).
2. **Alarm confirms** → generate the actual explanation and push only the
   **typed diff** between the speculation and the confirmed report (the prediction
   error). A perfect speculation pushes an empty diff.
3. **Warning clears** (back to nominal) → discard the speculation; nothing is
   pushed (a false alarm avoided, cheaply).
4. **Cold alarm** (no prior warning) → fall back to pushing the full document.

It composes the existing :class:`CUSUMChart` and :class:`LLMExplainer`; it does not
replace them. The explainer is LLM-optional, so this runs air-gapped.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass
from typing import Any

from ..industrial.explainer import AlarmEvent, IncidentReport, LLMExplainer
from ..industrial.spc_engine import SPCResult
from ..llrepr import Document, diff_documents, prediction_error
from .report_repr import incident_to_llrepr
from .transport import InMemorySink, PushFrame, PushSink
from .zones import Zone, classify_cusum_zone


@dataclass
class _Speculation:
    """A pre-generated explanation awaiting confirmation."""

    incident_id: str
    report: IncidentReport
    document: Document


@dataclass
class PredictiveMetrics:
    """Honest-disclosure counters for the predictive-coding loop."""

    speculations_made: int = 0      # warning zone entered → pre-generated
    speculations_used: int = 0      # speculation followed by a real alarm (latency win)
    speculations_wasted: int = 0    # warning cleared without alarm (work discarded)
    diff_pushes: int = 0            # alarm pushed as a typed diff (prediction error)
    full_pushes: int = 0            # cold alarm pushed as a full document
    total_prediction_error: int = 0  # summed diff-op count across diff pushes


@dataclass(frozen=True)
class ObserveResult:
    """Per-observation outcome."""

    zone: Zone
    spc_result: SPCResult
    frame: PushFrame | None = None
    speculated: bool = False
    report: IncidentReport | None = None


class PredictivePush:
    """Predictive-coding push coordinator around a CUSUM-style chart."""

    def __init__(
        self,
        chart: Any,
        *,
        explainer: LLMExplainer | None = None,
        sink: PushSink | None = None,
        sensor_id: str = "",
        h: float | None = None,
        warn_frac: float = 0.5,
        contributing_dims: tuple[str, ...] = (),
        clock=None,
        incident_id_factory=None,
    ) -> None:
        if chart is None:
            raise ValueError("chart is required")
        self._chart = chart
        self._explainer = explainer or LLMExplainer()
        self._sink = sink or InMemorySink()
        self._sensor_id = str(sensor_id)
        self._h = float(h if h is not None else getattr(chart, "h"))
        self._warn_frac = float(warn_frac)
        self._dims = tuple(contributing_dims)
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._mk_id = incident_id_factory or (lambda: uuid.uuid4().hex)
        self._spec: _Speculation | None = None
        self.metrics = PredictiveMetrics()

    @property
    def sink(self) -> PushSink:
        return self._sink

    @property
    def has_speculation(self) -> bool:
        return self._spec is not None

    # ------------------------------------------------------------------

    def observe(self, value: float) -> ObserveResult:
        spc = self._chart.update(value)
        zone = classify_cusum_zone(spc, self._h, self._warn_frac)

        if zone is Zone.ALARM:
            return self._on_alarm(spc)
        if zone is Zone.WARNING:
            return self._on_warning(spc)
        return self._on_nominal(spc)

    def observe_many(self, values) -> list[ObserveResult]:
        return [self.observe(v) for v in values]

    # ------------------------------------------------------------------

    def _on_warning(self, spc: SPCResult) -> ObserveResult:
        if self._spec is None:
            incident_id = self._mk_id()
            event = self._build_event(spc, incident_id, anticipated=True)
            report = self._explainer.explain(event)
            self._spec = _Speculation(incident_id, report, incident_to_llrepr(report))
            self.metrics.speculations_made += 1
            return ObserveResult(Zone.WARNING, spc, speculated=True, report=report)
        # Already speculating — hold the cached prediction.
        return ObserveResult(Zone.WARNING, spc)

    def _on_nominal(self, spc: SPCResult) -> ObserveResult:
        if self._spec is not None:
            # Warning cleared without an alarm: discard the speculation cheaply.
            self._spec = None
            self.metrics.speculations_wasted += 1
        return ObserveResult(Zone.NOMINAL, spc)

    def _on_alarm(self, spc: SPCResult) -> ObserveResult:
        # Reuse the speculative incident id so id churn never inflates the diff.
        incident_id = self._spec.incident_id if self._spec else self._mk_id()
        event = self._build_event(spc, incident_id, anticipated=False)
        report = self._explainer.explain(event)
        actual_doc = incident_to_llrepr(report)

        if self._spec is not None:
            ops = diff_documents(self._spec.document, actual_doc)
            err = prediction_error(ops)
            frame = PushFrame(
                kind="diff",
                incident_id=incident_id,
                ops=ops,
                prediction_error=err,
                meta={"speculated": True, "value": float(spc.value)},
            )
            self.metrics.speculations_used += 1
            self.metrics.diff_pushes += 1
            self.metrics.total_prediction_error += err
        else:
            frame = PushFrame(
                kind="full",
                incident_id=incident_id,
                document=actual_doc.to_dict(),
                meta={"speculated": False, "value": float(spc.value)},
            )
            self.metrics.full_pushes += 1

        self._sink.push(frame)
        self._spec = None
        return ObserveResult(Zone.ALARM, spc, frame=frame, report=report)

    # ------------------------------------------------------------------

    def _build_event(self, spc: SPCResult, incident_id: str, *, anticipated: bool) -> AlarmEvent:
        s_plus = float(spc.extra.get("s_plus", 0.0)) if spc.extra else 0.0
        s_minus = float(spc.extra.get("s_minus", 0.0)) if spc.extra else 0.0
        # Anticipated alarm: project the statistic to the decision interval (the
        # threshold it is trending toward). Confirmed alarm: use the real statistic.
        statistic = self._h if anticipated else max(s_plus, s_minus, abs(spc.value))
        return AlarmEvent(
            incident_id=incident_id,
            timestamp=self._clock().isoformat(),
            sensor_id=self._sensor_id,
            statistic=statistic,
            threshold=self._h,
            metric="cusum",
            contributing_dims=self._dims,
            metadata={
                "s_plus": s_plus,
                "s_minus": s_minus,
                "value": float(spc.value),
                "anticipated": anticipated,
            },
        )
