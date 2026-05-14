"""End-to-end integration tests for Industrial Phase A–G + v3 (v2.2.0).

Exercises the full chain in-memory without external services:

    SensorEvent producer → IndustrialPipeline → DiagnosisResult
                                    │
                                    ├→ TenantScope filter / wrap
                                    ├→ IndustrialMetrics counters
                                    └→ IndustrialTracer spans

Each test is a complete user-facing scenario, not a unit test of a
single class — these are the safety net against silent integration
regressions.
"""
from __future__ import annotations

import asyncio
import struct
from unittest.mock import MagicMock
import pytest

from llmesh.industrial import (
    SensorEvent, IndustrialPipeline, DiagnosisStatus, DiagnosisResult,
    IndustrialMetrics,
    TenantScope, TenantRegistry,
    IndustrialTracer, SPAN_STATUS_OK,
    XbarRChart,
)


# ---------------------------------------------------------------------------
# Helper: build a SensorEvent with a numeric payload
# ---------------------------------------------------------------------------

def _ev(value: float, *, sensor_id: str, device_id: str = "line_a",
        protocol: str = "modbus") -> SensorEvent:
    return SensorEvent.create(
        sensor_id=sensor_id, protocol=protocol, device_id=device_id,
        payload=struct.pack("<d", value),
        sensor_type="numeric",
    )


# ---------------------------------------------------------------------------
# Scenario 1: pipeline + tenant + metrics
# ---------------------------------------------------------------------------

class TestPipelineTenantMetrics:
    def test_full_pipeline_metrics_chain(self):
        pipeline = IndustrialPipeline()
        metrics = IndustrialMetrics()

        # Pipeline: CUSUM on pressure
        pipeline.attach_cusum(
            sensor_id="acme/pressure_01",
            target=100.0, k=0.5, h=2.0, sigma=1.0,
        )

        # Tenant filter at SensorEvent ingest stage; the wrapper prefixes
        # raw sensor_id "pressure_01" → "acme/pressure_01".
        tenant = TenantScope("acme", allow_sensor_prefixes={"pressure_01"})

        def collect_metric(d: DiagnosisResult) -> None:
            metrics.increment(
                "diagnoses_total",
                labels={"status": d.status.value},
            )

        pipeline.on_diagnosis(collect_metric)
        ingest = tenant.wrap_callback(pipeline.process)

        for v in [100.0, 100.1, 99.9, 100.0, 100.0]:
            ingest(_ev(v, sensor_id="pressure_01"))
        for v in [110.0, 112.0, 115.0, 118.0, 120.0]:
            ingest(_ev(v, sensor_id="pressure_01"))

        # Some normals + at least one warning expected
        normal = metrics.get("diagnoses_total", labels={"status": "normal"})
        warning = metrics.get("diagnoses_total", labels={"status": "warning"})
        assert (normal or 0) >= 1
        assert (warning or 0) >= 1
        assert tenant.forwarded == 10
        assert tenant.dropped == 0

    def test_cross_tenant_drops(self):
        pipeline = IndustrialPipeline()
        tenant = TenantScope("acme", allow_sensor_prefixes={"acme/"})
        seen: list[DiagnosisResult] = []
        pipeline.on_diagnosis(seen.append)

        # Tenant-gated ingest
        ingest = tenant.wrap_callback(pipeline.process)
        ingest(_ev(1.0, sensor_id="globex/pressure_01"))   # cross-tenant
        assert seen == []
        assert tenant.dropped == 1


# ---------------------------------------------------------------------------
# Scenario 2: tenant registry fan-out
# ---------------------------------------------------------------------------

class TestTenantRegistryFanout:
    def test_event_routes_to_correct_tenants(self):
        reg = TenantRegistry()
        acme = TenantScope("acme", allow_sensor_prefixes={"acme/"})
        globex = TenantScope("globex", allow_sensor_prefixes={"globex/"})
        reg.register(acme)
        reg.register(globex)

        ev = _ev(1.0, sensor_id="acme/p1")
        delivered = reg.fanout(ev)
        assert delivered == 1   # only acme allows


# ---------------------------------------------------------------------------
# Scenario 3: pipeline + tracing
# ---------------------------------------------------------------------------

class TestPipelineWithTracing:
    def test_tracer_captures_pipeline_call(self):
        tracer = IndustrialTracer()
        pipeline = IndustrialPipeline()
        pipeline.attach_cusum(
            sensor_id="s1", target=100.0, k=0.5, h=4.0, sigma=1.0,
        )

        with tracer.span("integration.cycle") as outer:
            with tracer.span("pipeline.process") as inner:
                d = pipeline.process(_ev(100.0, sensor_id="s1"))
                inner.set_attribute("status", d.status.value)
            outer.set_attribute("processed", 1)

        spans = tracer.collected_spans()
        assert len(spans) == 2
        # Inner finishes before outer
        names = {s.name for s in spans}
        assert names == {"integration.cycle", "pipeline.process"}

        outer_span = next(s for s in spans if s.name == "integration.cycle")
        inner_span = next(s for s in spans if s.name == "pipeline.process")
        assert inner_span.parent_span_id == outer_span.span_id
        assert inner_span.trace_id == outer_span.trace_id
        assert outer_span.status == SPAN_STATUS_OK


