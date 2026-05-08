"""LLMExplainer — natural-language root-cause reports for industrial alarms (v3-N7).

The explainer translates an SPC / MT-method alarm event into a structured
incident report (Markdown + JSON) suitable for SCADA dashboards and
operator notifications. It is **LLM-optional**: with no backend wired in
the explainer produces a deterministic, template-driven report so the
v3-N7 pipeline still runs in air-gapped environments.

Privacy invariant
-----------------
The caller is expected to have already passed any LLM prompt through
:class:`PromptFirewall` and (when required) :class:`PrivacySummarizer`.
``LLMExplainer`` itself never short-circuits the privacy stack — its
``_invoke_llm`` hook is a no-op unless an explicit backend is given.

Output shape
------------
:class:`IncidentReport` carries:

- ``incident_id`` — caller-supplied ULID/UUID
- ``severity``    — one of ``"info" | "warn" | "critical"``
- ``cause``       — short human-readable summary (LLM or template)
- ``suggestion``  — recommended operator action (LLM or template)
- ``markdown``    — rendered Markdown report
- ``payload``     — JSON-serialisable dict mirroring the report
"""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Iterable


_VALID_SEVERITY = ("info", "warn", "critical")


@dataclass(frozen=True)
class AlarmEvent:
    """The minimum information an SPC / MT alarm should carry."""

    incident_id: str
    timestamp: str        # ISO 8601 string; caller controls clock source
    sensor_id: str
    statistic: float
    threshold: float
    metric: str = "mahalanobis"   # "mahalanobis" | "t2" | "cusum" | "xbar"
    contributing_dims: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def deviation(self) -> float:
        """How much the statistic exceeded the threshold."""
        return float(self.statistic - self.threshold)


@dataclass(frozen=True)
class IncidentReport:
    """Structured root-cause report."""

    incident_id: str
    severity: str
    cause: str
    suggestion: str
    markdown: str
    payload: dict[str, Any]


# Type alias for an optional LLM call. Receives a prompt string and
# returns a free-text response. Implementations are expected to wrap
# their own privacy gates.
LLMCallable = Callable[[str], str]


class LLMExplainer:
    """Translate alarm events into operator-facing incident reports.

    Parameters
    ----------
    llm:
        Optional callable that accepts a Markdown prompt and returns the
        LLM-generated explanation. ``None`` (default) selects the
        template-only path.
    severity_map:
        Maps deviation thresholds (in stddev / α multiples) to severity
        labels. The first matching threshold wins. Default uses
        ``critical`` >= 2× threshold, ``warn`` >= 1×, else ``info``.
    """

    _DEFAULT_SEVERITY_MAP: tuple[tuple[float, str], ...] = (
        (2.0, "critical"),
        (1.0, "warn"),
        (0.0, "info"),
    )

    def __init__(
        self,
        llm: LLMCallable | None = None,
        *,
        severity_map: tuple[tuple[float, str], ...] | None = None,
    ) -> None:
        self._llm = llm
        if severity_map is None:
            severity_map = self._DEFAULT_SEVERITY_MAP
        # Sort descending so the first match wins on >= threshold.
        sorted_map = tuple(sorted(severity_map, key=lambda x: -x[0]))
        for _, label in sorted_map:
            if label not in _VALID_SEVERITY:
                raise ValueError(
                    f"severity label {label!r} not in {_VALID_SEVERITY}"
                )
        self._severity_map = sorted_map

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explain(self, event: AlarmEvent) -> IncidentReport:
        severity = self._classify_severity(event)
        cause = self._build_cause(event, severity)
        suggestion = self._build_suggestion(event, severity)
        markdown = self._render_markdown(event, severity, cause, suggestion)
        payload = {
            "incident_id": event.incident_id,
            "severity": severity,
            "cause": cause,
            "suggestion": suggestion,
            "event": asdict(event),
        }
        return IncidentReport(
            incident_id=event.incident_id,
            severity=severity,
            cause=cause,
            suggestion=suggestion,
            markdown=markdown,
            payload=payload,
        )

    def explain_many(self, events: Iterable[AlarmEvent]) -> list[IncidentReport]:
        return [self.explain(e) for e in events]

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _classify_severity(self, event: AlarmEvent) -> str:
        if event.threshold <= 0:
            ratio = event.deviation
        else:
            ratio = event.deviation / event.threshold
        for cutoff, label in self._severity_map:
            if ratio >= cutoff:
                return label
        return "info"

    def _build_cause(self, event: AlarmEvent, severity: str) -> str:
        template = (
            f"{event.metric.upper()} statistic {event.statistic:.3f} exceeded "
            f"threshold {event.threshold:.3f} on sensor {event.sensor_id}"
        )
        if event.contributing_dims:
            template += f"; top contributors: {', '.join(event.contributing_dims)}"
        if self._llm is None:
            return template
        # When an LLM is wired in, call it with a structured prompt.
        prompt = self._llm_prompt(event, severity, "cause")
        try:
            response = self._llm(prompt)
        except Exception:
            return template
        # Trim and bound the response so a verbose model does not flood
        # the report.
        return (response or template).strip()[:1024] or template

    def _build_suggestion(self, event: AlarmEvent, severity: str) -> str:
        template = {
            "info":     "Continue monitoring; no action required.",
            "warn":     "Inspect contributing dimensions; verify sensor calibration.",
            "critical": "Pause line if applicable; engage subject-matter expert.",
        }[severity]
        if self._llm is None:
            return template
        prompt = self._llm_prompt(event, severity, "suggestion")
        try:
            response = self._llm(prompt)
        except Exception:
            return template
        return (response or template).strip()[:1024] or template

    @staticmethod
    def _llm_prompt(event: AlarmEvent, severity: str, kind: str) -> str:
        return (
            f"You are an industrial SCADA root-cause analyst.\n"
            f"Severity: {severity}\n"
            f"Sensor: {event.sensor_id}\n"
            f"Metric: {event.metric}\n"
            f"Statistic / threshold: {event.statistic:.3f} / {event.threshold:.3f}\n"
            f"Contributing dimensions: {', '.join(event.contributing_dims) or '(none)'}\n"
            f"Metadata: {json.dumps(event.metadata, ensure_ascii=False)}\n"
            f"Respond with a single concise paragraph explaining the {kind}."
        )

    @staticmethod
    def _render_markdown(
        event: AlarmEvent,
        severity: str,
        cause: str,
        suggestion: str,
    ) -> str:
        return (
            f"# Incident {event.incident_id}\n\n"
            f"- **Severity:** {severity}\n"
            f"- **Sensor:** `{event.sensor_id}`\n"
            f"- **Metric:** {event.metric}\n"
            f"- **Statistic:** {event.statistic:.3f}\n"
            f"- **Threshold:** {event.threshold:.3f}\n"
            f"- **Deviation:** {event.deviation:.3f}\n"
            f"- **Timestamp:** {event.timestamp}\n\n"
            f"## Cause\n\n{cause}\n\n"
            f"## Suggested Action\n\n{suggestion}\n"
        )
