"""Tests for auth: TrustedPeers, RequestSigner, RequestVerifier.

v0.2.0: TrustedPeers uses trust_source instead of source; gossip TTL + bounds.
"""
from __future__ import annotations

import hashlib
import json
import time

import pytest

from llmesh.auth.trusted_peers import TrustedPeers, _fingerprint
from llmesh.auth.signer import RequestSigner, make_canonical
from llmesh.auth.verifier import _verify_request, SignatureVerificationError, TOLERANCE_MS
from llmesh.identity.node_id import NodeIdentity

_EMPTY_SHA = hashlib.sha256(b"").hexdigest()

_VALID_DID = "did:llmesh:1:zABCDEF1234567890"
_VALID_DID_2 = "did:llmesh:1:zXYZ9876543210abc"


# ── fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def identity():
    return NodeIdentity.generate()


@pytest.fixture
def peers_file(tmp_path):
    return tmp_path / "trusted_peers.json"


@pytest.fixture
def peers(peers_file, identity):
    tp = TrustedPeers.create_empty(peers_file)
    tp.add(
        node_id=identity.node_id,
        public_key_hex=identity.public_key_hex,
        did=identity.did_key,
        endpoint="https://localhost:8001",
        trust_source="manual",
    )
    return tp


# ── TrustedPeers (existing) ───────────────────────────────────────────────────

class TestTrustedPeers:
    def test_add_and_get(self, peers_file, identity):
        tp = TrustedPeers.create_empty(peers_file)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        peer = tp.get(identity.node_id)
        assert peer is not None
        assert peer.public_key_hex == identity.public_key_hex

    def test_is_trusted(self, peers_file, identity):
        tp = TrustedPeers.create_empty(peers_file)
        assert not tp.is_trusted(identity.node_id)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        assert tp.is_trusted(identity.node_id)

    def test_persist_and_reload(self, peers_file, identity):
        tp = TrustedPeers.create_empty(peers_file)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        tp2 = TrustedPeers(peers_file)
        assert tp2.is_trusted(identity.node_id)

    def test_remove(self, peers_file, identity):
        tp = TrustedPeers.create_empty(peers_file)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        assert tp.remove(identity.node_id)
        assert not tp.is_trusted(identity.node_id)

    def test_atomic_write(self, peers_file, identity):
        tp = TrustedPeers.create_empty(peers_file)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        assert not peers_file.with_suffix(".tmp").exists()

    def test_fingerprint_format(self, identity):
        fp = _fingerprint(identity.public_key_hex)
        parts = fp.split(":")
        assert len(parts) == 16
        assert all(len(p) == 2 for p in parts)

    def test_unknown_node_returns_none(self, peers_file):
        tp = TrustedPeers.create_empty(peers_file)
        assert tp.get("unknown") is None

    def test_trust_source_manual_default(self, peers_file, identity):
        tp = TrustedPeers.create_empty(peers_file)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        peer = tp.get(identity.node_id)
        assert peer.trust_source == "manual"

    def test_backward_compat_source_field(self, peers_file, identity):
        """Old JSON with 'source' key loads correctly as trust_source."""
        old_data = {
            identity.node_id: {
                "public_key_hex": identity.public_key_hex,
                "did": identity.did_key,
                "endpoint": "https://x:8001",
                "fingerprint": _fingerprint(identity.public_key_hex),
                "source": "manual",
                "added_at": "2026-01-01T00:00:00+00:00",
            }
        }
        peers_file.write_text(json.dumps(old_data), encoding="utf-8")
        tp = TrustedPeers(peers_file)
        peer = tp.get(identity.node_id)
        assert peer is not None
        assert peer.trust_source == "manual"


# ── Gossip bounds and TTL (P0-3) ─────────────────────────────────────────────

