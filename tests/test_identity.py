"""Tests for NodeIdentity and CapabilityManifest."""
import time
import pytest
from llmesh.identity import NodeIdentity, CapabilityManifest, ManifestVerificationError


class TestNodeIdentity:
    def test_generate_produces_unique_ids(self):
        a = NodeIdentity.generate()
        b = NodeIdentity.generate()
        assert a.node_id != b.node_id
        assert a.did_key != b.did_key

    def test_node_id_has_peer_prefix(self):
        identity = NodeIdentity.generate()
        assert identity.node_id.startswith("peer:")

    def test_did_key_format(self):
        identity = NodeIdentity.generate()
        assert identity.did_key.startswith("did:llmesh:1:z")

    def test_roundtrip_private_bytes(self):
        original = NodeIdentity.generate()
        restored = NodeIdentity.from_private_bytes(original.private_bytes())
        assert original.node_id == restored.node_id
        assert original.did_key == restored.did_key

    def test_sign_and_verify(self):
        identity = NodeIdentity.generate()
        msg = b"hello LLMesh"
        sig = identity.sign(msg)
        assert identity.verify(msg, sig)

    def test_verify_rejects_tampered_message(self):
        identity = NodeIdentity.generate()
        sig = identity.sign(b"original")
        assert not identity.verify(b"tampered", sig)

    def test_verify_with_public_hex(self):
        identity = NodeIdentity.generate()
        msg = b"test message"
        sig = identity.sign(msg)
        assert NodeIdentity.verify_with_public_hex(msg, sig, identity.public_key_hex)

    def test_verify_with_wrong_public_key(self):
        a = NodeIdentity.generate()
        b = NodeIdentity.generate()
        sig = a.sign(b"msg")
        assert not NodeIdentity.verify_with_public_hex(b"msg", sig, b.public_key_hex)


class TestCapabilityManifest:
    def _make(self, ttl: int = 3600):
        identity = NodeIdentity.generate()
        manifest = CapabilityManifest.create(
            identity=identity,
            display_name="test-node",
            tools=["generate_code", "review_code"],
            ttl_seconds=ttl,
        )
        manifest.sign(identity)
        return identity, manifest

    def test_create_has_required_fields(self):
        identity, m = self._make()
        assert m.schema_version == "0.1.0"
        assert m.node_id == identity.node_id
        assert m.did == identity.did_key
        assert m.signature.startswith("ed25519:")

    def test_verify_valid_manifest(self):
        identity, m = self._make()
        m.verify(pub_hex=identity.public_key_hex)

    def test_verify_expired_manifest_raises(self):
        identity, m = self._make(ttl=-1)
        with pytest.raises(ManifestVerificationError, match="expired"):
            m.verify(pub_hex=identity.public_key_hex)

    def test_verify_tampered_signature_raises(self):
        identity, m = self._make()
        m.signature = "ed25519:" + "ff" * 64
        with pytest.raises(ManifestVerificationError, match="signature"):
            m.verify(pub_hex=identity.public_key_hex)

    def test_verify_missing_signature_raises(self):
        identity = NodeIdentity.generate()
        m = CapabilityManifest.create(identity, "x", ["generate_code"])
        with pytest.raises(ManifestVerificationError, match="malformed"):
            m.verify(pub_hex=identity.public_key_hex)

    def test_roundtrip_json(self):
        identity, m = self._make()
        restored = CapabilityManifest.from_dict(m.to_dict())
        assert restored.node_id == m.node_id
        assert restored.signature == m.signature

    def test_accepts_data_levels_default(self):
        identity, m = self._make()
        assert m.privacy_policy["accepts_data_levels"] == ["L0", "L1"]
