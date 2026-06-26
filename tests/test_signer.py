"""Tests for auth.signer — Ed25519 request signing (RequestSigner / make_canonical).

Covers the canonical-string format, the three auth headers, that the signature
verifies against the reconstructed canonical, and that it is bound to the exact
body (replay/tamper protection — CLAUDE.md fail-closed / auditability).
"""
from __future__ import annotations

import hashlib

from llmesh.auth.signer import RequestSigner, make_canonical
from llmesh.identity.node_id import NodeIdentity

# Deterministic 32-byte private key so the derived node_id is stable.
_FIXED_KEY = bytes(range(32))


def _identity() -> NodeIdentity:
    return NodeIdentity.from_private_bytes(_FIXED_KEY)


def _sig_hex(headers: dict[str, str]) -> str:
    return headers["X-LLMesh-Signature"].split("ed25519:", 1)[1]


def test_make_canonical_format() -> None:
    canonical = make_canonical("get", "/tools/x", "peer:abc", 1700000000000, "d34db33f")
    # method upper-cased, newline-joined in the documented order
    assert canonical == "GET\n/tools/x\npeer:abc\n1700000000000\nd34db33f"


def test_make_canonical_deterministic() -> None:
    args = ("POST", "/p", "peer:n", 123, "h")
    assert make_canonical(*args) == make_canonical(*args)


def test_auth_headers_keys_and_shapes() -> None:
    signer = RequestSigner(_identity())
    headers = signer.auth_headers("POST", "/tools/gen", b"payload")
    assert set(headers) == {
        "X-LLMesh-Node-Id",
        "X-LLMesh-Timestamp",
        "X-LLMesh-Signature",
    }
    assert headers["X-LLMesh-Node-Id"] == signer.node_id
    assert headers["X-LLMesh-Node-Id"].startswith("peer:")
    assert int(headers["X-LLMesh-Timestamp"]) > 0  # parseable unix ms
    assert headers["X-LLMesh-Signature"].startswith("ed25519:")
    assert len(bytes.fromhex(_sig_hex(headers))) == 64  # Ed25519 signature length


def test_signature_verifies_against_canonical() -> None:
    identity = _identity()
    signer = RequestSigner(identity)
    body = b"the-body"
    headers = signer.auth_headers("POST", "/tools/gen", body)
    ts = int(headers["X-LLMesh-Timestamp"])
    canonical = make_canonical(
        "POST", "/tools/gen", identity.node_id, ts, hashlib.sha256(body).hexdigest()
    )
    assert identity.verify(canonical.encode(), bytes.fromhex(_sig_hex(headers)))


def test_signature_is_bound_to_body() -> None:
    identity = _identity()
    signer = RequestSigner(identity)
    headers = signer.auth_headers("POST", "/tools/gen", b"real-body")
    ts = int(headers["X-LLMesh-Timestamp"])
    # A signature over the real body must NOT verify against a different body.
    tampered = make_canonical(
        "POST", "/tools/gen", identity.node_id, ts, hashlib.sha256(b"evil-body").hexdigest()
    )
    assert identity.verify(tampered.encode(), bytes.fromhex(_sig_hex(headers))) is False


def test_empty_body_default_signs_sha256_of_empty() -> None:
    identity = _identity()
    signer = RequestSigner(identity)
    headers = signer.auth_headers("GET", "/health")  # no body
    ts = int(headers["X-LLMesh-Timestamp"])
    canonical = make_canonical(
        "GET", "/health", identity.node_id, ts, hashlib.sha256(b"").hexdigest()
    )
    assert identity.verify(canonical.encode(), bytes.fromhex(_sig_hex(headers)))
