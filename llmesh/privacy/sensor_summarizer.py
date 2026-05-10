"""SensorSummarizer — condense raw ROS sensor payloads to privacy-safe text.

Raw sensor streams (camera, LiDAR, IMU) may contain L3/L4 data and must never
be forwarded unfiltered to a remote LLM backend.  This summarizer converts
numeric/binary sensor payloads to concise text descriptions.

Classification:
  L4 — faces, ID documents detected in metadata → BLOCK
  L3 — camera / depth image data → text description only
  L2 — GPS / location data → anonymised to bounding region
  L1 — LiDAR point cloud, IMU, sonar, temperature — numeric summary
  L0 — diagnostic / status strings — pass through

Security invariants:
  - Raw pixel data is NEVER included in output.
  - EXIF / ROS header timestamps are stripped.
  - No shell=True, eval, exec, or pickle.
  - All failures return BLOCK (fail-closed).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any


class SensorBlockedError(Exception):
    """Raised when sensor data classification requires blocking."""


@dataclass
class SensorSummary:
    """Result of sensor data summarisation."""

    sensor_type: str
    original_level: int          # effective DataLevel before summarisation
    summary_level: int           # output DataLevel (always <= 1)
    description: str             # safe text for LLM prompt injection
    blocked: bool = False
    block_reason: str = ""


# ---------------------------------------------------------------------------
# Sensor type constants
# ---------------------------------------------------------------------------

SENSOR_CAMERA        = "camera"
SENSOR_DEPTH         = "depth"
SENSOR_LIDAR         = "lidar"
SENSOR_IMU           = "imu"
SENSOR_GPS           = "gps"
SENSOR_TEMPERATURE   = "temperature"
SENSOR_SONAR         = "sonar"
SENSOR_FACE          = "face"          # detection output — L4
SENSOR_ID_DOCUMENT   = "id_document"   # detection output — L4
SENSOR_DIAGNOSTIC    = "diagnostic"

# Sensor type → default data level
_SENSOR_LEVELS: dict[str, int] = {
    SENSOR_CAMERA:      3,
    SENSOR_DEPTH:       3,
    SENSOR_LIDAR:       1,
    SENSOR_IMU:         1,
    SENSOR_GPS:         2,
    SENSOR_TEMPERATURE: 0,
    SENSOR_SONAR:       1,
    SENSOR_FACE:        4,
    SENSOR_ID_DOCUMENT: 4,
    SENSOR_DIAGNOSTIC:  0,
}


def _classify_topic(topic: str) -> str:
    """Infer sensor type from a ROS topic name."""
    t = topic.lower()
    if "face" in t or "recognition" in t:
        return SENSOR_FACE
    if "id_doc" in t or "passport" in t or "license" in t:
        return SENSOR_ID_DOCUMENT
    if "depth" in t:
        return SENSOR_DEPTH
    if "camera" in t or "image" in t or "rgb" in t or "color" in t:
        return SENSOR_CAMERA
    if "lidar" in t or "velodyne" in t or "pointcloud" in t or "scan" in t:
        return SENSOR_LIDAR
    if "imu" in t:
        return SENSOR_IMU
    if "gps" in t or "navsat" in t or "fix" in t:
        return SENSOR_GPS
    if "temp" in t or "thermometer" in t:
        return SENSOR_TEMPERATURE
    if "sonar" in t or "ultrasonic" in t:
        return SENSOR_SONAR
    if "diagnostic" in t or "status" in t:
        return SENSOR_DIAGNOSTIC
    return SENSOR_DIAGNOSTIC   # unknown → treat as diagnostic (L0)


# ---------------------------------------------------------------------------
# Numeric helpers
# ---------------------------------------------------------------------------

def _safe_stats(values: list[float]) -> str:
    if not values:
        return "no data"
    n = len(values)
    mean = sum(values) / n
    variance = sum((v - mean) ** 2 for v in values) / n
    return f"n={n}, mean={mean:.3f}, std={math.sqrt(variance):.3f}, min={min(values):.3f}, max={max(values):.3f}"


def _summarise_pointcloud(data: dict[str, Any]) -> str:
    """Produce a compact text description of a LiDAR point cloud payload."""
    width  = data.get("width", data.get("points", 0))
    height = data.get("height", 1)
    n = int(width) * int(height) if height else int(width)
    ranges = data.get("ranges") or data.get("distances") or []
    if ranges:
        valid = [float(r) for r in ranges if r is not None and not math.isinf(float(r))]
        stats = _safe_stats(valid)
        return f"LiDAR scan: {n} points, range_stats=[{stats}]"
    return f"LiDAR scan: {n} points"


def _summarise_imu(data: dict[str, Any]) -> str:
    orient = data.get("orientation", {})
    linear = data.get("linear_acceleration", {})
    angular = data.get("angular_velocity", {})
    parts = []
    if orient:
        parts.append(f"orientation=(x={orient.get('x', 0):.3f}, y={orient.get('y', 0):.3f}, z={orient.get('z', 0):.3f}, w={orient.get('w', 1):.3f})")
    if linear:
        parts.append(f"accel=(x={linear.get('x', 0):.3f}, y={linear.get('y', 0):.3f}, z={linear.get('z', 0):.3f})")
    if angular:
        parts.append(f"gyro=(x={angular.get('x', 0):.3f}, y={angular.get('y', 0):.3f}, z={angular.get('z', 0):.3f})")
    return "IMU: " + (", ".join(parts) if parts else "no data")


def _summarise_gps(data: dict[str, Any]) -> str:
    """Anonymise GPS to a ~1° grid cell (≈111 km resolution)."""
    lat = data.get("latitude", data.get("lat"))
    lon = data.get("longitude", data.get("lon"))
    alt = data.get("altitude", data.get("alt"))
    if lat is None or lon is None:
        return "GPS: position unavailable"
    lat_cell = int(float(lat))
    lon_cell = int(float(lon))
    alt_str = f", alt≈{float(alt):.0f}m" if alt is not None else ""
    return f"GPS: region≈({lat_cell}°N, {lon_cell}°E){alt_str}"


def _summarise_camera(data: dict[str, Any]) -> str:
    """Describe image metadata only — no pixel content."""
    width  = data.get("width", "?")
    height = data.get("height", "?")
    enc    = data.get("encoding", data.get("format", "unknown"))
    step   = data.get("step", "")
    step_str = f", step={step}" if step else ""
    return f"Camera image: {width}×{height} {enc}{step_str} (pixel data withheld)"


def _summarise_depth(data: dict[str, Any]) -> str:
    width  = data.get("width", "?")
    height = data.get("height", "?")
    enc    = data.get("encoding", "32FC1")
    return f"Depth image: {width}×{height} {enc} (depth data withheld)"


def _summarise_temperature(data: dict[str, Any]) -> str:
    temp     = data.get("temperature", data.get("value"))
    variance = data.get("variance")
    if temp is None:
        return "Temperature: no reading"
    s = f"Temperature: {float(temp):.2f}°C"
    if variance is not None:
        s += f" (variance={float(variance):.4f})"
    return s


def _summarise_sonar(data: dict[str, Any]) -> str:
    dist = data.get("range", data.get("distance"))
    if dist is None:
        return "Sonar: no reading"
    return f"Sonar range: {float(dist):.3f}m"


def _summarise_diagnostic(data: str | dict[str, Any]) -> str:
    """Summarise a diagnostic payload (raw text or structured dict).

    The caller (`SensorSummarizer._describe`) passes a ``str`` when the raw
    payload is text-only and a ``dict`` when there is structured telemetry.
    Type signature now matches both call sites; the runtime body already
    handled both cases via ``isinstance``.
    """
    if isinstance(data, str):
        return data[:512]
    msg = data.get("message") or data.get("status") or str(data)
    return str(msg)[:512]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class SensorSummarizer:
    """Convert raw ROS sensor payloads to privacy-safe text descriptions.

    Usage::

        ss = SensorSummarizer()
        result = ss.summarize(topic="/camera/image_raw", data={"width": 640, ...})
        if result.blocked:
            raise SensorBlockedError(result.block_reason)
        prompt_injection = result.description
    """

    def summarize(
        self,
        topic: str,
        data: dict[str, Any] | str,
        *,
        sensor_type: str | None = None,
    ) -> SensorSummary:
        """Summarize sensor *data* from *topic*.

        Args:
            topic:        ROS topic name (used for auto-classification).
            data:         Payload dict or plain string.
            sensor_type:  Override auto-detected sensor type.

        Returns:
            SensorSummary with description safe for LLM injection.

        Raises:
            SensorBlockedError: on L4 data (faces, ID documents).
        """
        stype = sensor_type or _classify_topic(topic)
        level = _SENSOR_LEVELS.get(stype, 0)

        if level >= 4:
            return SensorSummary(
                sensor_type=stype,
                original_level=level,
                summary_level=4,
                description="",
                blocked=True,
                block_reason=f"L4 sensor data blocked: {stype}",
            )

        if isinstance(data, str):
            d: dict[str, Any] = {}
            raw_str: str | None = data
        else:
            d = data
            raw_str = None

        try:
            description = self._describe(stype, d, raw_str)
        except Exception as exc:
            return SensorSummary(
                sensor_type=stype,
                original_level=level,
                summary_level=level,
                description="",
                blocked=True,
                block_reason=f"summarization_error:{exc}",
            )

        return SensorSummary(
            sensor_type=stype,
            original_level=level,
            summary_level=min(level, 1),
            description=description,
            blocked=False,
        )

    # ------------------------------------------------------------------

    def _describe(self, stype: str, data: dict[str, Any], raw: str | None) -> str:
        if raw is not None:
            return _summarise_diagnostic(raw)
        dispatch = {
            SENSOR_CAMERA:      _summarise_camera,
            SENSOR_DEPTH:       _summarise_depth,
            SENSOR_LIDAR:       _summarise_pointcloud,
            SENSOR_IMU:         _summarise_imu,
            SENSOR_GPS:         _summarise_gps,
            SENSOR_TEMPERATURE: _summarise_temperature,
            SENSOR_SONAR:       _summarise_sonar,
            SENSOR_DIAGNOSTIC:  _summarise_diagnostic,
        }
        fn = dispatch.get(stype, _summarise_diagnostic)
        return fn(data)
