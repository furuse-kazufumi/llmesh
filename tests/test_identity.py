"""Tests for NodeIdentity and CapabilityManifest."""
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

    # P0-4: schema-version-aware signing -----------------------------------

    def test_signable_bytes_explicit_field_list(self):
        """_signable_bytes must use the declared v1 field list, not __dict__."""
        identity, m = self._make()
        raw = m._signable_bytes()
        import json
        payload = json.loads(raw)
        # Unsigned metadata fields must NOT appear in signed payload
        assert "signature" not in payload
        assert "performance" not in payload
        assert "verification" not in payload
        assert "revocation_endpoint" not in payload
        assert "revocation_token_hash" not in payload
        # Core signed fields must be present
        for field in ("schema_version", "node_id", "did", "issued_at", "expires_at",
                      "display_name", "owner_type", "subnets", "tools"):
            assert field in payload

    def test_extra_metadata_does_not_change_signature(self):
        """Adding non-signed metadata leaves the signature unchanged."""
        identity, m = self._make()
        sig_before = m.signature
        m.performance["tokens_per_second"] = 42
        m.verify(pub_hex=identity.public_key_hex)
        assert m.signature == sig_before

    def test_unknown_schema_version_fails_closed(self):
        identity, m = self._make()
        m.schema_version = "99.0.0"
        from llmesh.identity.manifest import ManifestVerificationError
        with pytest.raises(ManifestVerificationError, match="unknown_schema_version"):
            m._signable_bytes()

    def test_unknown_schema_version_fails_on_verify(self):
        identity, m = self._make()
        m.schema_version = "99.0.0"
        from llmesh.identity.manifest import ManifestVerificationError
        with pytest.raises(ManifestVerificationError):
            m.verify(pub_hex=identity.public_key_hex)

    def test_signed_field_change_invalidates_signature(self):
        identity, m = self._make()
        m.display_name = "tampered-name"
        from llmesh.identity.manifest import ManifestVerificationError
        with pytest.raises(ManifestVerificationError, match="signature"):
            m.verify(pub_hex=identity.public_key_hex)

    def test_field_reordering_does_not_break_verify(self):
        """sort_keys=True in json.dumps makes field order irrelevant."""
        identity, m = self._make()
        # Roundtrip through dict (may reorder fields)
        restored = type(m).from_dict(m.to_dict())
        restored.verify(pub_hex=identity.public_key_hex)
