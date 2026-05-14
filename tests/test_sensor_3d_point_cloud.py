"""Tests for PointCloud (v1.7.0)."""
from __future__ import annotations

import struct

from llmesh.industrial.sensor_3d.point_cloud import PointCloud


class TestPointCloudRoundtrip:
    def test_empty(self):
        pc = PointCloud(points=[])
        assert pc.to_bytes() == b""
        pc2 = PointCloud.from_bytes(b"")
        assert pc2.count == 0

    def test_single_point(self):
        pc = PointCloud(points=[(1.0, 2.0, 3.0)])
        data = pc.to_bytes()
        assert len(data) == 12
        pc2 = PointCloud.from_bytes(data)
        assert pc2.count == 1
        x, y, z = pc2.points[0]
        assert abs(x - 1.0) < 1e-6
        assert abs(y - 2.0) < 1e-6
        assert abs(z - 3.0) < 1e-6

    def test_multiple_points(self):
        pts = [(i * 0.1, i * 0.2, i * 0.5) for i in range(10)]
        pc = PointCloud(points=pts)
        pc2 = PointCloud.from_bytes(pc.to_bytes())
        assert pc2.count == 10
        for i, (x, y, z) in enumerate(pc2.points):
            assert abs(x - pts[i][0]) < 1e-5
            assert abs(y - pts[i][1]) < 1e-5
            assert abs(z - pts[i][2]) < 1e-5

    def test_truncates_incomplete_record(self):
        data = struct.pack("<fff", 1.0, 2.0, 3.0) + b"\x00"  # 13 bytes (one extra)
        pc = PointCloud.from_bytes(data)
        assert pc.count == 1  # trailing byte ignored

    def test_from_iterable(self):
        pc = PointCloud.from_iterable(iter([(0.0, 0.0, 1.0), (1.0, 1.0, 2.0)]))
        assert pc.count == 2


class TestPointCloudStats:
    def test_empty_stats(self):
        stats = PointCloud(points=[]).stats()
        assert stats["count"] == 0

    def test_stats_single(self):
        stats = PointCloud(points=[(1.0, 2.0, 3.0)]).stats()
        assert stats["count"] == 1
        assert stats["x_range"] == (1.0, 1.0)
        assert stats["centroid"] == (1.0, 2.0, 3.0)

    def test_stats_multiple(self):
        pts = [(0.0, 0.0, 0.0), (2.0, 4.0, 6.0)]
        stats = PointCloud(points=pts).stats()
        assert stats["x_range"] == (0.0, 2.0)
        cx, cy, cz = stats["centroid"]
        assert abs(cx - 1.0) < 1e-6
        assert abs(cy - 2.0) < 1e-6
        assert abs(cz - 3.0) < 1e-6

    def test_len(self):
        pc = PointCloud(points=[(0.0, 0.0, 0.0)] * 5)
        assert len(pc) == 5
