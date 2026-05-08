"""Serialization benchmarks for LLMesh Industrial (v2.2.0+).

Measures encode/decode throughput for the hot paths that are candidates
for the Rust extension (C-12).  Run::

    python benchmarks/bench_serialization.py

Output is a small markdown table on stdout — convenient for pasting into
release notes or comparing before/after a Rust port.
"""
from __future__ import annotations

import statistics
import struct
import time
from typing import Callable

from llmesh.industrial.sensor_3d.point_cloud import PointCloud
from llmesh.industrial.sensor_3d.event_adapter import (
    DvsEvent, encode_dvs_events, decode_dvs_events,
)


# ---------------------------------------------------------------------------
# Benchmark constants
# ---------------------------------------------------------------------------

# Number of repetitions per benchmark — high enough to stabilise timing,
# low enough to keep total runtime under a few seconds.
_REPEATS = 5

# Workload sizes (points / events) — chosen to span 1k → 1M.
_WORKLOAD_SIZES = (1_000, 10_000, 100_000, 1_000_000)


def _bench(name: str, fn: Callable[[], object], n_items: int) -> dict:
    """Run *fn* `_REPEATS` times, return throughput statistics."""
    timings_ns: list[int] = []
    for _ in range(_REPEATS):
        t0 = time.perf_counter_ns()
        fn()
        t1 = time.perf_counter_ns()
        timings_ns.append(t1 - t0)
    median = statistics.median(timings_ns)
    items_per_s = n_items / (median / 1e9)
    return {
        "name": name,
        "n": n_items,
        "median_us": median / 1000,
        "items_per_s": items_per_s,
    }


# ---------------------------------------------------------------------------
# PointCloud benchmarks
# ---------------------------------------------------------------------------

def _gen_points(n: int) -> list[tuple[float, float, float]]:
    return [(i * 0.001, i * 0.002, i * 0.003) for i in range(n)]


def bench_pointcloud_encode(n: int) -> dict:
    pts = _gen_points(n)
    pc = PointCloud(points=pts)
    return _bench(f"PointCloud.to_bytes({n:,})", pc.to_bytes, n)


def bench_pointcloud_decode(n: int) -> dict:
    pts = _gen_points(n)
    raw = PointCloud(points=pts).to_bytes()
    return _bench(f"PointCloud.from_bytes({n:,})",
                  lambda: PointCloud.from_bytes(raw), n)


# ---------------------------------------------------------------------------
# DVS benchmarks
# ---------------------------------------------------------------------------

def _gen_events(n: int) -> list[DvsEvent]:
    return [DvsEvent(x=i & 0xFFFF, y=(i >> 4) & 0xFFFF,
                     t_us=i, polarity=bool(i & 1))
            for i in range(n)]


def bench_dvs_encode(n: int) -> dict:
    events = _gen_events(n)
    return _bench(f"encode_dvs_events({n:,})",
                  lambda: encode_dvs_events(events), n)


def bench_dvs_decode(n: int) -> dict:
    raw = encode_dvs_events(_gen_events(n))
    return _bench(f"decode_dvs_events({n:,})",
                  lambda: decode_dvs_events(raw), n)


# ---------------------------------------------------------------------------
# Pipeline benchmark — small but representative
# ---------------------------------------------------------------------------

def bench_pipeline_cusum(n: int) -> dict:
    from llmesh.industrial import IndustrialPipeline, SensorEvent

    pipeline = IndustrialPipeline()
    pipeline.attach_cusum(
        sensor_id="s1", target=100.0, k=0.5, h=4.0, sigma=1.0,
    )
    payload = struct.pack("<d", 100.0)
    ev = SensorEvent.create(sensor_id="s1", protocol="bench", payload=payload)

    def run() -> None:
        for _ in range(n):
            pipeline.process(ev)

    return _bench(f"IndustrialPipeline.process+CUSUM ({n:,})", run, n)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    print("# LLMesh Industrial — Serialization & Pipeline Benchmarks")
    print()
    print("Repeats: ", _REPEATS, " | Workload sizes: ", _WORKLOAD_SIZES)
    print()
    print("| Operation | n | Median (µs) | Throughput (items/s) |")
    print("|-----------|---:|------------:|---------------------:|")

    for n in _WORKLOAD_SIZES:
        for fn in (bench_pointcloud_encode, bench_pointcloud_decode,
                   bench_dvs_encode, bench_dvs_decode):
            r = fn(n)
            print(f"| {r['name']} | {r['n']:,} | "
                  f"{r['median_us']:,.1f} | {r['items_per_s']:,.0f} |")

    # Pipeline benchmark with smaller n (CUSUM is per-event)
    for n in (1_000, 10_000):
        r = bench_pipeline_cusum(n)
        print(f"| {r['name']} | {r['n']:,} | "
              f"{r['median_us']:,.1f} | {r['items_per_s']:,.0f} |")


if __name__ == "__main__":
    main()
