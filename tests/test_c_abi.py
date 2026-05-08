"""Tests for the SensorEvent C ABI v1 (RTOS / embedded interop)."""
from __future__ import annotations

import pytest
from hypothesis import given, settings, strategies as st

from llmesh.industrial.c_abi import (
    encode, decode, parse_header,
    CABIError,
    _MAGIC, _VERSION, _HEADER_SIZE,
    _MAX_SENSOR_ID, _MAX_DEVICE_ID, _MAX_PAYLOAD,
    _PROTOCOL_BY_ID, _PRIORITY_BY_ID,
)
from llmesh.industrial.sensor_event import SensorEvent, Priority


_FAST = settings(max_examples=50, deadline=None)


def _ev(**kw) -> SensorEvent:
    return SensorEvent(
        sensor_id=kw.get("sensor_id", "s1"),
        protocol=kw.get("protocol", "modbus"),
        timestamp_ns=kw.get("timestamp_ns", 1_700_000_000_000_000_000),
        payload=kw.get("payload", b"\x01\x02"),
        priority=kw.get("priority", Priority.NORMAL),
        device_id=kw.get("device_id", "d1"),
        sensor_type=kw.get("sensor_type", "temperature"),
        unit=kw.get("unit", "C"),
        metadata={},
    )


# ---------------------------------------------------------------------------
# Header
# ---------------------------------------------------------------------------

class TestHeader:
    def test_too_short_raises(self):
        with pytest.raises(CABIError, match="header"):
            parse_header(b"")

    def test_bad_magic_raises(self):
        bogus = b"\xFF" * _HEADER_SIZE
        with pytest.raises(CABIError, match="magic"):
            parse_header(bogus)

    def test_valid_header_parses(self):
        ev = _ev()
        h = parse_header(encode(ev))
        assert h.protocol == "modbus"
        assert h.priority is Priority.NORMAL


class TestEncodeDecodeRoundtrip:
    def test_basic(self):
        ev = _ev()
        out = decode(encode(ev))
        assert out.sensor_id == ev.sensor_id
        assert out.protocol == ev.protocol
        assert out.timestamp_ns == ev.timestamp_ns
        assert out.payload == ev.payload
        assert out.priority is ev.priority
        assert out.device_id == ev.device_id
        assert out.sensor_type == ev.sensor_type
        assert out.unit == ev.unit

    def test_empty_strings_ok(self):
        ev = _ev(device_id="", sensor_type="", unit="", payload=b"")
        out = decode(encode(ev))
        assert out.device_id == ""
        assert out.payload == b""

    def test_all_protocols_roundtrip(self):
        for proto_id, name in _PROTOCOL_BY_ID.items():
            if name == "unknown":
                continue
            ev = _ev(protocol=name)
            out = decode(encode(ev))
            assert out.protocol == name

    def test_all_priorities_roundtrip(self):
        for priority_id, p in _PRIORITY_BY_ID.items():
            ev = _ev(priority=p)
            out = decode(encode(ev))
            assert out.priority is p

    def test_unicode_sensor_id(self):
        ev = _ev(sensor_id="温度センサー", unit="°C")
        out = decode(encode(ev))
        assert out.sensor_id == "温度センサー"
        assert out.unit == "°C"


class TestSizeLimits:
    def test_sensor_id_too_long_rejected(self):
        ev = _ev(sensor_id="x" * (_MAX_SENSOR_ID + 1))
        with pytest.raises(CABIError, match="sensor_id"):
            encode(ev)

    def test_device_id_too_long_rejected(self):
        ev = _ev(device_id="x" * (_MAX_DEVICE_ID + 1))
        with pytest.raises(CABIError, match="device_id"):
            encode(ev)

    def test_payload_too_long_rejected(self):
        ev = _ev(payload=b"\x00" * (_MAX_PAYLOAD + 1))
        with pytest.raises(CABIError, match="payload"):
            encode(ev)


class TestTruncatedBody:
    def test_truncated_body_rejected(self):
        full = encode(_ev(payload=b"\x00" * 32))
        truncated = full[:-5]
        with pytest.raises(CABIError, match="truncated"):
            decode(truncated)


# ---------------------------------------------------------------------------
# Property-based: random valid SensorEvents must round-trip
# ---------------------------------------------------------------------------

_safe_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),  # exclude surrogates
    min_size=0, max_size=64,
)


@_FAST
@given(
    sensor_id=_safe_text,
    device_id=_safe_text,
    payload=st.binary(min_size=0, max_size=512),
    timestamp_ns=st.integers(min_value=0, max_value=2**63 - 1),
)
def test_random_roundtrip(sensor_id, device_id, payload, timestamp_ns):
    # UTF-8 encoded length must fit
    sid_b = sensor_id.encode("utf-8")
    did_b = device_id.encode("utf-8")
    if len(sid_b) > _MAX_SENSOR_ID or len(did_b) > _MAX_DEVICE_ID:
        return  # skip
    ev = _ev(sensor_id=sensor_id, device_id=device_id,
             payload=payload, timestamp_ns=timestamp_ns)
    out = decode(encode(ev))
    assert out.sensor_id == sensor_id
    assert out.device_id == device_id
    assert out.payload == payload
    assert out.timestamp_ns == timestamp_ns


# ---------------------------------------------------------------------------
# Wire-format constants exposed
# ---------------------------------------------------------------------------

class TestConstants:
    def test_magic_value(self):
        assert _MAGIC == 0x4C4D4553

    def test_header_size(self):
        assert _HEADER_SIZE == 44

    def test_version(self):
        assert _VERSION == 1
