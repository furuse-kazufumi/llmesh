"""Hotelling's T² multivariate control chart — v3-N11.

A control chart for **vector-valued** observations.  Given a reference
window of normal data, T² monitors how far each new sample is from the
reference centroid in covariance-aware distance.

T²_i = (x_i − μ)ᵀ Σ⁻¹ (x_i − μ)

Anomalies are flagged when ``T² > UCL`` where the upper control limit
defaults to a chi-square quantile.  This is the multivariate analogue of
``XbarRChart`` in :mod:`llmesh.industrial.spc_engine`.

The chart is independent of :class:`MTEngine`: T² uses the **covariance**
matrix while MT uses the correlation matrix and per-feature scaling.
Both are useful — they answer different questions.

Requires: numpy>=1.26 (industrial extra). Falls back to RuntimeError
when numpy is missing.
"""
from __future__ import annotations

from dataclasses import dataclass

from .mt_engine import _require_numpy


@dataclass(frozen=True)
class T2Decision:
    """Result of scoring a single observation."""

    statistic: float
    in_control: bool
    ucl: float


@dataclass(frozen=True)
class T2BatchDecision:
    """Result of scoring a batch."""

    statistics: object       # numpy array, shape (n,)
    in_control: object       # numpy bool array, shape (n,)
    ucl: float


class HotellingT2Chart:
    """Multivariate Hotelling T² control chart.

    Parameters
    ----------
    ucl:
        Upper control limit on the T² statistic. If ``None``, defaults
        to ``chi2_ppf(1 - alpha, dof=n_features)``-style approximation:
        ``ucl = -2 * log(alpha) * (n_features / 2)``. Override when you
        have empirical UCLs from your reference data.
    alpha:
        False-alarm probability used when ``ucl`` is not supplied.
        Defaults to ``0.005`` (≈ 99.5 % control limit).
    """

    def __init__(
        self,
        *,
        ucl: float | None = None,
        alpha: float = 0.005,
    ) -> None:
        if alpha <= 0 or alpha >= 1:
            raise ValueError("alpha must be in (0, 1)")
        self._ucl_override = ucl
        self._alpha = float(alpha)
        self._mean = None
        self._inv_cov = None
        self._n_features = 0

    # ------------------------------------------------------------------
    # Fit
    # ------------------------------------------------------------------

    def fit(self, reference) -> "HotellingT2Chart":
        """Compute centroid and inverse covariance from reference data."""
        np = _require_numpy()
        ref = np.asarray(reference, dtype=np.float64)
        if ref.ndim != 2 or ref.shape[0] < 2:
            raise ValueError("reference must be 2-D with at least 2 rows")
        self._mean = ref.mean(axis=0)
        # Use ddof=1 for sample covariance.
        cov = np.cov(ref, rowvar=False, ddof=1)
        if cov.ndim == 0:  # univariate fall-through
            cov = np.array([[float(cov)]])
        # Tikhonov ridge for numerical stability with rank-deficient data.
        eps = 1e-9 * np.trace(cov) / max(cov.shape[0], 1)
        self._inv_cov = np.linalg.pinv(cov + eps * np.eye(cov.shape[0]))
        self._n_features = ref.shape[1]
        return self

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def n_features(self) -> int:
        return self._n_features

    @property
    def ucl(self) -> float:
        if self._ucl_override is not None:
            return float(self._ucl_override)
        # Quick-and-dirty asymptotic χ² upper-tail approximation.
        # See e.g. Montgomery, "Statistical Quality Control".
        import math
        # For high p, tail of χ²(p) ≈ p + sqrt(2p) * z_(1-alpha)
        # where z_(1-alpha) ≈ sqrt(-2 ln alpha) for small alpha.
        p = max(self._n_features, 1)
        z = math.sqrt(-2.0 * math.log(self._alpha))
        return float(p + math.sqrt(2.0 * p) * z)

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def score(self, observation) -> T2Decision:
        """Score a single ``p``-dimensional observation."""
        np = _require_numpy()
        if self._mean is None or self._inv_cov is None:
            raise ValueError("chart must be fit before scoring")
        x = np.asarray(observation, dtype=np.float64).reshape(-1)
        if x.shape[0] != self._n_features:
            raise ValueError(
                f"observation has {x.shape[0]} features, expected {self._n_features}"
            )
        d = x - self._mean
        t2 = float(d @ self._inv_cov @ d)
        return T2Decision(statistic=t2, in_control=t2 <= self.ucl, ucl=self.ucl)

    def score_batch(self, batch) -> T2BatchDecision:
        """Vectorized scoring over a batch ``(n, p)``."""
        np = _require_numpy()
        if self._mean is None or self._inv_cov is None:
            raise ValueError("chart must be fit before scoring")
        arr = np.asarray(batch, dtype=np.float64)
        if arr.ndim != 2 or arr.shape[1] != self._n_features:
            raise ValueError(
                f"batch has shape {arr.shape}, expected (n, {self._n_features})"
            )
        d = arr - self._mean
        t2 = np.einsum("ni,ij,nj->n", d, self._inv_cov, d)
        u = self.ucl
        return T2BatchDecision(statistics=t2, in_control=t2 <= u, ucl=u)
