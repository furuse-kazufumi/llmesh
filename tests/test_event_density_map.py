"""Tests for EventDensityMap — v3-N11 DVS event aggregator."""
from __future__ import annotations

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from llmesh.industrial.event_density_map import EventDensityMap  # noqa: E402


class TestConstruct:
    def test_invalid_sensor_dim(self):
        with pytest.raises(ValueError):
            EventDensityMap(0, 100)

    def test_invalid_grid_dim(self):
        with pytest.raises(ValueError):
            EventDensityMap(100, 100, grid_w=0)

    def test_invalid_polarity(self):
        with pytest.raises(ValueError):
            EventDensityMap(100, 100, polarity="weird")

    def test_feature_dim(self):
        m = EventDensityMap(346, 260, grid_w=8, grid_h=8)
        assert m.feature_dim == 64
        assert m.grid_shape == (8, 8)


class TestAggregate:
    def test_empty_events_yields_zero_vector(self):
        m = EventDensityMap(100, 100)
        out = m.aggregate(np.empty((0, 3)))
        assert out.event_count == 0
        assert (out.vector == 0).all()
        assert out.vector.shape == (m.feature_dim,)

    def test_xyp_triplets(self):
        m = EventDensityMap(100, 100, grid_w=2, grid_h=2)
        events = np.array([
            [10,  10, 1],
            [12,  12, 1],
            [60,  60, 1],
        ], dtype=np.int64)
        out = m.aggregate(events)
        # Two events in the top-left bin (idx 0), one in bottom-right (idx 3).
        assert out.event_count == 3
        assert int(out.vector[0]) == 2
        assert int(out.vector[3]) == 1

    def test_txyp_quadruplets(self):
        m = EventDensityMap(100, 100, grid_w=2, grid_h=2)
        events = np.array([
            [0, 10, 10, 1],
            [1, 60, 60, 1],
        ], dtype=np.int64)
        out = m.aggregate(events)
        assert out.event_count == 2

    def test_polarity_filter_on(self):
        m = EventDensityMap(100, 100, grid_w=2, grid_h=2, polarity="on")
        events = np.array([
            [10, 10,  1],
            [10, 10, -1],
        ], dtype=np.int64)
        out = m.aggregate(events)
        assert out.event_count == 1
        assert int(out.vector[0]) == 1

    def test_polarity_filter_off(self):
        m = EventDensityMap(100, 100, grid_w=2, grid_h=2, polarity="off")
        events = np.array([
            [10, 10,  1],
            [10, 10, -1],
        ], dtype=np.int64)
        out = m.aggregate(events)
        assert out.event_count == 1
        assert int(out.vector[0]) == 1

    def test_clipping_at_sensor_edge(self):
        m = EventDensityMap(100, 100, grid_w=2, grid_h=2)
        # x = sensor_w-1 should map to the last column, not overflow.
        events = np.array([[99, 99, 1]], dtype=np.int64)
        out = m.aggregate(events)
        assert int(out.vector[-1]) == 1

    def test_structured_array(self):
        dtype = np.dtype([("x", np.int64), ("y", np.int64), ("polarity", np.int64)])
        events = np.zeros(2, dtype=dtype)
        events["x"] = [10, 60]
        events["y"] = [10, 60]
        events["polarity"] = [1, 1]
        m = EventDensityMap(100, 100, grid_w=2, grid_h=2)
        out = m.aggregate(events)
        assert out.event_count == 2

    def test_invalid_array_shape_rejected(self):
        m = EventDensityMap(100, 100)
        with pytest.raises(ValueError):
            m.aggregate(np.zeros((10, 7)))
