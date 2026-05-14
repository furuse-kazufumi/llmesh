"""Verifies all industrial adapters satisfy the IndustrialAdapter Protocol (v2.2.0).

Each adapter is constructed with mocked optional dependencies and checked
against ``isinstance(adapter, IndustrialAdapter)``.  This ensures the
public API contract — start / stop / on_event — never silently drifts.
"""
from __future__ import annotations

import inspect

from llmesh.industrial.adapter_protocol import IndustrialAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _assert_protocol(adapter) -> None:
    """isinstance + signature shape for stricter coverage."""
    assert isinstance(adapter, IndustrialAdapter)

    # start() is async + 0-arg
    sig_start = inspect.signature(adapter.start)
    assert len(sig_start.parameters) == 0
    assert inspect.iscoroutinefunction(adapter.start)

    # stop() is async + 0-arg
    sig_stop = inspect.signature(adapter.stop)
    assert len(sig_stop.parameters) == 0
    assert inspect.iscoroutinefunction(adapter.stop)

    # on_event(callback) takes one positional arg
    sig_on_event = inspect.signature(adapter.on_event)
    assert len(sig_on_event.parameters) == 1


# ---------------------------------------------------------------------------
# Always-importable adapters (no optional deps required)
# ---------------------------------------------------------------------------

class TestPureStdlibAdapters:
    """Adapters whose imports do not require optional packages."""

    def test_aoi_adapter(self, tmp_path):
        from llmesh.industrial.sensor_3d.aoi_adapter import AoiAdapter
        _assert_protocol(AoiAdapter(tmp_path))

    def test_depth_camera_adapter(self, tmp_path):
        from llmesh.industrial.sensor_3d.depth_adapter import DepthCameraAdapter
        _assert_protocol(DepthCameraAdapter(tmp_path))

    def test_event_camera_adapter(self, tmp_path):
        from llmesh.industrial.sensor_3d.event_adapter import EventCameraAdapter
        _assert_protocol(EventCameraAdapter(tmp_path))


# ---------------------------------------------------------------------------
# Adapters needing optional deps — patched
# ---------------------------------------------------------------------------

class TestModbusProtocol:
    def test_modbus_adapter(self):
        import llmesh.industrial.modbus_adapter as mod
        old = mod._PYMODBUS_AVAILABLE
        mod._PYMODBUS_AVAILABLE = True
        # AsyncModbusTcpClient is only used inside start() — constructor only
        # needs the truthy flag.
        try:
            adapter = mod.ModbusAdapter.tcp("127.0.0.1", 502)
            _assert_protocol(adapter)
        finally:
            mod._PYMODBUS_AVAILABLE = old


class TestOpcuaProtocol:
    def test_opcua_adapter(self):
        import llmesh.industrial.opcua_adapter as mod
        old = mod._ASYNCUA_AVAILABLE
        mod._ASYNCUA_AVAILABLE = True
        try:
            adapter = mod.OPCUAAdapter("opc.tcp://localhost:4840")
            _assert_protocol(adapter)
        finally:
            mod._ASYNCUA_AVAILABLE = old


class TestMqttProtocol:
    def test_mqtt_adapter(self):
        import llmesh.industrial.mqtt_adapter as mod
        old_avail, old_v2 = mod._PAHO_AVAILABLE, mod._PAHO_V2
        mod._PAHO_AVAILABLE = True
        mod._PAHO_V2 = False  # construction only inspects flag
        try:
            adapter = mod.MQTTAdapter("localhost")
            _assert_protocol(adapter)
        finally:
            mod._PAHO_AVAILABLE, mod._PAHO_V2 = old_avail, old_v2


class TestEtherCATProtocol:
    def test_ethercat_adapter(self):
        import llmesh.industrial.ethercat_adapter as mod
        old = mod._PYSOEM_AVAILABLE
        mod._PYSOEM_AVAILABLE = True
        try:
            adapter = mod.EtherCATAdapter("eth0")
            _assert_protocol(adapter)
        finally:
            mod._PYSOEM_AVAILABLE = old


class TestCANProtocol:
    def test_can_adapter(self):
        import llmesh.industrial.can_adapter as mod
        old = mod._CAN_AVAILABLE
        mod._CAN_AVAILABLE = True
        try:
            adapter = mod.CANAdapter("can0")
            _assert_protocol(adapter)
        finally:
            mod._CAN_AVAILABLE = old


# ---------------------------------------------------------------------------
# Negative test
# ---------------------------------------------------------------------------

class TestProtocolNegative:
    def test_random_object_not_an_adapter(self):
        assert not isinstance(object(), IndustrialAdapter)

    def test_partial_implementation_rejected(self):
        class Partial:
            async def start(self): pass
            # missing stop() and on_event()

        assert not isinstance(Partial(), IndustrialAdapter)
