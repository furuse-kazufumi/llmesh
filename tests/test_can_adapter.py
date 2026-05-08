"""Tests for CANAdapter (v3 — C-2) — python-can mocked throughout."""
from __future__ import annotations

import struct
import sys
from unittest.mock import MagicMock, patch
import pytest

from llmesh.industrial.can_adapter import (
    FrameSpec,
    _STRUCT_FMT,
    _CAN_STD_MAX,
    _CAN_EXT_MAX,
)
from llmesh.industrial.sensor_event import SensorEvent


def _make_fake_can():
    fake = MagicMock()

    class FakeMsg:
        def __init__(self, arbitration_id=0x100, is_extended_id=False, data=b""):
            self.arbitration_id = arbitration_id
            self.is_extended_id = is_extended_id
            self.data = bytearray(data)

    class FakeBus:
        def __init__(self, channel="", bustype="", bitrate=0, fd=False):
            self.channel = channel
            self._queue: list[FakeMsg] = []

        def queue(self, msg: FakeMsg) -> None:
            self._queue.append(msg)

        def recv(self, timeout=None):
            if self._queue:
                return self._queue.pop(0)
            return None

        def shutdown(self):
            pass

    fake.Bus = FakeBus
    fake.Message = FakeMsg
    return fake


@pytest.fixture()
def fake_can():
    fake = _make_fake_can()
    with patch.dict(sys.modules, {"can": fake}):
        import llmesh.industrial.can_adapter as mod
        mod._CAN_AVAILABLE = True
        mod._can = fake
        yield fake, mod


# ---------------------------------------------------------------------------
# FrameSpec validation
# ---------------------------------------------------------------------------

class TestFrameSpec:
    def test_basic_standard(self):
        spec = FrameSpec(can_id=0x100, sensor_id="rpm")
        assert spec.can_id == 0x100
        assert spec.extended is False

    def test_basic_extended(self):
        spec = FrameSpec(can_id=0x10000, sensor_id="x", extended=True)
        assert spec.extended is True

    def test_standard_id_too_large(self):
        with pytest.raises(ValueError, match="can_id"):
            FrameSpec(can_id=_CAN_STD_MAX + 1, sensor_id="x")

    def test_extended_id_too_large(self):
        with pytest.raises(ValueError, match="can_id"):
            FrameSpec(can_id=_CAN_EXT_MAX + 1, sensor_id="x", extended=True)

    def test_negative_id_rejected(self):
        with pytest.raises(ValueError, match="can_id"):
            FrameSpec(can_id=-1, sensor_id="x")

    def test_invalid_data_type(self):
        with pytest.raises(ValueError, match="data_type"):
            FrameSpec(can_id=1, sensor_id="x", data_type="bool128")

    def test_negative_byte_offset(self):
        with pytest.raises(ValueError, match="byte_offset"):
            FrameSpec(can_id=1, sensor_id="x", byte_offset=-1)

    def test_all_data_types(self):
        for dt in _STRUCT_FMT:
            spec = FrameSpec(can_id=1, sensor_id="x", data_type=dt)
            assert spec.data_type == dt


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestCANAdapterConstruct:
    def test_requires_python_can(self):
        import llmesh.industrial.can_adapter as mod
        old = mod._CAN_AVAILABLE
        mod._CAN_AVAILABLE = False
        try:
            with pytest.raises(RuntimeError, match="python-can"):
                mod.CANAdapter("can0")
        finally:
            mod._CAN_AVAILABLE = old

    def test_bad_channel_rejected(self, fake_can):
        _, mod = fake_can
        with pytest.raises(ValueError, match="channel"):
            mod.CANAdapter("can0; rm -rf /")

    def test_channel_too_long_rejected(self, fake_can):
        _, mod = fake_can
        with pytest.raises(ValueError, match="channel"):
            mod.CANAdapter("a" * 65)

    def test_good_channel(self, fake_can):
        _, mod = fake_can
        adapter = mod.CANAdapter("can0")
        assert adapter._channel == "can0"


# ---------------------------------------------------------------------------
# add_frame / on_event
# ---------------------------------------------------------------------------

