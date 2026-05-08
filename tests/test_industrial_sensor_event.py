"""Tests for llmesh.industrial.sensor_event (Phase A — SensorEvent foundation)."""
from __future__ import annotations

import time

import pytest

from llmesh.industrial.sensor_event import Priority, SensorEvent


class TestPriority:
    def test_values(self):
        assert Priority.CRITICAL.value == "critical"
        assert Priority.HIGH.value == "high"
        assert Priority.NORMAL.value == "normal"

    def test_enum_members(self):
        assert len(Priority) == 3


class TestSensorEventCreate:
    def test_create_minimal(self):
        ev = SensorEvent.create("p01", "modbus", b"\x01\x02")
        assert ev.sensor_id == "p01"
        assert ev.protocol == "modbus"
        assert ev.payload == b"\x01\x02"
        assert ev.priority == Priority.NORMAL
        assert ev.device_id == ""
        assert ev.sensor_type == ""
        assert ev.unit == ""
        assert ev.metadata == {}

    def test_create_full(self):
        ts = SensorEvent.now_ns()
        ev = SensorEvent.create(
            "temp_01",
            "serial",
            b"\xff\x00",
            priority=Priority.HIGH,
            device_id="smt_line_a",
            sensor_type="temperature",
            unit="°C",
            metadata={"register": 100},
            timestamp_ns=ts,
        )
        assert ev.sensor_id == "temp_01"
        assert ev.protocol == "serial"
        assert ev.priority == Priority.HIGH
        assert ev.device_id == "smt_line_a"
        assert ev.sensor_type == "temperature"
        assert ev.unit == "°C"
        assert ev.metadata == {"register": 100}
        assert ev.timestamp_ns == ts

    def test_create_stamps_timestamp(self):
        before = time.time_ns()
        ev = SensorEvent.create("s", "modbus", b"")
        after = time.time_ns()
        assert before <= ev.timestamp_ns <= after

    def test_create_empty_payload(self):
        ev = SensorEvent.create("s", "opcua", b"")
        assert ev.payload == b""


class TestSensorEventImmutability:
    def test_frozen(self):
        ev = SensorEvent.create("s", "modbus", b"\x00")
        with pytest.raises((AttributeError, TypeError)):
            ev.sensor_id = "other"  # type: ignore[misc]

    def test_metadata_default_independent(self):
        ev1 = SensorEvent.create("s1", "modbus", b"")
        ev2 = SensorEvent.create("s2", "modbus", b"")
        assert ev1.metadata is not ev2.metadata


class TestSensorEventProperties:
    def test_timestamp_s(self):
        ts_ns = 1_700_000_000_123_456_789
        ev = SensorEvent(
            sensor_id="s",
            protocol="modbus",
            timestamp_ns=ts_ns,
            payload=b"",
        )
        assert abs(ev.timestamp_s - ts_ns / 1e9) < 1e-6

    def test_with_priority(self):
        ev = SensorEvent.create("s", "modbus", b"\x01", priority=Priority.NORMAL)
        ev2 = ev.with_priority(Priority.CRITICAL)
        assert ev2.priority == Priority.CRITICAL
        assert ev.priority == Priority.NORMAL  # original unchanged
        assert ev2.sensor_id == ev.sensor_id   # other fields preserved


class TestSensorEventProtocols:
    @pytest.mark.parametrize("proto", [
        "modbus", "serial", "opcua", "mqtt", "ethercat",
        "canbus", "mcp3d", "snmp", "ros2", "ros1",
    ])
    def test_known_protocols_accepted(self, proto):
        ev = SensorEvent.create("s", proto, b"")
        assert ev.protocol == proto

    def test_custom_protocol_accepted(self):
        ev = SensorEvent.create("s", "custom_proto_v2", b"")
        assert ev.protocol == "custom_proto_v2"


class TestSensorEventNowNs:
    def test_now_ns_monotonic(self):
        t1 = SensorEvent.now_ns()
        t2 = SensorEvent.now_ns()
        assert t2 >= t1

    def test_now_ns_reasonable(self):
        t = SensorEvent.now_ns()
        # Must be after 2020-01-01 in nanoseconds
        assert t > 1_577_836_800 * 1_000_000_000
