"""SPC (Statistical Process Control) engines for LLMesh Industrial (v1.5.0).

XbarRChart  — Shewhart Xbar-R control chart for subgroup data
CUSUMChart  — Cumulative Sum chart for individual measurements

Usage example:
    from llmesh.industrial.spc_engine import XbarRChart, CUSUMChart, SPCResult

    xbar = XbarRChart()
    xbar.fit([[2.1, 2.3, 2.0], [1.9, 2.2, 2.1], ...])  # baseline subgroups
    result = xbar.check([2.5, 2.8, 2.4])
    if not result.in_control:
        print(result.violations)

    cusum = CUSUMChart(target=2.0, k=0.5, h=5.0)
    result = cusum.update(2.7)

No external dependencies (pure stdlib math).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from collections.abc import Sequence

# -----------------------------------------------------------------------
# Shewhart control chart constants (ASTM / ISO 8258 table)
# Key: subgroup size n → (A2, D3, D4)
# D3 is 0 for n ≤ 6 (no lower R limit).
# -----------------------------------------------------------------------
_XBAR_R_CONSTANTS: dict[int, tuple[float, float, float]] = {
    2:  (1.880, 0.000, 3.267),
    3:  (1.023, 0.000, 2.575),
    4:  (0.729, 0.000, 2.282),
    5:  (0.577, 0.000, 2.115),
    6:  (0.483, 0.000, 2.004),
    7:  (0.419, 0.076, 1.924),
    8:  (0.373, 0.136, 1.864),
    9:  (0.337, 0.184, 1.816),
    10: (0.308, 0.223, 1.777),
}


@dataclass(frozen=True)
class SPCResult:
    """Result of a single SPC check.

    Attributes
    ----------
    in_control  : True if measurement is within control limits
    value       : the statistic checked (Xbar or individual)
    ucl         : upper control limit
    lcl         : lower control limit
    violations  : list of violation descriptions (empty when in_control)
    extra       : chart-specific extras (R value, CUSUM accumulators, ...)
    """

    in_control: bool
    value: float
    ucl: float
    lcl: float
    violations: tuple[str, ...] = ()
    extra: dict = field(default_factory=dict)


# -----------------------------------------------------------------------
# XbarRChart
# -----------------------------------------------------------------------

class XbarRChart:
    """Shewhart Xbar-R control chart.

    fit() computes control limits from baseline subgroups.
    check() evaluates a new subgroup against those limits.

    Supported subgroup sizes: 2–10.
    """

    def __init__(self) -> None:
        self._n: int = 0
        self._x_bar_bar: float = 0.0
        self._r_bar: float = 0.0
        self._ucl_x: float = 0.0
        self._lcl_x: float = 0.0
        self._ucl_r: float = 0.0
        self._lcl_r: float = 0.0
        self._is_fitted: bool = False

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(self, subgroups: Sequence[Sequence[float]]) -> XbarRChart:
        """Compute control limits from baseline subgroups.

        Parameters
        ----------
        subgroups : list of lists; each inner list is one subgroup.
                    All subgroups must have the same length n (2–10).
        """
        if not subgroups:
            raise ValueError("fit requires at least one subgroup")

        n = len(subgroups[0])
        if not (2 <= n <= 10):
            raise ValueError(f"Subgroup size must be 2–10, got {n}")

        for sg in subgroups:
            if len(sg) != n:
                raise ValueError("All subgroups must have the same size")

        if n not in _XBAR_R_CONSTANTS:
            raise ValueError(f"No constants for subgroup size {n}")

        a2, d3, d4 = _XBAR_R_CONSTANTS[n]

        xbars = [statistics.mean(sg) for sg in subgroups]
        ranges = [max(sg) - min(sg) for sg in subgroups]

        x_bar_bar = statistics.mean(xbars)
        r_bar = statistics.mean(ranges)

        self._n = n
        self._x_bar_bar = x_bar_bar
        self._r_bar = r_bar
        self._ucl_x = x_bar_bar + a2 * r_bar
        self._lcl_x = x_bar_bar - a2 * r_bar
        self._ucl_r = d4 * r_bar
        self._lcl_r = d3 * r_bar
        self._is_fitted = True
        return self

    @property
    def is_fitted(self) -> bool:
        return self._is_fitted

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def check(self, subgroup: Sequence[float]) -> SPCResult:
        """Check whether a new subgroup is in statistical control.

        Returns SPCResult with in_control=False and populated violations
        if either Xbar or R falls outside its control limits.
        """
        if not self._is_fitted:
            raise RuntimeError("XbarRChart.fit() must be called before check()")

        if len(subgroup) != self._n:
            raise ValueError(
                f"Subgroup has {len(subgroup)} observations; expected {self._n}"
            )

        xbar = statistics.mean(subgroup)
        r_val = max(subgroup) - min(subgroup)

        violations: list[str] = []

        if xbar > self._ucl_x:
            violations.append(f"Xbar={xbar:.4f} > UCL_x={self._ucl_x:.4f}")
        elif xbar < self._lcl_x:
            violations.append(f"Xbar={xbar:.4f} < LCL_x={self._lcl_x:.4f}")

        if r_val > self._ucl_r:
            violations.append(f"R={r_val:.4f} > UCL_R={self._ucl_r:.4f}")
        elif self._lcl_r > 0 and r_val < self._lcl_r:
            violations.append(f"R={r_val:.4f} < LCL_R={self._lcl_r:.4f}")

        return SPCResult(
            in_control=len(violations) == 0,
            value=xbar,
            ucl=self._ucl_x,
            lcl=self._lcl_x,
            violations=tuple(violations),
            extra={"r": r_val, "ucl_r": self._ucl_r, "lcl_r": self._lcl_r},
        )

    # Control limit properties (read-only)
    @property
    def ucl_x(self) -> float: return self._ucl_x
    @property
    def lcl_x(self) -> float: return self._lcl_x
    @property
    def x_bar_bar(self) -> float: return self._x_bar_bar
    @property
    def r_bar(self) -> float: return self._r_bar
    @property
    def ucl_r(self) -> float: return self._ucl_r
    @property
    def lcl_r(self) -> float: return self._lcl_r
    @property
    def subgroup_size(self) -> int: return self._n


# -----------------------------------------------------------------------
# CUSUMChart
# -----------------------------------------------------------------------

class CUSUMChart:
    """Two-sided CUSUM chart for individual measurements.

    Detects persistent shifts from the target mean using cumulative sums.

    Parameters
    ----------
    target  : in-control process mean (μ₀)
    k       : allowance (reference value); typically 0.5 × σ
    h       : decision interval; typically 4–5 × σ
    sigma   : process standard deviation estimate (used for z-scoring if provided)
    """

    def __init__(
        self,
        target: float,
        k: float,
        h: float,
        sigma: float | None = None,
    ) -> None:
        if k <= 0:
            raise ValueError(f"k must be positive, got {k}")
        if h <= 0:
            raise ValueError(f"h must be positive, got {h}")
        if sigma is not None and sigma <= 0:
            raise ValueError(f"sigma must be positive, got {sigma}")

        self.target = target
        self.k = k
        self.h = h
        self.sigma = sigma
        self._s_plus: float = 0.0
        self._s_minus: float = 0.0
        self._n_obs: int = 0

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update(self, value: float) -> SPCResult:
        """Process one new observation and return SPCResult.

        The cumulative sums are updated in-place; call reset() to start fresh.
        """
        x = value
        if self.sigma is not None:
            x = (value - self.target) / self.sigma + self.target

        self._s_plus = max(0.0, self._s_plus + (x - self.target - self.k))
        self._s_minus = max(0.0, self._s_minus + (self.target - x - self.k))
        self._n_obs += 1

        violations: list[str] = []
        if self._s_plus > self.h:
            violations.append(f"S+={self._s_plus:.4f} > h={self.h}")
        if self._s_minus > self.h:
            violations.append(f"S-={self._s_minus:.4f} > h={self.h}")

        return SPCResult(
            in_control=len(violations) == 0,
            value=value,
            ucl=self.target + self.h + self.k,
            lcl=self.target - self.h - self.k,
            violations=tuple(violations),
            extra={
                "s_plus": self._s_plus,
                "s_minus": self._s_minus,
                "n_obs": self._n_obs,
            },
        )

    def is_out_of_control(self) -> bool:
        """True if either accumulator has exceeded the decision interval."""
        return self._s_plus > self.h or self._s_minus > self.h

    def reset(self) -> None:
        """Reset both accumulators to zero."""
        self._s_plus = 0.0
        self._s_minus = 0.0
        self._n_obs = 0

    @property
    def s_plus(self) -> float: return self._s_plus
    @property
    def s_minus(self) -> float: return self._s_minus
    @property
    def n_obs(self) -> int: return self._n_obs
