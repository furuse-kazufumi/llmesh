"""Tests for EtherCATAdapter (v1.8.0) — pysoem mocked throughout."""
from __future__ import annotations

import asyncio
import struct
import sys
from unittest.mock import MagicMock, patch
import pytest

from llmesh.industrial.sensor_event import SensorEvent
from llmesh.industrial.ethercat_adapter import SlaveSpec, _STRUCT_FMT


# ---------------------------------------------------------------------------
# Fake pysoem
# ---------------------------------------------------------------------------

def _make_fake_pysoem():
    fake = MagicMock()
    fake.SAFEOP_STATE = 4
    fake.OP_STATE = 8

    class FakeSlave:
        def __init__(self, pos: int, input_data: bytes = b"\x00" * 16):
            self.position = pos
            self.state = fake.OP_STATE
            self.input = bytearray(input_data)

    class FakeMaster:
        def __init__(self):
            self.slaves: list[FakeSlave] = []
            self.state = 0
            self._open = False

        def open(self, ifname: str) -> None:
            self._open = True
            self.slaves = [FakeSlave(0), FakeSlave(1)]

        def config_init(self) -> int:
            return len(self.slaves)

        def config_map(self) -> None:
            pass

        def write_state(self) -> None:
            for s in self.slaves:
                s.state = self.state

        def read_state(self) -> None:
            pass

        def send_processdata(self) -> None:
            pass

        def recv_processdata(self, timeout_us: int) -> int:
            return len(self.slaves)

        def close(self) -> None:
            self._open = False

    fake.Master = FakeMaster
    return fake


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_pysoem():
    fake = _make_fake_pysoem()
    with patch.dict(sys.modules, {"pysoem": fake}):
        import llmesh.industrial.ethercat_adapter as mod
        mod._PYSOEM_AVAILABLE = True
        mod._pysoem = fake
        yield fake, mod


# ---------------------------------------------------------------------------
# SlaveSpec validation
# ---------------------------------------------------------------------------

