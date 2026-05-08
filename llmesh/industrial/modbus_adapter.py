"""ModbusAdapter — Modbus TCP / RTU sensor polling for LLMesh Industrial (v1.4.0).

Reads holding registers, input registers, discrete inputs, and coils from
Modbus devices and converts each poll result into a SensorEvent for the
unified industrial pipeline.

Modes
-----
tcp   — Modbus TCP over Ethernet (default port 502)
rtu   — Modbus RTU over RS-485 / RS-232 serial link

Usage::

    adapter = ModbusAdapter.tcp("192.168.1.10", 502)
    adapter.add_register(
        slave_id=1, address=0x0000, count=2,
        sensor_id="pressure_01", sensor_type="pressure", unit="Pa",
    )
    adapter.on_event(lambda ev: print(ev))
    await adapter.start()
    # ... poll loop runs until stop() is called
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Slave IDs, addresses, and counts are validated before use.
- No network address is interpolated into shell commands.
- pymodbus is an optional dependency; import errors produce a clear message.
"""
from __future__ import annotations

import asyncio
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional pymodbus import
# ---------------------------------------------------------------------------

try:
    from pymodbus.client import AsyncModbusTcpClient, AsyncModbusSerialClient
    from pymodbus.exceptions import ModbusException
    _PYMODBUS_AVAILABLE = True
except ImportError:
    _PYMODBUS_AVAILABLE = False
    AsyncModbusTcpClient = None       # type: ignore[assignment, misc]
    AsyncModbusSerialClient = None    # type: ignore[assignment, misc]
    ModbusException = Exception       # type: ignore[assignment, misc]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

class ModbusMode(Enum):
    TCP = "tcp"
    RTU = "rtu"


class RegisterType(Enum):
    HOLDING = "holding"
    INPUT = "input"
    COIL = "coil"
    DISCRETE = "discrete"


@dataclass
class RegisterSpec:
    """Specification for a single Modbus read operation."""

    slave_id: int           # 1-247
    address: int            # 0x0000 – 0xFFFF
    count: int              # number of registers / coils to read
    sensor_id: str
    sensor_type: str = ""
    unit: str = ""
    register_type: RegisterType = RegisterType.HOLDING
    device_id: str = ""
    priority: Priority = Priority.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (1 <= self.slave_id <= 247):
            raise ValueError(f"slave_id must be 1-247, got {self.slave_id}")
        if not (0 <= self.address <= 0xFFFF):
            raise ValueError(f"address must be 0-65535, got {self.address}")
        if not (1 <= self.count <= 125):
            raise ValueError(f"count must be 1-125, got {self.count}")


EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# ModbusAdapter
# ---------------------------------------------------------------------------

