"""Tests for AoiAdapter and AoiResult (v1.7.0)."""
from __future__ import annotations

import asyncio
import json
import pytest
from pathlib import Path

from llmesh.industrial.sensor_3d.aoi_adapter import AoiAdapter, AoiResult, _default_priority
from llmesh.industrial.sensor_event import Priority, SensorEvent


class TestAoiResult:
    def test_from_dict_ok(self):
        r = AoiResult.from_dict({"result": "ok", "board_id": "B001"})
        assert r.is_ok
        assert r.board_id == "B001"
        assert r.defect_count == 0

    def test_from_dict_ng(self):
        r = AoiResult.from_dict({
            "result": "NG",
            "defects": [{"label": "scratch", "confidence": 0.9, "bbox": [10, 20, 5, 5]}],
        })
        assert not r.is_ok
        assert r.defect_count == 1

    def test_from_dict_missing_result(self):
        r = AoiResult.from_dict({})
        assert r.result == "unknown"

    def test_default_priority_ok(self):
        assert _default_priority(AoiResult(result="ok")) is Priority.NORMAL

    def test_default_priority_ng(self):
        assert _default_priority(AoiResult(result="ng")) is Priority.HIGH


class TestAoiAdapter:
    @pytest.mark.asyncio
    async def test_processes_image(self, tmp_path):
        adapter = AoiAdapter(tmp_path, device_id="cam01", poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        # Write a fake JPEG
        img = tmp_path / "frame001.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 100)  # minimal JPEG-like bytes

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_type == "aoi_image"
        assert ev.protocol == "aoi"
        assert ev.device_id == "cam01"
        assert ev.metadata["aoi_result"] == "unknown"

    @pytest.mark.asyncio
    async def test_loads_sidecar(self, tmp_path):
        adapter = AoiAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        img = tmp_path / "frame002.jpg"
        img.write_bytes(b"\xff\xd8\xff")
        sidecar = tmp_path / "frame002.aoi.json"
        sidecar.write_text(json.dumps({
            "result": "ng",
            "board_id": "BOARD-007",
            "defects": [{"label": "void", "confidence": 0.95}],
        }))

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert len(events) == 1
        ev = events[0]
        assert ev.metadata["aoi_result"] == "ng"
        assert ev.metadata["board_id"] == "BOARD-007"
        assert ev.metadata["defect_count"] == 1
        assert ev.priority is Priority.HIGH

    @pytest.mark.asyncio
    async def test_skips_non_image_files(self, tmp_path):
        adapter = AoiAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "readme.txt").write_text("hello")
        (tmp_path / "data.csv").write_text("a,b,c")

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert events == []

    @pytest.mark.asyncio
    async def test_each_file_processed_once(self, tmp_path):
        adapter = AoiAdapter(tmp_path, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        (tmp_path / "frame003.png").write_bytes(b"\x89PNG\r\n")

        await adapter.start()
        await asyncio.sleep(0.3)   # two poll cycles
        await adapter.stop()

        assert len(events) == 1  # processed only once

    @pytest.mark.asyncio
    async def test_delete_after(self, tmp_path):
        adapter = AoiAdapter(tmp_path, poll_interval_s=0.05, delete_after=True)
        adapter.on_event(lambda ev: None)

        img = tmp_path / "frame004.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert not img.exists()

    @pytest.mark.asyncio
    async def test_move_processed_to(self, tmp_path):
        done_dir = tmp_path / "done"
        adapter = AoiAdapter(tmp_path, poll_interval_s=0.05, move_processed_to=done_dir)
        adapter.on_event(lambda ev: None)

        img = tmp_path / "frame005.jpg"
        img.write_bytes(b"\xff\xd8\xff")

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()

        assert not img.exists()
        assert (done_dir / "frame005.jpg").exists()

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self, tmp_path):
        adapter = AoiAdapter(tmp_path, poll_interval_s=0.05)
        await adapter.start()
        t = adapter._task
        await adapter.start()
        assert adapter._task is t
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash(self, tmp_path):
        adapter = AoiAdapter(tmp_path, poll_interval_s=0.05)
        adapter.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))

        (tmp_path / "frame006.jpg").write_bytes(b"\xff\xd8\xff")

        await adapter.start()
        await asyncio.sleep(0.15)
        await adapter.stop()  # must not raise
