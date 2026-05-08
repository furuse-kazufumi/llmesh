"""Tests for ExplainedCUSUM — v3-N7 self-narrating CUSUM."""
from __future__ import annotations

import datetime as dt
import itertools

import pytest

from llmesh.industrial.explained_cusum import ExplainedCUSUM, ExplainedSPCResult
from llmesh.industrial.explainer import LLMExplainer
from llmesh.industrial.spc_engine import CUSUMChart


def _fixed_clock(seq=("2026-05-08T10:00:00+00:00",)):
    it = itertools.cycle(seq)
    return lambda: dt.datetime.fromisoformat(next(it))


def _fixed_id(seq=("inc-001",)):
    it = itertools.cycle(seq)
    return lambda: next(it)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_chart_required(self):
        with pytest.raises(ValueError):
            ExplainedCUSUM(None)

    def test_default_explainer(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=4.0)
        ec = ExplainedCUSUM(chart)
        assert isinstance(ec.explainer, LLMExplainer)

    def test_sensor_and_dims_propagate(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=4.0)
        ec = ExplainedCUSUM(chart, sensor_id="line_a", contributing_dims=("temp",))
        assert ec._sensor_id == "line_a"
        assert ec._dims == ("temp",)


# ---------------------------------------------------------------------------
# In-control behaviour
# ---------------------------------------------------------------------------

class TestInControl:
    def test_in_control_returns_no_report(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=4.0)
        ec = ExplainedCUSUM(chart)
        out = ec.update(0.0)
        assert out.in_control is True
        assert out.report is None
        assert out.incident_id == ""

    def test_underlying_spc_result_passed_through(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=4.0)
        ec = ExplainedCUSUM(chart)
        out = ec.update(0.0)
        # The chart's SPCResult is exposed as-is.
        assert out.spc_result.value == 0.0
        assert out.violations == ()


# ---------------------------------------------------------------------------
# Out-of-control behaviour
# ---------------------------------------------------------------------------

class TestOutOfControl:
    def test_alarm_emits_report(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=2.0)
        ec = ExplainedCUSUM(
            chart,
            sensor_id="dnp3:plant_a",
            contributing_dims=("temp_in", "vibration_z"),
            clock=_fixed_clock(),
            incident_id_factory=_fixed_id(),
        )
        # Push the upper-arm cumulative sum past h.
        out = None
        for _ in range(5):
            out = ec.update(2.0)
        assert out is not None
        assert out.in_control is False
        assert out.report is not None
        assert out.incident_id == "inc-001"
        assert out.report.payload["event"]["sensor_id"] == "dnp3:plant_a"
        assert "temp_in" in out.report.cause

    def test_metric_label_is_cusum(self):
        chart = CUSUMChart(target=0.0, k=0.1, h=0.5)
        ec = ExplainedCUSUM(chart, sensor_id="line_a")
        out = None
        for _ in range(20):
            out = ec.update(1.0)
        assert out.report is not None
        assert out.report.payload["event"]["metric"] == "cusum"

    def test_threshold_passes_chart_h(self):
        chart = CUSUMChart(target=0.0, k=0.1, h=0.5)
        ec = ExplainedCUSUM(chart, sensor_id="line_a")
        out = None
        for _ in range(20):
            out = ec.update(1.0)
        assert out.report.payload["event"]["threshold"] == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# update_many
# ---------------------------------------------------------------------------

class TestUpdateMany:
    def test_returns_per_value_results(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=2.0)
        ec = ExplainedCUSUM(chart)
        outs = ec.update_many([0.0, 0.0, 2.0, 2.0, 2.0, 2.0])
        assert len(outs) == 6
        # First two are in control; later ones cross the threshold.
        assert outs[0].in_control
        assert any(not o.in_control for o in outs)


# ---------------------------------------------------------------------------
# Custom explainer
# ---------------------------------------------------------------------------

class TestCustomExplainer:
    def test_custom_llm_used(self):
        called = []
        def llm(prompt: str) -> str:
            called.append(prompt)
            return "Sensor drift due to upstream coolant fluctuation."
        explainer = LLMExplainer(llm=llm)
        chart = CUSUMChart(target=0.0, k=0.5, h=2.0)
        ec = ExplainedCUSUM(chart, explainer=explainer, sensor_id="line_a")
        for _ in range(5):
            out = ec.update(2.0)
        assert out.report is not None
        # LLM was invoked at least once for the report.
        assert called
        assert "coolant" in out.report.cause.lower()
