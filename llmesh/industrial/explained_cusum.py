"""ExplainedCUSUM — v3-N7 self-narrating CUSUM control chart.

Wraps :class:`CUSUMChart` and emits an :class:`IncidentReport` (via
:class:`LLMExplainer`) whenever an alarm fires. The chart still returns
the original :class:`SPCResult` so existing pipelines remain compatible —
the ``ExplainedSPCResult`` adds an optional ``report`` field.

Why this composition?
---------------------
v3-N7's "explainable SCADA" theme demands that each control-limit
violation comes paired with a human-readable cause + suggested action.
Doing this at the chart layer (rather than in the alarm router) lets
multiple downstream sinks (UI / Slack / SCADA HMI) reuse the same
report. The explainer is LLM-optional, so air-gapped deployments still
get template-rendered Markdown.

Privacy invariant
-----------------
The chart never inspects raw prompt content. Only sensor metadata
(``sensor_id``, the CUSUM statistic, contributing dimensions) is
forwarded to the explainer. Callers wiring an LLM into the explainer
remain responsible for routing the prompt through ``PromptFirewall``
before it reaches a model — this module does not bypass that pipeline.
"""
from __future__ import annotations

import datetime as _dt
import uuid
from dataclasses import dataclass
from typing import Iterable

from .explainer import AlarmEvent, IncidentReport, LLMExplainer
from .spc_engine import CUSUMChart, SPCResult


@dataclass(frozen=True)
class ExplainedSPCResult:
    """SPC verdict augmented with an optional incident report."""

    spc_result: SPCResult
    report: IncidentReport | None = None
    incident_id: str = ""

    @property
    def in_control(self) -> bool:
        return self.spc_result.in_control

    @property
    def violations(self) -> tuple[str, ...]:
        return self.spc_result.violations


class ExplainedCUSUM:
    """:class:`CUSUMChart` that emits root-cause reports on each alarm.

    Parameters
    ----------
    chart:
        A pre-configured :class:`CUSUMChart`. Existing CUSUM state is
        preserved.
    explainer:
        Either an :class:`LLMExplainer` or ``None`` (default). When
        ``None`` a template-only explainer is constructed lazily.
    sensor_id:
        Identifier embedded in every produced :class:`AlarmEvent`.
    contributing_dims:
        Optional list of dimension labels that the explainer can mention
        when describing the cause. Often a static list (e.g. the
        upstream feature names that feed the value being charted).
    clock:
        Callable returning a UTC :class:`datetime.datetime`. Override
        in tests for deterministic timestamps.
    incident_id_factory:
        Callable producing unique incident IDs. Defaults to
        ``uuid.uuid4().hex``.
    """

    def __init__(
        self,
        chart: CUSUMChart,
        *,
        explainer: LLMExplainer | None = None,
        sensor_id: str = "",
        contributing_dims: Iterable[str] = (),
        clock=None,
        incident_id_factory=None,
    ) -> None:
        if chart is None:
            raise ValueError("chart is required")
        self._chart = chart
        self._explainer = explainer or LLMExplainer()
        self._sensor_id = str(sensor_id)
        self._dims = tuple(contributing_dims)
        self._clock = clock or (lambda: _dt.datetime.now(_dt.timezone.utc))
        self._mk_id = incident_id_factory or (lambda: uuid.uuid4().hex)

    @property
    def chart(self) -> CUSUMChart:
        return self._chart

    @property
    def explainer(self) -> LLMExplainer:
        return self._explainer

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, value: float) -> ExplainedSPCResult:
        spc = self._chart.update(value)
        if spc.in_control:
            return ExplainedSPCResult(spc_result=spc)
        incident_id = self._mk_id()
        event = self._build_event(spc, incident_id)
        report = self._explainer.explain(event)
        return ExplainedSPCResult(
            spc_result=spc,
            report=report,
            incident_id=incident_id,
        )

    def update_many(self, values: Iterable[float]) -> list[ExplainedSPCResult]:
        return [self.update(v) for v in values]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_event(self, spc: SPCResult, incident_id: str) -> AlarmEvent:
        # CUSUM "statistic" of interest for the explainer is whichever
        # arm tripped. Pick the larger of (S+, S-) so the report
        # references the actual deviation magnitude.
        s_plus = float(spc.extra.get("s_plus", 0.0)) if spc.extra else 0.0
        s_minus = float(spc.extra.get("s_minus", 0.0)) if spc.extra else 0.0
        statistic = max(s_plus, s_minus, abs(spc.value))
        return AlarmEvent(
            incident_id=incident_id,
            timestamp=self._clock().isoformat(),
            sensor_id=self._sensor_id,
            statistic=statistic,
            threshold=float(self._chart.h),
            metric="cusum",
            contributing_dims=self._dims,
            metadata={
                "s_plus": s_plus,
                "s_minus": s_minus,
                "value": float(spc.value),
                "violations": list(spc.violations),
            },
        )
