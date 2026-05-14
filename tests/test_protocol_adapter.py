"""Tests for ProtocolAdapter ABC contract and AdapterRegistry."""
from __future__ import annotations

import pytest

from llmesh.protocol import (
    AdapterRegistry,
    HTTPAdapter,
    ProtocolAdapter,
    TCPAdapter,
    TransportError,
    UDPAdapter,
)


# ------------------------------------------------------------------
# AdapterRegistry
# ------------------------------------------------------------------

class TestAdapterRegistry:
    def test_builtin_adapters_registered(self):
        avail = AdapterRegistry.available()
        assert "http" in avail
        assert "tcp" in avail
        assert "udp" in avail

    def test_create_http(self):
        adapter = AdapterRegistry.create("http")
        assert isinstance(adapter, HTTPAdapter)
        assert adapter.protocol_name == "http"

    def test_create_tcp(self):
        adapter = AdapterRegistry.create("tcp")
        assert isinstance(adapter, TCPAdapter)
        assert adapter.protocol_name == "tcp"

    def test_create_udp(self):
        adapter = AdapterRegistry.create("udp")
        assert isinstance(adapter, UDPAdapter)
        assert adapter.protocol_name == "udp"

    def test_unknown_protocol_raises(self):
        with pytest.raises(KeyError, match="Unknown protocol"):
            AdapterRegistry.create("no_such_protocol_xyz")

    def test_custom_register_and_create(self):
        class FakeAdapter(TCPAdapter):
            @property
            def protocol_name(self) -> str:
                return "fake"

        AdapterRegistry.register("fake", FakeAdapter)
        try:
            adapter = AdapterRegistry.create("fake")
            assert isinstance(adapter, FakeAdapter)
        finally:
            AdapterRegistry.unregister("fake")

    def test_available_sorted(self):
        avail = AdapterRegistry.available()
        assert avail == sorted(avail)


# ------------------------------------------------------------------
# ProtocolAdapter ABC enforcement
# ------------------------------------------------------------------

class TestProtocolAdapterContract:
    def test_cannot_instantiate_abc(self):
        with pytest.raises(TypeError):
            ProtocolAdapter()  # type: ignore[abstract]

    def test_all_adapters_implement_interface(self):
        for name in ["http", "tcp", "udp"]:
            adapter = AdapterRegistry.create(name)
            assert hasattr(adapter, "start")
            assert hasattr(adapter, "stop")
            assert hasattr(adapter, "send")
            assert hasattr(adapter, "broadcast")
            assert hasattr(adapter, "on_message")
            assert hasattr(adapter, "protocol_name")
            assert hasattr(adapter, "is_running")

    def test_not_running_before_start(self):
        for name in ["tcp", "udp"]:
            adapter = AdapterRegistry.create(name)
            assert not adapter.is_running


# ------------------------------------------------------------------
# TransportError
# ------------------------------------------------------------------

class TestTransportError:
    def test_fields(self):
        err = TransportError("timeout", protocol="tcp", target="10.0.0.1:9000")
        assert err.protocol == "tcp"
        assert err.target == "10.0.0.1:9000"
        assert "timeout" in str(err)

    def test_is_exception(self):
        with pytest.raises(TransportError):
            raise TransportError("fail")
