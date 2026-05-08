"""OPCUAAdapter — OPC-UA client for LLMesh Industrial (v1.6.0).

Connects to an OPC-UA server as a *client*, subscribes to configured node IDs
via the OPC-UA subscription mechanism, and emits each data-change notification
as a SensorEvent for the unified industrial pipeline.

Usage::

    adapter = OPCUAAdapter("opc.tcp://192.168.1.10:4840")
    adapter.add_node(
        node_id="ns=2;i=1001",
        sensor_id="pressure_01",
        sensor_type="pressure",
        unit="Pa",
    )
    adapter.on_event(lambda ev: print(ev))
    await adapter.start()
    # ... subscription runs until stop() is called
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Node IDs are opaque strings — never interpolated into shell commands.
- asyncua is an optional dependency; import errors produce a clear message.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional asyncua import
# ---------------------------------------------------------------------------

try:
    from asyncua import Client as _AsyncuaClient
    from asyncua.common.subscription import DataChangeNotif
    _ASYNCUA_AVAILABLE = True
except ImportError:
    _ASYNCUA_AVAILABLE = False
    _AsyncuaClient = None        # type: ignore[assignment, misc]
    DataChangeNotif = None       # type: ignore[assignment, misc]


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class NodeSpec:
    """Configuration for a single OPC-UA node subscription."""

    node_id: str          # e.g. "ns=2;i=1001" or "ns=2;s=Pressure"
    sensor_id: str
    sensor_type: str = ""
    unit: str = ""
    device_id: str = ""
    priority: Priority = Priority.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)


EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# Internal subscription handler (asyncua callback interface)
# ---------------------------------------------------------------------------

class _SubHandler:
    """Called by asyncua on data-change notifications."""

    def __init__(self, adapter: OPCUAAdapter) -> None:
        self._adapter = adapter

    def datachange_notification(self, node: Any, val: Any, data: Any) -> None:
        try:
            node_id_str = node.nodeid.to_string()
        except Exception:
            return

        spec = self._adapter._node_map.get(node_id_str)
        if spec is None:
            return

        payload = str(val).encode()
        meta = dict(spec.metadata)
        meta["node_id"] = node_id_str
        meta["raw_value"] = repr(val)

        event = SensorEvent.create(
            sensor_id=spec.sensor_id,
            protocol="opcua",
            payload=payload,
            priority=spec.priority,
            device_id=spec.device_id,
            sensor_type=spec.sensor_type,
            unit=spec.unit,
            metadata=meta,
        )
        self._adapter._emit(event)


# ---------------------------------------------------------------------------
# OPCUAAdapter
# ---------------------------------------------------------------------------

class OPCUAAdapter:
    """Subscribe to OPC-UA node data changes and emit SensorEvents.

    Parameters
    ----------
    endpoint_url:
        OPC-UA endpoint, e.g. ``"opc.tcp://plc.factory.local:4840"``.
    subscription_period_ms:
        Requested subscription publishing interval in milliseconds.
    reconnect_delay_s:
        Seconds to wait before retrying after a connection failure.
    timeout_s:
        Connection timeout in seconds.
    """

    _DEFAULT_PERIOD_MS = 500
    _DEFAULT_RECONNECT_S = 5.0
    _DEFAULT_TIMEOUT_S = 10.0

    def __init__(
        self,
        endpoint_url: str,
        *,
        subscription_period_ms: int = _DEFAULT_PERIOD_MS,
        reconnect_delay_s: float = _DEFAULT_RECONNECT_S,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        if not _ASYNCUA_AVAILABLE:
            raise RuntimeError(
                "asyncua is not installed — run: pip install llmesh[industrial]"
            )
        if not endpoint_url.startswith("opc.tcp://"):
            raise ValueError(
                f"endpoint_url must start with 'opc.tcp://', got: {endpoint_url!r}"
            )
        self._endpoint_url = endpoint_url
        self._period_ms = max(50, subscription_period_ms)
        self._reconnect_delay_s = reconnect_delay_s
        self._timeout_s = timeout_s
        self._specs: list[NodeSpec] = []
        self._node_map: dict[str, NodeSpec] = {}  # node_id_str → spec
        self._callbacks: list[EventCallback] = []
        self._task: asyncio.Task | None = None   # type: ignore[type-arg]
        self._running = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_node(
        self,
        node_id: str,
        sensor_id: str,
        *,
        sensor_type: str = "",
        unit: str = "",
        device_id: str = "",
        priority: Priority = Priority.NORMAL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register an OPC-UA node to subscribe to."""
        spec = NodeSpec(
            node_id=node_id,
            sensor_id=sensor_id,
            sensor_type=sensor_type,
            unit=unit,
            device_id=device_id,
            priority=priority,
            metadata=dict(metadata) if metadata else {},
        )
        self._specs.append(spec)
        self._node_map[node_id] = spec

    def on_event(self, callback: EventCallback) -> None:
        """Register a callback invoked with each new SensorEvent."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to OPC-UA server and begin subscription. Non-blocking."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._sub_loop(), name="opcua_sub")

    async def stop(self) -> None:
        """Cancel subscription and disconnect."""
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _sub_loop(self) -> None:
        while self._running:
            try:
                await self._run_session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("OPCUAAdapter error: %s — reconnecting in %ss", exc, self._reconnect_delay_s)
                await asyncio.sleep(self._reconnect_delay_s)

    async def _run_session(self) -> None:
        client = _AsyncuaClient(self._endpoint_url, timeout=self._timeout_s)
        async with client:
            handler = _SubHandler(self)
            subscription = await client.create_subscription(self._period_ms, handler)
            try:
                nodes = [client.get_node(spec.node_id) for spec in self._specs]
                if nodes:
                    await subscription.subscribe_data_change(nodes)
                logger.info(
                    "OPCUAAdapter: subscribed to %d nodes at %s",
                    len(nodes), self._endpoint_url,
                )
                while self._running:
                    await asyncio.sleep(1.0)
            finally:
                try:
                    await subscription.delete()
                except Exception:
                    pass

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("OPCUAAdapter callback error: %s", exc)
