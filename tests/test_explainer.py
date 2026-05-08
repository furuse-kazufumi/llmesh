"""Tests for LLMExplainer — v3-N7 incident reports."""
from __future__ import annotations

import json

import pytest

from llmesh.industrial.explainer import (
    AlarmEvent,
    IncidentReport,
    LLMExplainer,
)


def _ev(**kw) -> AlarmEvent:
    base = dict(
        incident_id="inc-001",
        timestamp="2026-05-08T12:00:00Z",
        sensor_id="dnp3:01:0",
        statistic=4.2,
        threshold=3.0,
        metric="mahalanobis",
    )
    base.update(kw)
    return AlarmEvent(**base)


# ---------------------------------------------------------------------------
# Severity classification
# ---------------------------------------------------------------------------

class TestSeverity:
    def test_info_below_threshold(self):
        # statistic == threshold → ratio 0 → info
        e = _ev(statistic=3.0, threshold=3.0)
        r = LLMExplainer().explain(e)
        assert r.severity == "info"

    def test_warn_one_x_over(self):
        e = _ev(statistic=6.0, threshold=3.0)  # ratio = 1.0
        r = LLMExplainer().explain(e)
        assert r.severity == "warn"

    def test_critical_two_x_over(self):
        e = _ev(statistic=9.0, threshold=3.0)  # ratio = 2.0
        r = LLMExplainer().explain(e)
        assert r.severity == "critical"

    def test_zero_threshold_uses_raw_deviation(self):
        e = _ev(statistic=2.5, threshold=0.0)
        r = LLMExplainer().explain(e)
        assert r.severity == "critical"

    def test_invalid_severity_label_rejected(self):
        with pytest.raises(ValueError):
            LLMExplainer(severity_map=((0.0, "weird_severity"),))


# ---------------------------------------------------------------------------
# Template path (no LLM)
# ---------------------------------------------------------------------------

class TestTemplatePath:
    def test_cause_includes_metric_and_sensor(self):
        e = _ev()
        r = LLMExplainer().explain(e)
        assert "MAHALANOBIS" in r.cause.upper()
        assert "dnp3:01:0" in r.cause
        assert str(round(e.statistic, 3)) in r.cause

    def test_contributing_dims_listed(self):
        e = _ev(contributing_dims=("temp_in", "vibration_axis_2"))
        r = LLMExplainer().explain(e)
        assert "temp_in" in r.cause
        assert "vibration_axis_2" in r.cause

    def test_suggestion_per_severity(self):
        info = LLMExplainer().explain(_ev(statistic=3.0, threshold=3.0)).suggestion
        warn = LLMExplainer().explain(_ev(statistic=6.0, threshold=3.0)).suggestion
        critical = LLMExplainer().explain(_ev(statistic=9.0, threshold=3.0)).suggestion
        assert info != warn != critical


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------

class TestLLMPath:
    def test_llm_response_used_for_cause_and_suggestion(self):
        captured = []
        def llm(prompt):
            captured.append(prompt)
            return "Custom diagnostic narrative."
        ex = LLMExplainer(llm=llm)
        r = ex.explain(_ev())
        assert r.cause == "Custom diagnostic narrative."
        assert r.suggestion == "Custom diagnostic narrative."
        # The LLM was called twice (cause + suggestion), each with a
        # structured prompt that mentions the alarm.
        assert len(captured) == 2
        for p in captured:
            assert "dnp3:01:0" in p
            assert "Severity:" in p

    def test_llm_failure_falls_back_to_template(self):
        def boom(prompt):
            raise RuntimeError("network down")
        ex = LLMExplainer(llm=boom)
        r = ex.explain(_ev())
        assert r.cause  # not empty
        assert "MAHALANOBIS" in r.cause.upper()

    def test_llm_response_is_bounded(self):
        long_text = "x" * 5000
        ex = LLMExplainer(llm=lambda p: long_text)
        r = ex.explain(_ev())
        assert len(r.cause) <= 1024
        assert len(r.suggestion) <= 1024

    def test_empty_llm_response_falls_back(self):
        ex = LLMExplainer(llm=lambda p: "   ")
        r = ex.explain(_ev())
        assert r.cause  # template fallback
        assert "MAHALANOBIS" in r.cause.upper()


# ---------------------------------------------------------------------------
# Output shape
# ---------------------------------------------------------------------------

class TestOutputShape:
    def test_report_payload_is_json_serialisable(self):
        ex = LLMExplainer()
        r = ex.explain(_ev())
        # Round-trip through json to be sure.
        s = json.dumps(r.payload)
        assert json.loads(s)["incident_id"] == "inc-001"

    def test_markdown_contains_sections(self):
        ex = LLMExplainer()
        r = ex.explain(_ev())
        assert r.markdown.startswith("# Incident inc-001")
        assert "## Cause" in r.markdown
        assert "## Suggested Action" in r.markdown

    def test_explain_many(self):
        ex = LLMExplainer()
        rs = ex.explain_many([_ev(incident_id="a"), _ev(incident_id="b")])
        assert [r.incident_id for r in rs] == ["a", "b"]
