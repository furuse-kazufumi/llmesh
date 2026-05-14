"""Tests for DebugRecorder / DebugReplayer / PipelineProfiler / describe_event."""
from __future__ import annotations

import json
import struct
from pathlib import Path
import pytest

from llmesh.industrial.debug import (
    DebugRecorder, DebugReplayer, PipelineProfiler, describe_event,
    _event_to_dict, _event_from_dict,
    _diagnosis_to_dict, _diagnosis_from_dict,
    _coerce_jsonable,
)
from llmesh.industrial.sensor_event import SensorEvent
from llmesh.industrial.pipeline import (
    IndustrialPipeline, DiagnosisResult, DiagnosisStatus,
)


def _ev(sensor_id="s1", **kw) -> SensorEvent:
    return SensorEvent.create(
        sensor_id=sensor_id,
        protocol=kw.get("protocol", "modbus"),
        payload=kw.get("payload", struct.pack("<d", 42.0)),
        device_id=kw.get("device_id", "d1"),
        sensor_type=kw.get("sensor_type", "pressure"),
        unit=kw.get("unit", "Pa"),
        metadata=kw.get("metadata", {}),
    )


# ---------------------------------------------------------------------------
# Coerce helpers
# ---------------------------------------------------------------------------

class TestCoerce:
    def test_passthrough_primitives(self):
        d = _coerce_jsonable({"a": 1, "b": "x", "c": True, "d": None, "e": 1.5})
        assert d == {"a": 1, "b": "x", "c": True, "d": None, "e": 1.5}

    def test_bytes_to_hex(self):
        d = _coerce_jsonable({"k": b"\x01\x02"})
        assert d["k"] == "0102"

    def test_nested_dict(self):
        d = _coerce_jsonable({"k": {"inner": b"\xff"}})
        assert d["k"] == {"inner": "ff"}


# ---------------------------------------------------------------------------
# Event/Diagnosis serialisation roundtrip
# ---------------------------------------------------------------------------

class TestSerialisation:
    def test_event_roundtrip(self):
        orig = _ev()
        d = _event_to_dict(orig)
        restored = _event_from_dict(d)
        assert restored.sensor_id == orig.sensor_id
        assert restored.protocol == orig.protocol
        assert restored.payload == orig.payload
        assert restored.priority is orig.priority

    def test_diagnosis_roundtrip(self):
        orig = DiagnosisResult(
            sensor_id="s1", device_id="d1",
            status=DiagnosisStatus.ANOMALY,
            severity=0.9, summary="MD=10",
            evidence={"md": 10.0},
            timestamp_ns=12345,
            source_protocol="modbus",
        )
        d = _diagnosis_to_dict(orig)
        restored = _diagnosis_from_dict(d)
        assert restored.status is DiagnosisStatus.ANOMALY
        assert restored.severity == 0.9


# ---------------------------------------------------------------------------
# DebugRecorder
# ---------------------------------------------------------------------------

class TestDebugRecorder:
    def test_creates_jsonl(self, tmp_path: Path):
        rec_path = tmp_path / "rec.jsonl"
        with DebugRecorder(rec_path) as rec:
            rec.record_event(_ev())
            rec.record_event(_ev(sensor_id="s2"))
        assert rec_path.exists()
        lines = rec_path.read_text().strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            d = json.loads(line)
            assert d["kind"] == "event"

    def test_record_diagnosis(self, tmp_path: Path):
        rec_path = tmp_path / "rec.jsonl"
        with DebugRecorder(rec_path) as rec:
            rec.record_diagnosis(DiagnosisResult(
                sensor_id="s1", device_id="d1",
                status=DiagnosisStatus.NORMAL,
                severity=0.0, summary="ok",
                evidence={}, timestamp_ns=0, source_protocol="t",
            ))
        d = json.loads(rec_path.read_text().strip())
        assert d["kind"] == "diagnosis"

    def test_counts(self, tmp_path: Path):
        rec_path = tmp_path / "rec.jsonl"
        with DebugRecorder(rec_path) as rec:
            for _ in range(3):
                rec.record_event(_ev())
            assert rec.event_count == 3

    def test_ensures_parent_directory(self, tmp_path: Path):
        rec_path = tmp_path / "deep/nested/rec.jsonl"
        with DebugRecorder(rec_path) as rec:
            rec.record_event(_ev())
        assert rec_path.exists()


