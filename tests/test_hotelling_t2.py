"""Tests for HotellingT2Chart — v3-N11 multivariate control chart."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from llmesh.industrial.hotelling_t2 import HotellingT2Chart  # noqa: E402


class TestConstruct:
    def test_invalid_alpha(self):
        with pytest.raises(ValueError):
            HotellingT2Chart(alpha=0.0)
        with pytest.raises(ValueError):
            HotellingT2Chart(alpha=1.5)

    def test_default_alpha(self):
        c = HotellingT2Chart()
        assert c._alpha == 0.005


class TestFit:
    def test_rejects_1d_reference(self):
        c = HotellingT2Chart()
        with pytest.raises(ValueError):
            c.fit(np.zeros(10))

    def test_rejects_single_row(self):
        c = HotellingT2Chart()
        with pytest.raises(ValueError):
            c.fit(np.zeros((1, 3)))

    def test_features_recorded(self):
        rng = np.random.default_rng(0)
        c = HotellingT2Chart().fit(rng.normal(size=(50, 3)))
        assert c.n_features == 3


class TestUCL:
    def test_override_used(self):
        c = HotellingT2Chart(ucl=42.0).fit(np.random.default_rng(0).normal(size=(20, 2)))
        assert c.ucl == 42.0

    def test_default_grows_with_features(self):
        rng = np.random.default_rng(1)
        c2 = HotellingT2Chart().fit(rng.normal(size=(30, 2)))
        c8 = HotellingT2Chart().fit(rng.normal(size=(30, 8)))
        assert c8.ucl > c2.ucl


class TestScore:
    def test_unfit_raises(self):
        c = HotellingT2Chart()
        with pytest.raises(ValueError):
            c.score(np.zeros(2))

    def test_dim_mismatch_raises(self):
        rng = np.random.default_rng(2)
        c = HotellingT2Chart().fit(rng.normal(size=(30, 3)))
        with pytest.raises(ValueError):
            c.score(np.zeros(5))

    def test_centroid_low_statistic(self):
        rng = np.random.default_rng(3)
        ref = rng.normal(size=(200, 3))
        c = HotellingT2Chart().fit(ref)
        d = c.score(ref.mean(axis=0))
        assert d.statistic < 1e-6
        assert d.in_control is True

    def test_extreme_outlier_out_of_control(self):
        rng = np.random.default_rng(4)
        ref = rng.normal(size=(200, 3))
        c = HotellingT2Chart().fit(ref)
        d = c.score(np.full(3, 100.0))
        assert d.in_control is False


class TestBatch:
    def test_batch_shape(self):
        rng = np.random.default_rng(5)
        ref = rng.normal(size=(200, 4))
        c = HotellingT2Chart().fit(ref)
        out = c.score_batch(rng.normal(size=(50, 4)))
        assert out.statistics.shape == (50,)
        assert out.in_control.shape == (50,)

    def test_batch_dim_mismatch_raises(self):
        rng = np.random.default_rng(6)
        ref = rng.normal(size=(50, 3))
        c = HotellingT2Chart().fit(ref)
        with pytest.raises(ValueError):
            c.score_batch(np.zeros((10, 5)))

    def test_batch_matches_single(self):
        rng = np.random.default_rng(7)
        ref = rng.normal(size=(100, 3))
        c = HotellingT2Chart().fit(ref)
        sample = rng.normal(size=(20, 3))
        batch_out = c.score_batch(sample)
        for i in range(sample.shape[0]):
            single = c.score(sample[i])
            assert math.isclose(batch_out.statistics[i], single.statistic, rel_tol=1e-9)
