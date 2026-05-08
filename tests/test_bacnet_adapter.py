"""Tests for BACnetAdapter (v2.4 — K-10.1) — bacpypes3 mocked throughout."""
from __future__ import annotations

import asyncio
import struct
import sys
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from llmesh.industrial.bacnet_adapter import (
    BACnetObjectSpec,
    _SUPPORTED_OBJECT_TYPES,
    _DEVICE_ID_MAX,
)
from llmesh.industrial.sensor_event import SensorEvent


def _make_fake_bacpypes():
    fake = MagicMock()
    fake.app = MagicMock()
    fake.local = MagicMock()
    fake.pdu = MagicMock()
    fake.primitivedata = MagicMock()
    return fake


@pytest.fixture()
def fake_bacpypes():
    fake = _make_fake_bacpypes()
    with patch.dict(sys.modules, {
        "bacpypes3": fake,
        "bacpypes3.app": fake.app,
        "bacpypes3.local": fake.local,
        "bacpypes3.local.device": fake.local.device,
        "bacpypes3.pdu": fake.pdu,
        "bacpypes3.primitivedata": fake.primitivedata,
    }):
        import llmesh.industrial.bacnet_adapter as mod
        mod._BACPYPES_AVAILABLE = True
        mod._bacpypes3 = fake
        yield fake, mod


# ---------------------------------------------------------------------------
# BACnetObjectSpec validation
# ---------------------------------------------------------------------------

class TestObjectSpec:
    def test_basic(self):
        s = BACnetObjectSpec(device_id=1, object_type="analog-input",
                              instance=10, sensor_id="t1")
        assert s.property_name == "present-value"

    def test_negative_device_id(self):
        with pytest.raises(ValueError, match="device_id"):
            BACnetObjectSpec(device_id=-1, object_type="analog-input",
                              instance=0, sensor_id="x")

    def test_device_id_too_large(self):
        with pytest.raises(ValueError, match="device_id"):
            BACnetObjectSpec(device_id=_DEVICE_ID_MAX + 1,
                              object_type="analog-input",
                              instance=0, sensor_id="x")

    def test_invalid_object_type(self):
        with pytest.raises(ValueError, match="object_type"):
            BACnetObjectSpec(device_id=1, object_type="quantum-state",
                              instance=0, sensor_id="x")

    def test_negative_instance(self):
        with pytest.raises(ValueError, match="instance"):
            BACnetObjectSpec(device_id=1, object_type="analog-input",
                              instance=-1, sensor_id="x")

    def test_all_supported_types(self):
        for ot in _SUPPORTED_OBJECT_TYPES:
            s = BACnetObjectSpec(device_id=1, object_type=ot,
                                  instance=0, sensor_id="x")
            assert s.object_type == ot


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_requires_bacpypes(self):
        import llmesh.industrial.bacnet_adapter as mod
        old = mod._BACPYPES_AVAILABLE
        mod._BACPYPES_AVAILABLE = False
        try:
            with pytest.raises(RuntimeError, match="bacpypes3"):
                mod.BACnetAdapter("192.168.1.10/24")
        finally:
            mod._BACPYPES_AVAILABLE = old

    def test_invalid_local_ip_format(self, fake_bacpypes):
        _, mod = fake_bacpypes
        with pytest.raises(ValueError, match="local_ip"):
            mod.BACnetAdapter("192.168.1.10")  # missing /prefix

    def test_invalid_local_ip_octet(self, fake_bacpypes):
        _, mod = fake_bacpypes
        with pytest.raises(ValueError, match="local_ip"):
            mod.BACnetAdapter("999.0.0.1/24")

    def test_invalid_local_ip_prefix(self, fake_bacpypes):
        _, mod = fake_bacpypes
        with pytest.raises(ValueError, match="local_ip"):
            mod.BACnetAdapter("10.0.0.1/33")

    def test_invalid_device_id(self, fake_bacpypes):
        _, mod = fake_bacpypes
        with pytest.raises(ValueError, match="device_id_local"):
            mod.BACnetAdapter("10.0.0.1/24", device_id_local=_DEVICE_ID_MAX + 1)

    def test_min_poll_clamped(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24", poll_interval_s=0.01)
        assert a._poll_interval_s >= 0.5


# ---------------------------------------------------------------------------
# add_object / on_event
# ---------------------------------------------------------------------------

class TestConfig:
    def test_add_object(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24")
        a.add_object(1, "analog-input", 1, "t1", sensor_type="temperature")
        assert len(a._specs) == 1

    def test_add_object_invalid_type_rejected(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24")
        with pytest.raises(ValueError):
            a.add_object(1, "not-a-type", 0, "x")


# ---------------------------------------------------------------------------
# Poll spec → SensorEvent (via mocked _read_property)
# ---------------------------------------------------------------------------

class TestPollSpec:
    @pytest.mark.asyncio
    async def test_numeric_value_emits_event(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24")
        a.add_object(1001, "analog-input", 1, "t1",
                     sensor_type="temperature", unit="degC")
        events: list[SensorEvent] = []
        a.on_event(events.append)

        a._read_property = AsyncMock(return_value=21.5)
        await a._poll_spec(a._specs[0])

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_id == "t1"
        assert ev.protocol == "bacnet"
        decoded = struct.unpack("<d", ev.payload)[0]
        assert decoded == 21.5
        assert ev.metadata["bacnet_device_id"] == 1001

    @pytest.mark.asyncio
    async def test_string_value_emits_event(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24")
        a.add_object(1001, "binary-input", 1, "fan_state")
        events: list[SensorEvent] = []
        a.on_event(events.append)

        a._read_property = AsyncMock(return_value="active")
        await a._poll_spec(a._specs[0])
        assert events[0].payload == b"active"

    @pytest.mark.asyncio
    async def test_read_exception_does_not_crash(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24")
        a.add_object(1, "analog-input", 0, "x")
        events = []
        a.on_event(events.append)
        a._read_property = AsyncMock(side_effect=RuntimeError("network"))
        await a._poll_spec(a._specs[0])
        assert events == []

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24")
        a.add_object(1, "analog-input", 0, "x")
        a.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
        a._read_property = AsyncMock(return_value=1.0)
        await a._poll_spec(a._specs[0])  # must not raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24", reconnect_delay_s=0.01)
        # Patch _open_app to fast-succeed without touching real bacpypes
        a._open_app = AsyncMock(return_value=False)  # repeat retries
        await a.start()
        assert a._running is True
        await a.stop()
        assert a._running is False

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self, fake_bacpypes):
        _, mod = fake_bacpypes
        a = mod.BACnetAdapter("10.0.0.1/24", reconnect_delay_s=0.01)
        a._open_app = AsyncMock(return_value=False)
        await a.start()
        t = a._task
        await a.start()
        assert a._task is t
        await a.stop()
