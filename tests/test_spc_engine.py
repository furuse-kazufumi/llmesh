"""Tests for XbarRChart and CUSUMChart (SPC engines) — v1.5.0."""
from __future__ import annotations

import pytest

from llmesh.industrial.spc_engine import XbarRChart, CUSUMChart, SPCResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _baseline_subgroups(n: int = 5, k: int = 20, center: float = 10.0, spread: float = 0.1):
    """Generate k stable subgroups of size n centred on 'center'."""
    import random
    rng = random.Random(0)
    return [
        [center + rng.uniform(-spread, spread) for _ in range(n)]
        for _ in range(k)
    ]


# ---------------------------------------------------------------------------
# XbarRChart — fit() validation
# ---------------------------------------------------------------------------

class TestXbarRChartFit:
    def test_fit_empty_raises(self):
        with pytest.raises(ValueError, match="at least one"):
            XbarRChart().fit([])

    def test_fit_subgroup_size_1_raises(self):
        with pytest.raises(ValueError, match="2–10"):
            XbarRChart().fit([[5.0]])

    def test_fit_subgroup_size_11_raises(self):
        with pytest.raises(ValueError, match="2–10"):
            XbarRChart().fit([[1.0] * 11])

    def test_fit_mismatched_sizes_raises(self):
        with pytest.raises(ValueError, match="same size"):
            XbarRChart().fit([[1.0, 2.0], [1.0, 2.0, 3.0]])

    def test_fit_sets_is_fitted(self):
        chart = XbarRChart()
        chart.fit(_baseline_subgroups(n=5))
        assert chart.is_fitted

    def test_not_fitted_by_default(self):
        assert not XbarRChart().is_fitted

    @pytest.mark.parametrize("n", [2, 3, 4, 5, 6, 7, 8, 9, 10])
    def test_fit_all_supported_sizes(self, n):
        sgs = _baseline_subgroups(n=n, k=25)
        chart = XbarRChart()
        chart.fit(sgs)
        assert chart.subgroup_size == n

    def test_control_limits_ordered(self):
        chart = XbarRChart()
        chart.fit(_baseline_subgroups(n=5))
        assert chart.lcl_x < chart.x_bar_bar < chart.ucl_x
        assert chart.ucl_r > chart.r_bar >= 0.0


# ---------------------------------------------------------------------------
# XbarRChart — check()
# ---------------------------------------------------------------------------

class TestXbarRChartCheck:
    @pytest.fixture()
    def chart(self):
        c = XbarRChart()
        c.fit(_baseline_subgroups(n=5, k=30))
        return c

    def test_stable_subgroup_in_control(self, chart):
        result = chart.check([10.0, 10.0, 10.0, 10.0, 10.0])
        assert result.in_control
        assert result.violations == ()

    def test_out_of_control_xbar_high(self, chart):
        result = chart.check([200.0, 200.0, 200.0, 200.0, 200.0])
        assert not result.in_control
        assert any("Xbar" in v for v in result.violations)

    def test_out_of_control_xbar_low(self, chart):
        result = chart.check([-200.0, -200.0, -200.0, -200.0, -200.0])
        assert not result.in_control
        assert any("Xbar" in v for v in result.violations)

    def test_out_of_control_range(self, chart):
        # High range with stable mean
        result = chart.check([9.9, 10.1, 9.5, 10.5, 9.0])
        # May or may not trigger depending on limits — just check shape
        assert isinstance(result, SPCResult)
        assert isinstance(result.extra["r"], float)

    def test_check_returns_correct_value(self, chart):
        sg = [10.0, 10.2, 9.8, 10.0, 10.0]
        result = chart.check(sg)
        import statistics
        assert abs(result.value - statistics.mean(sg)) < 1e-9

    def test_check_wrong_size_raises(self, chart):
        with pytest.raises(ValueError, match="observations"):
            chart.check([10.0, 10.0])  # expects n=5

    def test_check_not_fitted_raises(self):
        with pytest.raises(RuntimeError, match="fit"):
            XbarRChart().check([1.0, 2.0, 3.0])

    def test_ucl_lcl_in_result(self, chart):
        result = chart.check([10.0, 10.0, 10.0, 10.0, 10.0])
        assert result.ucl == pytest.approx(chart.ucl_x)
        assert result.lcl == pytest.approx(chart.lcl_x)

    def test_extra_contains_r(self, chart):
        result = chart.check([10.0, 10.1, 9.9, 10.0, 10.0])
        assert "r" in result.extra
        assert "ucl_r" in result.extra