# ---------------------------------------------------------------------------
# Scenario 4: multiple analyzers — highest severity wins
# ---------------------------------------------------------------------------

class TestMultiAnalyzer:
    def test_max_severity_diagnosis_returned(self):
        pipeline = IndustrialPipeline()

        # MT: returns large MD => high severity
        eng = MagicMock()
        eng.md.return_value = 8.0
        pipeline.attach_mt("line_a", eng, threshold=3.0)

        # CUSUM: in-control => low severity
        pipeline.attach_cusum(
            sensor_id="line_a/p1", target=100.0, k=0.5, h=4.0, sigma=1.0,
        )

        d = pipeline.process(_ev(100.0, sensor_id="line_a/p1"))
        assert d.status is DiagnosisStatus.ANOMALY  # MT wins
        assert d.severity > 0.5


# ---------------------------------------------------------------------------
# Scenario 5: Xbar-R subgroup buffering
# ---------------------------------------------------------------------------

class TestXbarRSubgroupBuffering:
    def test_xbar_r_emits_only_when_subgroup_full(self):
        pipeline = IndustrialPipeline()
        chart = XbarRChart()
        chart.fit([
            [10.0, 10.1, 9.9],
            [10.0, 10.05, 9.95],
            [10.0, 10.02, 9.98],
        ])
        pipeline.attach_xbar_r("torque", chart=chart, subgroup_size=3)
        diagnoses: list[DiagnosisResult] = []
        pipeline.on_diagnosis(diagnoses.append)

        # Two events: not enough for subgroup
        pipeline.process(_ev(10.0, sensor_id="torque"))
        pipeline.process(_ev(10.0, sensor_id="torque"))
        assert all(d.status is DiagnosisStatus.UNKNOWN for d in diagnoses)

        # Third event triggers Xbar-R check
        pipeline.process(_ev(10.0, sensor_id="torque"))
        # Last diagnosis should be NORMAL or WARNING (not UNKNOWN)
        assert diagnoses[-1].status is not DiagnosisStatus.UNKNOWN


# ---------------------------------------------------------------------------
# Scenario 6: Metrics HTTP endpoint with real socket
# ---------------------------------------------------------------------------

class TestMetricsHttpEndToEnd:
    @pytest.mark.asyncio
    async def test_scrape_endpoint_returns_pipeline_data(self):
        import socket
        pipeline = IndustrialPipeline()
        metrics = IndustrialMetrics()

        pipeline.attach_cusum(
            sensor_id="s1", target=100.0, k=0.5, h=4.0, sigma=1.0,
        )

        def emit(d: DiagnosisResult) -> None:
            metrics.increment(
                "industrial_e2e_total",
                labels={"status": d.status.value},
            )

        pipeline.on_diagnosis(emit)

        # Ephemeral port
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
        await metrics.serve_http("127.0.0.1", port)
        try:
            for v in [100.0, 100.0, 100.0]:
                pipeline.process(_ev(v, sensor_id="s1"))

            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(-1), timeout=2.0)
            writer.close()
            await writer.wait_closed()
            assert b"200 OK" in response
            assert b"industrial_e2e_total" in response
        finally:
            await metrics.stop_http()


# ---------------------------------------------------------------------------
# Scenario 7: Pipeline robust to analyzer exceptions
# ---------------------------------------------------------------------------

class TestPipelineRobustness:
    def test_one_analyzer_failing_does_not_crash_others(self):
        pipeline = IndustrialPipeline()

        # MT engine that always raises
        bad_eng = MagicMock()
        bad_eng.md.side_effect = RuntimeError("boom")
        pipeline.attach_mt("line_a", bad_eng, threshold=3.0)

        # CUSUM that works fine
        pipeline.attach_cusum(
            sensor_id="line_a/p1", target=100.0, k=0.5, h=4.0, sigma=1.0,
        )

        # Should not raise, and CUSUM result wins (severity > UNKNOWN)
        d = pipeline.process(_ev(100.0, sensor_id="line_a/p1"))
        assert d.status is DiagnosisStatus.NORMAL  # from CUSUM


# ---------------------------------------------------------------------------
# Scenario 8: Tracer error capture via integration
# ---------------------------------------------------------------------------

class TestTracerErrorCapture:
    def test_exception_in_span_marked_error(self):
        tracer = IndustrialTracer()
        with pytest.raises(ValueError):
            with tracer.span("integration.fail") as s:
                s.set_attribute("attempt", 1)
                raise ValueError("intentional")

        spans = tracer.collected_spans()
        assert len(spans) == 1
        assert spans[0].status == "ERROR"
        assert "intentional" in spans[0].error_message
