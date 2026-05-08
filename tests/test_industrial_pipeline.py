"""Tests for IndustrialPipeline (v2.0.0)."""
from __future__ import annotations

import struct
import pytest
from unittest.mock import MagicMock

from llmesh.industrial.pipeline import (
    IndustrialPipeline,
    DiagnosisResult,
    DiagnosisStatus,
)
from llmesh.industrial.sensor_event import SensorEvent, Priority


def _ev(sensor_id="s1", device_id="d1", payload=b"", **meta) -> SensorEvent:
    return SensorEvent.create(
        sensor_id=sensor_id, protocol="test", payload=payload,
        device_id=device_id, metadata=meta,
    )


class TestProcessNoAnalyzers:
    def test_returns_unknown(self):
        p = IndustrialPipeline()
        d = p.process(_ev())
        assert d.status is DiagnosisStatus.UNKNOWN
        assert d.severity == 0.0


class TestMtAnalyzer:
    def _engine(self, md_value: float):
        eng = MagicMock()
        eng.md.return_value = md_value
        return eng

    def test_normal(self):
        p = IndustrialPipeline()
        p.attach_mt("d1", self._engine(1.5), threshold=3.0)
        payload = struct.pack("<dd", 0.1, 0.2)
        d = p.process(_ev(payload=payload))
        assert d.status is DiagnosisStatus.NORMAL
        assert "MD=1.50" in d.summary

    def test_anomaly(self):
        p = IndustrialPipeline()
        p.attach_mt("d1", self._engine(5.0), threshold=3.0)
        payload = struct.pack("<dd", 0.1, 0.2)
        d = p.process(_ev(payload=payload))
        assert d.status is DiagnosisStatus.ANOMALY
        assert d.severity > 0.5
        assert "anomalous" in d.summary

    def test_custom_extractor(self):
        p = IndustrialPipeline()
        eng = self._engine(2.0)
        p.attach_mt("d1", eng, threshold=3.0,
                    feature_extractor=lambda e: [1.0, 2.0, 3.0])
        p.process(_ev())
        eng.md.assert_called_once_with([1.0, 2.0, 3.0])

    def test_engine_exception_does_not_crash(self):
        p = IndustrialPipeline()
        eng = MagicMock()
        eng.md.side_effect = RuntimeError("boom")
        p.attach_mt("d1", eng, threshold=3.0)
        d = p.process(_ev(payload=struct.pack("<d", 1.0)))
        assert d.status is DiagnosisStatus.UNKNOWN


class TestCusumAnalyzer:
    def test_in_control(self):
        p = IndustrialPipeline()
        p.attach_cusum("s1", target=100.0, k=0.5, h=4.0, sigma=1.0)
        d = p.process(_ev(payload=struct.pack("<d", 100.5)))
        assert d.status is DiagnosisStatus.NORMAL

    def test_out_of_control(self):
        p = IndustrialPipeline()
        p.attach_cusum("s1", target=100.0, k=0.5, h=2.0, sigma=1.0)
        # Inject several large values to trigger out-of-control
        last = None
        for _ in range(20):
            last = p.process(_ev(payload=struct.pack("<d", 110.0)))
        assert last.status is DiagnosisStatus.WARNING
        assert "out-of-control" in last.summary

    def test_uses_physical_value_metadata(self):
        p = IndustrialPipeline()
        p.attach_cusum("s1", target=0.0, k=0.5, h=4.0, sigma=1.0)
        d = p.process(_ev(physical_value=0.1))
        assert d.status is DiagnosisStatus.NORMAL


class TestXbarRAnalyzer:
    def test_buffers_until_subgroup_full(self):
        p = IndustrialPipeline()
        chart = MagicMock()
        chart.check.return_value = MagicMock(in_control=True, violations=[], extra={})
        p.attach_xbar_r("s1", chart=chart, subgroup_size=3)

        d1 = p.process(_ev(payload=struct.pack("<d", 1.0)))
        assert d1.status is DiagnosisStatus.UNKNOWN  # not yet enough samples
        d2 = p.process(_ev(payload=struct.pack("<d", 2.0)))
        assert d2.status is DiagnosisStatus.UNKNOWN
        d3 = p.process(_ev(payload=struct.pack("<d", 3.0)))
        assert d3.status is DiagnosisStatus.NORMAL
        chart.check.assert_called_once_with([1.0, 2.0, 3.0])

    def test_out_of_control(self):
        p = IndustrialPipeline()
        chart = MagicMock()
        chart.check.return_value = MagicMock(
            in_control=False, violations=["Xbar > UCL"], extra={"xbar": 5.0},
        )
        p.attach_xbar_r("s1", chart=chart, subgroup_size=2)
        p.process(_ev(payload=struct.pack("<d", 1.0)))
        d = p.process(_ev(payload=struct.pack("<d", 9.0)))
        assert d.status is DiagnosisStatus.WARNING


class TestPipelineCallbacks:
    def test_callback_receives_diagnosis(self):
        p = IndustrialPipeline()
        seen: list[DiagnosisResult] = []
        p.on_diagnosis(seen.append)
        p.process(_ev())
        assert len(seen) == 1

    def test_callback_exception_does_not_crash(self):
        p = IndustrialPipeline()
        p.on_diagnosis(lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
        p.process(_ev())  # must not raise

    def test_max_severity_wins(self):
        """When multiple analyzers fire, highest severity wins."""
        p = IndustrialPipeline()
        # MT returns anomaly with high severity
        eng = MagicMock()
        eng.md.return_value = 10.0
        p.attach_mt("d1", eng, threshold=3.0)
        # CUSUM in control (low severity)
        p.attach_cusum("s1", target=100.0, k=0.5, h=4.0, sigma=1.0)
        d = p.process(_ev(payload=struct.pack("<d", 100.0)))
        assert d.status is DiagnosisStatus.ANOMALY


class TestDiagnosisResult:
    def test_to_prompt_text(self):
        d = DiagnosisResult(
            sensor_id="s1", device_id="d1",
            status=DiagnosisStatus.ANOMALY,
            severity=0.9, summary="MD=10.0",
            evidence={"md": 10.0}, source_protocol="modbus",
        )
        text = d.to_prompt_text()
        assert "ANOMALY" in text
        assert "s1@d1" in text
        assert "MD=10.0" in text
        assert "md=10.0" in text


class TestValueExtractor:
    def test_payload_float64(self):
        p = IndustrialPipeline()
        p.attach_cusum("s1", target=0.0, k=0.5, h=4.0, sigma=1.0)
        d = p.process(_ev(payload=struct.pack("<d", 1.5)))
        assert d.evidence["value"] == 1.5

    def test_payload_float32_fallback(self):
        p = IndustrialPipeline()
        p.attach_cusum("s1", target=0.0, k=0.5, h=4.0, sigma=1.0)
        d = p.process(_ev(payload=struct.pack("<f", 1.5)))
        assert d.evidence["value"] == pytest.approx(1.5)

    def test_no_payload_raises_in_extractor(self):
        p = IndustrialPipeline()
        p.attach_cusum("s1", target=0.0, k=0.5, h=4.0, sigma=1.0)
        d = p.process(_ev(payload=b""))
        # internal exception → analyzer returns None → status UNKNOWN
        assert d.status is DiagnosisStatus.UNKNOWN