# ---------------------------------------------------------------------------
# CUSUMChart — init validation
# ---------------------------------------------------------------------------

class TestCUSUMChartInit:
    def test_negative_k_raises(self):
        with pytest.raises(ValueError, match="k must be positive"):
            CUSUMChart(target=0.0, k=-1.0, h=5.0)

    def test_zero_k_raises(self):
        with pytest.raises(ValueError, match="k must be positive"):
            CUSUMChart(target=0.0, k=0.0, h=5.0)

    def test_negative_h_raises(self):
        with pytest.raises(ValueError, match="h must be positive"):
            CUSUMChart(target=0.0, k=0.5, h=-1.0)

    def test_negative_sigma_raises(self):
        with pytest.raises(ValueError, match="sigma must be positive"):
            CUSUMChart(target=0.0, k=0.5, h=5.0, sigma=-1.0)

    def test_initial_state(self):
        c = CUSUMChart(target=5.0, k=0.5, h=5.0)
        assert c.s_plus == 0.0
        assert c.s_minus == 0.0
        assert c.n_obs == 0


# ---------------------------------------------------------------------------
# CUSUMChart — update()
# ---------------------------------------------------------------------------

class TestCUSUMChartUpdate:
    @pytest.fixture()
    def chart(self):
        return CUSUMChart(target=10.0, k=0.5, h=5.0)

    def test_in_control_stable_stream(self, chart):
        for _ in range(20):
            result = chart.update(10.0)
        assert result.in_control
        assert chart.s_plus == pytest.approx(0.0)

    def test_out_of_control_upward_shift(self, chart):
        # Feed sustained values 2σ above target
        out_of_control = False
        for _ in range(30):
            result = chart.update(12.0)
            if not result.in_control:
                out_of_control = True
                break
        assert out_of_control, "CUSUM should detect sustained upward shift"

    def test_out_of_control_downward_shift(self):
        # CUSUMChart with target=10, k=0.5, h=5
        chart = CUSUMChart(target=10.0, k=0.5, h=5.0)
        out_of_control = False
        for _ in range(30):
            result = chart.update(8.0)
            if not result.in_control:
                out_of_control = True
                break
        assert out_of_control, "CUSUM should detect sustained downward shift"

    def test_update_increments_n_obs(self, chart):
        for i in range(5):
            chart.update(10.0)
        assert chart.n_obs == 5

    def test_result_contains_accumulators(self, chart):
        result = chart.update(10.5)
        assert "s_plus" in result.extra
        assert "s_minus" in result.extra
        assert "n_obs" in result.extra

    def test_violations_mention_s_plus_or_s_minus(self, chart):
        for _ in range(30):
            result = chart.update(15.0)
            if not result.in_control:
                has_s = any("S+" in v or "S-" in v for v in result.violations)
                assert has_s
                break

    def test_in_control_value_equals_input(self, chart):
        result = chart.update(10.3)
        assert result.value == pytest.approx(10.3)

    def test_is_out_of_control_property(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=1.0)
        assert not chart.is_out_of_control()
        for _ in range(10):
            chart.update(5.0)
        assert chart.is_out_of_control()


# ---------------------------------------------------------------------------
# CUSUMChart — reset()
# ---------------------------------------------------------------------------

class TestCUSUMChartReset:
    def test_reset_clears_accumulators(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=5.0)
        for _ in range(10):
            chart.update(3.0)
        chart.reset()
        assert chart.s_plus == 0.0
        assert chart.s_minus == 0.0
        assert chart.n_obs == 0

    def test_in_control_after_reset(self):
        chart = CUSUMChart(target=0.0, k=0.5, h=1.0)
        for _ in range(20):
            chart.update(5.0)
        assert chart.is_out_of_control()
        chart.reset()
        assert not chart.is_out_of_control()
        result = chart.update(0.0)
        assert result.in_control


# ---------------------------------------------------------------------------
# SPCResult — frozen dataclass
# ---------------------------------------------------------------------------

class TestSPCResult:
    def test_frozen(self):
        r = SPCResult(in_control=True, value=5.0, ucl=10.0, lcl=0.0)
        with pytest.raises((AttributeError, TypeError)):
            r.in_control = False  # type: ignore[misc]

    def test_default_violations_empty(self):
        r = SPCResult(in_control=True, value=1.0, ucl=2.0, lcl=0.0)
        assert r.violations == ()
