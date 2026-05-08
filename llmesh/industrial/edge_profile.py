"""EdgeProfile — resource-constrained operation profile (v2.6).

Pre-tuned configuration bundles for running LLMesh on edge devices
where RAM, CPU, and power are scarce.  A profile sets reasonable
defaults for queue sizes, polling intervals, retention windows, and
analyzer options so a single import gets you "edge-safe" behaviour.

Usage::

    from llmesh.industrial.edge_profile import apply_profile, EdgePreset

    apply_profile(EdgePreset.MICRO)       # 256 MB RAM tier (Pi Zero 2 W)
    apply_profile(EdgePreset.NANO)        # 512 MB tier
    apply_profile(EdgePreset.SMALL)       # 1 GB tier (Pi 4 1GB)
    apply_profile(EdgePreset.MEDIUM)      # 4 GB tier (Pi 5, Jetson Nano)
    apply_profile(EdgePreset.WORKSTATION) # default

Each preset adjusts:
    * IndustrialMetrics series cap
    * IndustrialTracer span retention
    * AOI/Depth/DVS adapter `_seen` cap and stability tolerance
    * DVS max events per batch
    * Default poll intervals

The profile only mutates module-level *constants* — already-running
adapters keep their original sizes.  Apply at process start.

Security invariants
-------------------
- No shell, no eval, no pickle.
- Profile values clamped to safe minima.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Floor values to prevent presets from disabling features entirely.
_MIN_SEEN_CAP = 32
_MIN_SPAN_RETENTION = 16
_MIN_METRICS_SERIES = 32
_MIN_DVS_BATCH = 256


class EdgePreset(Enum):
    MICRO = "micro"               # ≤ 256 MB RAM
    NANO = "nano"                 # ≤ 512 MB RAM
    SMALL = "small"               # ≤ 1 GB RAM
    MEDIUM = "medium"             # ≤ 4 GB RAM
    WORKSTATION = "workstation"   # default (no clamping)


# ---------------------------------------------------------------------------
# Preset definitions
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Profile:
    name: str
    seen_cap: int
    span_retention: int
    metrics_series_cap: int
    dvs_max_events: int
    default_poll_s: float
    description: str


_PROFILES: dict[EdgePreset, _Profile] = {
    EdgePreset.MICRO: _Profile(
        name="micro",
        seen_cap=64,
        span_retention=128,
        metrics_series_cap=512,
        dvs_max_events=10_000,
        default_poll_s=2.0,
        description="≤256 MB RAM (Raspberry Pi Zero 2 W, ESP32-S3 + Linux)",
    ),
    EdgePreset.NANO: _Profile(
        name="nano",
        seen_cap=256,
        span_retention=512,
        metrics_series_cap=2_048,
        dvs_max_events=50_000,
        default_poll_s=1.0,
        description="≤512 MB RAM (Pi 3B, BeagleBone, low-end OpenWrt)",
    ),
    EdgePreset.SMALL: _Profile(
        name="small",
        seen_cap=1_024,
        span_retention=2_048,
        metrics_series_cap=10_000,
        dvs_max_events=200_000,
        default_poll_s=0.5,
        description="≤1 GB RAM (Pi 4 1GB, Jetson Nano)",
    ),
    EdgePreset.MEDIUM: _Profile(
        name="medium",
        seen_cap=4_096,
        span_retention=5_000,
        metrics_series_cap=50_000,
        dvs_max_events=500_000,
        default_poll_s=0.2,
        description="≤4 GB RAM (Pi 5, Jetson Orin Nano)",
    ),
    EdgePreset.WORKSTATION: _Profile(
        name="workstation",
        seen_cap=10_000,
        span_retention=10_000,
        metrics_series_cap=100_000,
        dvs_max_events=1_000_000,
        default_poll_s=0.1,
        description="default (server / workstation)",
    ),
}


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_profile(preset: EdgePreset) -> _Profile:
    """Mutate module-level caps to the given preset.  Returns the profile."""
    if preset not in _PROFILES:
        raise ValueError(f"unknown preset: {preset}")
    p = _PROFILES[preset]

    # Clamp to safe minima
    seen_cap = max(_MIN_SEEN_CAP, p.seen_cap)
    span_retention = max(_MIN_SPAN_RETENTION, p.span_retention)
    metrics_series_cap = max(_MIN_METRICS_SERIES, p.metrics_series_cap)
    dvs_max_events = max(_MIN_DVS_BATCH, p.dvs_max_events)

    # Override module constants
    from llmesh.industrial.sensor_3d import aoi_adapter, depth_adapter, event_adapter
    aoi_adapter._SEEN_SET_MAX = seen_cap
    depth_adapter._SEEN_SET_MAX = seen_cap
    event_adapter._SEEN_SET_MAX = seen_cap
    event_adapter._MAX_EVENTS_PER_BATCH = dvs_max_events

    from llmesh.industrial import metrics, tracing
    metrics._MAX_SERIES = metrics_series_cap
    tracing._MAX_SPANS_RETAINED = span_retention

    logger.info(
        "EdgeProfile applied: %s (seen_cap=%d, span_retention=%d, "
        "metrics_series=%d, dvs_max=%d, default_poll=%.2fs)",
        p.name, seen_cap, span_retention, metrics_series_cap,
        dvs_max_events, p.default_poll_s,
    )
    return p


def detect_recommended_preset() -> EdgePreset:
    """Best-effort recommendation based on available system memory."""
    try:
        # psutil is optional — fall back to MEDIUM if absent
        import psutil          # type: ignore[import-not-found]
        total_gb = psutil.virtual_memory().total / (1024 ** 3)
    except ImportError:
        return EdgePreset.MEDIUM

    if total_gb <= 0.30:
        return EdgePreset.MICRO
    if total_gb <= 0.60:
        return EdgePreset.NANO
    if total_gb <= 1.10:
        return EdgePreset.SMALL
    if total_gb <= 4.20:
        return EdgePreset.MEDIUM
    return EdgePreset.WORKSTATION


def list_profiles() -> dict[str, str]:
    """Return name → description map for all presets."""
    return {p.value: _PROFILES[p].description for p in EdgePreset}
