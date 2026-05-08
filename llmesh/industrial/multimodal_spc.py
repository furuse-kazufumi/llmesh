"""Multimodal Statistical Process Control — v3-N15.

Unifies sensor time-series (numerical) and VLM-derived text features
(numerical scores produced by ``VLMFeatureExtractor``) into a single
SPC monitoring stream.

Design
------
``UnifiedSPC`` aggregates two parallel SPC channels:

- a **sensor** channel monitored by an existing :class:`XbarRChart`
  (subgroup data) or :class:`CUSUMChart` (individuals).
- a **text-feature** channel monitored by a separate chart of either
  type.

The combination mode controls how the two verdicts are reduced:

- ``"and"``   — out-of-control only when **both** channels alarm
                (low false-alarm, high specificity).
- ``"or"``    — out-of-control when **either** channel alarms
                (high sensitivity, default).
- ``"weighted"`` — weighted vote: ``sensor_w * (1 - ic_s) +
                  text_w * (1 - ic_t) > threshold`` triggers an alarm.

The class is **pure-stdlib** — it depends only on the existing
``spc_engine`` charts.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .spc_engine import CUSUMChart, SPCResult, XbarRChart


_VALID_MODES = ("and", "or", "weighted")


@dataclass(frozen=True)
class UnifiedSPCResult:
    """Combined verdict from sensor + text-feature SPC channels."""

    in_control: bool
    sensor_result: SPCResult
    text_result: SPCResult
    mode: str
    score: float = 0.0     # weighted-mode score (0 otherwise)
    violations: list[str] = field(default_factory=list)


class UnifiedSPC:
    """Two-channel SPC monitor combining sensor + text features.

    Parameters
    ----------
    sensor_chart:
        An existing :class:`XbarRChart` or :class:`CUSUMChart`. Must be
        already ``fit`` (Xbar-R) or initialized (CUSUM).
    text_chart:
        Same — for the VLM-derived text-feature channel.
    mode:
        ``"and"`` | ``"or"`` | ``"weighted"``. Defaults to ``"or"``.
    sensor_weight, text_weight:
        Used only when ``mode="weighted"``. The combined "out of
        control" score is ``sensor_weight * out_s + text_weight * out_t``
        (each ``out_*`` is 0 or 1). An alarm fires when the score
        exceeds ``threshold``.
    threshold:
        Score threshold for ``"weighted"`` mode. Default ``0.5``.
    """

    def __init__(
        self,
        sensor_chart: XbarRChart | CUSUMChart,
        text_chart: XbarRChart | CUSUMChart,
        *,
        mode: str = "or",
        sensor_weight: float = 0.5,
        text_weight: float = 0.5,
        threshold: float = 0.5,
    ) -> None:
        if mode not in _VALID_MODES:
            raise ValueError(f"mode must be one of {_VALID_MODES}")
        if mode == "weighted":
            if sensor_weight < 0 or text_weight < 0:
                raise ValueError("weights must be non-negative")
            if sensor_weight + text_weight == 0:
                raise ValueError("at least one weight must be positive")
        if threshold < 0:
            raise ValueError("threshold must be non-negative")
        self._sensor = sensor_chart
        self._text = text_chart
        self._mode = mode
        self._sensor_w = float(sensor_weight)
        self._text_w = float(text_weight)
        self._threshold = float(threshold)

    @property
    def mode(self) -> str:
        return self._mode

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(
        self,
        sensor_value: Any,
        text_value: Any,
    ) -> UnifiedSPCResult:
        """Push one new observation through both channels.

        ``sensor_value`` and ``text_value`` must each match the input
        shape expected by their respective chart:

        - ``XbarRChart.check(subgroup_list)``
        - ``CUSUMChart.update(individual_value)``
        """
        s_res = self._dispatch(self._sensor, sensor_value)
        t_res = self._dispatch(self._text, text_value)
        return self._combine(s_res, t_res)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _dispatch(chart, value) -> SPCResult:
        if isinstance(chart, XbarRChart):
            return chart.check(list(value))
        if isinstance(chart, CUSUMChart):
            return chart.update(float(value))
        raise TypeError(
            "chart must be XbarRChart or CUSUMChart, got "
            f"{type(chart).__name__}"
        )

    def _combine(self, s: SPCResult, t: SPCResult) -> UnifiedSPCResult:
        violations: list[str] = []
        for tag, res in (("sensor", s), ("text", t)):
            for v in res.violations:
                violations.append(f"{tag}:{v}")

        if self._mode == "and":
            in_ctrl = s.in_control or t.in_control  # alarm only if BOTH fail
            return UnifiedSPCResult(
                in_control=in_ctrl,
                sensor_result=s,
                text_result=t,
                mode=self._mode,
                violations=violations,
            )
        if self._mode == "or":
            in_ctrl = s.in_control and t.in_control
            return UnifiedSPCResult(
                in_control=in_ctrl,
                sensor_result=s,
                text_result=t,
                mode=self._mode,
                violations=violations,
            )
        # weighted
        out_s = 0.0 if s.in_control else 1.0
        out_t = 0.0 if t.in_control else 1.0
        score = self._sensor_w * out_s + self._text_w * out_t
        return UnifiedSPCResult(
            in_control=score <= self._threshold,
            sensor_result=s,
            text_result=t,
            mode=self._mode,
            score=score,
            violations=violations,
        )