class ModbusAdapter:
    """Poll Modbus registers and emit SensorEvents.

    Create via the class-method factories::

        ModbusAdapter.tcp(host, port)
        ModbusAdapter.rtu(port, baud_rate)
    """

    _DEFAULT_TCP_PORT = 502
    _DEFAULT_BAUD_RATE = 9600

    def __init__(
        self,
        mode: ModbusMode,
        *,
        host: str = "",
        port: int = _DEFAULT_TCP_PORT,
        serial_port: str = "",
        baud_rate: int = _DEFAULT_BAUD_RATE,
        poll_interval_s: float = 1.0,
        timeout_s: float = 3.0,
        reconnect_delay_s: float = 5.0,
    ) -> None:
        if not _PYMODBUS_AVAILABLE:
            raise RuntimeError(
                "pymodbus is not installed — run: pip install llmesh[industrial]"
            )
        self._mode = mode
        self._host = host
        self._port = port
        self._serial_port = serial_port
        self._baud_rate = baud_rate
        self._poll_interval_s = max(0.1, poll_interval_s)
        self._timeout_s = timeout_s
        self._reconnect_delay_s = reconnect_delay_s
        self._specs: list[RegisterSpec] = []
        self._callbacks: list[EventCallback] = []
        self._client: Any = None
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def tcp(
        cls,
        host: str,
        port: int = _DEFAULT_TCP_PORT,
        *,
        poll_interval_s: float = 1.0,
        timeout_s: float = 3.0,
        reconnect_delay_s: float = 5.0,
    ) -> ModbusAdapter:
        """Create a Modbus TCP adapter."""
        return cls(
            ModbusMode.TCP,
            host=host,
            port=port,
            poll_interval_s=poll_interval_s,
            timeout_s=timeout_s,
            reconnect_delay_s=reconnect_delay_s,
        )

    @classmethod
    def rtu(
        cls,
        serial_port: str,
        baud_rate: int = _DEFAULT_BAUD_RATE,
        *,
        poll_interval_s: float = 1.0,
        timeout_s: float = 3.0,
        reconnect_delay_s: float = 5.0,
    ) -> ModbusAdapter:
        """Create a Modbus RTU adapter (RS-485 / RS-232)."""
        return cls(
            ModbusMode.RTU,
            serial_port=serial_port,
            baud_rate=baud_rate,
            poll_interval_s=poll_interval_s,
            timeout_s=timeout_s,
            reconnect_delay_s=reconnect_delay_s,
        )

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_register(
        self,
        slave_id: int,
        address: int,
        count: int,
        sensor_id: str,
        *,
        sensor_type: str = "",
        unit: str = "",
        register_type: RegisterType = RegisterType.HOLDING,
        device_id: str = "",
        priority: Priority = Priority.NORMAL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a Modbus address range to poll."""
        spec = RegisterSpec(
            slave_id=slave_id,
            address=address,
            count=count,
            sensor_id=sensor_id,
            sensor_type=sensor_type,
            unit=unit,
            register_type=register_type,
            device_id=device_id,
            priority=priority,
            metadata=dict(metadata) if metadata else {},
        )
        self._specs.append(spec)

    def on_event(self, callback: EventCallback) -> None:
        """Register a callback invoked with each new SensorEvent."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect and begin polling. Non-blocking; polls in background task."""
        if self._running:
            return
        self._running = True
        self._client = self._build_client()
        self._task = asyncio.create_task(self._poll_loop(), name="modbus_poll")

    async def stop(self) -> None:
        """Stop polling and close the Modbus connection."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._client is not None:
            try:
                self._client.close()
            except Exception:
                pass
            self._client = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _build_client(self) -> Any:
        if self._mode is ModbusMode.TCP:
            return AsyncModbusTcpClient(
                self._host,
                port=self._port,
                timeout=self._timeout_s,
            )
        return AsyncModbusSerialClient(
            self._serial_port,
            baudrate=self._baud_rate,
            timeout=self._timeout_s,
        )

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                if not self._client.connected:
                    await self._client.connect()
                    if not self._client.connected:
                        logger.warning(
                            "ModbusAdapter: connection failed — retrying in %ss",
                            self._reconnect_delay_s,
                        )
                        await asyncio.sleep(self._reconnect_delay_s)
                        continue

                for spec in self._specs:
                    await self._poll_spec(spec)

                await asyncio.sleep(self._poll_interval_s)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("ModbusAdapter poll error: %s — reconnecting", exc)
                await asyncio.sleep(self._reconnect_delay_s)

    async def _poll_spec(self, spec: RegisterSpec) -> None:
        try:
            raw, values = await self._read(spec)
        except Exception as exc:
            logger.warning(
                "ModbusAdapter: failed to read %s (slave=%d addr=0x%04X): %s",
                spec.sensor_id, spec.slave_id, spec.address, exc,
            )
            return

        meta = dict(spec.metadata)
        meta.update({
            "slave_id": spec.slave_id,
            "address": spec.address,
            "count": spec.count,
            "register_type": spec.register_type.value,
            **({"values": list(values)} if values else {}),
        })
        event = SensorEvent.create(
            sensor_id=spec.sensor_id,
            protocol="modbus",
            payload=raw,
            priority=spec.priority,
            device_id=spec.device_id,
            sensor_type=spec.sensor_type,
            unit=spec.unit,
            metadata=meta,
        )
        self._emit(event)

    async def _read(self, spec: RegisterSpec) -> tuple[bytes, list[int]]:
        rt = spec.register_type
        if rt is RegisterType.HOLDING:
            result = await self._client.read_holding_registers(
                spec.address, spec.count, slave=spec.slave_id
            )
        elif rt is RegisterType.INPUT:
            result = await self._client.read_input_registers(
                spec.address, spec.count, slave=spec.slave_id
            )
        elif rt is RegisterType.COIL:
            result = await self._client.read_coils(
                spec.address, spec.count, slave=spec.slave_id
            )
        else:  # DISCRETE
            result = await self._client.read_discrete_inputs(
                spec.address, spec.count, slave=spec.slave_id
            )

        if result.isError():
            raise ModbusException(f"Modbus error response: {result}")

        if rt in (RegisterType.HOLDING, RegisterType.INPUT):
            values: list[int] = result.registers
            raw = struct.pack(f">{len(values)}H", *values)
        else:
            values = [int(b) for b in result.bits[: spec.count]]
            raw = bytes(values)

        return raw, values

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("ModbusAdapter callback error: %s", exc)
