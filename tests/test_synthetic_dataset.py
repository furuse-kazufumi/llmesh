"""End-to-end tests using the synthetic dataset generator (v2.2.0+).

Generates a small synthetic dataset on-the-fly and feeds it through each
real LLMesh adapter — verifying the *full* path from drop-folder file
to SensorEvent emission, with no mocks.

This is the closest thing to an integration test we can run without
external hardware or downloaded datasets.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
import pytest

# Make the project tools/ importable
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools.gen_synthetic_dataset import _run as gen_run


@pytest.fixture
def synth_aoi(tmp_path) -> Path:
    out = tmp_path / "aoi"
    n = gen_run("aoi", 10, out, seed=42)
    assert n == 10
    return out


@pytest.fixture
def synth_depth(tmp_path) -> Path:
    out = tmp_path / "depth"
    n = gen_run("depth", 5, out, seed=42)
    assert n == 5
    return out


@pytest.fixture
def synth_dvs(tmp_path) -> Path:
    out = tmp_path / "dvs"
    n = gen_run("dvs", 5, out, seed=42)
    assert n == 5
    return out


# ---------------------------------------------------------------------------
# AOI
# ---------------------------------------------------------------------------

class TestSyntheticAoi:
    def test_files_have_expected_extensions(self, synth_aoi):
        jpegs = list(synth_aoi.glob("*.jpg"))
        sidecars = list(synth_aoi.glob("*.aoi.json"))
        assert len(jpegs) == 10
        assert len(sidecars) == 10

    def test_sidecar_is_valid_json(self, synth_aoi):
        for p in synth_aoi.glob("*.aoi.json"):
            data = json.loads(p.read_text())
            assert data["result"] in ("ok", "ng")
            assert "board_id" in data
            assert isinstance(data["defects"], list)

    def test_jpeg_files_have_soi_eoi(self, synth_aoi):
        for p in synth_aoi.glob("*.jpg"):
            raw = p.read_bytes()
            assert raw.startswith(b"\xff\xd8")
            assert raw.endswith(b"\xff\xd9")

    def test_seed_reproducibility(self, tmp_path):
        out_a = tmp_path / "a"
        out_b = tmp_path / "b"
        gen_run("aoi", 5, out_a, seed=99)
        gen_run("aoi", 5, out_b, seed=99)
        for fa, fb in zip(sorted(out_a.iterdir()), sorted(out_b.iterdir())):
            assert fa.read_bytes() == fb.read_bytes()

    @pytest.mark.asyncio
    async def test_aoi_adapter_processes_synthetic(self, synth_aoi):
        from llmesh.industrial.sensor_3d.aoi_adapter import AoiAdapter
        from llmesh.industrial.sensor_event import SensorEvent

        adapter = AoiAdapter(synth_aoi, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        await adapter.start()
        # Two stability cycles + processing time
        await asyncio.sleep(0.4)
        await adapter.stop()

        assert len(events) == 10
        # Mix of OK and NG (with non-zero NG ratio)
        ng_events = [e for e in events if e.metadata["aoi_result"] == "ng"]
        assert 0 < len(ng_events) <= 10


# ---------------------------------------------------------------------------
# Depth
# ---------------------------------------------------------------------------

class TestSyntheticDepth:
    def test_files_have_correct_extension(self, synth_depth):
        bins = list(synth_depth.glob("*.depth.bin"))
        assert len(bins) == 5

    def test_depth_header_parsable(self, synth_depth):
        import struct
        for p in synth_depth.glob("*.depth.bin"):
            raw = p.read_bytes()
            w, h = struct.unpack_from("<II", raw, 0)
            assert w > 0 and h > 0
            expected = 8 + w * h * 4
            assert len(raw) == expected

    @pytest.mark.asyncio
    async def test_depth_adapter_processes_synthetic(self, synth_depth):
        from llmesh.industrial.sensor_3d.depth_adapter import DepthCameraAdapter
        from llmesh.industrial.sensor_3d.point_cloud import PointCloud
        from llmesh.industrial.sensor_event import SensorEvent

        adapter = DepthCameraAdapter(synth_depth, poll_interval_s=0.05,
                                      max_range_m=10.0)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        await adapter.start()
        await asyncio.sleep(0.5)
        await adapter.stop()

        assert len(events) == 5
        for ev in events:
            pc = PointCloud.from_bytes(ev.payload)
            assert pc.count > 0


# ---------------------------------------------------------------------------
# DVS
# ---------------------------------------------------------------------------

class TestSyntheticDvs:
    def test_files_size_multiple_of_event_size(self, synth_dvs):
        for p in synth_dvs.glob("*.dvs.bin"):
            raw = p.read_bytes()
            assert len(raw) % 9 == 0
            assert len(raw) > 0

    @pytest.mark.asyncio
    async def test_dvs_adapter_processes_synthetic(self, synth_dvs):
        from llmesh.industrial.sensor_3d.event_adapter import (
            EventCameraAdapter, decode_dvs_events,
        )
        from llmesh.industrial.sensor_event import SensorEvent

        adapter = EventCameraAdapter(synth_dvs, poll_interval_s=0.05)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        await adapter.start()
        await asyncio.sleep(0.4)
        await adapter.stop()

        assert len(events) == 5
        for ev in events:
            decoded = decode_dvs_events(ev.payload)
            assert len(decoded) == ev.metadata["event_count"]
            # polarity must be 0 or 1
            assert all(e.polarity in (False, True) for e in decoded)


# ---------------------------------------------------------------------------
# End-to-end: synthetic → pipeline → diagnosis
# ---------------------------------------------------------------------------

class TestSyntheticIntegration:
    @pytest.mark.asyncio
    async def test_aoi_to_diagnosis_chain(self, synth_aoi):
        """Full chain: AoiAdapter → SpatialSummarizer → IndustrialPipeline."""
        from llmesh.industrial.sensor_3d.aoi_adapter import AoiAdapter
        from llmesh.industrial.sensor_3d.spatial_summarizer import SpatialSummarizer
        from llmesh.industrial.sensor_event import SensorEvent

        adapter = AoiAdapter(synth_aoi, poll_interval_s=0.05)
        summarizer = SpatialSummarizer()
        summaries: list[str] = []

        def on_event(ev: SensorEvent) -> None:
            summaries.append(summarizer.summarize(ev))

        adapter.on_event(on_event)
        await adapter.start()
        await asyncio.sleep(0.4)
        await adapter.stop()

        assert len(summaries) == 10
        # NG summaries should mention defects
        ng_summaries = [s for s in summaries if "NG" in s]
        assert all("defect" in s.lower() for s in ng_summaries)