class TestGossipBounds:
    def _make_tp(self, tmp_path, max_gossip=3, gossip_ttl=3600) -> TrustedPeers:
        p = tmp_path / "peers.json"
        return TrustedPeers.create_empty(p, max_gossip_peers=max_gossip,
                                          gossip_ttl_seconds=gossip_ttl)

    def _add_gossip(self, tp, n=0):
        node_id = f"peer:gossip-{n}"
        key = "a" * 64
        return tp.add_gossip(
            node_id=node_id,
            public_key_hex=key,
            did=f"did:llmesh:1:z{n:040x}",
            endpoint=f"https://node-{n}:8001",
            introduced_by=_VALID_DID,
        )

    def test_gossip_peer_accepted(self, tmp_path):
        tp = self._make_tp(tmp_path)
        result = self._add_gossip(tp, 0)
        assert result is not None
        assert tp.is_trusted("peer:gossip-0")

    def test_gossip_peer_trust_source(self, tmp_path):
        tp = self._make_tp(tmp_path)
        self._add_gossip(tp, 0)
        peer = tp.get("peer:gossip-0")
        assert peer.trust_source == "gossip"
        assert peer.introduced_by == _VALID_DID

    def test_gossip_max_size_enforced(self, tmp_path):
        tp = self._make_tp(tmp_path, max_gossip=2)
        assert self._add_gossip(tp, 0) is not None
        assert self._add_gossip(tp, 1) is not None
        # Third gossip peer rejected
        assert self._add_gossip(tp, 2) is None
        assert not tp.is_trusted("peer:gossip-2")

    def test_gossip_disabled_silently_ignored(self, tmp_path):
        p = tmp_path / "peers.json"
        tp = TrustedPeers.create_empty(p, allow_gossip=False)
        result = tp.add_gossip(
            node_id="peer:x",
            public_key_hex="a" * 64,
            did=_VALID_DID,
            endpoint="https://x:8001",
            introduced_by=_VALID_DID_2,
        )
        assert result is None
        assert not tp.is_trusted("peer:x")

    def test_malformed_did_rejected(self, tmp_path):
        tp = self._make_tp(tmp_path)
        result = tp.add_gossip(
            node_id="peer:bad",
            public_key_hex="b" * 64,
            did="not-a-valid-did",
            endpoint="https://bad:8001",
            introduced_by=_VALID_DID,
        )
        assert result is None

    def test_malformed_introducer_did_rejected(self, tmp_path):
        tp = self._make_tp(tmp_path)
        result = tp.add_gossip(
            node_id="peer:bad2",
            public_key_hex="c" * 64,
            did=_VALID_DID,
            endpoint="https://bad2:8001",
            introduced_by="not-a-did",
        )
        assert result is None

    def test_gossip_cannot_overwrite_manual_peer(self, tmp_path, identity):
        tp = self._make_tp(tmp_path)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        result = tp.add_gossip(
            node_id=identity.node_id,
            public_key_hex="d" * 64,
            did=_VALID_DID,
            endpoint="https://evil:9999",
            introduced_by=_VALID_DID_2,
        )
        assert result is None
        peer = tp.get(identity.node_id)
        assert peer.trust_source == "manual"


class TestGossipTTL:
    def test_expired_gossip_peer_not_trusted(self, tmp_path):
        p = tmp_path / "peers.json"
        tp = TrustedPeers.create_empty(p, gossip_ttl_seconds=1)
        tp.add_gossip(
            node_id="peer:exp",
            public_key_hex="e" * 64,
            did=_VALID_DID,
            endpoint="https://exp:8001",
            introduced_by=_VALID_DID_2,
        )
        assert tp.is_trusted("peer:exp")
        time.sleep(1.1)
        assert not tp.is_trusted("peer:exp")

    def test_cleanup_removes_expired_gossip_only(self, tmp_path, identity):
        p = tmp_path / "peers.json"
        tp = TrustedPeers.create_empty(p, gossip_ttl_seconds=1)
        tp.add(identity.node_id, identity.public_key_hex, identity.did_key, "https://x:8001")
        tp.add_gossip(
            node_id="peer:exp2",
            public_key_hex="f" * 64,
            did=_VALID_DID,
            endpoint="https://exp2:8001",
            introduced_by=_VALID_DID_2,
        )
        time.sleep(1.1)
        removed = tp.cleanup_gossip_expired()
        assert removed == 1
        assert tp.is_trusted(identity.node_id)       # manual — not removed
        assert not tp.is_trusted("peer:exp2")        # gossip expired — removed

    def test_unexpired_gossip_not_removed_by_cleanup(self, tmp_path):
        p = tmp_path / "peers.json"
        tp = TrustedPeers.create_empty(p, gossip_ttl_seconds=3600)
        tp.add_gossip(
            node_id="peer:fresh",
            public_key_hex="a" * 64,
            did=_VALID_DID,
            endpoint="https://fresh:8001",
            introduced_by=_VALID_DID_2,
        )
        removed = tp.cleanup_gossip_expired()
        assert removed == 0
        assert tp.is_trusted("peer:fresh")

    def test_cleanup_evicted_entries_not_in_len(self, tmp_path):
        p = tmp_path / "peers.json"
        tp = TrustedPeers.create_empty(p, gossip_ttl_seconds=1)
        tp.add_gossip(
            node_id="peer:e",
            public_key_hex="a" * 64,
            did=_VALID_DID,
            endpoint="https://e:8001",
            introduced_by=_VALID_DID_2,
        )
        assert len(tp) == 1
        time.sleep(1.1)
        assert len(tp) == 0   # expired peer excluded from __len__


# ── RequestSigner ─────────────────────────────────────────────────────────────

