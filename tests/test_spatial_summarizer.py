"""Tests for SpatialSummarizer (v1.7.0)."""
from __future__ import annotations


from llmesh.industrial.sensor_3d.spatial_summarizer import SpatialSummarizer
from llmesh.industrial.sensor_3d.point_cloud import PointCloud
from llmesh.industrial.sensor_3d.event_adapter import DvsEvent, encode_dvs_events
from llmesh.industrial.sensor_event import SensorEvent


def _make_event(sensor_type: str, payload: bytes = b"", **meta) -> SensorEvent:
    return SensorEvent.create(
        sensor_id="test_sensor",
        protocol="test",
        payload=payload,
        sensor_type=sensor_type,
        device_id="dev01",
        metadata=meta,
    )


class TestSpatialSummarizerAoi:
    def setup_method(self):
        self.s = SpatialSummarizer()

    def test_ok_no_defects(self):
        ev = _make_event("aoi_image", b"\xff" * 100,
                         aoi_result="ok", defect_count=0, size_bytes=100)
        text = self.s.summarize(ev)
        assert "OK" in text
        assert "0 defects" in text

    def test_ng_with_defects(self):
        ev = _make_event(
            "aoi_image", b"\xff" * 50,
            aoi_result="ng", defect_count=2, size_bytes=50,
            board_id="BOARD-007",
            defects=[
                {"label": "scratch", "confidence": 0.92, "bbox": [10, 20, 5, 5]},
                {"label": "void",    "confidence": 0.85},
            ],
        )
        text = self.s.summarize(ev)
        assert "NG" in text
        assert "2 defect" in text
        assert "BOARD-007" in text
        assert "scratch" in text

    def test_max_defects_shown(self):
        s = SpatialSummarizer(max_defects_shown=2)
        defects = [{"label": f"d{i}"} for i in range(5)]
        ev = _make_event("aoi_image", b"x", aoi_result="ng", defect_count=5, defects=defects)
        text = s.summarize(ev)
        assert "and 3 more" in text

    def test_unknown_result(self):
        ev = _make_event("aoi_image", b"x", aoi_result="unknown", defect_count=0)
        text = self.s.summarize(ev)
        assert "UNKNOWN" in text


class TestSpatialSummarizerDepth:
    def setup_method(self):
        self.s = SpatialSummarizer()

    def test_depth_with_points(self):
        pts = [(0.0, 0.0, 1.0), (1.0, 1.0, 2.0), (0.5, 0.5, 1.5)]
        pc = PointCloud(points=pts)
        ev = _make_event("depth_frame", pc.to_bytes(), width=2, height=2)
        text = self.s.summarize(ev)
        assert "Depth frame" in text
        assert "dev01" in text
        assert "3" in text  # point count
        assert "m" in text

    def test_depth_no_points(self):
        ev = _make_event("depth_frame", b"", width=4, height=4)
        text = self.s.summarize(ev)
        assert "0 valid points" in text

    def test_depth_z_range(self):
        pts = [(0.0, 0.0, 0.5), (1.0, 1.0, 3.0)]
        pc = PointCloud(points=pts)
        ev = _make_event("depth_frame", pc.to_bytes(), width=1, height=2)
        text = self.s.summarize(ev)
        assert "0.50" in text
        assert "3.00" in text


class TestSpatialSummarizerDvs:
    def setup_method(self):
        self.s = SpatialSummarizer()

    def test_dvs_summary(self):
        ev = _make_event(
            "dvs_events", b"\x00" * 9,
            event_count=1024,
            positive_events=600,
            negative_events=424,
            duration_us=5000,
        )
        text = self.s.summarize(ev)
        assert "DVS" in text
        assert "1,024" in text
        assert "+600" in text
        assert "-424" in text
        assert "5,000" in text

    def test_dvs_from_payload(self):
        events = [DvsEvent(i, i, i * 10, True) for i in range(8)]
        data = encode_dvs_events(events)
        ev = _make_event(
            "dvs_events", data,
            event_count=8, positive_events=8, negative_events=0, duration_us=70,
        )
        text = self.s.summarize(ev)
        assert "8" in text


class TestSpatialSummarizerUnknown:
    def test_unknown_type_fallback(self):
        s = SpatialSummarizer()
        ev = _make_event("lidar_sweep", b"\x00" * 24)
        text = s.summarize(ev)
        assert "3D sensor event" in text
        assert "lidar_sweep" in text
