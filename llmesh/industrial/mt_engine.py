"""MT-method (Mahalanobis-Taguchi) engine for LLMesh Industrial (v1.5.0).

Offline training flow:
    engine = MTEngine()
    engine.fit(normal_data)          # numpy array (N, p)
    engine.save("unit_space.npz")

Real-time inference:
    engine = MTEngine.load("unit_space.npz")
    md = engine.md(sample)           # float — Mahalanobis distance
    if engine.is_anomaly(sample, threshold=3.0):
        ...

MD = sqrt(z^T * R^{-1} * z / p)
where z = standardised vector, R = correlation matrix, p = feature count.

Requires: numpy>=1.26, scipy>=1.12 (in industrial extra).
Graceful degradation: RuntimeError if not installed.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Sequence


def _require_numpy():
    try:
        import numpy as np  # noqa: PLC0415
        return np
    except ImportError as exc:
        raise RuntimeError(
            "numpy is required for MT-method: pip install 'llmesh[industrial]'"
        ) from exc


def _require_scipy_linalg():
    try:
        from scipy import linalg  # noqa: PLC0415
        return linalg
    except ImportError as exc:
        raise RuntimeError(
            "scipy is required for MT-method: pip install 'llmesh[industrial]'"
        ) from exc


@dataclass
class MTEngine:
    """Mahalanobis-Taguchi method engine.

    Attributes
    ----------
    _mean       : feature means of unit space (shape: p,)
    _std        : feature standard deviations of unit space (shape: p,)
    _inv_corr   : inverse correlation matrix of unit space (shape: p, p)
    _n_features : number of features (p)
    device_id   : identifier used when saving/loading unit spaces
    """

    device_id: str = ""
    _mean: object = field(default=None, repr=False)
    _std: object = field(default=None, repr=False)
    _inv_corr: object = field(default=None, repr=False)
    _n_features: int = field(default=0, repr=False)

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(self, data: np.ndarray) -> MTEngine:  # type: ignore[name-defined]
        """Compute unit space from normal data.

        Parameters
        ----------
        data : array-like, shape (N, p)
            N observations of p features. All rows must be normal operating data.

        Returns
        -------
        self (for chaining)
        """
        np = _require_numpy()
        linalg = _require_scipy_linalg()

        data = np.asarray(data, dtype=float)
        if data.ndim != 2:
            raise ValueError(f"data must be 2-D array (N, p), got shape {data.shape}")
        if data.shape[0] < 2:
            raise ValueError("fit requires at least 2 observations")
        if data.shape[1] < 1:
            raise ValueError("fit requires at least 1 feature")

        p = data.shape[1]
        mean = data.mean(axis=0)
        std = data.std(axis=0, ddof=1)

        zero_var = std == 0
        if zero_var.any():
            std = std.copy()
            std[zero_var] = 1.0  # prevent division by zero; those features carry no information

        z = (data - mean) / std          # standardised matrix (N, p)
        # ``np.corrcoef`` on a constant column (zero variance, std forced to 1)
        # divides by zero internally and produces NaN rows/cols. Replace those
        # with the identity contribution so the feature carries no information
        # but the matrix stays invertible.
        with np.errstate(divide="ignore", invalid="ignore"):
            corr = np.corrcoef(z, rowvar=False)
        if not np.all(np.isfinite(corr)):
            corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
            # Restore the diagonal so each feature has unit "self correlation".
            if corr.ndim == 2:
                np.fill_diagonal(corr, 1.0)
            elif corr.ndim == 0:
                corr = np.array([[1.0]])

        if p == 1:
            inv_corr = np.array([[1.0]])
        else:
            try:
                inv_corr = linalg.inv(corr)
            except linalg.LinAlgError:
                # Singular correlation matrix — use pseudo-inverse
                inv_corr = linalg.pinv(corr)

        self._mean = mean
        self._std = std
        self._inv_corr = inv_corr
        self._n_features = p
        return self

    @property
    def is_fitted(self) -> bool:
        return self._mean is not None

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def md(self, sample: np.ndarray | Sequence[float]) -> float:  # type: ignore[name-defined]
        """Compute Mahalanobis distance for one sample vector.

        Parameters
        ----------
        sample : 1-D array-like, length p

        Returns
        -------
        float — Mahalanobis distance (≥0; 1.0 is unit-space baseline)
        """
        if not self.is_fitted:
            raise RuntimeError("MTEngine.fit() must be called before md()")

        np = _require_numpy()
        x = np.asarray(sample, dtype=float).ravel()
        if x.shape[0] != self._n_features:
            raise ValueError(
                f"sample has {x.shape[0]} features, expected {self._n_features}"
            )

        z = (x - self._mean) / self._std
        d_sq = float(z @ self._inv_corr @ z) / self._n_features
        return math.sqrt(max(d_sq, 0.0))

    def is_anomaly(
        self,
        sample: np.ndarray | Sequence[float],  # type: ignore[name-defined]
        threshold: float = 3.0,
    ) -> bool:
        """Return True if MD exceeds threshold (default 3.0 × unit-space baseline)."""
        return self.md(sample) > threshold

    def md_batch(self, data: np.ndarray) -> np.ndarray:  # type: ignore[name-defined]
        """Compute MD for each row in a 2-D array. Returns shape (N,)."""
        np = _require_numpy()
        data = np.asarray(data, dtype=float)
        if data.ndim == 1:
            return np.array([self.md(data)])
        return np.array([self.md(row) for row in data])

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path: str | Path) -> None:
        """Save unit space to a .npz file."""
        if not self.is_fitted:
            raise RuntimeError("MTEngine must be fitted before saving")

        np = _require_numpy()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path,
            mean=self._mean,
            std=self._std,
            inv_corr=self._inv_corr,
            device_id=self.device_id,
        )

    @classmethod
    def load(cls, path: str | Path) -> MTEngine:
        """Load a previously saved unit space from a .npz file."""
        np = _require_numpy()
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Unit space file not found: {path}")

        arrays = np.load(path, allow_pickle=False)
        engine = cls()
        engine._mean = arrays["mean"]
        engine._std = arrays["std"]
        engine._inv_corr = arrays["inv_corr"]
        engine._n_features = int(engine._mean.shape[0])
        engine.device_id = str(arrays["device_id"])
        return engine
