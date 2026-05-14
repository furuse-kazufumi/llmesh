"""Tests for GossipClient peer exchange."""
from __future__ import annotations

import json
from unittest.mock import patch, MagicMock

import pytest

from llmesh.auth.trusted_peers import TrustedPeers
from llmesh.discovery.gossip import GossipClient
from llmesh.discovery.registry import NodeRegistry
from llmesh.identity.node_id import NodeIdentity
from llmesh.identity.manifest import CapabilityManifest


def _make_manifest(identity: NodeIdentity) -> dict:
    m = CapabilityManifest.create(identity, display_name="test", tools=["generate_code"])
    m.sign(identity)
    return m.to_dict()


def _peer_item(identity: NodeIdentity, endpoint: str = "https://peer:8001") -> dict:
    return {
        "node_id": identity.node_id,
        "public_key_hex": identity.public_key_hex,
        "endpoint": endpoint,
        "manifest": _make_manifest(identity),
    }


@pytest.fixture
def setup(tmp_path):
    peers_file = tmp_path / "trusted_peers.json"
    introducer = NodeIdentity.generate()
    tp = TrustedPeers.create_empty(peers_file)
    tp.add(
        node_id=introducer.node_id,
        public_key_hex=introducer.public_key_hex,
        did=introducer.did_key,
        endpoint="https://introducer:8001",
        trust_source="manual",
    )
    registry = NodeRegistry(verify_signatures=True)
    gossip = GossipClient(tp, registry, interval_s=9999)
    return gossip, tp, registry, introducer


class TestGossipIngest:
    def test_new_valid_peer_added(self, setup):
        gossip, peers, registry, introducer = setup
        new_node = NodeIdentity.generate()
        item = _peer_item(new_node)

        gossip._ingest(item, introducer.node_id)

        assert peers.is_trusted(new_node.node_id)
        peer = peers.get(new_node.node_id)
        assert peer.trust_source == "gossip"

    def test_already_known_peer_skipped(self, setup):
        gossip, peers, registry, introducer = setup
        new_node = NodeIdentity.generate()
        item = _peer_item(new_node)

        result1 = gossip._ingest(item, introducer.node_id)
        result2 = gossip._ingest(item, introducer.node_id)

        assert result1 is True
        assert result2 is False

    def test_bad_manifest_signature_rejected(self, setup):
        gossip, peers, registry, introducer = setup
        node = NodeIdentity.generate()
        item = _peer_item(node)
        item["manifest"]["signature"] = "ed25519:" + "ff" * 64  # corrupt

        result = gossip._ingest(item, introducer.node_id)

        assert result is False
        assert not peers.is_trusted(node.node_id)

    def test_missing_fields_rejected(self, setup):
        gossip, peers, registry, introducer = setup
        result = gossip._ingest({"node_id": "x"}, introducer.node_id)
        assert result is False

    def test_pull_from_calls_ingest(self, setup):
        gossip, peers, registry, introducer = setup
        new_node = NodeIdentity.generate()
        peer_data = {"peers": [_peer_item(new_node)]}

        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps(peer_data).encode()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            gossip._pull_from(introducer.node_id, "https://introducer:8001")

        assert peers.is_trusted(new_node.node_id)

    def test_network_error_is_silently_ignored(self, setup):
        import urllib.error
        gossip, peers, registry, introducer = setup
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("refused")):
            gossip._pull_from(introducer.node_id, "https://introducer:8001")
        # No exception raised — graceful degradation

    def test_run_once_pulls_all_peers(self, setup):
        gossip, peers, registry, introducer = setup
        new_node = NodeIdentity.generate()
        peer_data = {"peers": [_peer_item(new_node)]}

        fake_resp = MagicMock()
        fake_resp.read.return_value = json.dumps(peer_data).encode()
        fake_resp.__enter__ = lambda s: s
        fake_resp.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=fake_resp):
            gossip.run_once()

        assert peers.is_trusted(new_node.node_id)