class TestRequestSigner:
    def test_headers_present(self, identity):
        signer = RequestSigner(identity)
        hdrs = signer.auth_headers("POST", "/tools/generate_code")
        assert "X-LLMesh-Node-Id" in hdrs
        assert "X-LLMesh-Timestamp" in hdrs
        assert hdrs["X-LLMesh-Signature"].startswith("ed25519:")

    def test_node_id_matches_identity(self, identity):
        signer = RequestSigner(identity)
        hdrs = signer.auth_headers("GET", "/identity")
        assert hdrs["X-LLMesh-Node-Id"] == identity.node_id

    def test_canonical_deterministic(self):
        c1 = make_canonical("POST", "/tools/x", "node-a", 1234567890000, _EMPTY_SHA)
        c2 = make_canonical("POST", "/tools/x", "node-a", 1234567890000, _EMPTY_SHA)
        assert c1 == c2

    def test_canonical_case_insensitive_method(self):
        assert make_canonical("post", "/p", "n", 1, _EMPTY_SHA) == make_canonical("POST", "/p", "n", 1, _EMPTY_SHA)

    def test_canonical_includes_body_sha256(self):
        sha = hashlib.sha256(b'{"key":"value"}').hexdigest()
        c = make_canonical("POST", "/tools/x", "node-a", 123, sha)
        assert sha in c


# ── RequestVerifier ────────────────────────────────────────────────────────────

class TestRequestVerifier:
    def _sign(self, identity, method, path):
        signer = RequestSigner(identity)
        return signer.auth_headers(method, path)

    def test_valid_request_passes(self, peers, identity):
        hdrs = self._sign(identity, "POST", "/tools/generate_code")
        _verify_request(
            hdrs["X-LLMesh-Node-Id"],
            hdrs["X-LLMesh-Timestamp"],
            hdrs["X-LLMesh-Signature"],
            "POST", "/tools/generate_code",
            peers,
        )

    def test_untrusted_node_blocked(self, peers):
        other = NodeIdentity.generate()
        hdrs = RequestSigner(other).auth_headers("POST", "/tools/x")
        with pytest.raises(SignatureVerificationError, match="untrusted_node"):
            _verify_request(
                hdrs["X-LLMesh-Node-Id"], hdrs["X-LLMesh-Timestamp"],
                hdrs["X-LLMesh-Signature"], "POST", "/tools/x", peers,
            )

    def test_stale_timestamp_blocked(self, peers, identity):
        hdrs = self._sign(identity, "POST", "/tools/x")
        old_ts = str(int(time.time() * 1000) - TOLERANCE_MS - 1000)
        with pytest.raises(SignatureVerificationError, match="timestamp_stale"):
            _verify_request(
                identity.node_id, old_ts,
                hdrs["X-LLMesh-Signature"], "POST", "/tools/x", peers,
            )

    def test_bad_signature_blocked(self, peers, identity):
        hdrs = self._sign(identity, "POST", "/tools/x")
        with pytest.raises(SignatureVerificationError, match="bad_signature"):
            _verify_request(
                identity.node_id, hdrs["X-LLMesh-Timestamp"],
                "ed25519:" + "00" * 64,
                "POST", "/tools/x", peers,
            )

    def test_path_substitution_blocked(self, peers, identity):
        hdrs = self._sign(identity, "POST", "/tools/a")
        with pytest.raises(SignatureVerificationError, match="bad_signature"):
            _verify_request(
                identity.node_id, hdrs["X-LLMesh-Timestamp"],
                hdrs["X-LLMesh-Signature"],
                "POST", "/tools/b", peers,
            )

    def test_malformed_signature_blocked(self, peers, identity):
        with pytest.raises(SignatureVerificationError, match="malformed_signature"):
            _verify_request(
                identity.node_id, str(int(time.time() * 1000)),
                "not-a-valid-header", "POST", "/tools/x", peers,
            )

    def test_tampered_body_rejected(self, peers, identity):
        body_a = b'{"prompt": "original"}'
        body_b = b'{"prompt": "tampered"}'
        signer = RequestSigner(identity)
        hdrs = signer.auth_headers("POST", "/tools/generate_code", body=body_a)
        with pytest.raises(SignatureVerificationError, match="bad_signature"):
            _verify_request(
                hdrs["X-LLMesh-Node-Id"],
                hdrs["X-LLMesh-Timestamp"],
                hdrs["X-LLMesh-Signature"],
                "POST", "/tools/generate_code",
                peers,
                body=body_b,
            )

    def test_correct_body_passes(self, peers, identity):
        body = b'{"prompt": "write a sort function"}'
        signer = RequestSigner(identity)
        hdrs = signer.auth_headers("POST", "/tools/generate_code", body=body)
        _verify_request(
            hdrs["X-LLMesh-Node-Id"],
            hdrs["X-LLMesh-Timestamp"],
            hdrs["X-LLMesh-Signature"],
            "POST", "/tools/generate_code",
            peers,
            body=body,
        )

    def test_empty_body_default_consistent(self, peers, identity):
        signer = RequestSigner(identity)
        hdrs = signer.auth_headers("GET", "/health")
        _verify_request(
            hdrs["X-LLMesh-Node-Id"],
            hdrs["X-LLMesh-Timestamp"],
            hdrs["X-LLMesh-Signature"],
            "GET", "/health",
            peers,
        )
