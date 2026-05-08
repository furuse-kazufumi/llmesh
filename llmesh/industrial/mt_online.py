"""Online MT-method inference engine — v3-N11 / µs anomaly detection.

Wraps the existing :class:`llmesh.industrial.mt_engine.MTEngine` with a
**streaming** interface so DVS / sensor data can be scored as it arrives
without re-loading the unit space per sample.

Design constraints
------------------
- The unit space (mean, std, inverse correlation) is loaded **once** at
  construction time. ``score_batch`` operates on numpy arrays and reuses
  the cached matrices.
- Memory budget: ``LLMESH_MT_ONLINE_MAX_BATCH_BYTES`` (default 16 MiB)
  caps the largest batch; oversize calls are split into chunks
  internally to avoid OOM. This satisfies the v3-N11 spec invariant.
- Fail-closed: any internal error raises rather than returning silently.
  The DVS adapter wraps these calls with its own error handling.

Threading
---------
``OnlineMTEngine`` is **not** thread-safe; one engine per worker. The
unit space matrices are read-only after load, so multiple engines can
share them safely if you wire them in by hand.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from .mt_engine import MTEngine, _require_numpy

if TYPE_CHECKING:
    import numpy as np


_DEFAULT_MAX_BATCH_BYTES = 16 * 1024 * 1024  # 16 MiB


@dataclass(frozen=True)
class OnlineScore:
    """Result of scoring a batch of observations."""

    distances: object   # numpy array, shape (n,)
    anomalies: object   # numpy bool array, shape (n,)
    threshold: float


class OnlineMTEngine:
    """Streaming wrapper around :class:`MTEngine`.

    Parameters
    ----------
    engine:
        A pre-fit :class:`MTEngine` (or one loaded from a unit-space file).
    threshold:
        Mahalanobis distance threshold for anomaly classification.
        Defaults to ``3.0`` (Taguchi rule of thumb).
    max_batch_bytes:
        Memory cap for a single batch. Defaults to the value of the
        ``LLMESH_MT_ONLINE_MAX_BATCH_BYTES`` env var, falling back to
        16 MiB. Larger inputs are processed in chunks transparently.
    """

    def __init__(
        self,
        engine: MTEngine,
        *,
        threshold: float = 3.0,
        max_batch_bytes: int | None = None,
    ) -> None:
        if engine._mean is None or engine._inv_corr is None:
            raise ValueError("engine must be fit (or loaded) before use")
        if threshold <= 0:
            raise ValueError("threshold must be positive")
        self._engine = engine
        self._threshold = float(threshold)
        env_cap = os.environ.get("LLMESH_MT_ONLINE_MAX_BATCH_BYTES")
        if max_batch_bytes is None:
            max_batch_bytes = int(env_cap) if env_cap else _DEFAULT_MAX_BATCH_BYTES
        if max_batch_bytes <= 0:
            raise ValueError("max_batch_bytes must be positive")
        self._max_bytes = int(max_batch_bytes)

    @property
    def threshold(self) -> float:
        return self._threshold

    @property
    def n_features(self) -> int:
        return self._engine._n_features

    def score_batch(self, batch) -> OnlineScore:
        """Score a batch of observations and flag anomalies.

        ``batch`` is an array-like of shape ``(n, p)`` where p is the
        unit-space feature count. Distances are computed in chunks so
        the resident memory never exceeds ``max_batch_bytes``.
        """
        np = _require_numpy()
        arr = np.asarray(batch, dtype=np.float64)
        if arr.ndim != 2 or arr.shape[1] != self.n_features:
            raise ValueError(
                f"expected shape (n, {self.n_features}), got {arr.shape}"
            )
        chunk_rows = max(1, self._max_bytes // (self.n_features * 8))
        out = np.empty(arr.shape[0], dtype=np.float64)
        for start in range(0, arr.shape[0], chunk_rows):
            stop = min(start + chunk_rows, arr.shape[0])
            chunk = arr[start:stop]
            out[start:stop] = self._md_vectorized(chunk)
        return OnlineScore(
            distances=out,
            anomalies=out > self._threshold,
            threshold=self._threshold,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _md_vectorized(self, chunk):
        """Vectorized Mahalanobis distance over a chunk of rows.

        Mirrors :meth:`MTEngine.md` but operates on ``(n, p)`` rather
        than a single row. Returns shape ``(n,)``.
        """
        np = _require_numpy()
        # z = (x - mean) / std  (broadcast)
        z = (chunk - self._engine._mean) / self._engine._std
        # quad_i = z_i @ inv_corr @ z_i^T, vectorized via einsum
        quad = np.einsum("ni,ij,nj->n", z, self._engine._inv_corr, z)
        return np.sqrt(np.maximum(quad, 0.0) / self.n_features)
