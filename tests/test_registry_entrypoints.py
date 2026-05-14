"""Tests for AdapterRegistry.load_entrypoints() (v1.0.0)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch


from llmesh.protocol.adapter import ProtocolAdapter
from llmesh.protocol.registry import AdapterRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_adapter(protocol: str) -> type:
    class _FA(ProtocolAdapter):
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

    _FA.__name__ = f"Fake{protocol.title()}Adapter"
    return _FA


def _make_entry_point(name: str, cls: type) -> MagicMock:
    ep = MagicMock()
    ep.name = name
    ep.load.return_value = cls
    return ep


# ---------------------------------------------------------------------------
# load_entrypoints — happy path
# ---------------------------------------------------------------------------

class TestLoadEntrypoints:
    def setup_method(self):
        AdapterRegistry.unregister("_ep_proto_a")
        AdapterRegistry.unregister("_ep_proto_b")

    def teardown_method(self):
        AdapterRegistry.unregister("_ep_proto_a")
        AdapterRegistry.unregister("_ep_proto_b")

    def test_loads_single_entrypoint(self):
        cls = _make_fake_adapter("_ep_proto_a")
        eps = [_make_entry_point("_ep_proto_a", cls)]

        with patch("llmesh.protocol.registry.entry_points", return_value=eps):
            loaded = AdapterRegistry.load_entrypoints()

        assert "_ep_proto_a" in loaded
        assert "_ep_proto_a" in AdapterRegistry.available()

    def test_loads_multiple_entrypoints(self):
        cls_a = _make_fake_adapter("_ep_proto_a")
        cls_b = _make_fake_adapter("_ep_proto_b")
        eps = [
            _make_entry_point("_ep_proto_a", cls_a),
            _make_entry_point("_ep_proto_b", cls_b),
        ]

        with patch("llmesh.protocol.registry.entry_points", return_value=eps):
            loaded = AdapterRegistry.load_entrypoints()

        assert set(loaded) == {"_ep_proto_a", "_ep_proto_b"}

    def test_returns_empty_when_no_entrypoints(self):
        with patch("llmesh.protocol.registry.entry_points", return_value=[]):
            loaded = AdapterRegistry.load_entrypoints()
        assert loaded == []

    def test_adapter_usable_after_load(self):
        cls = _make_fake_adapter("_ep_proto_a")
        eps = [_make_entry_point("_ep_proto_a", cls)]

        with patch("llmesh.protocol.registry.entry_points", return_value=eps):
            AdapterRegistry.load_entrypoints()

        adapter = AdapterRegistry.create("_ep_proto_a")
        assert adapter.protocol_name == "_ep_proto_a"


# ---------------------------------------------------------------------------
# load_entrypoints — error handling
# ---------------------------------------------------------------------------

class TestLoadEntrypointsErrors:
    def setup_method(self):
        AdapterRegistry.unregister("_ep_proto_a")

    def teardown_method(self):
        AdapterRegistry.unregister("_ep_proto_a")

    def test_skips_entrypoint_that_fails_to_load(self):
        ep_bad = MagicMock()
        ep_bad.name = "_ep_proto_a"
        ep_bad.load.side_effect = ImportError("no such module")

        with patch("llmesh.protocol.registry.entry_points", return_value=[ep_bad]):
            loaded = AdapterRegistry.load_entrypoints()

        assert loaded == []
        assert "_ep_proto_a" not in AdapterRegistry.available()

    def test_skips_non_adapter_class(self):
        ep = MagicMock()
        ep.name = "_ep_proto_a"
        ep.load.return_value = object  # not a ProtocolAdapter subclass

        with patch("llmesh.protocol.registry.entry_points", return_value=[ep]):
            loaded = AdapterRegistry.load_entrypoints()

        assert loaded == []

    def test_handles_entry_points_exception_gracefully(self):
        with patch("llmesh.protocol.registry.entry_points", side_effect=RuntimeError("ep error")):
            loaded = AdapterRegistry.load_entrypoints()
        assert loaded == []

    def test_good_entrypoint_loaded_despite_bad_sibling(self):
        cls_a = _make_fake_adapter("_ep_proto_a")
        ep_good = _make_entry_point("_ep_proto_a", cls_a)

        ep_bad = MagicMock()
        ep_bad.name = "_ep_proto_b"
        ep_bad.load.side_effect = ImportError("broken")

        with patch("llmesh.protocol.registry.entry_points", return_value=[ep_good, ep_bad]):
            loaded = AdapterRegistry.load_entrypoints()

        assert "_ep_proto_a" in loaded
        assert "_ep_proto_b" not in loaded


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

class TestLoadEntrypointsIdempotency:
    def setup_method(self):
        AdapterRegistry.unregister("_ep_proto_a")

    def teardown_method(self):
        AdapterRegistry.unregister("_ep_proto_a")

    def test_calling_twice_does_not_duplicate(self):
        cls = _make_fake_adapter("_ep_proto_a")
        eps = [_make_entry_point("_ep_proto_a", cls)]

        with patch("llmesh.protocol.registry.entry_points", return_value=eps):
            AdapterRegistry.load_entrypoints()
            AdapterRegistry.load_entrypoints()

        available = AdapterRegistry.available()
        assert available.count("_ep_proto_a") == 1
