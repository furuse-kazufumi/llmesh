"""EtherCATAdapter — EtherCAT master adapter for LLMesh Industrial (v1.8.0).

Connects to EtherCAT slave devices via a raw Ethernet interface using the
SOEM (Simple Open EtherCAT Master) library through the ``pysoem`` Python
binding, reads PDO (Process Data Object) input data from each slave on
every cycle, and emits the values as SensorEvents.

EtherCAT state machine used here
----------------------------------
INIT → PRE-OP → SAFE-OP → OPERATIONAL

Setup flow::

    adapter = EtherCATAdapter("eth0", cycle_time_us=1000)
    adapter.add_slave(
        slave_pos=0,
        sensor_id="torque_01",
        data_type="float32",
        byte_offset=0,
        unit="Nm",
        sensor_type="torque",
    )
    adapter.on_event(lambda ev: print(ev))
    await adapter.start()          # opens iface, transitions to OP state
    await adapter.stop()

Platform note
-------------
**Linux only.** ``pysoem`` wraps SOEM which requires raw socket access
(``CAP_NET_RAW`` or root).  On Windows / macOS the import will fail and
``EtherCATAdapter.__init__`` raises ``RuntimeError``.

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Interface name is validated to a safe character set before use.
- ``pysoem`` is an optional dependency — install with
  ``pip install llmesh[ethercat]`` on Linux.
"""
from __future__ import annotations

import asyncio
import logging
import re
import struct
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional pysoem import
# ---------------------------------------------------------------------------

try:
    import pysoem as _pysoem
    _PYSOEM_AVAILABLE = True
except ImportError:
    _pysoem = None              # type: ignore[assignment]
    _PYSOEM_AVAILABLE = False

# ---------------------------------------------------------------------------
# Supported PDO data types
# ---------------------------------------------------------------------------

_STRUCT_FMT: dict[str, str] = {
    "int8":    "<b",
    "uint8":   "<B",
    "int16":   "<h",
    "uint16":  "<H",
    "int32":   "<i",
    "uint32":  "<I",
    "int64":   "<q",
    "uint64":  "<Q",
    "float32": "<f",
    "float64": "<d",
}
_TYPE_SIZE = {k: struct.calcsize(v) for k, v in _STRUCT_FMT.items()}

# Allowed characters in an Ethernet interface name (Linux ethtool convention)
_IFNAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.]{1,15}$")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class SlaveSpec:
    """Configuration for reading one value from a single EtherCAT slave PDO."""

    slave_pos: int          # 0-based slave index on the bus
    sensor_id: str
    data_type: str = "float32"   # key from _STRUCT_FMT
    byte_offset: int = 0         # byte offset within slave.input
    scale: float = 1.0           # raw_value * scale + offset → physical value
    offset: float = 0.0
    sensor_type: str = ""
    unit: str = ""
    device_id: str = ""
    priority: Priority = Priority.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.slave_pos < 0:
            raise ValueError(f"slave_pos must be ≥ 0, got {self.slave_pos}")
        if self.data_type not in _STRUCT_FMT:
            raise ValueError(
                f"data_type {self.data_type!r} not supported; "
                f"choose from {sorted(_STRUCT_FMT)}"
            )
        if self.byte_offset < 0:
            raise ValueError(f"byte_offset must be ≥ 0, got {self.byte_offset}")


EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# EtherCATAdapter
# ---------------------------------------------------------------------------

