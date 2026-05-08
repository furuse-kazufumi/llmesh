"""Tests for UnifiedSPC — v3-N15 multimodal sensor + VLM-text monitor."""
from __future__ import annotations

import pytest

from llmesh.industrial.multimodal_spc import UnifiedSPC, UnifiedSPCResult
from llmesh.industrial.spc_engine import CUSUMChart, XbarRChart


def _fitted_xbar(center: float = 2.0, n: int = 3) -> XbarRChart:
    chart = XbarRChart()
    # 30 baseline subgroups of size n, slight variation around center.
    subgroups = [[center, center + 0.05, center - 0.05][:n] for _ in range(30)]
    chart.fit(subgroups)
    return chart


def _cusum(target: float = 0.0) -> CUSUMChart:
    return CUSUMChart(target=target, k=0.5, h=4.0)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_invalid_mode(self):
        with pytest.raises(ValueError):
            UnifiedSPC(_fitted_xbar(), _fitted_xbar(), mode="bogus")

    def test_negative_weight_rejected(self):
        with pytest.raises(ValueError):
            UnifiedSPC(_fitted_xbar(), _fitted_xbar(),
                       mode="weighted", sensor_weight=-1.0)

    def test_zero_weights_rejected(self):
        with pytest.raises(ValueError):
            UnifiedSPC(_fitted_xbar(), _fitted_xbar(),
                       mode="weighted", sensor_weight=0.0, text_weight=0.0)

    def test_negative_threshold_rejected(self):
        with pytest.raises(ValueError):
            UnifiedSPC(_fitted_xbar(), _fitted_xbar(), threshold=-0.1)


# ---------------------------------------------------------------------------
# OR mode (default)
# ---------------------------------------------------------------------------

class TestOrMode:
    def test_both_in_control_passes(self):
        spc = UnifiedSPC(_fitted_xbar(), _fitted_xbar(), mode="or")
        out = spc.update([2.0, 2.0, 2.0], [2.0, 2.0, 2.0])
        assert out.in_control is True

    def test_either_alarms(self):
        spc = UnifiedSPC(_fitted_xbar(), _fitted_xbar(), mode="or")
        out = spc.update([2.0, 2.0, 2.0], [99.0, 99.0, 99.0])
        assert out.in_control is False

    def test_returns_both_subresults(self):
        spc = UnifiedSPC(_fitted_xbar(), _fitted_xbar(), mode="or")
        out = spc.update([2.0, 2.0, 2.0], [99.0, 99.0, 99.0])
        assert out.sensor_result.in_control is True
        assert out.text_result.in_control is False
        assert out.mode == "or"


# ---------------------------------------------------------------------------
# AND mode
# ---------------------------------------------------------------------------

class TestAndMode:
    def test_one_alarm_still_in_control(self):
        spc = UnifiedSPC(_fitted_xbar(), _fitted_xbar(), mode="and")
        out = spc.update([2.0, 2.0, 2.0], [99.0, 99.0, 99.0])
        # Only text channel alarms — AND mode → still in control.
        assert out.in_control is True

    def test_both_alarm_triggers(self):
        spc = UnifiedSPC(_fitted_xbar(), _fitted_xbar(), mode="and")
        out = spc.update([99.0, 99.0, 99.0], [99.0, 99.0, 99.0])
        assert out.in_control is False


# ---------------------------------------------------------------------------
# Weighted mode
# ---------------------------------------------------------------------------

class TestWeightedMode:
    def test_below_threshold_in_control(self):
        spc = UnifiedSPC(
            _fitted_xbar(), _fitted_xbar(),
            mode="weighted",
            sensor_weight=0.4, text_weight=0.4, threshold=0.5,
        )
        out = spc.update([2.0, 2.0, 2.0], [99.0, 99.0, 99.0])
        # text alarm contributes 0.4 — under 0.5 threshold.
        assert out.in_control is True
        assert out.score == pytest.approx(0.4)

    def test_above_threshold_alarms(self):
        spc = UnifiedSPC(
            _fitted_xbar(), _fitted_xbar(),
            mode="weighted",
            sensor_weight=0.6, text_weight=0.6, threshold=0.5,
        )
        out = spc.update([2.0, 2.0, 2.0], [99.0, 99.0, 99.0])
        # text alarm contributes 0.6 — above 0.5 threshold.
        assert out.in_control is False
        assert out.score == pytest.approx(0.6)


# ---------------------------------------------------------------------------
# Mixed-chart usage
# ---------------------------------------------------------------------------

class TestMixedCharts:
    def test_xbar_plus_cusum(self):
        spc = UnifiedSPC(_fitted_xbar(), _cusum(), mode="or")
        out = spc.update([2.0, 2.0, 2.0], 0.1)
        assert isinstance(out, UnifiedSPCResult)

    def test_unsupported_chart_raises(self):
        class _Bogus:
            pass
        spc = UnifiedSPC(_fitted_xbar(), _fitted_xbar())
        # Replace internal text chart with bogus to trigger TypeError.
        spc._text = _Bogus()
        with pytest.raises(TypeError):
            spc.update([2.0, 2.0, 2.0], [0.0])


# ---------------------------------------------------------------------------
# Violations propagation
# ---------------------------------------------------------------------------

class TestViolations:
    def test_violations_tagged_per_channel(self):
        spc = UnifiedSPC(_fitted_xbar(), _fitted_xbar(), mode="or")
        out = spc.update([2.0, 2.0, 2.0], [99.0, 99.0, 99.0])
        assert any(v.startswith("text:") for v in out.violations)
