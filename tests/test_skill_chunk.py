"""Tests for llmesh.skills (Phase 3.1)."""
from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from llmesh.skills import (
    SCHEMA_VERSION,
    SkillChunk,
    SkillChunkError,
    compute_merkle_root,
    merkle_proof,
    verify_merkle_proof,
)


@pytest.fixture
def keypair() -> tuple[Ed25519PrivateKey, Ed25519PublicKey]:
    sk = Ed25519PrivateKey.generate()
    return sk, sk.public_key()


@pytest.fixture
def sample_body() -> bytes:
    return b"hello world\n" * 200  # ~2400 bytes


# --- Merkle helpers --------------------------------------------------


def test_merkle_root_deterministic() -> None:
    body = b"abc" * 1000
    r1 = compute_merkle_root(body, chunk_size=128)
    r2 = compute_merkle_root(body, chunk_size=128)
    assert r1 == r2
    assert len(r1) == 64  # hex SHA-256


def test_merkle_root_differs_on_modification() -> None:
    body = b"abc" * 1000
    modified = bytearray(body)
    modified[10] = (modified[10] + 1) % 256
    assert compute_merkle_root(body) != compute_merkle_root(bytes(modified))


def test_merkle_root_empty_body() -> None:
    """Empty body still produces a valid root."""
    root = compute_merkle_root(b"")
    assert len(root) == 64


def test_merkle_proof_round_trip(sample_body: bytes) -> None:
    import hashlib

    chunk_size = 128
    root = compute_merkle_root(sample_body, chunk_size)
    n_chunks = (len(sample_body) + chunk_size - 1) // chunk_size
    # verify proof for each leaf
    for leaf_idx in (0, 1, n_chunks // 2, n_chunks - 1):
        proof = merkle_proof(sample_body, leaf_idx, chunk_size)
        leaf_data = sample_body[leaf_idx * chunk_size : (leaf_idx + 1) * chunk_size]
        leaf_hash = hashlib.sha256(leaf_data).hexdigest()
        assert verify_merkle_proof(leaf_hash, proof, root) is True


def test_merkle_proof_rejects_modified_leaf(sample_body: bytes) -> None:
    import hashlib

    chunk_size = 128
    root = compute_merkle_root(sample_body, chunk_size)
    proof = merkle_proof(sample_body, 0, chunk_size)
    wrong_leaf = hashlib.sha256(b"different").hexdigest()
    assert verify_merkle_proof(wrong_leaf, proof, root) is False


def test_merkle_proof_out_of_range() -> None:
    with pytest.raises(IndexError):
        merkle_proof(b"abc", 999)


# --- SkillChunk core -------------------------------------------------


def test_create_unsigned_fills_hashes(sample_body: bytes) -> None:
    c = SkillChunk.create_unsigned(
        skill_id="test/foo",
        version="2026-05-16T00:00:00Z",
        body=sample_body,
        license="Apache-2.0",
    )
    assert c.schema_version == SCHEMA_VERSION
    assert c.content_sha256 != ""
    assert c.merkle_root != ""
    assert c.size_bytes == len(sample_body)
    assert c.signature == ""


def test_sign_then_verify_ok(sample_body: bytes, keypair: tuple) -> None:
    sk, pk = keypair
    chunk = SkillChunk.create_unsigned(
        skill_id="test/ok",
        version="v1",
        body=sample_body,
        license="MIT",
    ).sign(sk)
    chunk.verify(pk)  # must not raise


def test_verify_rejects_tampered_body(sample_body: bytes, keypair: tuple) -> None:
    sk, pk = keypair
    chunk = SkillChunk.create_unsigned(
        skill_id="test/tamper",
        version="v1",
        body=sample_body,
        license="MIT",
    ).sign(sk)
    # Manually swap body to mismatch content_sha256
    from dataclasses import replace

    tampered = replace(chunk, body=sample_body + b"!")
    with pytest.raises(SkillChunkError, match="content_sha256"):
        tampered.verify(pk)


def test_verify_rejects_bad_signature(sample_body: bytes, keypair: tuple) -> None:
    sk, _ = keypair
    bad_pk = Ed25519PrivateKey.generate().public_key()
    chunk = SkillChunk.create_unsigned(
        skill_id="test/sig",
        version="v1",
        body=sample_body,
        license="Apache-2.0",
    ).sign(sk)
    with pytest.raises(SkillChunkError, match="signature"):
        chunk.verify(bad_pk)


def test_verify_rejects_missing_signature(sample_body: bytes, keypair: tuple) -> None:
    _, pk = keypair
    chunk = SkillChunk.create_unsigned(
        skill_id="test/nosig",
        version="v1",
        body=sample_body,
        license="MIT",
    )
    with pytest.raises(SkillChunkError, match="missing signature"):
        chunk.verify(pk)


def test_verify_rejects_unsupported_schema(sample_body: bytes, keypair: tuple) -> None:
    sk, pk = keypair
    chunk = SkillChunk.create_unsigned(
        skill_id="x", version="v1", body=sample_body, license="MIT"
    ).sign(sk)
    from dataclasses import replace

    upgraded = replace(chunk, schema_version=999)
    with pytest.raises(SkillChunkError, match="schema_version"):
        upgraded.verify(pk)


# --- JSON round trip -------------------------------------------------


def test_json_round_trip_preserves_signature(sample_body: bytes, keypair: tuple) -> None:
    sk, pk = keypair
    chunk = SkillChunk.create_unsigned(
        skill_id="rt/x",
        version="v1",
        body=sample_body,
        license="Apache-2.0",
        domains=["code", "math"],
        language="ja",
    ).sign(sk)
    js = json.dumps(chunk.to_json())
    rebuilt = SkillChunk.from_json(json.loads(js))
    assert rebuilt.skill_id == chunk.skill_id
    assert rebuilt.body == chunk.body
    assert rebuilt.signature == chunk.signature
    assert rebuilt.domains == ("code", "math")
    rebuilt.verify(pk)  # signature survives round trip


def test_json_decode_rejects_bad_body() -> None:
    bad = {
        "schema_version": SCHEMA_VERSION,
        "skill_id": "x",
        "version": "v1",
        "body_b64": "!!!not base64!!!",
        "license": "MIT",
        "content_sha256": "00" * 32,
        "merkle_root": "00" * 32,
        "size_bytes": 0,
    }
    with pytest.raises(SkillChunkError, match="body_b64"):
        SkillChunk.from_json(bad)
