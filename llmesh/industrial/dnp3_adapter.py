"""DNP3Adapter — v3-N7 / K-1.1 SCADA outstation client (skeleton).

Status
------
**v2.14 skeleton.** The wire-protocol layer is delegated to the
optional ``pydnp3`` (or compatible) library. When that library is not
installed, the adapter raises ``RuntimeError`` on ``connect`` /
``poll`` so the rest of the framework still imports cleanly.

The pure-stdlib parts (SensorEvent normalisation, point-class →
``sensor_type`` mapping, polling loop, allow-list validation) are fully
implemented and unit-tested without any DNP3 library installed.

DNP3 group conventions
----------------------
The IEEE 1815-2012 standard defines numeric group codes per object
category. We map the common ones to LLMesh ``sensor_type`` strings::

    Group 1, 2   — Binary Input            → "binary_input"
    Group 10, 11 — Binary Output           → "binary_output"
    Group 20, 21 — Counter                 → "counter"
    Group 30, 31, 32 — Analog Input        → "analog_input"
    Group 40, 41 — Analog Output           → "analog_output"
    Group 50     — Time                    → "time"

Security invariants
-------------------
- Outstation address is validated against ``allow_addresses`` (a
  whitelist of (master, outstation) pairs).
- All values are wrapped as ``SensorEvent`` and routed through the
  caller's pipeline; the adapter itself never invokes shells or imports
  pickle.
- The optional ``pydnp3`` import is strictly lazy — importing this
  module never triggers a DNP3 stack initialisation.
"""
from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Callable, Iterable

from .sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)


# Group code → semantic sensor_type
_GROUP_MAP: dict[int, str] = {
    1: "binary_input", 2: "binary_input",
    10: "binary_output", 11: "binary_output",
    20: "counter", 21: "counter",
    30: "analog_input", 31: "analog_input", 32: "analog_input",
    40: "analog_output", 41: "analog_output",
    50: "time",
}


@dataclass(frozen=True)
class DNP3Point:
    """One DNP3 (group, variation, index, value) tuple."""

    group: int
    variation: int
    index: int
    value: float | int | bool


def _encode_value(value) -> bytes:
    """Serialize a DNP3 point value into bytes for SensorEvent.payload."""
    if isinstance(value, bool):
        return b"\x01" if value else b"\x00"
    if isinstance(value, int):
        return struct.pack("<q", int(value))
    if isinstance(value, float):
        return struct.pack("<d", float(value))
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    return str(value).encode("utf-8")


def _validate_address(master: int, outstation: int,
                      allow: Iterable[tuple[int, int]] | None) -> bool:
    if allow is None:
        return True
    return (int(master), int(outstation)) in {(int(a), int(b)) for a, b in allow}


def point_to_event(
    point: DNP3Point,
    *,
    device_id: str,
    sensor_id: str | None = None,
    timestamp_ns: int | None = None,
) -> SensorEvent:
    """Convert a :class:`DNP3Point` into a :class:`SensorEvent`."""
    sensor_type = _GROUP_MAP.get(point.group, f"dnp3_g{point.group}")
    sid = sensor_id or f"dnp3:g{point.group}:{point.index}"
    metadata = {
        "dnp3_group": point.group,
        "dnp3_variation": point.variation,
        "dnp3_index": point.index,
    }
    return SensorEvent.create(
        sensor_id=sid,
        protocol="dnp3",
        payload=_encode_value(point.value),
        priority=Priority.NORMAL,
        device_id=device_id,
        sensor_type=sensor_type,
        metadata=metadata,
        timestamp_ns=timestamp_ns,
    )


class DNP3Adapter:
    """SCADA outstation client. Skeleton — wire layer is optional.

    Parameters
    ----------
    host, port:
        Outstation network address.
    master_addr, outstation_addr:
        DNP3 master / outstation 16-bit addresses.
    poll_interval_s:
        How often :meth:`poll` should be called by the surrounding loop.
    allow_addresses:
        Whitelist of accepted (master, outstation) pairs. ``None``
        disables the gate (testing only — not recommended in production).
    device_id:
        Forwarded to every emitted ``SensorEvent``.
    """

    def __init__(
        self,
        host: str,
        port: int,
        *,
        master_addr: int = 1,
        outstation_addr: int = 10,
        poll_interval_s: float = 1.0,
        allow_addresses: Iterable[tuple[int, int]] | None = ((1, 10),),
        device_id: str = "",
    ) -> None:
        if poll_interval_s <= 0:
            raise ValueError("poll_interval_s must be positive")
        if not _validate_address(master_addr, outstation_addr, allow_addresses):
            raise ValueError(
                f"address pair ({master_addr}, {outstation_addr}) not in allow_addresses"
            )
        self._host = host
        self._port = int(port)
        self._master = int(master_addr)
        self._outstation = int(outstation_addr)
        self._poll = float(poll_interval_s)
        self._device_id = device_id
        self._callbacks: list[Callable[[SensorEvent], None]] = []
        self._connected = False
        self._driver = None  # populated lazily by .connect()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def device_id(self) -> str:
        return self._device_id

    def on_event(self, callback: Callable[[SensorEvent], None]) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def connect(self, *, driver=None) -> None:
        """Connect to the outstation.

        ``driver`` is an explicit dependency-injection hook; if ``None``
        we attempt the optional ``pydnp3`` import and raise
        :class:`RuntimeError` when it is unavailable.
        """
        if driver is not None:
            self._driver = driver
            self._connected = True
            return
        try:
            import pydnp3  # noqa: F401, PLC0415  — optional
        except ImportError as exc:
            raise RuntimeError(
                "pydnp3 is not installed; install llmesh[dnp3] or pass driver= "
                "to DNP3Adapter.connect() for testing"
            ) from exc
        # The real wire-up is left to a follow-up implementation —
        # this skeleton verifies that the optional import path works.
        self._connected = True

    def disconnect(self) -> None:
        self._connected = False
        self._driver = None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll(self) -> list[SensorEvent]:
        """Read pending values from the outstation and fan out as events.

        Without a driver wired in, returns an empty list — the
        surrounding loop will simply spin at ``poll_interval_s``. When a
        driver is present, it is expected to expose ``read_static()``
        returning an iterable of :class:`DNP3Point`.
        """
        if not self._connected or self._driver is None:
            return []
        events: list[SensorEvent] = []
        for point in self._driver.read_static():
            ev = point_to_event(point, device_id=self._device_id)
            events.append(ev)
            for cb in self._callbacks:
                try:
                    cb(ev)
                except Exception as exc:
                    # Callback errors must not stop the polling loop, but
                    # they need to be visible in operations logs.
                    logger.warning(
                        "DNP3Adapter callback error: %s", exc, exc_info=True,
                    )
        return events
