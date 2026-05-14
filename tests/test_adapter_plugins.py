"""Tests for AdapterRegistry plugin loading and settings integration."""
from __future__ import annotations

import sys
import types

import pytest

from llmesh.protocol.registry import AdapterRegistry
from llmesh.protocol.adapter import ProtocolAdapter


# ---------------------------------------------------------------------------
# Helpers — minimal fake adapters for testing
# ---------------------------------------------------------------------------

def _make_fake_adapter(protocol: str) -> type:
    """Create a minimal ProtocolAdapter subclass with the given protocol name."""

    class _FakeAdapter(ProtocolAdapter):
        _proto = protocol

        @property
        def protocol_name(self) -> str:
            return self._proto

        @property
        def is_running(self) -> bool:
            return False

        async def start(self, host, port):
            pass

        async def stop(self):
            pass

        async def send(self, message, target):
            return None

        async def broadcast(self, message, targets=None):
            pass

        def on_message(self, handler):
            pass

    _FakeAdapter.__name__ = f"Fake{protocol.title()}Adapter"
    return _FakeAdapter


def _inject_module(module_name: str, class_name: str, cls: type) -> None:
    """Inject *cls* as *module_name*.*class_name* into sys.modules."""
    mod = types.ModuleType(module_name)
    setattr(mod, class_name, cls)
    sys.modules[module_name] = mod


def _cleanup_module(module_name: str) -> None:
    sys.modules.pop(module_name, None)


# ---------------------------------------------------------------------------
# AdapterRegistry.load_plugin
# ---------------------------------------------------------------------------

class TestLoadPlugin:
    def setup_method(self):
        AdapterRegistry.unregister("_test_proto")
        _cleanup_module("_test_adapter_mod")

    def teardown_method(self):
        AdapterRegistry.unregister("_test_proto")
        _cleanup_module("_test_adapter_mod")

    def test_load_plugin_registers_adapter(self):
        cls = _make_fake_adapter("_test_proto")
        _inject_module("_test_adapter_mod", "FakeAdapter", cls)

        name = AdapterRegistry.load_plugin("_test_adapter_mod:FakeAdapter=_test_proto")
        assert name == "_test_proto"
        assert "_test_proto" in AdapterRegistry.available()

    def test_load_plugin_returns_protocol_name(self):
        cls = _make_fake_adapter("_test_proto")
        _inject_module("_test_adapter_mod", "FakeAdapter", cls)

        result = AdapterRegistry.load_plugin("_test_adapter_mod:FakeAdapter=_test_proto")
        assert result == "_test_proto"

    def test_load_plugin_tracks_spec(self):
        cls = _make_fake_adapter("_test_proto")
        _inject_module("_test_adapter_mod", "FakeAdapter", cls)
        spec = "_test_adapter_mod:FakeAdapter=_test_proto"

        AdapterRegistry.load_plugin(spec)
        assert AdapterRegistry.plugin_specs()["_test_proto"] == spec

    def test_load_plugin_bad_spec_no_equals(self):
        with pytest.raises(ValueError, match="module:ClassName=protocol_name"):
            AdapterRegistry.load_plugin("_test_adapter_mod:FakeAdapter")

    def test_load_plugin_bad_spec_no_colon(self):
        with pytest.raises(ValueError):
            AdapterRegistry.load_plugin("_test_adapter_mod=_test_proto")

    def test_load_plugin_missing_module(self):
        with pytest.raises(ImportError):
            AdapterRegistry.load_plugin("_no_such_module_xyz:Cls=proto")

    def test_load_plugin_missing_class(self):
        mod = types.ModuleType("_test_adapter_mod")
        sys.modules["_test_adapter_mod"] = mod
        with pytest.raises(AttributeError):
            AdapterRegistry.load_plugin("_test_adapter_mod:NoSuchClass=proto")

    def test_load_plugin_non_adapter_class(self):
        mod = types.ModuleType("_test_adapter_mod")
        mod.NotAnAdapter = object  # type: ignore[attr-defined]
        sys.modules["_test_adapter_mod"] = mod
        with pytest.raises(TypeError, match="ProtocolAdapter subclass"):
            AdapterRegistry.load_plugin("_test_adapter_mod:NotAnAdapter=proto")

    def test_create_after_load_plugin(self):
        cls = _make_fake_adapter("_test_proto")
        _inject_module("_test_adapter_mod", "FakeAdapter", cls)
        AdapterRegistry.load_plugin("_test_adapter_mod:FakeAdapter=_test_proto")

        adapter = AdapterRegistry.create("_test_proto")
        assert adapter.protocol_name == "_test_proto"

    def test_unregister_removes_plugin_spec(self):
        cls = _make_fake_adapter("_test_proto")
        _inject_module("_test_adapter_mod", "FakeAdapter", cls)
        AdapterRegistry.load_plugin("_test_adapter_mod:FakeAdapter=_test_proto")

        AdapterRegistry.unregister("_test_proto")
        assert "_test_proto" not in AdapterRegistry.plugin_specs()


