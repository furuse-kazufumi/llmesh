"""Tests for llmesh.identity.x25519 — Ed25519→X25519 conversion and ECDH."""
from __future__ import annotations

import pytest

from llmesh.identity.node_id import NodeIdentity
from llmesh.identity.x25519 import (
    ecdh_shared_secret,
    ed25519_private_to_x25519,
    ed25519_pub_to_x25519_pub_bytes,
)


class TestEd25519PrivateToX25519:
    def test_returns_x25519_private_key(self):
        from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
        identity = NodeIdentity.generate()
        x_priv = ed25519_private_to_x25519(identity)
        assert isinstance(x_priv, X25519PrivateKey)

    def test_deterministic_for_same_identity(self):
        identity = NodeIdentity.generate()
        k1 = ed25519_private_to_x25519(identity)
        k2 = ed25519_private_to_x25519(identity)
        # Compare via public key bytes
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        pub1 = k1.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        pub2 = k2.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert pub1 == pub2

    def test_different_identities_produce_different_keys(self):
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        k1 = ed25519_private_to_x25519(id1)
        k2 = ed25519_private_to_x25519(id2)
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        pub1 = k1.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        pub2 = k2.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert pub1 != pub2

    def test_restored_identity_produces_same_x25519_key(self):
        identity = NodeIdentity.generate()
        restored = NodeIdentity.from_private_bytes(identity.private_bytes())
        k1 = ed25519_private_to_x25519(identity)
        k2 = ed25519_private_to_x25519(restored)
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        pub1 = k1.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        pub2 = k2.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        assert pub1 == pub2


class TestEd25519PubToX25519PubBytes:
    def test_returns_32_bytes(self):
        identity = NodeIdentity.generate()
        result = ed25519_pub_to_x25519_pub_bytes(identity.public_key_hex)
        assert len(result) == 32

    def test_deterministic(self):
        identity = NodeIdentity.generate()
        r1 = ed25519_pub_to_x25519_pub_bytes(identity.public_key_hex)
        r2 = ed25519_pub_to_x25519_pub_bytes(identity.public_key_hex)
        assert r1 == r2

    def test_different_keys_produce_different_output(self):
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        r1 = ed25519_pub_to_x25519_pub_bytes(id1.public_key_hex)
        r2 = ed25519_pub_to_x25519_pub_bytes(id2.public_key_hex)
        assert r1 != r2

    def test_invalid_length_raises(self):
        with pytest.raises(ValueError, match="32 bytes"):
            ed25519_pub_to_x25519_pub_bytes("deadbeef")  # too short

    def test_consistent_with_private_key_conversion(self):
        """Public-side conversion matches the public key derived from private-side conversion."""
        identity = NodeIdentity.generate()
        x_priv = ed25519_private_to_x25519(identity)
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        expected = x_priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        got = ed25519_pub_to_x25519_pub_bytes(identity.public_key_hex)
        assert got == expected


class TestEcdhSharedSecret:
    def test_shared_secret_is_32_bytes(self):
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        secret = ecdh_shared_secret(id1, id2.public_key_hex)
        assert len(secret) == 32

    def test_shared_secret_is_symmetric(self):
        """ECDH must yield the same secret from both sides."""
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        s1 = ecdh_shared_secret(id1, id2.public_key_hex)
        s2 = ecdh_shared_secret(id2, id1.public_key_hex)
        assert s1 == s2

    def test_different_pairs_produce_different_secrets(self):
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        id3 = NodeIdentity.generate()
        s12 = ecdh_shared_secret(id1, id2.public_key_hex)
        s13 = ecdh_shared_secret(id1, id3.public_key_hex)
        assert s12 != s13

    def test_secret_is_deterministic(self):
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        s1 = ecdh_shared_secret(id1, id2.public_key_hex)
        s2 = ecdh_shared_secret(id1, id2.public_key_hex)
        assert s1 == s2

    def test_wrong_public_key_hex_raises(self):
        identity = NodeIdentity.generate()
        with pytest.raises((ValueError, Exception)):
            ecdh_shared_secret(identity, "zz" * 32)  # invalid hex
