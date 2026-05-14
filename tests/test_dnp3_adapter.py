"""Tests for DNP3Adapter — v3-N7 / K-1.1 outstation client (skeleton)."""
from __future__ import annotations

import struct

import pytest

from llmesh.industrial.dnp3_adapter import (
    DNP3Adapter,
    DNP3Point,
    point_to_event,
    _encode_value,
    _validate_address,
)
from llmesh.industrial.sensor_event import SensorEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestEncodeValue:
    def test_bool_true(self):
        assert _encode_value(True) == b"\x01"

    def test_bool_false(self):
        assert _encode_value(False) == b"\x00"

    def test_int(self):
        assert _encode_value(42) == struct.pack("<q", 42)

    def test_float(self):
        assert _encode_value(3.5) == struct.pack("<d", 3.5)

    def test_bytes_passthrough(self):
        assert _encode_value(b"raw") == b"raw"

    def test_str_fallback(self):
        assert _encode_value("hi") == b"hi"


class TestValidateAddress:
    def test_no_allow_means_open(self):
        assert _validate_address(1, 2, None) is True

    def test_match(self):
        assert _validate_address(1, 10, [(1, 10)]) is True

    def test_no_match(self):
        assert _validate_address(2, 10, [(1, 10)]) is False


# ---------------------------------------------------------------------------
# point_to_event
# ---------------------------------------------------------------------------

class TestPointToEvent:
    def test_analog_input_mapping(self):
        ev = point_to_event(
            DNP3Point(group=30, variation=1, index=5, value=12.5),
            device_id="plant_a",
        )
        assert isinstance(ev, SensorEvent)
        assert ev.protocol == "dnp3"
        assert ev.sensor_type == "analog_input"
        assert ev.metadata["dnp3_group"] == 30
        assert ev.metadata["dnp3_index"] == 5
        assert struct.unpack("<d", ev.payload)[0] == 12.5

    def test_binary_input_mapping(self):
        ev = point_to_event(
            DNP3Point(group=1, variation=1, index=0, value=True),
            device_id="plant_a",
        )
        assert ev.sensor_type == "binary_input"
        assert ev.payload == b"\x01"

    def test_unknown_group_falls_back(self):
        ev = point_to_event(
            DNP3Point(group=99, variation=1, index=0, value=0),
            device_id="x",
        )
        assert ev.sensor_type == "dnp3_g99"

    def test_explicit_sensor_id(self):
        ev = point_to_event(
            DNP3Point(group=30, variation=1, index=5, value=0.0),
            device_id="x",
            sensor_id="custom_id",
        )
        assert ev.sensor_id == "custom_id"


# ---------------------------------------------------------------------------
# Adapter — construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_negative_poll_rejected(self):
        with pytest.raises(ValueError):
            DNP3Adapter("127.0.0.1", 20000, poll_interval_s=0)

    def test_address_outside_allow_rejected(self):
        with pytest.raises(ValueError):
            DNP3Adapter("127.0.0.1", 20000,
                        master_addr=2, outstation_addr=10,
                        allow_addresses=[(1, 10)])

    def test_default_allowlist_accepts_default_pair(self):
        a = DNP3Adapter("127.0.0.1", 20000)
        assert a.connected is False


# ---------------------------------------------------------------------------
# Adapter — connect (driver injection)
# ---------------------------------------------------------------------------

class _FakeDriver:
    def __init__(self, points):
        self._points = points
    def read_static(self):
        return list(self._points)


class TestConnect:
    def test_connect_with_driver(self):
        a = DNP3Adapter("127.0.0.1", 20000)
        a.connect(driver=_FakeDriver([]))
        assert a.connected is True
        a.disconnect()
        assert a.connected is False

    def test_connect_without_driver_requires_pydnp3(self):
        a = DNP3Adapter("127.0.0.1", 20000)
        # pydnp3 is not installed in CI, so this should raise.
        try:
            import pydnp3  # noqa: F401
        except ImportError:
            with pytest.raises(RuntimeError):
                a.connect()
        else:
            pytest.skip("pydnp3 is installed; cannot exercise the missing-import path")


# ---------------------------------------------------------------------------
# Adapter — polling
# ---------------------------------------------------------------------------

class TestPoll:
    def test_poll_when_disconnected_returns_empty(self):
        a = DNP3Adapter("127.0.0.1", 20000)
        assert a.poll() == []

    def test_poll_emits_events(self):
        a = DNP3Adapter("127.0.0.1", 20000, device_id="plant_a")
        captured = []
        a.on_event(captured.append)
        a.connect(driver=_FakeDriver([
            DNP3Point(group=30, variation=1, index=0, value=1.0),
            DNP3Point(group=30, variation=1, index=1, value=2.0),
        ]))
        events = a.poll()
        assert len(events) == 2
        assert events[0].sensor_type == "analog_input"
        assert len(captured) == 2

    def test_poll_callback_exception_does_not_break_loop(self):
        a = DNP3Adapter("127.0.0.1", 20000)
        a.connect(driver=_FakeDriver([
            DNP3Point(group=30, variation=1, index=0, value=1.0),
        ]))
        a.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("nope")))
        events = a.poll()
        assert len(events) == 1   # event still emitted despite callback error
