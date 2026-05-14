"""Tests for OnlineMTEngine — v3-N11 streaming Mahalanobis scoring."""
from __future__ import annotations


import pytest

pytest.importorskip("numpy")
pytest.importorskip("scipy")

import numpy as np  # noqa: E402

from llmesh.industrial.mt_engine import MTEngine  # noqa: E402
from llmesh.industrial.mt_online import OnlineMTEngine  # noqa: E402


def _fitted_engine(rng, n=300, p=4):
    data = rng.normal(loc=0.0, scale=1.0, size=(n, p))
    eng = MTEngine(device_id="dev0")
    eng.fit(data)
    return eng


class TestConstruct:
    def test_unfit_engine_rejected(self):
        with pytest.raises(ValueError):
            OnlineMTEngine(MTEngine())

    def test_negative_threshold_rejected(self):
        rng = np.random.default_rng(0)
        eng = _fitted_engine(rng)
        with pytest.raises(ValueError):
            OnlineMTEngine(eng, threshold=-1.0)

    def test_zero_max_bytes_rejected(self):
        rng = np.random.default_rng(0)
        eng = _fitted_engine(rng)
        with pytest.raises(ValueError):
            OnlineMTEngine(eng, max_batch_bytes=0)

    def test_env_var_override(self, monkeypatch):
        rng = np.random.default_rng(0)
        eng = _fitted_engine(rng)
        monkeypatch.setenv("LLMESH_MT_ONLINE_MAX_BATCH_BYTES", "1024")
        on = OnlineMTEngine(eng)
        assert on._max_bytes == 1024


class TestScoreBatch:
    def test_scores_shape_matches_input(self):
        rng = np.random.default_rng(1)
        eng = _fitted_engine(rng)
        on = OnlineMTEngine(eng)
        batch = rng.normal(size=(50, 4))
        result = on.score_batch(batch)
        assert result.distances.shape == (50,)
        assert result.anomalies.shape == (50,)

    def test_normal_data_mostly_in_control(self):
        rng = np.random.default_rng(2)
        eng = _fitted_engine(rng)
        on = OnlineMTEngine(eng, threshold=3.0)
        batch = rng.normal(size=(500, 4))
        result = on.score_batch(batch)
        # With normal data, anomaly rate should be modest.
        assert result.anomalies.mean() < 0.2

    def test_extreme_outliers_flagged(self):
        rng = np.random.default_rng(3)
        eng = _fitted_engine(rng)
        on = OnlineMTEngine(eng, threshold=3.0)
        outlier = np.full((1, 4), 100.0)
        result = on.score_batch(outlier)
        assert result.anomalies[0] is np.True_ or bool(result.anomalies[0]) is True

    def test_dim_mismatch_raises(self):
        rng = np.random.default_rng(4)
        eng = _fitted_engine(rng)
        on = OnlineMTEngine(eng)
        with pytest.raises(ValueError):
            on.score_batch(np.zeros((10, 99)))

    def test_chunking_matches_unchunked(self):
        rng = np.random.default_rng(5)
        eng = _fitted_engine(rng)
        big = OnlineMTEngine(eng)
        # Force 1-row chunks via tiny memory cap.
        small = OnlineMTEngine(eng, max_batch_bytes=8)
        batch = rng.normal(size=(40, 4))
        a = big.score_batch(batch).distances
        b = small.score_batch(batch).distances
        assert np.allclose(a, b, atol=1e-9)
