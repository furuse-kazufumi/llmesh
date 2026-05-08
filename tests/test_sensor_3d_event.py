"""Tests for EventCameraAdapter, DvsEvent, encode/decode (v1.7.0)."""
from __future__ import annotations

import asyncio
import struct
import pytest
from pathlib import Path

from llmesh.industrial.sensor_3d.event_adapter import (
    EventCameraAdapter,
    DvsEvent,
    encode_dvs_events,
    decode_dvs_events,
    _batch_stats,
    _EVENT_BYTES,
)
from llmesh.industrial.sensor_event import SensorEvent


# ---------------------------------------------------------------------------
# encode / decode
# ---------------------------------------------------------------------------

class TestDvsCodec:
    def test_roundtrip_empty(self):
        assert decode_dvs_events(encode_dvs_events([])) == []

    def test_roundtrip_single(self):
        ev = DvsEvent(x=100, y=200, t_us=5000, polarity=True)
        decoded = decode_dvs_events(encode_dvs_events([ev]))
        assert len(decoded) == 1
        assert decoded[0].x == 100
        assert decoded[0].y == 200
        assert decoded[0].t_us == 5000
        assert decoded[0].polarity is True

    def test_roundtrip_multiple(self):
        events = [
            DvsEvent(x=i, y=i * 2, t_us=i * 100, polarity=bool(i % 2))
            for i in range(50)
        ]
        decoded = decode_dvs_events(encode_dvs_events(events))
        assert len(decoded) == 50
        for orig, dec in zip(events, decoded):
            assert orig == dec

    def test_negative_polarity(self):
        ev = DvsEvent(x=0, y=0, t_us=0, polarity=False)
        decoded = decode_dvs_events(encode_dvs_events([ev]))
        assert decoded[0].polarity is False

    def test_truncates_incomplete_record(self):
        data = encode_dvs_events([DvsEvent(0, 0, 0, True)]) + b"\x00"
        decoded = decode_dvs_events(data)
        assert len(decoded) == 1


# ---------------------------------------------------------------------------
# _batch_stats
# ---------------------------------------------------------------------------

class TestBatchStats:
    def test_basic(self):
        events = [
            DvsEvent(x=0, y=0, t_us=100, polarity=True),
            DvsEvent(x=1, y=1, t_us=200, polarity=False),
            DvsEvent(x=2, y=2, t_us=300, polarity=True),
        ]
        data = encode_dvs_events(events)
        stats = _batch_stats(data, 3)
        assert stats["event_count"] == 3
        assert stats["positive_events"] == 2
        assert stats["negative_events"] == 1
        assert stats["t_start_us"] == 100
        assert stats["t_end_us"] == 300
        assert stats["duration_us"] == 200


# ---------------------------------------------------------------------------
# EventCameraAdapter
# ---------------------------------------------------------------------------

class TestEventCameraAdapter:
    def _make_dvs_bin(self, n: int = 10) -> bytes:
        events = [DvsEvent(x=i, y=i, t_us=i * 100, polarity=bool(i % 2)) for i in range(n)]
        return encode_dvs_events(events)

    @pytest.mark.asyncio
    async def test_processes_dvs_file(self, tmp_path):
        adapter = EventCameraAdapter(tmp_path, device_id="dvs01", poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "batch001.dvs.bin").write_bytes(self._make_dvs_bin(20))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_type == "dvs_events"
        assert ev.protocol == "dvs"
        assert ev.device_id == "dvs01"
        assert ev.metadata["event_count"] == 20

    @pytest.mark.asyncio
    async def test_skips_non_dvs_files(self, tmp_path):
        adapter = EventCameraAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "frame.jpg").write_bytes(b"\xff\xd8")
        (tmp_path / "data.bin").write_bytes(b"\x00" * 9)  # wrong double-ext

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert events == []

    @pytest.mark.asyncio
    async def test_each_file_once(self, tmp_path):
        adapter = EventCameraAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "batch002.dvs.bin").write_bytes(self._make_dvs_bin(5))

        await adapter.start()
        await asyncio.sleep(0.3)
        await adapter.stop()

        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_delete_after(self, tmp_path):
        adapter = EventCameraAdapter(tmp_path, poll_interval_s=0.05, delete_after=True)
        adapter.on_event(lambda ev: None)

        f = tmp_path / "batch003.dvs.bin"
        f.write_bytes(self._make_dvs_bin(3))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert not f.exists()

    @pytest.mark.asyncio
    async def test_empty_file_skipped(self, tmp_path):
        adapter = EventCameraAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "empty.dvs.bin").write_bytes(b"")

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert events == []

    @pytest.mark.asyncio
    async def test_metadata_polarity_counts(self, tmp_path):
        adapter = EventCameraAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        dvs_events = [DvsEvent(0, 0, i, i < 3) for i in range(6)]  # 3 pos, 3 neg
        (tmp_path / "batch004.dvs.bin").write_bytes(encode_dvs_events(dvs_events))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        meta = events[0].metadata
        assert meta["positive_events"] == 3
        assert meta["negative_events"] == 3