class TestCANAdapterConfig:
    def test_add_frame(self, fake_can):
        _, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "rpm", data_type="uint16", byte_offset=0)
        assert (0x100, False) in adapter._specs
        assert len(adapter._specs[(0x100, False)]) == 1

    def test_multiple_specs_per_frame(self, fake_can):
        _, mod = fake_can
        adapter = mod.CANAdapter("can0")
        # Two values from same frame
        adapter.add_frame(0x100, "rpm", data_type="uint16", byte_offset=0)
        adapter.add_frame(0x100, "temp", data_type="uint16", byte_offset=2)
        assert len(adapter._specs[(0x100, False)]) == 2

    def test_extended_id_separate_key(self, fake_can):
        _, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "a", extended=False)
        adapter.add_frame(0x100, "b", extended=True)
        assert len(adapter._specs) == 2


# ---------------------------------------------------------------------------
# Frame dispatch
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_uint16_decoded(self, fake_can):
        fake, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "rpm", data_type="uint16",
                          byte_offset=0, scale=0.25,
                          sensor_type="rpm", unit="rpm")
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        msg = fake.Message(arbitration_id=0x100, data=struct.pack("<H", 4000))
        adapter._dispatch(msg)

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_id == "rpm"
        assert ev.protocol == "can"
        assert ev.metadata["physical_value"] == pytest.approx(4000 * 0.25)
        assert ev.metadata["can_id"] == 0x100

    def test_unregistered_can_id_ignored(self, fake_can):
        fake, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "rpm")
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        msg = fake.Message(arbitration_id=0x999, data=b"\x00\x00")
        adapter._dispatch(msg)
        assert events == []

    def test_extended_vs_standard_different(self, fake_can):
        fake, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "std", extended=False)
        adapter.add_frame(0x100, "ext", extended=True)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        adapter._dispatch(fake.Message(arbitration_id=0x100, is_extended_id=False,
                                       data=b"\x00\x00"))
        adapter._dispatch(fake.Message(arbitration_id=0x100, is_extended_id=True,
                                       data=b"\x00\x00"))
        assert sorted(e.sensor_id for e in events) == ["ext", "std"]

    def test_short_frame_skipped(self, fake_can):
        fake, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "x", data_type="uint32", byte_offset=0)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        # uint32 needs 4 bytes; only 2 provided
        adapter._dispatch(fake.Message(arbitration_id=0x100, data=b"\x00\x00"))
        assert events == []

    def test_multiple_specs_one_frame(self, fake_can):
        fake, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "rpm", data_type="uint16", byte_offset=0)
        adapter.add_frame(0x100, "temp", data_type="uint16", byte_offset=2)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        # Two uint16 values: rpm=1000, temp=85
        data = struct.pack("<HH", 1000, 85)
        adapter._dispatch(fake.Message(arbitration_id=0x100, data=data))

        assert len(events) == 2
        assert {e.sensor_id for e in events} == {"rpm", "temp"}

    def test_callback_exception_does_not_crash(self, fake_can):
        fake, mod = fake_can
        adapter = mod.CANAdapter("can0")
        adapter.add_frame(0x100, "x", data_type="uint8", byte_offset=0)
        adapter.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))

        adapter._dispatch(fake.Message(arbitration_id=0x100, data=b"\x42"))

    def test_can_2_0_dlc_truncation(self, fake_can):
        """CAN 2.0 caps DLC at 8 bytes — extra bytes are truncated."""
        fake, mod = fake_can
        adapter = mod.CANAdapter("can0", fd=False)
        adapter.add_frame(0x100, "x", data_type="uint8", byte_offset=0)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        # 16-byte data; CAN 2.0 only sees first 8
        adapter._dispatch(fake.Message(arbitration_id=0x100, data=b"\x42" + b"\x00" * 15))
        assert len(events) == 1
        assert events[0].metadata["frame_dlc"] == 8


# ---------------------------------------------------------------------------
# Lifecycle (smoke test only — full bus loop hits I/O)
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, fake_can):
        _, mod = fake_can
        adapter = mod.CANAdapter("can0", reconnect_delay_s=0.01)
        await adapter.start()
        assert adapter._running is True
        assert adapter._task is not None
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self, fake_can):
        _, mod = fake_can
        adapter = mod.CANAdapter("can0", reconnect_delay_s=0.01)
        await adapter.start()
        t = adapter._task
        await adapter.start()
        assert adapter._task is t
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, fake_can):
        _, mod = fake_can
        adapter = mod.CANAdapter("can0", reconnect_delay_s=0.01)
        await adapter.start()
        await adapter.stop()
        assert adapter._running is False
        assert adapter._task is None
