"""Tests for llmesh.discovery — NodeRegistry, DiscoveryClient, registry router."""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from llmesh.identity.manifest import CapabilityManifest
from llmesh.identity.node_id import NodeIdentity
from llmesh.discovery.registry import NodeRegistry, RegistryError
from llmesh.discovery.client import DiscoveryClient, DiscoveryError
from llmesh.discovery.router import set_registry
from llmesh.mcp.server import app

client = TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_identity() -> NodeIdentity:
    return NodeIdentity.generate()


def _make_manifest(identity: NodeIdentity, ttl: int = 3600) -> CapabilityManifest:
    m = CapabilityManifest.create(
        identity=identity,
        display_name="test-node",
        tools=["generate_code", "review_code"],
        subnets=["code-dev"],
        ttl_seconds=ttl,
    )
    m.sign(identity)
    return m


def _register_payload(
    identity: NodeIdentity,
    manifest: CapabilityManifest,
    endpoint: str = "http://10.0.0.1:9000",
) -> dict[str, Any]:
    return {
        "manifest": manifest.to_dict(),
        "endpoint": endpoint,
        "public_key_hex": identity.public_key_hex,
    }


# ---------------------------------------------------------------------------
# NodeRegistry — unit tests
# ---------------------------------------------------------------------------

class TestNodeRegistry:
    def setup_method(self):
        self.reg = NodeRegistry(verify_signatures=True)
        self.identity = _make_identity()
        self.manifest = _make_manifest(self.identity)

    def test_register_valid_manifest(self):
        entry = self.reg.register(
            self.manifest.to_dict(),
            endpoint="http://10.0.0.1:9000",
            public_key_hex=self.identity.public_key_hex,
        )
        assert entry.node_id == self.identity.node_id

    def test_register_increments_count(self):
        assert self.reg.count == 0
        self.reg.register(
            self.manifest.to_dict(), "http://a:9000", self.identity.public_key_hex
        )
        assert self.reg.count == 1

    def test_get_returns_entry(self):
        self.reg.register(
            self.manifest.to_dict(), "http://a:9000", self.identity.public_key_hex
        )
        entry = self.reg.get(self.identity.node_id)
        assert entry is not None
        assert entry.did == self.identity.did_key

    def test_get_unknown_returns_none(self):
        assert self.reg.get("nonexistent") is None

    def test_deregister_existing_returns_true(self):
        self.reg.register(
            self.manifest.to_dict(), "http://a:9000", self.identity.public_key_hex
        )
        assert self.reg.deregister(self.identity.node_id) is True
        assert self.reg.count == 0

    def test_deregister_unknown_returns_false(self):
        assert self.reg.deregister("ghost") is False

    def test_list_nodes_all(self):
        id2 = _make_identity()
        m2 = _make_manifest(id2)
        self.reg.register(self.manifest.to_dict(), "http://a:9000", self.identity.public_key_hex)
        self.reg.register(m2.to_dict(), "http://b:9000", id2.public_key_hex)
        assert len(self.reg.list_nodes()) == 2

    def test_list_nodes_subnet_filter(self):
        nodes = self.reg.list_nodes(subnet="code-dev")
        self.reg.register(self.manifest.to_dict(), "http://a:9000", self.identity.public_key_hex)
        nodes = self.reg.list_nodes(subnet="code-dev")
        assert len(nodes) == 1
        nodes_other = self.reg.list_nodes(subnet="other-subnet")
        assert len(nodes_other) == 0

    def test_list_nodes_tool_filter(self):
        self.reg.register(self.manifest.to_dict(), "http://a:9000", self.identity.public_key_hex)
        assert len(self.reg.list_nodes(tool="generate_code")) == 1
        assert len(self.reg.list_nodes(tool="nonexistent_tool")) == 0

    def test_expired_node_not_returned(self):
        reg = NodeRegistry(verify_signatures=False)
        m_short = _make_manifest(self.identity, ttl=-1)  # already expired
        reg.register(m_short.to_dict(), "http://a:9000", self.identity.public_key_hex)
        assert reg.count == 0
        assert reg.get(self.identity.node_id) is None

    def test_bad_signature_raises_registry_error(self):
        wrong_identity = _make_identity()
        with pytest.raises(RegistryError, match="manifest_verification_failed"):
            self.reg.register(
                self.manifest.to_dict(),
                "http://a:9000",
                wrong_identity.public_key_hex,  # wrong key
            )

    def test_max_nodes_evicts_oldest(self):
        reg = NodeRegistry(max_nodes=2, verify_signatures=False)
        for _ in range(3):
            ident = _make_identity()
            m = _make_manifest(ident)
            reg.register(m.to_dict(), "http://x:9000", ident.public_key_hex)
        assert reg.count == 2

    def test_node_entry_to_dict_has_required_keys(self):
        self.reg.register(
            self.manifest.to_dict(), "http://a:9000", self.identity.public_key_hex
        )
        entry = self.reg.get(self.identity.node_id)
        d = entry.to_dict()
        assert set(d.keys()) >= {"node_id", "did", "endpoint", "subnets", "tools",
                                   "registered_at", "expires_at"}


