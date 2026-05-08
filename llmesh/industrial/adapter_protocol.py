"""IndustrialAdapter — formal Protocol for all Industrial input adapters (v2.2.0).

Defines the **contract** that every industrial input adapter must satisfy:

    * ``async start()``      — bind / connect / begin polling
    * ``async stop()``       — graceful shutdown
    * ``on_event(callback)`` — register a SensorEvent consumer

This is a structural :class:`typing.Protocol` (PEP 544), so adapters are
*not required* to subclass it — they only need to expose matching method
signatures.  This preserves the existing duck-typing while giving:

  * Static type checkers (mypy, pyright) a single contract to verify
  * Runtime test harness ``assert isinstance(adapter, IndustrialAdapter)``
  * Autocompletion for libraries that consume "any" industrial adapter
  * Documentation: a single place to find the adapter contract

Adapters covered (verified at runtime by `tests/test_adapter_protocol.py`):

  - ModbusAdapter / SerialAdapter / OPCUAAdapter / MQTTAdapter
  - EtherCATAdapter / CANAdapter
  - AoiAdapter / DepthCameraAdapter / EventCameraAdapter

Security invariants
-------------------
- This module declares interfaces only; no I/O, no mutable state.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Protocol, runtime_checkable

from llmesh.industrial.sensor_event import SensorEvent


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class IndustrialAdapter(Protocol):
    """Common contract for input adapters that emit SensorEvents.

    All Phase A–G adapters and v3 adapters (CAN, …) implement this
    protocol.  Use it as the single point of integration for code that
    needs to operate on "any" industrial adapter without depending on a
    specific protocol implementation.

    Example (multi-adapter fan-in)::

        adapters: list[IndustrialAdapter] = [modbus, opcua, mqtt, can]
        for a in adapters:
            a.on_event(pipeline.process)
            await a.start()
    """

    async def start(self) -> None:
        """Connect / bind / begin emitting SensorEvents.

        Idempotent: calling start() twice has no effect on a running
        adapter.  Implementations must not block — long-running work
        should run in a background asyncio task.
        """
        ...

    async def stop(self) -> None:
        """Stop emitting events and release all resources.

        Implementations must:
          - cancel any background tasks created in ``start``
          - close protocol-level connections (sockets, files, buses)
          - be safe to call multiple times
        """
        ...

    def on_event(self, callback: EventCallback) -> None:
        """Register a callback invoked on every SensorEvent.

        Multiple callbacks may be registered.  Exceptions raised by a
        callback must not propagate out of the adapter or stop other
        callbacks from being invoked.
        """
        ...


__all__ = ["IndustrialAdapter", "EventCallback"]
