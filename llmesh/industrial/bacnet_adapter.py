"""BACnetAdapter — Building Automation & Control Networks adapter (v2.4 — K-10.1).

Polls BACnet devices over BACnet/IP using ``bacpypes3`` and emits
SensorEvents for each configured object.  BACnet is the dominant
building management protocol (ASHRAE 135 / ISO 16484-5) covering HVAC,
lighting, fire, security, and energy management.

Supported object types
----------------------
- ``analog-input`` / ``analog-output`` / ``analog-value``
- ``binary-input`` / ``binary-output`` / ``binary-value``
- ``multi-state-input`` / ``multi-state-output`` / ``multi-state-value``

Usage::

    adapter = BACnetAdapter(local_ip="192.168.1.50/24",
                            device_id_local=901)
    adapter.add_object(
        device_id=1001, object_type="analog-input", instance=1,
        sensor_id="zone1_temp",
        sensor_type="temperature", unit="degC",
        property_name="present-value",
    )
    adapter.on_event(lambda ev: print(ev))
    await adapter.start()
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Object types validated against the allowed list before use.
- Local IP / netmask validated.
- bacpypes3 is an optional dependency; import errors raise RuntimeError.
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
# Optional bacpypes3 import
# ---------------------------------------------------------------------------

try:
    import bacpypes3 as _bacpypes3      # type: ignore[import-not-found]
    _BACPYPES_AVAILABLE = True
except ImportError:
    _bacpypes3 = None                   # type: ignore[assignment]
    _BACPYPES_AVAILABLE = False


# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# BACnet object types we accept.  The full BACnet standard defines >50
# but for sensor monitoring these 9 cover ~95% of use cases.
_SUPPORTED_OBJECT_TYPES = frozenset({
    "analog-input", "analog-output", "analog-value",
    "binary-input", "binary-output", "binary-value",
    "multi-state-input", "multi-state-output", "multi-state-value",
})

# Default property to read when not specified.
_DEFAULT_PROPERTY = "present-value"

# Local IP/netmask format: "<dotted-quad>/<prefix-len>" — bacpypes3 syntax.
_LOCAL_IP_RE = re.compile(
    r"^(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
    r"(?:\.(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)){3}"
    r"/(?:[0-9]|[12]\d|3[0-2])$"
)

# Device ID range per BACnet spec (4-octet unsigned).
_DEVICE_ID_MAX = 4_194_303     # 0x3FFFFF

# Default poll interval — 5 seconds is conservative; BMS rarely needs faster.
_DEFAULT_POLL_S = 5.0
_MIN_POLL_S = 0.5
_DEFAULT_RECONNECT_S = 10.0


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class BACnetObjectSpec:
    """Configuration for polling one BACnet object property."""

    device_id: int                      # BACnet device instance number
    object_type: str                    # one of _SUPPORTED_OBJECT_TYPES
    instance: int                       # object instance number
    sensor_id: str
    property_name: str = _DEFAULT_PROPERTY
    sensor_type: str = ""
    unit: str = ""
    device_id_label: str = ""           # local label / name
    priority: Priority = Priority.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not (0 <= self.device_id <= _DEVICE_ID_MAX):
            raise ValueError(
                f"device_id {self.device_id} out of range "
                f"(0–{_DEVICE_ID_MAX})"
            )
        if self.object_type not in _SUPPORTED_OBJECT_TYPES:
            raise ValueError(
                f"object_type {self.object_type!r} not supported; "
                f"choose from {sorted(_SUPPORTED_OBJECT_TYPES)}"
            )
        if self.instance < 0:
            raise ValueError(f"instance must be ≥ 0, got {self.instance}")


EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# BACnetAdapter
# ---------------------------------------------------------------------------

class BACnetAdapter:
    """Poll BACnet device properties via BACnet/IP and emit SensorEvents.

    Parameters
    ----------
    local_ip:
        Local interface in CIDR form, e.g. ``"192.168.1.50/24"``.
    device_id_local:
        BACnet device instance number assigned to this LLMesh node
        (any unused integer in 0–4,194,303).
    poll_interval_s:
        Seconds between consecutive read cycles (≥ 0.5 s).
    reconnect_delay_s:
        Seconds to wait before retrying after a fatal error.
    """

    def __init__(
        self,
        local_ip: str,
        *,
        device_id_local: int = 999,
        poll_interval_s: float = _DEFAULT_POLL_S,
        reconnect_delay_s: float = _DEFAULT_RECONNECT_S,
    ) -> None:
        if not _BACPYPES_AVAILABLE:
            raise RuntimeError(
                "bacpypes3 is not installed — run: pip install llmesh[bacnet]"
            )
        if not _LOCAL_IP_RE.match(local_ip):
            raise ValueError(
                f"local_ip {local_ip!r} must be CIDR form 'a.b.c.d/prefix'"
            )
        if not (0 <= device_id_local <= _DEVICE_ID_MAX):
            raise ValueError(
                f"device_id_local {device_id_local} out of range "
                f"(0–{_DEVICE_ID_MAX})"
            )
        self._local_ip = local_ip
        self._device_id_local = device_id_local
        self._poll_interval_s = max(_MIN_POLL_S, poll_interval_s)
        self._reconnect_delay_s = reconnect_delay_s
        self._specs: list[BACnetObjectSpec] = []
        self._callbacks: list[EventCallback] = []
        self._app: Any = None
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._running = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_object(
        self,
        device_id: int,
        object_type: str,
        instance: int,
        sensor_id: str,
        *,
        property_name: str = _DEFAULT_PROPERTY,
        sensor_type: str = "",
        unit: str = "",
        device_id_label: str = "",
        priority: Priority = Priority.NORMAL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        spec = BACnetObjectSpec(
            device_id=device_id,
            object_type=object_type,
            instance=instance,
            sensor_id=sensor_id,
            property_name=property_name,
            sensor_type=sensor_type,
            unit=unit,
            device_id_label=device_id_label,
            priority=priority,
            metadata=dict(metadata) if metadata else {},
        )
        self._specs.append(spec)

    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._poll_loop(), name="bacnet_poll")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        await self._close_app()

    # ------------------------------------------------------------------
    # Internal — poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                if self._app is None:
                    ok = await self._open_app()
                    if not ok:
                        await asyncio.sleep(self._reconnect_delay_s)
                        continue
                for spec in self._specs:
                    await self._poll_spec(spec)
                await asyncio.sleep(self._poll_interval_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "BACnetAdapter: poll error: %s — reconnecting in %ss",
                    exc, self._reconnect_delay_s,
                )
                await self._close_app()
                await asyncio.sleep(self._reconnect_delay_s)

    async def _open_app(self) -> bool:
        """Initialise the bacpypes3 BACnet application."""
        try:
            from bacpypes3.app import Application       # type: ignore
            from bacpypes3.local.device import DeviceObject  # type: ignore
            self._app = Application.from_args(
                argv=[],
                ip_address=self._local_ip,
                device_object=DeviceObject(
                    objectIdentifier=("device", self._device_id_local),
                    objectName=f"llmesh-{self._device_id_local}",
                ),
            )
            return True
        except Exception as exc:
            logger.error("BACnetAdapter open error: %s", exc)
            return False

    async def _poll_spec(self, spec: BACnetObjectSpec) -> None:
        try:
            value = await self._read_property(spec)
        except Exception as exc:
            logger.warning(
                "BACnetAdapter: read failed dev=%d %s.%d.%s: %s",
                spec.device_id, spec.object_type, spec.instance,
                spec.property_name, exc,
            )
            return

        meta = dict(spec.metadata)
        meta.update({
            "bacnet_device_id": spec.device_id,
            "object_type": spec.object_type,
            "instance": spec.instance,
            "property_name": spec.property_name,
            "raw_value": value,
        })
        # Coerce numeric-looking values to float for downstream analyzers.
        try:
            physical = float(value)
            payload = struct.pack("<d", physical)
            meta["physical_value"] = physical
        except (TypeError, ValueError):
            payload = str(value).encode("utf-8")

        event = SensorEvent.create(
            sensor_id=spec.sensor_id,
            protocol="bacnet",
            payload=payload,
            priority=spec.priority,
            device_id=spec.device_id_label or f"bacnet-{spec.device_id}",
            sensor_type=spec.sensor_type,
            unit=spec.unit,
            metadata=meta,
        )
        self._emit(event)

    async def _read_property(self, spec: BACnetObjectSpec) -> Any:
        """Read one property — separated for easy mocking in tests."""
        # bacpypes3.read_property returns the value directly (async).
        # Implementations may override this in tests.
        from bacpypes3.pdu import Address                # type: ignore
        from bacpypes3.primitivedata import ObjectIdentifier  # type: ignore

        target = Address(f"{spec.device_id}")
        oid = ObjectIdentifier((spec.object_type, spec.instance))
        return await self._app.read_property(
            target, oid, spec.property_name,
        )

    async def _close_app(self) -> None:
        if self._app is not None:
            try:
                close = getattr(self._app, "close", None)
                if asyncio.iscoroutinefunction(close):
                    await close()
                elif callable(close):
                    close()
            except Exception:
                pass
            self._app = None

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("BACnetAdapter callback error: %s", exc)
