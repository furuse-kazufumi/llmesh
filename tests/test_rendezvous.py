"""Tests for llmesh.rendezvous — server (via TestClient) and client helpers."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from llmesh.identity.node_id import NodeIdentity
from llmesh.rendezvous.server import (
    ANNOUNCE_WINDOW_SECONDS,
    _Registry,
    _signed_message,
    make_app,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_client(registry: _Registry | None = None) -> TestClient:
    return TestClient(make_app(registry))


def _announce_payload(
    identity: NodeIdentity,
    endpoint: str = "http://10.0.0.1:8001",
    *,
    timestamp_utc: str | None = None,
) -> dict:
    ts = timestamp_utc or datetime.now(timezone.utc).isoformat()
    msg = _signed_message(identity.node_id, endpoint, ts,
                          identity.public_key_hex, identity.did_key)
    sig = identity.sign(msg).hex()
    return {
        "node_id": identity.node_id,
        "did": identity.did_key,
        "endpoint": endpoint,
        "public_key_hex": identity.public_key_hex,
        "timestamp_utc": ts,
        "signature": sig,
    }


# ---------------------------------------------------------------------------
# POST /announce — happy path
# ---------------------------------------------------------------------------

class TestAnnounceHappyPath:
    def test_announce_returns_201(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        resp = client.post("/announce", json=_announce_payload(identity))
        assert resp.status_code == 201

    def test_announce_response_contains_node_id(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        resp = client.post("/announce", json=_announce_payload(identity))
        assert resp.json()["node_id"] == identity.node_id

    def test_reannounce_same_key_succeeds(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        client.post("/announce", json=_announce_payload(identity))
        resp = client.post("/announce", json=_announce_payload(identity, "http://10.0.0.2:8001"))
        assert resp.status_code == 201

    def test_lookup_after_announce_returns_endpoint(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        endpoint = "http://10.0.0.5:9000"
        client.post("/announce", json=_announce_payload(identity, endpoint))
        resp = client.get(f"/lookup/{identity.node_id}")
        assert resp.status_code == 200
        assert resp.json()["endpoint"] == endpoint


# ---------------------------------------------------------------------------
# POST /announce — validation errors
# ---------------------------------------------------------------------------

class TestAnnounceValidation:
    def test_bad_signature_rejected_403(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        payload = _announce_payload(identity)
        payload["signature"] = "ff" * 64  # wrong sig
        resp = client.post("/announce", json=payload)
        assert resp.status_code == 403

    def test_stale_timestamp_rejected_400(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        stale = (datetime.now(timezone.utc) - timedelta(seconds=ANNOUNCE_WINDOW_SECONDS + 60)).isoformat()
        payload = _announce_payload(identity, timestamp_utc=stale)
        # Re-sign with the stale timestamp so the sig itself is valid
        msg = _signed_message(identity.node_id, payload["endpoint"], stale,
                              identity.public_key_hex, identity.did_key)
        payload["signature"] = identity.sign(msg).hex()
        resp = client.post("/announce", json=payload)
        assert resp.status_code == 400

    def test_future_timestamp_rejected_400(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        future = (datetime.now(timezone.utc) + timedelta(seconds=ANNOUNCE_WINDOW_SECONDS + 60)).isoformat()
        payload = _announce_payload(identity, timestamp_utc=future)
        msg = _signed_message(identity.node_id, payload["endpoint"], future,
                              identity.public_key_hex, identity.did_key)
        payload["signature"] = identity.sign(msg).hex()
        resp = client.post("/announce", json=payload)
        assert resp.status_code == 400

    def test_invalid_endpoint_rejected_422(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        payload = _announce_payload(identity)
        payload["endpoint"] = "ftp://bad.endpoint"
        resp = client.post("/announce", json=payload)
        assert resp.status_code == 422

    def test_short_pubkey_rejected_422(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        payload = _announce_payload(identity)
        payload["public_key_hex"] = "deadbeef"
        resp = client.post("/announce", json=payload)
        assert resp.status_code == 422

    def test_tofu_violation_rejected_409(self):
        client = _make_client()
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        # Register id1's node_id
        client.post("/announce", json=_announce_payload(id1))
        # Try to re-register same node_id with a different public key
        payload = _announce_payload(id2)
        payload["node_id"] = id1.node_id  # same node_id, different key
        # Resign with id2 using id1's node_id but id2's key/did
        msg = _signed_message(id1.node_id, payload["endpoint"],
                              payload["timestamp_utc"],
                              id2.public_key_hex, id2.did_key)
        payload["signature"] = id2.sign(msg).hex()
        resp = client.post("/announce", json=payload)
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# GET /lookup
# ---------------------------------------------------------------------------

class TestLookup:
    def test_lookup_unknown_node_returns_404(self):
        client = _make_client()
        resp = client.get("/lookup/peer:nonexistent")
        assert resp.status_code == 404

    def test_lookup_returns_did_and_pubkey(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        client.post("/announce", json=_announce_payload(identity))
        data = client.get(f"/lookup/{identity.node_id}").json()
        assert data["did"] == identity.did_key
        assert data["public_key_hex"] == identity.public_key_hex

    def test_lookup_reflects_latest_endpoint(self):
        client = _make_client()
        identity = NodeIdentity.generate()
        client.post("/announce", json=_announce_payload(identity, "http://10.0.0.1:8001"))
        client.post("/announce", json=_announce_payload(identity, "http://10.0.0.2:8002"))
        data = client.get(f"/lookup/{identity.node_id}").json()
        assert data["endpoint"] == "http://10.0.0.2:8002"


# ---------------------------------------------------------------------------
# GET /nodes
# ---------------------------------------------------------------------------

class TestListNodes:
    def test_empty_registry(self):
        client = _make_client()
        assert client.get("/nodes").json() == []

    def test_lists_all_registered_nodes(self):
        client = _make_client()
        ids = [NodeIdentity.generate() for _ in range(3)]
        for i, identity in enumerate(ids):
            client.post("/announce", json=_announce_payload(identity, f"http://10.0.0.{i+1}:8001"))
        nodes = client.get("/nodes").json()
        assert len(nodes) == 3
        node_ids = {n["node_id"] for n in nodes}
        assert node_ids == {identity.node_id for identity in ids}
