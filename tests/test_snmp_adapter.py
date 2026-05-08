"""Tests for SNMPAdapter — SNMPv3 read-only SNMP agent."""
from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from llmesh.protocol import AdapterRegistry, SNMPAdapter, TransportError
from llmesh.protocol.snmp_adapter import (
    _LlmeshMibController,
    _OID_DEFS,
    _OID_ORDER,
    _MIB_BASE,
)
from llmesh.protocol.message import NodeAddress, UnifiedMessage
from llmesh.protocol.message import MessageType


# ---------------------------------------------------------------------------
# Unit: SNMPAdapter properties
# ---------------------------------------------------------------------------

class TestSNMPAdapterUnit:
    def test_protocol_name(self):
        assert SNMPAdapter().protocol_name == "snmp"

    def test_not_running_by_default(self):
        assert SNMPAdapter().is_running is False

    def test_registry_registered(self):
        assert "snmp" in AdapterRegistry.available()
        adapter = AdapterRegistry.create("snmp")
        assert isinstance(adapter, SNMPAdapter)

    def test_on_message_sets_handler(self):
        adapter = SNMPAdapter()
        handler = MagicMock()
        adapter.on_message(handler)
        assert adapter._handler is handler

    def test_mib_controller_none_before_start(self):
        assert SNMPAdapter().mib_controller is None

    def test_oid_base_correct(self):
        adapter = SNMPAdapter()
        assert adapter.oid_base == _MIB_BASE

    def test_custom_auth_priv_stored(self):
        adapter = SNMPAdapter(
            username="testuser",
            auth_key="authpass123",
            priv_key="privpass123",
            auth_protocol="md5",
            priv_protocol="des",
        )
        assert adapter._username == "testuser"
        assert adapter._auth_protocol == "md5"
        assert adapter._priv_protocol == "des"

    @pytest.mark.asyncio
    async def test_send_raises_transport_error(self):
        adapter = SNMPAdapter()
        target = NodeAddress(host="127.0.0.1", port=161, node_id="remote")
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": "test"},
            sender=target,
        )
        with pytest.raises(TransportError, match="read-only"):
            await adapter.send(msg, target)

    @pytest.mark.asyncio
    async def test_broadcast_noop(self):
        adapter = SNMPAdapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={},
            sender=NodeAddress(host="127.0.0.1", port=0, node_id="x"),
        )
        await adapter.broadcast(msg)


# ---------------------------------------------------------------------------
# Unit: LlmeshMibController
# ---------------------------------------------------------------------------

class TestLlmeshMibController:
    def test_default_values_populated(self):
        ctrl = _LlmeshMibController()
        assert len(ctrl._values) == len(_OID_DEFS)

    def test_refresh_calls_provider(self):
        called = []

        def provider():
            called.append(1)
            return {"nodeId": b"test-node", "requestsTotal": 42}

        ctrl = _LlmeshMibController(provider)
        ctrl.refresh()
        assert len(called) == 1
        oid_node_id = _MIB_BASE + (1, 0)
        assert ctrl._values[oid_node_id] == b"test-node"

    def test_refresh_without_provider_is_noop(self):
        ctrl = _LlmeshMibController(None)
        before = dict(ctrl._values)
        ctrl.refresh()
        assert ctrl._values == before

    def test_refresh_tolerates_provider_exception(self):
        def bad_provider():
            raise RuntimeError("provider failure")

        ctrl = _LlmeshMibController(bad_provider)
        ctrl.refresh()  # must not raise

    def test_read_variables_known_oid(self):
        from pysnmp.proto import rfc1902

        ctrl = _LlmeshMibController()
        ctrl._values[_MIB_BASE + (1, 0)] = b"mynode"
        oid = rfc1902.ObjectName(_MIB_BASE + (1, 0))
        result = ctrl.read_variables((oid, None))
        assert len(result) == 1
        name, val = result[0]
        assert bytes(val) == b"mynode"

    def test_read_variables_unknown_oid_raises(self):
        from pysnmp.proto import rfc1902
        from pysnmp.smi import error as smi_error

        ctrl = _LlmeshMibController()
        unknown = rfc1902.ObjectName((1, 3, 6, 1, 4, 1, 99999, 9, 9, 0))
        with pytest.raises(smi_error.NoSuchInstanceError):
            ctrl.read_variables((unknown, None))

    def test_read_next_variables_traversal(self):
        from pysnmp.proto import rfc1902

        ctrl = _LlmeshMibController()
        # Start before the first OID
        before_first = rfc1902.ObjectName(_MIB_BASE + (0,))
        result = ctrl.read_next_variables((before_first, None))
        assert len(result) == 1
        name, _ = result[0]
        assert tuple(name) == _OID_ORDER[0]

    def test_read_next_variables_end_of_mib(self):
        from pysnmp.proto import rfc1902
        from pysnmp.smi import error as smi_error

        ctrl = _LlmeshMibController()
        last_oid = rfc1902.ObjectName(_OID_ORDER[-1])
        with pytest.raises(smi_error.EndOfMibViewError):
            ctrl.read_next_variables((last_oid, None))

    def test_write_variables_raises(self):
        from pysnmp.smi import error as smi_error

        ctrl = _LlmeshMibController()
        with pytest.raises(smi_error.NoSuchObjectError):
            ctrl.write_variables()

    def test_counter64_oid_returns_counter64_type(self):
        from pysnmp.proto import rfc1902

        ctrl = _LlmeshMibController()
        oid_requests = _MIB_BASE + (4, 0)
        ctrl._values[oid_requests] = 1000
        name = rfc1902.ObjectName(oid_requests)
        result = ctrl.read_variables((name, None))
        _, val = result[0]
        assert isinstance(val, rfc1902.Counter64)

    def test_integer32_oid_returns_integer32_type(self):
        from pysnmp.proto import rfc1902

        ctrl = _LlmeshMibController()
        oid_conns = _MIB_BASE + (3, 0)
        ctrl._values[oid_conns] = 5
        name = rfc1902.ObjectName(oid_conns)
        result = ctrl.read_variables((name, None))
        _, val = result[0]
        assert isinstance(val, rfc1902.Integer32)

    def test_update_stats_updates_values(self):
        adapter = SNMPAdapter()
        adapter._mib_controller = _LlmeshMibController()
        adapter.update_stats({"activeConnections": 7, "trustedPeerCount": 3})
        oid_conns = _MIB_BASE + (3, 0)
        oid_peers = _MIB_BASE + (8, 0)
        assert adapter._mib_controller._values[oid_conns] == 7
        assert adapter._mib_controller._values[oid_peers] == 3