class TestSlaveSpec:
    def test_basic(self):
        spec = SlaveSpec(slave_pos=0, sensor_id="torque")
        assert spec.slave_pos == 0
        assert spec.data_type == "float32"
        assert spec.byte_offset == 0
        assert spec.scale == 1.0

    def test_negative_slave_pos_raises(self):
        with pytest.raises(ValueError, match="slave_pos"):
            SlaveSpec(slave_pos=-1, sensor_id="s")

    def test_invalid_data_type_raises(self):
        with pytest.raises(ValueError, match="data_type"):
            SlaveSpec(slave_pos=0, sensor_id="s", data_type="float128")

    def test_negative_byte_offset_raises(self):
        with pytest.raises(ValueError, match="byte_offset"):
            SlaveSpec(slave_pos=0, sensor_id="s", byte_offset=-1)

    def test_all_data_types_valid(self):
        for dt in _STRUCT_FMT:
            spec = SlaveSpec(slave_pos=0, sensor_id="s", data_type=dt)
            assert spec.data_type == dt


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestEtherCATAdapterConstruct:
    def test_requires_pysoem(self):
        import llmesh.industrial.ethercat_adapter as mod
        old = mod._PYSOEM_AVAILABLE
        mod._PYSOEM_AVAILABLE = False
        try:
            with pytest.raises(RuntimeError, match="pysoem"):
                mod.EtherCATAdapter("eth0")
        finally:
            mod._PYSOEM_AVAILABLE = old

    def test_bad_ifname_raises(self, fake_pysoem):
        _, mod = fake_pysoem
        with pytest.raises(ValueError, match="ifname"):
            mod.EtherCATAdapter("eth0; rm -rf /")

    def test_ifname_too_long_raises(self, fake_pysoem):
        _, mod = fake_pysoem
        with pytest.raises(ValueError, match="ifname"):
            mod.EtherCATAdapter("a" * 16)

    def test_good_ifname(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0")
        assert adapter._ifname == "eth0"

    def test_cycle_clamped(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0", cycle_time_us=10)
        assert adapter._cycle_time_s >= 1e-4


# ---------------------------------------------------------------------------
# add_slave / on_event
# ---------------------------------------------------------------------------

class TestEtherCATAdapterConfig:
    def test_add_slave(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0")
        adapter.add_slave(0, "torque_01", data_type="float32", byte_offset=0, unit="Nm")
        assert len(adapter._specs) == 1
        assert adapter._specs[0].sensor_id == "torque_01"

    def test_add_multiple_slaves(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0")
        adapter.add_slave(0, "s1")
        adapter.add_slave(1, "s2", data_type="int16")
        assert len(adapter._specs) == 2

    def test_on_event(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0")
        cb = MagicMock()
        adapter.on_event(cb)
        assert cb in adapter._callbacks


# ---------------------------------------------------------------------------
# PDO parsing — _emit_from_pdo
# ---------------------------------------------------------------------------

class TestEmitFromPdo:
    def _make_adapter(self, mod):
        adapter = mod.EtherCATAdapter("eth0")
        adapter.add_slave(
            0, "pressure_01",
            data_type="float32", byte_offset=4,
            scale=0.001, offset=100.0,
            sensor_type="pressure", unit="kPa", device_id="plc01",
        )
        return adapter

    def test_float32_decoded(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = self._make_adapter(mod)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        raw_val = 5000.0
        pdo = b"\x00" * 4 + struct.pack("<f", raw_val) + b"\x00" * 8
        spec = adapter._specs[0]
        adapter._emit_from_pdo(pdo, spec)

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_id == "pressure_01"
        assert ev.protocol == "ethercat"
        expected_physical = raw_val * 0.001 + 100.0
        assert abs(ev.metadata["physical_value"] - expected_physical) < 1e-4

    def test_pdo_too_short_skipped(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = self._make_adapter(mod)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        pdo = b"\x00" * 4   # too short (need at least 8 bytes for offset=4, float32)
        adapter._emit_from_pdo(pdo, adapter._specs[0])
        assert events == []

    def test_int16_decoded(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0")
        adapter.add_slave(0, "speed", data_type="int16", byte_offset=0, scale=0.1)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        pdo = struct.pack("<h", -250) + b"\x00" * 6
        adapter._emit_from_pdo(pdo, adapter._specs[0])

        assert events[0].metadata["physical_value"] == pytest.approx(-25.0)

    def test_payload_is_float64(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0")
        adapter.add_slave(0, "s", data_type="uint32", byte_offset=0)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        pdo = struct.pack("<I", 12345) + b"\x00" * 4
        adapter._emit_from_pdo(pdo, adapter._specs[0])
        (decoded,) = struct.unpack("<d", events[0].payload)
        assert decoded == 12345.0

    def test_callback_exception_does_not_crash(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0")
        adapter.add_slave(0, "s", data_type="uint8", byte_offset=0)
        adapter.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))

        pdo = struct.pack("<B", 42) + b"\x00" * 4
        adapter._emit_from_pdo(pdo, adapter._specs[0])  # must not raise


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestEtherCATAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0", reconnect_delay_s=0.01, cycle_time_us=50_000)
        await adapter.start()
        assert adapter._running is True
        assert adapter._task is not None
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0", reconnect_delay_s=0.01, cycle_time_us=50_000)
        await adapter.start()
        t = adapter._task
        await adapter.start()
        assert adapter._task is t
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, fake_pysoem):
        _, mod = fake_pysoem
        adapter = mod.EtherCATAdapter("eth0", reconnect_delay_s=0.01, cycle_time_us=50_000)
        await adapter.start()
        await adapter.stop()
        assert adapter._running is False
        assert adapter._task is None

    @pytest.mark.asyncio
    async def test_cycle_emits_events(self, fake_pysoem):
        fake, mod = fake_pysoem
        adapter = mod.EtherCATAdapter(
            "eth0", cycle_time_us=50_000, reconnect_delay_s=0.01
        )
        adapter.add_slave(0, "sensor_a", data_type="float32", byte_offset=0)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        # Pre-seed slave PDO input with a known value
        raw = struct.pack("<f", 3.14) + b"\x00" * 12

        original_open = fake.Master().open.__class__

        def patched_open_and_transition(self_adapter):
            result = mod.EtherCATAdapter._open_and_transition(self_adapter)
            if self_adapter._master:
                self_adapter._master.slaves[0].input = bytearray(raw)
            return result

        adapter._open_and_transition = lambda: patched_open_and_transition(adapter)

        await adapter.start()
        await asyncio.sleep(0.2)
        await adapter.stop()

        assert len(events) >= 1
        assert events[0].sensor_id == "sensor_a"
