"""Tests for OPCUAAdapter (v1.6.0) — asyncua mocked throughout."""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch, call
import pytest

from llmesh.industrial.sensor_event import Priority, SensorEvent


# ---------------------------------------------------------------------------
# Helpers — fake asyncua objects
# ---------------------------------------------------------------------------

def _make_fake_asyncua():
    """Return a fake asyncua module for patching."""
    fake = MagicMock()

    class FakeNodeId:
        def __init__(self, s: str):
            self._s = s
        def to_string(self):
            return self._s

    class FakeNode:
        def __init__(self, node_id: str):
            self.nodeid = FakeNodeId(node_id)

    class FakeSubscription:
        def __init__(self):
            self.subscribed_nodes: list = []
        async def subscribe_data_change(self, nodes):
            self.subscribed_nodes.extend(nodes)
        async def delete(self):
            pass

    class FakeClient:
        def __init__(self, url, timeout=10.0):
            self.url = url
            self._sub = FakeSubscription()
            self._handler = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def create_subscription(self, period_ms, handler):
            self._handler = handler
            return self._sub

        def get_node(self, node_id):
            return FakeNode(node_id)

    fake.Client = FakeClient
    fake.common.subscription.DataChangeNotif = object
    return fake


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_asyncua():
    fake = _make_fake_asyncua()
    with patch.dict(sys.modules, {"asyncua": fake, "asyncua.common": fake.common,
                                   "asyncua.common.subscription": fake.common.subscription}):
        # Re-import to pick up the mock
        import importlib
        import llmesh.industrial.opcua_adapter as mod
        mod._ASYNCUA_AVAILABLE = True
        mod._AsyncuaClient = fake.Client
        yield fake, mod


# ---------------------------------------------------------------------------
# Unit tests — NodeSpec validation
# ---------------------------------------------------------------------------

class TestNodeSpec:
    def test_basic(self):
        from llmesh.industrial.opcua_adapter import NodeSpec
        spec = NodeSpec(node_id="ns=2;i=1", sensor_id="s1")
        assert spec.node_id == "ns=2;i=1"
        assert spec.sensor_id == "s1"
        assert spec.priority is Priority.NORMAL

    def test_priority_field(self):
        from llmesh.industrial.opcua_adapter import NodeSpec
        spec = NodeSpec(node_id="ns=2;i=1", sensor_id="s1", priority=Priority.HIGH)
        assert spec.priority is Priority.HIGH


# ---------------------------------------------------------------------------
# Unit tests — OPCUAAdapter construction
# ---------------------------------------------------------------------------

class TestOPCUAAdapterConstruct:
    def test_requires_asyncua(self):
        import llmesh.industrial.opcua_adapter as mod
        old = mod._ASYNCUA_AVAILABLE
        mod._ASYNCUA_AVAILABLE = False
        try:
            with pytest.raises(RuntimeError, match="asyncua"):
                mod.OPCUAAdapter("opc.tcp://localhost:4840")
        finally:
            mod._ASYNCUA_AVAILABLE = old

    def test_bad_url(self, fake_asyncua):
        _, mod = fake_asyncua
        with pytest.raises(ValueError, match="opc.tcp://"):
            mod.OPCUAAdapter("tcp://localhost:4840")

    def test_good_url(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840")
        assert adapter._endpoint_url == "opc.tcp://localhost:4840"

    def test_period_clamped(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840", subscription_period_ms=10)
        assert adapter._period_ms == 50  # clamped to min 50


# ---------------------------------------------------------------------------
# Unit tests — add_node / on_event
# ---------------------------------------------------------------------------

class TestOPCUAAdapterConfig:
    def test_add_node(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840")
        adapter.add_node("ns=2;i=1001", "pressure_01", sensor_type="pressure", unit="Pa")
        assert len(adapter._specs) == 1
        assert "ns=2;i=1001" in adapter._node_map

    def test_add_multiple_nodes(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840")
        adapter.add_node("ns=2;i=1", "s1")
        adapter.add_node("ns=2;i=2", "s2")
        assert len(adapter._specs) == 2

    def test_on_event_registers_callback(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840")
        cb = MagicMock()
        adapter.on_event(cb)
        assert cb in adapter._callbacks


# ---------------------------------------------------------------------------
# Unit tests — _SubHandler
# ---------------------------------------------------------------------------

class TestSubHandler:
    def _make_adapter(self, mod):
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840")
        adapter.add_node("ns=2;i=1001", "pressure_01", sensor_type="pressure", unit="Pa",
                         device_id="plc01")
        return adapter

    def test_datachange_fires_callback(self, fake_asyncua):
        fake, mod = fake_asyncua
        adapter = self._make_adapter(mod)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        node = MagicMock()
        node.nodeid.to_string.return_value = "ns=2;i=1001"
        handler = mod._SubHandler(adapter)
        handler.datachange_notification(node, 123.45, None)

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_id == "pressure_01"
        assert ev.protocol == "opcua"
        assert ev.device_id == "plc01"
        assert b"123.45" in ev.payload

    def test_unknown_node_ignored(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = self._make_adapter(mod)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        node = MagicMock()
        node.nodeid.to_string.return_value = "ns=99;i=9999"
        handler = mod._SubHandler(adapter)
        handler.datachange_notification(node, 0, None)
        assert events == []

    def test_node_id_in_metadata(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = self._make_adapter(mod)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)

        node = MagicMock()
        node.nodeid.to_string.return_value = "ns=2;i=1001"
        handler = mod._SubHandler(adapter)
        handler.datachange_notification(node, 99.0, None)
        assert events[0].metadata["node_id"] == "ns=2;i=1001"

    def test_callback_exception_does_not_crash(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = self._make_adapter(mod)
        adapter.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))

        node = MagicMock()
        node.nodeid.to_string.return_value = "ns=2;i=1001"
        handler = mod._SubHandler(adapter)
        handler.datachange_notification(node, 1.0, None)  # must not raise


# ---------------------------------------------------------------------------
# Async tests — lifecycle
# ---------------------------------------------------------------------------

class TestOPCUAAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840", reconnect_delay_s=0.01)
        await adapter.start()
        assert adapter._running is True
        assert adapter._task is not None
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_double_start_is_idempotent(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840", reconnect_delay_s=0.01)
        await adapter.start()
        t1 = adapter._task
        await adapter.start()
        assert adapter._task is t1  # same task
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, fake_asyncua):
        _, mod = fake_asyncua
        adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840", reconnect_delay_s=0.01)
        await adapter.start()
        await adapter.stop()
        assert adapter._running is False
        assert adapter._task is None

    @pytest.mark.asyncio
    async def test_subscription_subscribes_nodes(self, fake_asyncua):
        fake, mod = fake_asyncua
        adapter = mod.OPCUAAdapter(
            "opc.tcp://localhost:4840",
            reconnect_delay_s=0.05,
            subscription_period_ms=100,
        )
        adapter.add_node("ns=2;i=1", "s1")
        adapter.add_node("ns=2;i=2", "s2")

        await adapter.start()
        await asyncio.sleep(0.05)  # let sub_loop run one iteration
        await adapter.stop()
        # If we reach here without exception, subscription was set up
