"""CANAdapter — CAN-bus adapter for LLMesh Industrial (v3 — C-2).

Receives CAN 2.0 / CAN-FD frames via the ``python-can`` library, decodes
specified bytes from each frame as numeric sensor values, and emits them
as SensorEvents.

Supports any python-can interface: SocketCAN (Linux), Vector, PCAN,
Kvaser, IXXAT, ICS NeoVI, virtual, etc.

Usage::

    adapter = CANAdapter(channel="can0", bustype="socketcan")
    adapter.add_frame(
        can_id=0x100, sensor_id="engine_rpm",
        data_type="uint16", byte_offset=0,
        scale=0.25, offset=0.0,
        sensor_type="rpm", unit="rpm",
    )
    adapter.on_event(lambda ev: print(ev.metadata["physical_value"]))
    await adapter.start()
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Channel name validated to safe character set before use.
- python-can is an optional dependency.
- CAN IDs validated to 11-bit (standard) or 29-bit (extended) ranges.
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
# Optional python-can import
# ---------------------------------------------------------------------------

try:
    import can as _can
    _CAN_AVAILABLE = True
except ImportError:
    _can = None              # type: ignore[assignment]
    _CAN_AVAILABLE = False

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Same struct format table as EtherCAT for consistency.
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

# CAN ID limits per ISO 11898 (CAN 2.0):
#   - 11-bit standard: 0x000–0x7FF (0–2047)
#   - 29-bit extended: 0x00000000–0x1FFFFFFF (0–536,870,911)
_CAN_STD_MAX = 0x7FF
_CAN_EXT_MAX = 0x1FFFFFFF

# CAN-FD maximum DLC (data length code) is 64 bytes; CAN 2.0 is 8 bytes.
_CAN_2_0_MAX_DLC = 8
_CAN_FD_MAX_DLC = 64

# Channel names allowed character set — keeps shell injection out.
_CHANNEL_NAME_RE = re.compile(r"^[a-zA-Z0-9_\-\.:/]{1,64}$")


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class FrameSpec:
    """Configuration for decoding one numeric value from a CAN frame."""

    can_id: int
    sensor_id: str
    data_type: str = "uint16"     # key from _STRUCT_FMT
    byte_offset: int = 0          # offset within the frame data bytes
    scale: float = 1.0            # raw_value * scale + offset → physical
    offset: float = 0.0
    extended: bool = False        # True for 29-bit extended CAN IDs
    sensor_type: str = ""
    unit: str = ""
    device_id: str = ""
    priority: Priority = Priority.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        max_id = _CAN_EXT_MAX if self.extended else _CAN_STD_MAX
        if not (0 <= self.can_id <= max_id):
            raise ValueError(
                f"can_id {hex(self.can_id)} out of range "
                f"(0-{hex(max_id)} for {'extended' if self.extended else 'standard'})"
            )
        if self.data_type not in _STRUCT_FMT:
            raise ValueError(
                f"data_type {self.data_type!r} not supported; "
                f"choose from {sorted(_STRUCT_FMT)}"
            )
        if self.byte_offset < 0:
            raise ValueError(f"byte_offset must be ≥ 0, got {self.byte_offset}")


EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# CANAdapter
# ---------------------------------------------------------------------------

class CANAdapter:
    """Listen on a CAN bus and emit SensorEvents for configured frame IDs.

    Parameters
    ----------
    channel:
        python-can channel string (e.g. ``"can0"`` for SocketCAN).
    bustype:
        python-can interface backend (``"socketcan"``, ``"vector"``,
        ``"pcan"``, ``"virtual"``, ...).
    bitrate:
        CAN bitrate in bps (default 500_000).
    fd:
        True for CAN-FD; False for CAN 2.0.
    reconnect_delay_s:
        Seconds to wait before retrying after a fatal bus error.
    """

    _DEFAULT_BITRATE = 500_000
    _DEFAULT_RECONNECT_S = 5.0

    def __init__(
        self,
        channel: str,
        *,
        bustype: str = "socketcan",
        bitrate: int = _DEFAULT_BITRATE,
        fd: bool = False,
        reconnect_delay_s: float = _DEFAULT_RECONNECT_S,
    ) -> None:
        if not _CAN_AVAILABLE:
            raise RuntimeError(
                "python-can is not installed — run: pip install llmesh[can]"
            )
        if not _CHANNEL_NAME_RE.match(channel):
            raise ValueError(
                f"channel {channel!r} contains invalid characters; "
                "use alphanumeric / '-' / '_' / '.' / ':' / '/' (max 64 chars)"
            )
        self._channel = channel
        self._bustype = bustype
        self._bitrate = bitrate
        self._fd = fd
        self._reconnect_delay_s = reconnect_delay_s
        self._specs: dict[tuple[int, bool], list[FrameSpec]] = {}
        self._callbacks: list[EventCallback] = []
        self._bus: Any = None
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_frame(
        self,
        can_id: int,
        sensor_id: str,
        *,
        data_type: str = "uint16",
        byte_offset: int = 0,
        scale: float = 1.0,
        offset: float = 0.0,
        extended: bool = False,
        sensor_type: str = "",
        unit: str = "",
        device_id: str = "",
        priority: Priority = Priority.NORMAL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register a frame ID + byte range to decode into a SensorEvent."""
        spec = FrameSpec(
            can_id=can_id,
            sensor_id=sensor_id,
            data_type=data_type,
            byte_offset=byte_offset,
            scale=scale,
            offset=offset,
            extended=extended,
            sensor_type=sensor_type,
            unit=unit,
            device_id=device_id,
            priority=priority,
            metadata=dict(metadata) if metadata else {},
        )
        # Multiple specs per (can_id, extended) allow extracting multiple
        # values from one frame (e.g. RPM in bytes 0–1, temperature in 2–3).
        self._specs.setdefault((can_id, extended), []).append(spec)

    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._bus_loop(), name="can_recv")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await asyncio.get_event_loop().run_in_executor(None, self._close_bus)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _bus_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._running:
            try:
                ok = await loop.run_in_executor(None, self._open_bus)
                if not ok:
                    logger.warning(
                        "CANAdapter: open failed on %s — retrying in %ss",
                        self._channel, self._reconnect_delay_s,
                    )
                    await asyncio.sleep(self._reconnect_delay_s)
                    continue
                logger.info("CANAdapter: %s online", self._channel)
                while self._running:
                    msg = await loop.run_in_executor(None, self._recv_one)
                    if msg is not None:
                        self._dispatch(msg)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "CANAdapter: bus error on %s: %s — reconnecting in %ss",
                    self._channel, exc, self._reconnect_delay_s,
                )
                self._close_bus()
                await asyncio.sleep(self._reconnect_delay_s)

    def _open_bus(self) -> bool:
        try:
            self._bus = _can.Bus(
                channel=self._channel,
                bustype=self._bustype,
                bitrate=self._bitrate,
                fd=self._fd,
            )
            return True
        except Exception as exc:
            logger.error("CANAdapter open error: %s", exc)
            return False

    def _recv_one(self) -> Any:
        try:
            return self._bus.recv(timeout=1.0)
        except Exception as exc:
            logger.warning("CANAdapter recv error: %s", exc)
            return None

    def _dispatch(self, msg: Any) -> None:
        can_id = int(getattr(msg, "arbitration_id", 0))
        extended = bool(getattr(msg, "is_extended_id", False))
        data = bytes(getattr(msg, "data", b""))
        max_dlc = _CAN_FD_MAX_DLC if self._fd else _CAN_2_0_MAX_DLC
        if len(data) > max_dlc:
            data = data[:max_dlc]

        specs = self._specs.get((can_id, extended)) or []
        for spec in specs:
            self._emit_from_frame(data, spec, can_id, extended)

    def _emit_from_frame(
        self, data: bytes, spec: FrameSpec, can_id: int, extended: bool,
    ) -> None:
        fmt = _STRUCT_FMT[spec.data_type]
        size = _TYPE_SIZE[spec.data_type]
        end = spec.byte_offset + size
        if len(data) < end:
            logger.debug(
                "CANAdapter: frame too short for %s (need %d, got %d)",
                spec.sensor_id, end, len(data),
            )
            return
        (raw_val,) = struct.unpack_from(fmt, data, spec.byte_offset)
        physical = float(raw_val) * spec.scale + spec.offset

        meta = dict(spec.metadata)
        meta.update({
            "can_id": can_id,
            "extended": extended,
            "data_type": spec.data_type,
            "byte_offset": spec.byte_offset,
            "raw_value": raw_val,
            "physical_value": physical,
            "frame_dlc": len(data),
        })

        event = SensorEvent.create(
            sensor_id=spec.sensor_id,
            protocol="can",
            payload=struct.pack("<d", physical),
            priority=spec.priority,
            device_id=spec.device_id,
            sensor_type=spec.sensor_type,
            unit=spec.unit,
            metadata=meta,
        )
        self._emit(event)

    def _close_bus(self) -> None:
        if self._bus is not None:
            try:
                self._bus.shutdown()
            except Exception:
                pass
            self._bus = None

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("CANAdapter callback error: %s", exc)
