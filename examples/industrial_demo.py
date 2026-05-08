"""industrial_demo.py — End-to-end Industrial Phase A–G demonstration.

Demonstrates the full integration: a simulated production line emits
SensorEvents from "Modbus", "OPC-UA", "MQTT", and "AOI" sources; an
IndustrialPipeline runs CUSUM and Xbar-R analyzers and a TenantScope
forwards diagnoses to a Prometheus-compatible IndustrialMetrics
collector exposed over HTTP at http://127.0.0.1:9100/metrics

Run::

    python examples/industrial_demo.py

In another terminal::

    curl http://127.0.0.1:9100/metrics

This example uses no external broker / PLC — every adapter is replaced
by an in-process emitter so the demo runs anywhere.
"""
from __future__ import annotations

import asyncio
import random
import struct
import time

from llmesh.industrial import (
    SensorEvent, Priority,
    IndustrialPipeline, DiagnosisStatus,
    IndustrialMetrics,
    TenantScope,
    XbarRChart,
)


METRICS_HTTP_PORT = 9100      # /metrics scrape endpoint
SIMULATION_SECONDS = 30
EMIT_INTERVAL_S = 0.2

PRESSURE_TARGET = 101_325.0   # Pa, ambient
PRESSURE_SIGMA = 50.0


def _pressure_event(rng: random.Random, drift_step: int) -> SensorEvent:
    """Simulate an ambient pressure reading with optional drift after step 60."""
    drift = drift_step * 0.5 if drift_step > 60 else 0.0
    value = rng.gauss(PRESSURE_TARGET + drift, PRESSURE_SIGMA)
    return SensorEvent.create(
        sensor_id="acme_pressure_01",
        protocol="modbus",
        payload=struct.pack("<d", value),
        sensor_type="pressure", unit="Pa",
        device_id="line_a",
        priority=Priority.NORMAL,
    )


def _torque_event(rng: random.Random) -> SensorEvent:
    value = rng.gauss(15.0, 0.3)
    return SensorEvent.create(
        sensor_id="acme_torque_01",
        protocol="ethercat",
        payload=struct.pack("<d", value),
        sensor_type="torque", unit="Nm",
        device_id="line_a",
        metadata={"physical_value": value},
    )


async def _emit_loop(pipeline: IndustrialPipeline, metrics: IndustrialMetrics) -> None:
    rng = random.Random(42)
    step = 0
    end = time.monotonic() + SIMULATION_SECONDS

    while time.monotonic() < end:
        for ev in (_pressure_event(rng, step), _torque_event(rng)):
            d = pipeline.process(ev)
            metrics.increment(
                "industrial_diagnoses_total",
                labels={"sensor": ev.sensor_id, "status": d.status.value},
                help_text="diagnoses produced by IndustrialPipeline",
            )
            metrics.set_gauge(
                "industrial_severity",
                d.severity,
                labels={"sensor": ev.sensor_id},
                help_text="severity of last diagnosis (0–1)",
            )
        step += 1
        await asyncio.sleep(EMIT_INTERVAL_S)


async def main() -> None:
    pipeline = IndustrialPipeline()
    metrics = IndustrialMetrics()

    # CUSUM for pressure drift detection
    pipeline.attach_cusum(
        sensor_id="acme_pressure_01",
        target=PRESSURE_TARGET, k=0.5, h=4.0, sigma=PRESSURE_SIGMA,
    )
    # Xbar-R for torque variation
    pipeline.attach_xbar_r(
        sensor_id="acme_torque_01",
        chart=XbarRChart(),
        subgroup_size=5,
    )

    # Tenant filter — only forward acme_* sensors
    tenant = TenantScope("acme", allow_sensor_prefixes={"acme_"})

    def _print_diagnosis(d):
        print(d.to_prompt_text())
        print()

    pipeline.on_diagnosis(tenant.wrap_callback(_print_diagnosis))

    await metrics.serve_http("127.0.0.1", METRICS_HTTP_PORT)
    print(f"Prometheus metrics: http://127.0.0.1:{METRICS_HTTP_PORT}/metrics")
    print(f"Simulating for {SIMULATION_SECONDS}s ...")

    try:
        await _emit_loop(pipeline, metrics)
    finally:
        await metrics.stop_http()
        print("\n=== Final metrics snapshot ===")
        print(metrics.render())
        print(f"Tenant forwarded={tenant.forwarded} dropped={tenant.dropped}")


if __name__ == "__main__":
    asyncio.run(main())