# ---------------------------------------------------------------------------
# AdapterRegistry.available and plugin_specs
# ---------------------------------------------------------------------------

class TestAvailableAndSpecs:
    def test_built_in_adapters_present(self):
        available = AdapterRegistry.available()
        assert "http" in available
        assert "tcp" in available
        assert "udp" in available

    def test_plugin_specs_snapshot_is_copy(self):
        specs = AdapterRegistry.plugin_specs()
        specs["_mutated"] = "x"
        assert "_mutated" not in AdapterRegistry.plugin_specs()


# ---------------------------------------------------------------------------
# register_adapter convenience wrappers
# ---------------------------------------------------------------------------

class TestConvenienceWrappers:
    def setup_method(self):
        AdapterRegistry.unregister("_conv_proto")

    def teardown_method(self):
        AdapterRegistry.unregister("_conv_proto")

    def test_node_client_register_adapter(self):
        from llmesh.orchestrator.node_client import register_adapter
        cls = _make_fake_adapter("_conv_proto")
        register_adapter("_conv_proto", cls)
        assert "_conv_proto" in AdapterRegistry.available()

    def test_fanout_register_adapter(self):
        from llmesh.orchestrator.fanout import register_adapter
        cls = _make_fake_adapter("_conv_proto")
        register_adapter("_conv_proto", cls)
        assert "_conv_proto" in AdapterRegistry.available()


# ---------------------------------------------------------------------------
# LLMeshSettings adapter_plugins field
# ---------------------------------------------------------------------------

class TestSettingsAdapterPlugins:
    def test_default_adapter_plugins_is_empty_list(self):
        from llmesh.config.settings import LLMeshSettings
        s = LLMeshSettings()
        assert s.adapter_plugins == []

    def test_two_instances_dont_share_list(self):
        from llmesh.config.settings import LLMeshSettings
        a = LLMeshSettings()
        b = LLMeshSettings()
        a.adapter_plugins.append("x")
        assert b.adapter_plugins == []

    def test_adapter_plugins_saves_and_loads(self, tmp_path):
        from llmesh.config.settings import LLMeshSettings
        p = tmp_path / "settings.json"
        s = LLMeshSettings()
        s.adapter_plugins = ["mypkg:Adapter=grpc"]
        s.save(p)

        s2 = LLMeshSettings.load(p)
        assert s2.adapter_plugins == ["mypkg:Adapter=grpc"]

    def test_set_value_not_applicable_to_list(self):
        from llmesh.config.settings import LLMeshSettings
        s = LLMeshSettings()
        # adapter_plugins is a list; set_value should raise (no type coercion for list)
        with pytest.raises((KeyError, ValueError)):
            s.set_value("adapter_plugins", "something")
