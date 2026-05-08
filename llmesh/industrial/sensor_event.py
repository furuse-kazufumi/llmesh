"""SensorEvent — unified sensor data envelope for LLMesh Industrial (v1.3.0).

All sensor adapters (Modbus, Serial, OPC-UA, MQTT, mcp-3d, ...) produce
SensorEvent instances so the rest of the pipeline is protocol-agnostic.

Design invariants:
  - frozen dataclass: events are immutable after creation.
  - timestamp_ns is always UNIX nanoseconds (monotonic-safe via time.time_ns()).
  - payload holds raw bytes; decoding is the adapter's responsibility before
    passing the event downstream, or the consumer's responsibility if raw is needed.
  - No shell=True, eval, exec, pickle anywhere.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Priority(Enum):
    CRITICAL = "critical"   # immediate response required (safety / hard stop)
    HIGH = "high"           # early intervention recommended (degradation)
    NORMAL = "normal"       # routine data collection


@dataclass(frozen=True)
class SensorEvent:
    """Immutable envelope for a single sensor reading.

    Fields
    ------
    sensor_id       : unique identifier within a device (e.g. "nozzle_pressure_01")
    protocol        : source protocol tag ("modbus", "serial", "opcua", "mqtt",
                      "mcp3d", "snmp", "ros2", ...)
    timestamp_ns    : UNIX epoch nanoseconds at acquisition time
    payload         : raw bytes — format defined by *protocol* and *sensor_type*
    priority        : severity hint for downstream routing
    device_id       : parent device identifier (e.g. "smt_line_a_machine_01")
    sensor_type     : semantic type ("pressure", "temperature", "vibration",
                      "image", "point_cloud", "current", ...)
    unit            : SI unit string ("Pa", "°C", "mm/s²", "A", ...)
    metadata        : protocol-specific ancillary data (register address, node ID, ...)
    """

    sensor_id: str
    protocol: str
    timestamp_ns: int
    payload: bytes
    priority: Priority = Priority.NORMAL
    device_id: str = ""
    sensor_type: str = ""
    unit: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------------
    # Factory helpers
    # ------------------------------------------------------------------

    @staticmethod
    def now_ns() -> int:
        """Current UNIX time in nanoseconds."""
        return time.time_ns()

    @classmethod
    def create(
        cls,
        sensor_id: str,
        protocol: str,
        payload: bytes,
        *,
        priority: Priority = Priority.NORMAL,
        device_id: str = "",
        sensor_type: str = "",
        unit: str = "",
        metadata: dict[str, Any] | None = None,
        timestamp_ns: int | None = None,
    ) -> SensorEvent:
        """Convenience constructor that stamps *timestamp_ns* automatically."""
        return cls(
            sensor_id=sensor_id,
            protocol=protocol,
            timestamp_ns=timestamp_ns if timestamp_ns is not None else time.time_ns(),
            payload=payload,
            priority=priority,
            device_id=device_id,
            sensor_type=sensor_type,
            unit=unit,
            metadata=metadata or {},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def timestamp_s(self) -> float:
        """Timestamp as UNIX seconds (float)."""
        return self.timestamp_ns / 1_000_000_000

    def with_priority(self, priority: Priority) -> SensorEvent:
        """Return a copy with a different priority (frozen dataclass workaround)."""
        import dataclasses
        return dataclasses.replace(self, priority=priority)
