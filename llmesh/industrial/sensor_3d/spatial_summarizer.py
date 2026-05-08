"""SpatialSummarizer — 3D sensor data → LLM-friendly text summary (v1.7.0).

Converts SensorEvents from AOI, depth, and DVS event cameras into concise
natural-language descriptions suitable for passing to an LLM backend.

Integrates with the existing LLMesh privacy pipeline — call
:meth:`SpatialSummarizer.summarize` before forwarding a 3D sensor event to
an LLM so that raw pixel/point data is never sent verbatim.

Usage::

    summarizer = SpatialSummarizer()
    text = summarizer.summarize(sensor_event)
    # → "AOI [board_001] OK — 0 defects detected."
    # → "Depth frame [realsense_01] 15,234 points; z 0.42–3.18 m, mean 1.87 m"
    # → "DVS [prophesee_01] 4,096 events; +2,048 / -2,048; dt 12,500 µs"

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Payload bytes are never included verbatim in the summary string.
- PointCloud decode is bounded (MAX_POINTS_FOR_STATS).
"""
from __future__ import annotations

from typing import Any

from llmesh.industrial.sensor_event import SensorEvent
from llmesh.industrial.sensor_3d.point_cloud import PointCloud

_MAX_POINTS_FOR_STATS = 100_000   # avoid O(N) cost on huge clouds
_DVS_EVENT_BYTES = 9


class SpatialSummarizer:
    """Convert 3D SensorEvents into LLM-readable summary strings.

    Parameters
    ----------
    max_defects_shown:
        Maximum number of AOI defects to enumerate in the summary.
    """

    def __init__(self, *, max_defects_shown: int = 5) -> None:
        self._max_defects = max_defects_shown

    def summarize(self, event: SensorEvent) -> str:
        """Return a natural-language summary of *event*.

        Falls back to a generic description for unknown sensor types.
        """
        st = event.sensor_type
        if st == "aoi_image":
            return self._summarize_aoi(event)
        if st == "depth_frame":
            return self._summarize_depth(event)
        if st == "dvs_events":
            return self._summarize_dvs(event)
        return (
            f"3D sensor event [{event.sensor_id}] "
            f"protocol={event.protocol} type={st or 'unknown'} "
            f"payload={len(event.payload)} bytes"
        )

    # ------------------------------------------------------------------
    # AOI
    # ------------------------------------------------------------------

    def _summarize_aoi(self, event: SensorEvent) -> str:
        meta = event.metadata
        result = str(meta.get("aoi_result", "unknown")).upper()
        defect_count = int(meta.get("defect_count", 0))
        board_id = str(meta.get("board_id", "")) or event.sensor_id
        size = int(meta.get("size_bytes", len(event.payload)))

        parts = [f"AOI [{board_id}] {result}"]
        if defect_count == 0:
            parts.append("— 0 defects detected.")
        else:
            parts.append(f"— {defect_count} defect(s) detected.")
            defects: list[dict[str, Any]] = meta.get("defects", [])
            for d in defects[: self._max_defects]:
                label = d.get("label", "?")
                conf = d.get("confidence")
                bbox = d.get("bbox")
                detail = label
                if conf is not None:
                    detail += f" ({conf:.0%})"
                if bbox:
                    detail += f" bbox={bbox}"
                parts.append(f"  · {detail}")
            if len(defects) > self._max_defects:
                parts.append(f"  · ... and {len(defects) - self._max_defects} more.")
        parts.append(f"[image {size} bytes]")
        return " ".join(parts[:1]) + " " + " ".join(parts[1:])

    # ------------------------------------------------------------------
    # Depth
    # ------------------------------------------------------------------

    def _summarize_depth(self, event: SensorEvent) -> str:
        meta = event.metadata
        w = meta.get("width", "?")
        h = meta.get("height", "?")
        device = event.device_id or event.sensor_id

        # Try to decode a sample of the point cloud
        payload = event.payload
        n_points = len(payload) // 12
        sample_n = min(n_points, _MAX_POINTS_FOR_STATS)
        sample_bytes = payload[: sample_n * 12]

        if sample_n > 0:
            pc = PointCloud.from_bytes(sample_bytes)
            stats = pc.stats()
            z_min, z_max = stats.get("z_range", (0.0, 0.0))
            cx, cy, cz = stats.get("centroid", (0.0, 0.0, 0.0))
            return (
                f"Depth frame [{device}] {n_points:,} points "
                f"({w}×{h} px); z {z_min:.2f}–{z_max:.2f} m, "
                f"centroid ({cx:.2f}, {cy:.2f}, {cz:.2f}) m"
            )
        return (
            f"Depth frame [{device}] 0 valid points "
            f"({w}×{h} px)"
        )

    # ------------------------------------------------------------------
    # DVS
    # ------------------------------------------------------------------

    def _summarize_dvs(self, event: SensorEvent) -> str:
        meta = event.metadata
        n = int(meta.get("event_count", len(event.payload) // _DVS_EVENT_BYTES))
        pos = int(meta.get("positive_events", 0))
        neg = int(meta.get("negative_events", 0))
        dt = int(meta.get("duration_us", 0))
        device = event.device_id or event.sensor_id
        return (
            f"DVS [{device}] {n:,} events; "
            f"+{pos:,} / -{neg:,}; "
            f"Δt {dt:,} µs"
        )
