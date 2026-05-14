"""Tests for llmesh.identity.resolver — DIDResolver and DIDDocument."""
from __future__ import annotations

import pytest

from llmesh.identity.node_id import NodeIdentity
from llmesh.identity.resolver import (
    DIDResolutionError,
    DIDResolver,
    _b58decode,
    _resolve_did_llmesh,
)


# ---------------------------------------------------------------------------
# _b58decode
# ---------------------------------------------------------------------------

class TestB58Decode:
    def test_roundtrip_with_node_id_encoder(self):
        from llmesh.identity.node_id import _b58encode
        data = b"\xed\x01" + bytes(range(32))
        encoded = _b58encode(data)
        assert _b58decode(encoded) == data

    def test_invalid_character_raises(self):
        with pytest.raises(DIDResolutionError, match="invalid base58 character"):
            _b58decode("0OIl")  # excluded chars in Bitcoin base58

    def test_empty_string_returns_empty_bytes(self):
        assert _b58decode("") == b""

    def test_leading_ones_become_zero_bytes(self):
        result = _b58decode("111")
        assert result == b"\x00\x00\x00"


# ---------------------------------------------------------------------------
# _resolve_did_key (internal)
# ---------------------------------------------------------------------------

class TestResolveDIDKey:
    def _identity_did(self) -> tuple[NodeIdentity, str]:
        identity = NodeIdentity.generate()
        return identity, identity.did_key

    def test_resolves_valid_did_key(self):
        identity, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        assert doc.id == did

    def test_document_has_one_verification_method(self):
        _, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        assert len(doc.verification_method) == 1

    def test_verification_method_type(self):
        _, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        assert doc.verification_method[0].type == "Ed25519VerificationKey2020"

    def test_verification_method_controller_is_did(self):
        identity, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        assert doc.verification_method[0].controller == did

    def test_public_key_bytes_match_identity(self):
        identity, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        assert doc.public_key_bytes == bytes.fromhex(identity.public_key_hex)

    def test_all_relationship_arrays_contain_vm_id(self):
        _, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        vm_id = doc.verification_method[0].id
        assert vm_id in doc.authentication
        assert vm_id in doc.assertion_method
        assert vm_id in doc.capability_invocation
        assert vm_id in doc.capability_delegation

    def test_vm_id_fragment_format(self):
        _, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        vm_id = doc.verification_method[0].id
        assert vm_id.startswith(did + "#")

    def test_context_contains_did_v1(self):
        _, did = self._identity_did()
        doc = _resolve_did_llmesh(did)
        assert "https://www.w3.org/ns/did/v1" in doc.context

    def test_non_did_key_raises(self):
        with pytest.raises(DIDResolutionError, match="not a did:llmesh:1:"):
            _resolve_did_llmesh("did:web:example.com")

    def test_empty_key_material_raises(self):
        with pytest.raises(DIDResolutionError):
            _resolve_did_llmesh("did:llmesh:1:z")

    def test_wrong_multicodec_prefix_raises(self):
        # 0x1200 is secp256k1, not Ed25519 (0xed01)
        from llmesh.identity.node_id import _b58encode
        bad_key = b"\x12\x00" + b"\xab" * 33
        bad_did = "did:llmesh:1:z" + _b58encode(bad_key)
        with pytest.raises(DIDResolutionError, match="unsupported key type"):
            _resolve_did_llmesh(bad_did)

    def test_wrong_pubkey_length_raises(self):
        from llmesh.identity.node_id import _b58encode
        short_key = b"\xed\x01" + b"\xab" * 16  # 16 bytes, not 32
        bad_did = "did:llmesh:1:z" + _b58encode(short_key)
        with pytest.raises(DIDResolutionError, match="32 bytes"):
            _resolve_did_llmesh(bad_did)


# ---------------------------------------------------------------------------
# DIDDocument.to_dict()
# ---------------------------------------------------------------------------

class TestDIDDocumentToDict:
    def test_to_dict_has_context(self):
        identity = NodeIdentity.generate()
        doc = _resolve_did_llmesh(identity.did_key)
        d = doc.to_dict()
        assert "@context" in d
        assert isinstance(d["@context"], list)

    def test_to_dict_vm_has_required_keys(self):
        identity = NodeIdentity.generate()
        doc = _resolve_did_llmesh(identity.did_key)
        vm = doc.to_dict()["verificationMethod"][0]
        assert set(vm.keys()) >= {"id", "type", "controller", "publicKeyMultibase"}

    def test_public_key_multibase_starts_with_z(self):
        identity = NodeIdentity.generate()
        doc = _resolve_did_llmesh(identity.did_key)
        pkm = doc.to_dict()["verificationMethod"][0]["publicKeyMultibase"]
        assert pkm.startswith("z")


# ---------------------------------------------------------------------------
# DIDResolver
# ---------------------------------------------------------------------------

class TestDIDResolver:
    def test_resolve_did_llmesh(self):
        identity = NodeIdentity.generate()
        resolver = DIDResolver()
        doc = resolver.resolve(identity.did_key)
        assert doc.id == identity.did_key

    def test_resolve_caches_result(self):
        identity = NodeIdentity.generate()
        resolver = DIDResolver()
        doc1 = resolver.resolve(identity.did_key)
        doc2 = resolver.resolve(identity.did_key)
        assert doc1 is doc2  # same object — cache hit

    def test_is_registered_after_resolve(self):
        identity = NodeIdentity.generate()
        resolver = DIDResolver()
        assert not resolver.is_registered(identity.did_key)
        resolver.resolve(identity.did_key)
        assert resolver.is_registered(identity.did_key)

    def test_register_peer_document(self):
        identity = NodeIdentity.generate()
        resolver = DIDResolver()
        doc = _resolve_did_llmesh(identity.did_key)
        resolver.register(doc)
        assert resolver.is_registered(identity.did_key)
        assert resolver.resolve(identity.did_key) is doc

    def test_public_key_bytes_convenience(self):
        identity = NodeIdentity.generate()
        resolver = DIDResolver()
        pub = resolver.public_key_bytes(identity.did_key)
        assert pub == bytes.fromhex(identity.public_key_hex)

    def test_unsupported_method_raises(self):
        resolver = DIDResolver()
        with pytest.raises(DIDResolutionError, match="unsupported DID method"):
            resolver.resolve("did:web:example.com")

    def test_resolve_multiple_identities_independently(self):
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        resolver = DIDResolver()
        doc1 = resolver.resolve(id1.did_key)
        doc2 = resolver.resolve(id2.did_key)
        assert doc1.id != doc2.id
        assert doc1.public_key_bytes != doc2.public_key_bytes

    def test_resolved_pubkey_verifies_signature(self):
        """Public key from DIDDocument correctly verifies a signature made by NodeIdentity."""
        identity = NodeIdentity.generate()
        msg = b"hello llmesh"
        sig = identity.sign(msg)

        resolver = DIDResolver()
        pub_bytes = resolver.public_key_bytes(identity.did_key)

        ok = NodeIdentity.verify_with_public_hex(msg, sig, pub_bytes.hex())
        assert ok

    def test_wrong_pubkey_fails_verification(self):
        """DIDDocument from a different identity must not verify the signature."""
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        msg = b"hello llmesh"
        sig = id1.sign(msg)

        resolver = DIDResolver()
        pub_bytes = resolver.public_key_bytes(id2.did_key)

        ok = NodeIdentity.verify_with_public_hex(msg, sig, pub_bytes.hex())
        assert not ok
