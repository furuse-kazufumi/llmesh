"""Tests for ModbusAdapter (v1.4.0)."""
from __future__ import annotations

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from llmesh.industrial.sensor_event import Priority, SensorEvent
from llmesh.industrial.modbus_adapter import (
    ModbusAdapter,
    ModbusMode,
    RegisterSpec,
    RegisterType,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_pymodbus(monkeypatch):
    """Patch pymodbus so tests run without the package installed."""
    tcp_client = MagicMock()
    tcp_client.connected = True
    tcp_client.connect = AsyncMock(return_value=True)
    tcp_client.close = MagicMock()

    serial_client = MagicMock()
    serial_client.connected = True
    serial_client.connect = AsyncMock(return_value=True)
    serial_client.close = MagicMock()

    monkeypatch.setattr(
        "llmesh.industrial.modbus_adapter._PYMODBUS_AVAILABLE", True
    )
    monkeypatch.setattr(
        "llmesh.industrial.modbus_adapter.AsyncModbusTcpClient",
        MagicMock(return_value=tcp_client),
    )
    monkeypatch.setattr(
        "llmesh.industrial.modbus_adapter.AsyncModbusSerialClient",
        MagicMock(return_value=serial_client),
    )
    return tcp_client, serial_client


def _holding_result(values: list[int]) -> MagicMock:
    r = MagicMock()
    r.isError.return_value = False
    r.registers = values
    return r


def _coil_result(bits: list[bool]) -> MagicMock:
    r = MagicMock()
    r.isError.return_value = False
    r.bits = bits
    return r


def _error_result() -> MagicMock:
    r = MagicMock()
    r.isError.return_value = True
    return r


# ---------------------------------------------------------------------------
# RegisterSpec validation
# ---------------------------------------------------------------------------

class TestRegisterSpec:
    def test_valid(self):
        spec = RegisterSpec(
            slave_id=1, address=0x0000, count=2,
            sensor_id="p01", sensor_type="pressure", unit="Pa",
        )
        assert spec.slave_id == 1

    def test_slave_id_out_of_range(self):
        with pytest.raises(ValueError, match="slave_id"):
            RegisterSpec(slave_id=0, address=0, count=1, sensor_id="x")

    def test_slave_id_too_large(self):
        with pytest.raises(ValueError, match="slave_id"):
            RegisterSpec(slave_id=248, address=0, count=1, sensor_id="x")

    def test_address_out_of_range(self):
        with pytest.raises(ValueError, match="address"):
            RegisterSpec(slave_id=1, address=0x10000, count=1, sensor_id="x")

    def test_count_zero(self):
        with pytest.raises(ValueError, match="count"):
            RegisterSpec(slave_id=1, address=0, count=0, sensor_id="x")

    def test_count_too_large(self):
        with pytest.raises(ValueError, match="count"):
            RegisterSpec(slave_id=1, address=0, count=126, sensor_id="x")


# ---------------------------------------------------------------------------
# Factory methods
# ---------------------------------------------------------------------------

class TestModbusAdapterFactories:
    def test_tcp_factory(self, mock_pymodbus):
        adapter = ModbusAdapter.tcp("192.168.1.1")
        assert adapter._mode is ModbusMode.TCP
        assert adapter._host == "192.168.1.1"
        assert adapter._port == 502

    def test_tcp_factory_custom_port(self, mock_pymodbus):
        adapter = ModbusAdapter.tcp("10.0.0.1", 1502)
        assert adapter._port == 1502

    def test_rtu_factory(self, mock_pymodbus):
        adapter = ModbusAdapter.rtu("/dev/ttyUSB0", 9600)
        assert adapter._mode is ModbusMode.RTU
        assert adapter._serial_port == "/dev/ttyUSB0"
        assert adapter._baud_rate == 9600

    def test_rtu_factory_defaults(self, mock_pymodbus):
        adapter = ModbusAdapter.rtu("COM3")
        assert adapter._baud_rate == 9600

    def test_missing_pymodbus_raises(self, monkeypatch):
        monkeypatch.setattr(
            "llmesh.industrial.modbus_adapter._PYMODBUS_AVAILABLE", False
        )
        with pytest.raises(RuntimeError, match="pymodbus"):
            ModbusAdapter.tcp("127.0.0.1")


# ---------------------------------------------------------------------------
# add_register
# ---------------------------------------------------------------------------

class TestAddRegister:
    def test_add_holding(self, mock_pymodbus):
        adapter = ModbusAdapter.tcp("127.0.0.1")
        adapter.add_register(1, 0x0000, 2, "p01")
        assert len(adapter._specs) == 1
        assert adapter._specs[0].register_type is RegisterType.HOLDING

    def test_add_coil(self, mock_pymodbus):
        adapter = ModbusAdapter.tcp("127.0.0.1")
        adapter.add_register(
            1, 0x0100, 4, "valve_state",
            register_type=RegisterType.COIL,
        )
        assert adapter._specs[0].register_type is RegisterType.COIL

    def test_add_multiple_specs(self, mock_pymodbus):
        adapter = ModbusAdapter.tcp("127.0.0.1")
        adapter.add_register(1, 0, 1, "a")
        adapter.add_register(1, 2, 1, "b")
        assert len(adapter._specs) == 2

    def test_invalid_slave_id_rejected(self, mock_pymodbus):
        adapter = ModbusAdapter.tcp("127.0.0.1")
        with pytest.raises(ValueError, match="slave_id"):
            adapter.add_register(0, 0, 1, "bad")


# ---------------------------------------------------------------------------
# Polling — holding registers
# ---------------------------------------------------------------------------

class TestPollHolding:
    @pytest.mark.asyncio
    async def test_holding_register_emits_event(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.read_holding_registers = AsyncMock(
            return_value=_holding_result([1000, 2000])
        )
        adapter = ModbusAdapter.tcp("127.0.0.1", poll_interval_s=9999)
        adapter.add_register(
            1, 0x0000, 2, "pressure_01",
            sensor_type="pressure", unit="Pa", device_id="dev_a",
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        await adapter.start()
        await asyncio.sleep(0.05)
        await adapter.stop()

        assert len(events) >= 1
        ev = events[0]
        assert ev.sensor_id == "pressure_01"
        assert ev.protocol == "modbus"
        assert ev.sensor_type == "pressure"
        assert ev.unit == "Pa"
        assert ev.device_id == "dev_a"
        # payload: two big-endian uint16
        assert ev.payload == struct.pack(">2H", 1000, 2000)
        assert ev.metadata["slave_id"] == 1
        assert ev.metadata["address"] == 0x0000
        assert ev.metadata["register_type"] == "holding"
        assert ev.metadata["values"] == [1000, 2000]

    @pytest.mark.asyncio
    async def test_input_register(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.read_input_registers = AsyncMock(
            return_value=_holding_result([500])
        )
        adapter = ModbusAdapter.tcp("127.0.0.1", poll_interval_s=9999)
        adapter.add_register(
            1, 0x0010, 1, "temp_01",
            register_type=RegisterType.INPUT,
            sensor_type="temperature", unit="°C",
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        await adapter.start()
        await asyncio.sleep(0.05)
        await adapter.stop()

        assert events[0].metadata["register_type"] == "input"
        assert events[0].payload == struct.pack(">H", 500)


# ---------------------------------------------------------------------------
# Polling — coils
# ---------------------------------------------------------------------------

class TestPollCoils:
    @pytest.mark.asyncio
    async def test_coil_emits_event(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.read_coils = AsyncMock(
            return_value=_coil_result([True, False, True, False])
        )
        adapter = ModbusAdapter.tcp("127.0.0.1", poll_interval_s=9999)
        adapter.add_register(
            1, 0x0200, 4, "valve_state",
            register_type=RegisterType.COIL,
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        await adapter.start()
        await asyncio.sleep(0.05)
        await adapter.stop()

        assert len(events) >= 1
        ev = events[0]
        assert ev.payload == bytes([1, 0, 1, 0])
        assert ev.metadata["register_type"] == "coil"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_modbus_error_response_does_not_crash(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.read_holding_registers = AsyncMock(
            return_value=_error_result()
        )
        adapter = ModbusAdapter.tcp("127.0.0.1", poll_interval_s=9999)
        adapter.add_register(1, 0, 1, "bad_sensor")
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        await adapter.start()
        await asyncio.sleep(0.05)
        await adapter.stop()

        assert events == []  # no event on error

    @pytest.mark.asyncio
    async def test_callback_exception_is_isolated(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.read_holding_registers = AsyncMock(
            return_value=_holding_result([42])
        )
        adapter = ModbusAdapter.tcp("127.0.0.1", poll_interval_s=9999)
        adapter.add_register(1, 0, 1, "s01")

        good_events: list[SensorEvent] = []

        def bad_cb(ev: SensorEvent) -> None:
            raise RuntimeError("callback crash")

        adapter.on_event(bad_cb)
        adapter.on_event(good_events.append)

        await adapter.start()
        await asyncio.sleep(0.05)
        await adapter.stop()

        assert len(good_events) >= 1

    @pytest.mark.asyncio
    async def test_reconnect_on_disconnect(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.connected = False
        tcp_mock.connect = AsyncMock(return_value=False)
        adapter = ModbusAdapter.tcp(
            "127.0.0.1",
            poll_interval_s=9999,
            reconnect_delay_s=0.01,
        )
        adapter.add_register(1, 0, 1, "s01")
        await adapter.start()
        await asyncio.sleep(0.05)
        await adapter.stop()
        # connect should have been called (reconnect loop ran)
        assert tcp_mock.connect.call_count >= 1


# ---------------------------------------------------------------------------
# Stop is idempotent
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_double_start_is_safe(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.read_holding_registers = AsyncMock(
            return_value=_holding_result([1])
        )
        adapter = ModbusAdapter.tcp("127.0.0.1", poll_interval_s=9999)
        await adapter.start()
        await adapter.start()  # second start should be no-op
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self, mock_pymodbus):
        adapter = ModbusAdapter.tcp("127.0.0.1")
        await adapter.stop()  # should not raise

    @pytest.mark.asyncio
    async def test_priority_propagated(self, mock_pymodbus):
        tcp_mock, _ = mock_pymodbus
        tcp_mock.read_holding_registers = AsyncMock(
            return_value=_holding_result([9999])
        )
        adapter = ModbusAdapter.tcp("127.0.0.1", poll_interval_s=9999)
        adapter.add_register(
            1, 0, 1, "critical_sensor", priority=Priority.CRITICAL
        )
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        await adapter.start()
        await asyncio.sleep(0.05)
        await adapter.stop()
        assert events[0].priority is Priority.CRITICAL