# ---------------------------------------------------------------------------
# DebugReplayer
# ---------------------------------------------------------------------------

class TestDebugReplayer:
    def test_replay_events(self, tmp_path: Path):
        rec_path = tmp_path / "rec.jsonl"
        original = [_ev(sensor_id=f"s{i}") for i in range(5)]
        with DebugRecorder(rec_path) as rec:
            for ev in original:
                rec.record_event(ev)

        replayer = DebugReplayer(rec_path)
        events = list(replayer.events())
        assert len(events) == 5
        assert [e.sensor_id for e in events] == [e.sensor_id for e in original]

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            DebugReplayer(tmp_path / "nonexistent.jsonl")

    def test_replay_into_pipeline(self, tmp_path: Path):
        rec_path = tmp_path / "rec.jsonl"
        with DebugRecorder(rec_path) as rec:
            for _ in range(3):
                rec.record_event(_ev(sensor_id="pressure_01"))

        # Replay into a pipeline
        pipeline = IndustrialPipeline()
        pipeline.attach_cusum("pressure_01", target=42.0, k=0.5, h=4.0, sigma=1.0)
        replayer = DebugReplayer(rec_path)
        diagnoses = []
        pipeline.on_diagnosis(diagnoses.append)
        for ev in replayer.events():
            pipeline.process(ev)
        assert len(diagnoses) == 3


# ---------------------------------------------------------------------------
# PipelineProfiler
# ---------------------------------------------------------------------------

class TestPipelineProfiler:
    def test_collects_samples(self):
        p = IndustrialPipeline()
        p.attach_cusum("s1", target=0.0, k=0.5, h=4.0, sigma=1.0)
        prof = PipelineProfiler(p)
        for _ in range(10):
            p.process(_ev(payload=struct.pack("<d", 1.0)))
        assert len(prof.samples) == 10
        prof.detach()

    def test_summary_structure(self):
        p = IndustrialPipeline()
        prof = PipelineProfiler(p)
        for _ in range(5):
            p.process(_ev(payload=b"\x00" * 8))
        s = prof.summary()
        assert s["count"] == 5
        assert "p50_us" in s
        assert "p95_us" in s
        prof.detach()

    def test_summary_empty(self):
        p = IndustrialPipeline()
        prof = PipelineProfiler(p)
        s = prof.summary()
        assert s["count"] == 0
        prof.detach()

    def test_detach_restores_original(self):
        p = IndustrialPipeline()
        prof = PipelineProfiler(p)
        # While attached, samples accumulate
        p.process(_ev(payload=b"\x00" * 8))
        assert len(prof.samples) == 1
        prof.detach()
        # After detach: no new samples accumulate
        p.process(_ev(payload=b"\x00" * 8))
        assert len(prof.samples) == 1


# ---------------------------------------------------------------------------
# describe_event
# ---------------------------------------------------------------------------

class TestDescribeEvent:
    def test_includes_basic_fields(self):
        text = describe_event(_ev())
        assert "s1@d1" in text
        assert "modbus" in text
        assert "pressure" in text
        assert "Pa" in text

    def test_payload_preview_truncated(self):
        ev = _ev(payload=b"\x00" * 200)
        text = describe_event(ev, max_payload_preview=8)
        # Should contain "..." marker and only 8 bytes worth of hex
        assert "..." in text

    def test_float64_hint(self):
        ev = _ev(payload=struct.pack("<d", 3.14))
        text = describe_event(ev)
        assert "f64 LE" in text
        assert "3.14" in text