# ---------------------------------------------------------------------------
# Unit: OID definitions completeness
# ---------------------------------------------------------------------------

class TestOidDefinitions:
    def test_all_eight_oids_defined(self):
        assert len(_OID_DEFS) == 8

    def test_oid_order_sorted(self):
        assert _OID_ORDER == sorted(_OID_ORDER)

    def test_all_keys_present(self):
        keys = {v[0] for v in _OID_DEFS.values()}
        expected = {
            "nodeId", "did", "activeConnections", "requestsTotal",
            "firewallBlocksTotal", "auditChainValid", "nonceStoreSize",
            "trustedPeerCount",
        }
        assert keys == expected


# ---------------------------------------------------------------------------
# Integration: start / stop lifecycle
# ---------------------------------------------------------------------------

class TestSNMPAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_is_running_after_start(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        adapter = SNMPAdapter(auth_key="testauth123", priv_key="testpriv123")
        await adapter.start("127.0.0.1", port)
        try:
            assert adapter.is_running is True
            assert adapter.mib_controller is not None
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_not_running_after_stop(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        adapter = SNMPAdapter(auth_key="testauth123", priv_key="testpriv123")
        await adapter.start("127.0.0.1", port)
        await adapter.stop()
        assert adapter.is_running is False

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        adapter = SNMPAdapter()
        await adapter.stop()  # must not raise

    @pytest.mark.asyncio
    async def test_node_id_propagated_to_mib(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        adapter = SNMPAdapter(
            node_id="test-node-abc",
            auth_key="testauth123",
            priv_key="testpriv123",
        )
        await adapter.start("127.0.0.1", port)
        try:
            from pysnmp.proto import rfc1902
            oid = rfc1902.ObjectName(_MIB_BASE + (1, 0))
            result = adapter.mib_controller.read_variables((oid, None))
            _, val = result[0]
            assert b"test-node-abc" in bytes(val)
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_stats_provider_called_on_refresh(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        calls = []

        def provider():
            calls.append(1)
            return {"activeConnections": 99}

        adapter = SNMPAdapter(
            stats_provider=provider,
            auth_key="testauth123",
            priv_key="testpriv123",
        )
        await adapter.start("127.0.0.1", port)
        try:
            from pysnmp.proto import rfc1902
            oid = rfc1902.ObjectName(_MIB_BASE + (3, 0))
            result = adapter.mib_controller.read_variables((oid, None))
            _, val = result[0]
            assert int(val) == 99
            assert len(calls) >= 1
        finally:
            await adapter.stop()
