"""Tests for DepthCameraAdapter (v1.7.0)."""
from __future__ import annotations

import asyncio
import struct
import pytest

from llmesh.industrial.sensor_3d.depth_adapter import DepthCameraAdapter
from llmesh.industrial.sensor_3d.point_cloud import PointCloud
from llmesh.industrial.sensor_event import SensorEvent


def _make_depth_bin(width: int, height: int, value: float = 1.5) -> bytes:
    """Create a minimal .depth.bin file."""
    header = struct.pack("<II", width, height)
    pixels = struct.pack(f"<{width * height}f", *([value] * (width * height)))
    return header + pixels


class TestDepthCameraAdapter:
    @pytest.mark.asyncio
    async def test_processes_bin_file(self, tmp_path):
        adapter = DepthCameraAdapter(tmp_path, device_id="rs01", poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "frame001.depth.bin").write_bytes(_make_depth_bin(4, 3, 2.0))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_type == "depth_frame"
        assert ev.protocol == "depth"
        assert ev.device_id == "rs01"
        assert ev.metadata["width"] == 4
        assert ev.metadata["height"] == 3

    @pytest.mark.asyncio
    async def test_point_cloud_in_payload(self, tmp_path):
        adapter = DepthCameraAdapter(tmp_path, poll_interval_s=0.05, max_range_m=5.0)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "frame002.depth.bin").write_bytes(_make_depth_bin(2, 2, 1.0))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        ev = events[0]
        pc = PointCloud.from_bytes(ev.payload)
        assert pc.count == 4  # 2×2 pixels all at 1.0 m (within range)

    @pytest.mark.asyncio
    async def test_out_of_range_filtered(self, tmp_path):
        adapter = DepthCameraAdapter(tmp_path, poll_interval_s=0.05, max_range_m=2.0)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        # depth=5.0 m > max_range_m=2.0 — all points should be filtered
        (tmp_path / "frame003.depth.bin").write_bytes(_make_depth_bin(2, 2, 5.0))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        ev = events[0]
        pc = PointCloud.from_bytes(ev.payload)
        assert pc.count == 0

    @pytest.mark.asyncio
    async def test_skips_non_depth_files(self, tmp_path):
        adapter = DepthCameraAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "frame.jpg").write_bytes(b"\xff\xd8")
        (tmp_path / "frame.bin").write_bytes(b"\x00" * 16)  # wrong extension

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert events == []

    @pytest.mark.asyncio
    async def test_each_file_once(self, tmp_path):
        adapter = DepthCameraAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "frame004.depth.bin").write_bytes(_make_depth_bin(1, 1, 1.0))

        await adapter.start()
        await asyncio.sleep(0.3)
        await adapter.stop()

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_delete_after(self, tmp_path):
        adapter = DepthCameraAdapter(tmp_path, poll_interval_s=0.05, delete_after=True)
        adapter.on_event(lambda ev: None)

        f = tmp_path / "frame005.depth.bin"
        f.write_bytes(_make_depth_bin(1, 1, 1.0))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert not f.exists()

    @pytest.mark.asyncio
    async def test_invalid_bin_skipped(self, tmp_path):
        adapter = DepthCameraAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "bad.depth.bin").write_bytes(b"\x00\x00")  # too short

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert events == []
