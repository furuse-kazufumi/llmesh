"""Tests for the llove JSONL exporter."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pytest

from llmesh.export.llove import LloveJSONLExporter, dump_llove_jsonl


@dataclass
class _FakeSensorEvent:
    sensor_id: str
    sensor_type: str
    value: float
    quality: str = "good"
    ts: float = 0.0
    meta: dict = field(default_factory=dict)


@dataclass
class _FakeIncidentReport:
    sensor_id: str
    cusum: float
    threshold: float
    ts: datetime
    hypothesis: str
    tokens: int = 200
    latency_ms: int = 350


def _read_lines(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_sensor_event_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "out.jsonl"
    sev = _FakeSensorEvent(sensor_id="s1", sensor_type="temperature", value=23.4)
    with LloveJSONLExporter(p) as ex:
        ex.feed_sensor_event(sev)
    lines = _read_lines(p)
    assert len(lines) == 1
    assert lines[0]["kind"] == "sensor"
    assert lines[0]["payload"]["sensor_id"] == "s1"
    assert lines[0]["payload"]["value"] == 23.4


def test_incident_report_writes_alarm_and_llm(tmp_path: Path) -> None:
    p = tmp_path / "incident.jsonl"
    rep = _FakeIncidentReport(
        sensor_id="bearing_temp_07",
        cusum=9.4,
        threshold=5.0,
        ts=datetime(2026, 5, 9, 3, 22, 11, tzinfo=timezone.utc),
        hypothesis="Drift began ~12 minutes prior; bearing wear plausible.",
    )
    with LloveJSONLExporter(p) as ex:
        ex.feed_incident_report(rep)
    lines = _read_lines(p)
    kinds = [line["kind"] for line in lines]
    assert "spc_alarm" in kinds
    assert "llm_call" in kinds
    llm_line = next(line for line in lines if line["kind"] == "llm_call")
    assert "Drift began" in llm_line["payload"]["hypothesis"]


def test_audit_entry_passthrough(tmp_path: Path) -> None:
    p = tmp_path / "audit.jsonl"
    entry = {
        "event": "firewall.allow",
        "layer": "L2",
        "user": "ops",
        "ts": "2026-05-09T03:22:11Z",
    }
    with LloveJSONLExporter(p) as ex:
        ex.feed_audit_entry(entry)
    lines = _read_lines(p)
    assert lines[0]["kind"] == "audit"
    assert lines[0]["payload"]["event"] == "firewall.allow"
    assert lines[0]["ts"] == "2026-05-09T03:22:11Z"


def test_dump_helper_writes_full_snapshot(tmp_path: Path) -> None:
    p = tmp_path / "snap.jsonl"
    sevs = [_FakeSensorEvent(sensor_id=f"s{i}", sensor_type="t", value=float(i)) for i in range(3)]
    audits = [{"event": "ok", "ts": "2026-05-09T00:00:00Z"}]
    reports = [
        _FakeIncidentReport(
            sensor_id="s1",
            cusum=7.0,
            threshold=5.0,
            ts=datetime.now(tz=timezone.utc),
            hypothesis="test",
        )
    ]
    out = dump_llove_jsonl(p, sensor_events=sevs, audit_entries=audits, incident_reports=reports)
    assert out == p
    kinds = [line["kind"] for line in _read_lines(p)]
    assert kinds.count("sensor") == 3
    assert "audit" in kinds
    assert "spc_alarm" in kinds
    assert "llm_call" in kinds


def test_writer_requires_context_manager(tmp_path: Path) -> None:
    """Calling feed_* outside ``with`` should raise rather than silently lose data."""
    ex = LloveJSONLExporter(tmp_path / "x.jsonl")
    with pytest.raises(RuntimeError):
        ex.feed_audit_entry({"event": "x"})


def test_llm_call_records_extra_fields(tmp_path: Path) -> None:
    p = tmp_path / "llm.jsonl"
    with LloveJSONLExporter(p) as ex:
        ex.feed_llm_call(tokens=200, latency_ms=350, model="llama3.2", run_id="r-42")
    line = _read_lines(p)[0]
    assert line["payload"]["model"] == "llama3.2"
    assert line["payload"]["run_id"] == "r-42"
