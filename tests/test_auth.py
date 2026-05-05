"""Tests for auth: TrustedPeers, RequestSigner, RequestVerifier."""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from llmesh.auth.trusted_peers import TrustedPeers, _fingerprint
from llmesh.auth.signer import RequestSigner, make_canonical
from llmesh.auth.verifier import _verify_request, SignatureVerificationError, TOLERANCE_MS
from llmesh.identity.node_id import NodeIdentity

_EMPTY_SHA = hashlib.sha256(b"").hexdigest()


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
        source="manual",
    )
    return tp


# ── TrustedPeers ─────────────────────────────────────────────────────────────

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
        """No .tmp file left behind after save."""
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
        """Signature over /tools/a must not verify for /tools/b."""
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
        """Signature over body A must not verify when body B is presented."""
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
        """Signature and body must match on both sides."""
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
        """auth_headers() with no body and _verify_request() with no body agree."""
        signer = RequestSigner(identity)
        hdrs = signer.auth_headers("GET", "/health")
        _verify_request(
            hdrs["X-LLMesh-Node-Id"],
            hdrs["X-LLMesh-Timestamp"],
            hdrs["X-LLMesh-Signature"],
            "GET", "/health",
            peers,
            # body defaults to b"" on both sides
        )