# ---------------------------------------------------------------------------
# Registry HTTP router (via TestClient)
# ---------------------------------------------------------------------------

class TestRegistryRouter:
    def setup_method(self):
        set_registry(NodeRegistry(verify_signatures=True))
        self.identity = _make_identity()
        self.manifest = _make_manifest(self.identity)

    def _register(self, endpoint: str = "http://10.0.0.1:9000") -> Any:
        return client.post(
            "/registry/register",
            json=_register_payload(self.identity, self.manifest, endpoint),
        )

    def test_register_returns_201(self):
        resp = self._register()
        assert resp.status_code == 201
        assert resp.json()["node_id"] == self.identity.node_id

    def test_list_nodes_after_register(self):
        self._register()
        resp = client.get("/registry/nodes")
        assert resp.status_code == 200
        nodes = resp.json()
        assert len(nodes) == 1

    def test_list_nodes_subnet_filter(self):
        self._register()
        resp = client.get("/registry/nodes?subnet=code-dev")
        assert len(resp.json()) == 1
        resp2 = client.get("/registry/nodes?subnet=no-such-subnet")
        assert len(resp2.json()) == 0

    def test_list_nodes_tool_filter(self):
        self._register()
        resp = client.get("/registry/nodes?tool=generate_code")
        assert len(resp.json()) == 1
        resp2 = client.get("/registry/nodes?tool=fuzz_binary")
        assert len(resp2.json()) == 0

    def test_get_node_by_id(self):
        self._register()
        node_id = self.identity.node_id
        resp = client.get(f"/registry/nodes/{node_id}")
        assert resp.status_code == 200
        assert resp.json()["node_id"] == node_id

    def test_get_unknown_node_404(self):
        resp = client.get("/registry/nodes/ghost-node")
        assert resp.status_code == 404

    def test_deregister_node(self):
        self._register()
        node_id = self.identity.node_id
        resp = client.delete(f"/registry/nodes/{node_id}")
        assert resp.status_code == 200
        assert resp.json()["removed"] == node_id
        assert client.get(f"/registry/nodes/{node_id}").status_code == 404

    def test_deregister_unknown_404(self):
        resp = client.delete("/registry/nodes/ghost-node")
        assert resp.status_code == 404

    def test_register_missing_endpoint_422(self):
        payload = _register_payload(self.identity, self.manifest)
        del payload["endpoint"]
        resp = client.post("/registry/register", json=payload)
        assert resp.status_code == 422

    def test_register_missing_pubkey_422(self):
        payload = _register_payload(self.identity, self.manifest)
        del payload["public_key_hex"]
        resp = client.post("/registry/register", json=payload)
        assert resp.status_code == 422

    def test_register_wrong_pubkey_422(self):
        wrong = _make_identity()
        payload = _register_payload(self.identity, self.manifest)
        payload["public_key_hex"] = wrong.public_key_hex
        resp = client.post("/registry/register", json=payload)
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# DiscoveryClient — unit tests (urllib mocked)
# ---------------------------------------------------------------------------

def _mock_response(data: Any, status: int = 200) -> MagicMock:
    import json as _json
    mock = MagicMock()
    mock.read.return_value = _json.dumps(data).encode()
    mock.status = status
    mock.__enter__ = lambda s: s
    mock.__exit__ = MagicMock(return_value=False)
    return mock


class TestDiscoveryClient:
    def setup_method(self):
        self.client = DiscoveryClient(timeout=5)
        self.identity = _make_identity()
        self.manifest = _make_manifest(self.identity)

    def test_register_calls_correct_url(self):
        expected_entry = {"node_id": self.identity.node_id, "endpoint": "http://me:9000"}
        with patch("urllib.request.urlopen", return_value=_mock_response(expected_entry)):
            result = self.client.register(
                "http://registry:8080",
                self.manifest,
                "http://me:9000",
                self.identity.public_key_hex,
            )
        assert result["node_id"] == self.identity.node_id

    def test_discover_returns_list(self):
        nodes = [{"node_id": "n1"}, {"node_id": "n2"}]
        with patch("urllib.request.urlopen", return_value=_mock_response(nodes)):
            result = self.client.discover("http://registry:8080")
        assert len(result) == 2

    def test_discover_non_list_raises(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({"bad": "data"})):
            with pytest.raises(DiscoveryError, match="expected list"):
                self.client.discover("http://registry:8080")

    def test_health_check_true_on_ok(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({"status": "ok"})):
            assert self.client.health_check("http://node:8080") is True

    def test_health_check_false_on_error(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            assert self.client.health_check("http://node:8080") is False

    def test_health_check_false_on_bad_status(self):
        with patch("urllib.request.urlopen", return_value=_mock_response({"status": "down"})):
            assert self.client.health_check("http://node:8080") is False

    def test_connection_error_raises(self):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            with pytest.raises(DiscoveryError, match="url_error"):
                self.client.discover("http://registry:8080")

    def test_timeout_raises(self):
        with patch("urllib.request.urlopen", side_effect=TimeoutError()):
            with pytest.raises(DiscoveryError, match="timeout"):
                self.client.discover("http://registry:8080")
