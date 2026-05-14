"""Tests for MTEngine (MT-method Mahalanobis-Taguchi) — v1.5.0."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("numpy", reason="numpy required for MT-method tests")
pytest.importorskip("scipy", reason="scipy required for MT-method tests")

import numpy as np

from llmesh.industrial.mt_engine import MTEngine


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def normal_2d() -> np.ndarray:
    """50 observations of 3 features drawn from N(0,1) with known seed."""
    rng = np.random.default_rng(42)
    return rng.standard_normal((50, 3))


@pytest.fixture()
def fitted_engine(normal_2d) -> MTEngine:
    engine = MTEngine(device_id="test_device")
    engine.fit(normal_2d)
    return engine


# ---------------------------------------------------------------------------
# fit() validation
# ---------------------------------------------------------------------------

def test_fit_requires_2d_array():
    eng = MTEngine()
    with pytest.raises(ValueError, match="2-D"):
        eng.fit(np.array([1.0, 2.0, 3.0]))


def test_fit_requires_at_least_2_observations():
    eng = MTEngine()
    with pytest.raises(ValueError, match="at least 2"):
        eng.fit(np.array([[1.0, 2.0]]))


def test_fit_requires_at_least_1_feature():
    eng = MTEngine()
    with pytest.raises(ValueError, match="at least 1"):
        eng.fit(np.ones((5, 0)))


def test_fit_sets_is_fitted(fitted_engine):
    assert fitted_engine.is_fitted


def test_not_fitted_by_default():
    assert not MTEngine().is_fitted


def test_fit_stores_correct_n_features(normal_2d, fitted_engine):
    assert fitted_engine._n_features == normal_2d.shape[1]


def test_fit_zero_variance_feature():
    """A constant feature column must not cause ZeroDivisionError."""
    data = np.ones((10, 3))
    data[:, 0] = np.arange(10, dtype=float)
    data[:, 2] = 5.0  # constant — zero variance
    eng = MTEngine()
    eng.fit(data)
    assert eng.is_fitted


# ---------------------------------------------------------------------------
# md() — Mahalanobis distance
# ---------------------------------------------------------------------------

def test_md_unit_space_center_near_one(fitted_engine, normal_2d):
    """MD of the unit-space mean should be ~0 (or very small)."""
    mean_sample = fitted_engine._mean
    md = fitted_engine.md(mean_sample)
    assert md < 0.5, f"MD at unit-space mean should be small, got {md}"


def test_md_of_far_outlier_is_large(fitted_engine):
    outlier = np.array([100.0, 100.0, 100.0])
    assert fitted_engine.md(outlier) > 10.0


def test_md_nonnegative(fitted_engine, normal_2d):
    for row in normal_2d:
        assert fitted_engine.md(row) >= 0.0


def test_md_wrong_feature_count_raises(fitted_engine):
    with pytest.raises(ValueError, match="features"):
        fitted_engine.md([1.0, 2.0])  # expects 3


def test_md_raises_if_not_fitted():
    with pytest.raises(RuntimeError, match="fit"):
        MTEngine().md([1.0, 2.0, 3.0])


# ---------------------------------------------------------------------------
# is_anomaly()
# ---------------------------------------------------------------------------

def test_is_anomaly_outlier(fitted_engine):
    assert fitted_engine.is_anomaly([100.0, 100.0, 100.0], threshold=3.0)


def test_is_anomaly_normal_data(fitted_engine, normal_2d):
    # Most normal observations should NOT be flagged at threshold=5
    n_flagged = sum(fitted_engine.is_anomaly(row, threshold=5.0) for row in normal_2d)
    assert n_flagged < len(normal_2d) * 0.1, "Too many normal observations flagged as anomalies"


# ---------------------------------------------------------------------------
# md_batch()
# ---------------------------------------------------------------------------

def test_md_batch_shape(fitted_engine, normal_2d):
    mds = fitted_engine.md_batch(normal_2d)
    assert mds.shape == (normal_2d.shape[0],)


def test_md_batch_1d_input(fitted_engine):
    sample = np.array([0.1, 0.2, 0.3])
    result = fitted_engine.md_batch(sample)
    assert result.shape == (1,)
    assert math.isclose(result[0], fitted_engine.md(sample), rel_tol=1e-9)


# ---------------------------------------------------------------------------
# save() / load()
# ---------------------------------------------------------------------------

def test_save_load_roundtrip(fitted_engine, normal_2d, tmp_path):
    path = tmp_path / "unit_space.npz"
    fitted_engine.save(path)
    assert path.exists()

    loaded = MTEngine.load(path)
    assert loaded.is_fitted
    assert loaded.device_id == fitted_engine.device_id
    assert loaded._n_features == fitted_engine._n_features

    # MD values must be identical
    for row in normal_2d[:5]:
        assert math.isclose(fitted_engine.md(row), loaded.md(row), rel_tol=1e-9)


def test_load_nonexistent_file_raises():
    with pytest.raises(FileNotFoundError):
        MTEngine.load("/nonexistent/unit_space.npz")


def test_save_creates_parent_dir(fitted_engine, tmp_path):
    nested = tmp_path / "subdir" / "unit.npz"
    fitted_engine.save(nested)
    assert nested.exists()


def test_save_raises_if_not_fitted():
    with pytest.raises(RuntimeError, match="fitted"):
        MTEngine().save("/tmp/unit.npz")


# ---------------------------------------------------------------------------
# Single-feature case
# ---------------------------------------------------------------------------

def test_single_feature_fit_and_md():
    data = np.array([[1.0], [1.1], [0.9], [1.05], [0.95]])
    eng = MTEngine(device_id="single")
    eng.fit(data)
    md_near = eng.md([1.0])
    md_far = eng.md([10.0])
    assert md_near < md_far