class EtherCATAdapter:
    """Cyclic EtherCAT master that reads slave PDOs and emits SensorEvents.

    Parameters
    ----------
    ifname:
        Ethernet interface name (e.g. ``"eth0"``).  Must match
        ``[a-zA-Z0-9_\\-.]{1,15}``.
    cycle_time_us:
        Desired cycle time in microseconds.  LLMesh runs non-real-time so
        this is a best-effort target (``asyncio.sleep``-based).
    recv_timeout_us:
        Timeout for ``recv_processdata`` in microseconds.
    reconnect_delay_s:
        Seconds to wait before retrying after a fatal bus error.
    op_state_timeout_s:
        Seconds to wait for all slaves to reach OPERATIONAL state.
    """

    _DEFAULT_CYCLE_US = 1_000       # 1 ms
    _DEFAULT_RECV_TIMEOUT_US = 2_000
    _DEFAULT_RECONNECT_S = 5.0
    _DEFAULT_OP_TIMEOUT_S = 10.0

    def __init__(
        self,
        ifname: str,
        *,
        cycle_time_us: int = _DEFAULT_CYCLE_US,
        recv_timeout_us: int = _DEFAULT_RECV_TIMEOUT_US,
        reconnect_delay_s: float = _DEFAULT_RECONNECT_S,
        op_state_timeout_s: float = _DEFAULT_OP_TIMEOUT_S,
    ) -> None:
        if not _PYSOEM_AVAILABLE:
            raise RuntimeError(
                "pysoem is not installed — run: pip install llmesh[ethercat]  "
                "(Linux only; requires CAP_NET_RAW or root)"
            )
        if not _IFNAME_RE.match(ifname):
            raise ValueError(
                f"ifname {ifname!r} contains invalid characters; "
                "use alphanumeric, hyphen, underscore, or dot (max 15 chars)"
            )
        self._ifname = ifname
        self._cycle_time_s = max(1e-4, cycle_time_us / 1_000_000)
        self._recv_timeout_us = recv_timeout_us
        self._reconnect_delay_s = reconnect_delay_s
        self._op_state_timeout_s = op_state_timeout_s
        self._specs: list[SlaveSpec] = []
        self._callbacks: list[EventCallback] = []
        self._master: Any = None
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_slave(
        self,
        slave_pos: int,
        sensor_id: str,
        *,
        data_type: str = "float32",
        byte_offset: int = 0,
        scale: float = 1.0,
        offset: float = 0.0,
        sensor_type: str = "",
        unit: str = "",
        device_id: str = "",
        priority: Priority = Priority.NORMAL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register an EtherCAT slave PDO field to read each cycle."""
        spec = SlaveSpec(
            slave_pos=slave_pos,
            sensor_id=sensor_id,
            data_type=data_type,
            byte_offset=byte_offset,
            scale=scale,
            offset=offset,
            sensor_type=sensor_type,
            unit=unit,
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
        """Open EtherCAT master and start cyclic PDO exchange."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._bus_loop(), name="ethercat_cycle")

    async def stop(self) -> None:
        """Stop cyclic exchange and close the EtherCAT master."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await asyncio.get_event_loop().run_in_executor(None, self._close_master)

    # ------------------------------------------------------------------
    # Internal — asyncio bus loop
    # ------------------------------------------------------------------

    async def _bus_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                ok = await loop.run_in_executor(None, self._open_and_transition)
                if not ok:
                    logger.warning(
                        "EtherCATAdapter: bus init failed on %s — retrying in %ss",
                        self._ifname, self._reconnect_delay_s,
                    )
                    await asyncio.sleep(self._reconnect_delay_s)
                    continue

                logger.info(
                    "EtherCATAdapter: %s online, %d slave(s)",
                    self._ifname, len(self._master.slaves),
                )

                while self._running:
                    await loop.run_in_executor(None, self._do_cycle)
                    await asyncio.sleep(self._cycle_time_s)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "EtherCATAdapter: bus error on %s: %s — reconnecting in %ss",
                    self._ifname, exc, self._reconnect_delay_s,
                )
                self._close_master()
                await asyncio.sleep(self._reconnect_delay_s)

    # ------------------------------------------------------------------
    # Internal — blocking SOEM calls (run in executor)
    # ------------------------------------------------------------------

    def _open_and_transition(self) -> bool:
        """Open master, discover slaves, transition to OPERATIONAL. Returns True on success."""
        try:
            self._master = _pysoem.Master()
            self._master.open(self._ifname)
            if self._master.config_init() == 0:
                logger.warning("EtherCATAdapter: no slaves found on %s", self._ifname)
                return False
            self._master.config_map()
            self._master.state = _pysoem.SAFEOP_STATE
            self._master.write_state()
            self._master.state = _pysoem.OP_STATE
            self._master.write_state()
            self._master.read_state()
            for slave in self._master.slaves:
                if slave.state != _pysoem.OP_STATE:
                    logger.warning(
                        "EtherCATAdapter: slave %d not in OP state (state=%d)",
                        slave.position, slave.state,
                    )
                    return False
            return True
        except Exception as exc:
            logger.error("EtherCATAdapter _open_and_transition error: %s", exc)
            return False

    def _do_cycle(self) -> None:
        """One PDO exchange cycle — read all registered slave fields."""
        self._master.send_processdata()
        self._master.recv_processdata(self._recv_timeout_us)
        slaves = self._master.slaves
        for spec in self._specs:
            if spec.slave_pos >= len(slaves):
                logger.debug(
                    "EtherCATAdapter: slave_pos=%d out of range (%d slaves)",
                    spec.slave_pos, len(slaves),
                )
                continue
            slave = slaves[spec.slave_pos]
            pdo_input = bytes(slave.input)
            self._emit_from_pdo(pdo_input, spec)

    def _emit_from_pdo(self, pdo_input: bytes, spec: SlaveSpec) -> None:
        fmt = _STRUCT_FMT[spec.data_type]
        size = _TYPE_SIZE[spec.data_type]
        end = spec.byte_offset + size
        if len(pdo_input) < end:
            logger.debug(
                "EtherCATAdapter: PDO too short for %s (need %d, got %d)",
                spec.sensor_id, end, len(pdo_input),
            )
            return
        (raw_val,) = struct.unpack_from(fmt, pdo_input, spec.byte_offset)
        physical = float(raw_val) * spec.scale + spec.offset

        payload = struct.pack("<d", physical)   # float64 LE for interop
        meta = dict(spec.metadata)
        meta.update({
            "slave_pos": spec.slave_pos,
            "data_type": spec.data_type,
            "byte_offset": spec.byte_offset,
            "raw_value": raw_val,
            "physical_value": physical,
        })

        event = SensorEvent.create(
            sensor_id=spec.sensor_id,
            protocol="ethercat",
            payload=payload,
            priority=spec.priority,
            device_id=spec.device_id,
            sensor_type=spec.sensor_type,
            unit=spec.unit,
            metadata=meta,
        )
        self._emit(event)

    def _close_master(self) -> None:
        if self._master is not None:
            try:
                self._master.close()
            except Exception:
                pass
            self._master = None

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("EtherCATAdapter callback error: %s", exc)
