"""Tests for llmesh.discovery.encrypted_announce."""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.exceptions import InvalidTag

from llmesh.identity.node_id import NodeIdentity
from llmesh.identity.x25519 import ecdh_shared_secret
from llmesh.discovery.encrypted_announce import (
    VERIFY_WINDOW_SECONDS,
    AnnouncementError,
    build_announcement,
    decrypt_endpoint,
    derive_encryption_key,
    encrypt_endpoint,
    verify_announcement,
)


# ---------------------------------------------------------------------------
# build_announcement
# ---------------------------------------------------------------------------

class TestBuildAnnouncement:
    def test_returns_required_fields(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        assert {"node_id", "did", "endpoint", "public_key_hex", "timestamp_utc", "signature"} <= ann.keys()

    def test_node_id_matches_identity(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        assert ann["node_id"] == identity.node_id

    def test_did_matches_identity(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        assert ann["did"] == identity.did_key

    def test_endpoint_preserved(self):
        identity = NodeIdentity.generate()
        ep = "https://192.168.1.50:9443"
        ann = build_announcement(identity, ep)
        assert ann["endpoint"] == ep

    def test_signature_is_hex(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        bytes.fromhex(ann["signature"])  # must not raise

    def test_timestamp_includes_timezone(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        ts = datetime.fromisoformat(ann["timestamp_utc"])
        assert ts.tzinfo is not None

    def test_different_calls_produce_different_timestamps(self):
        identity = NodeIdentity.generate()
        ann1 = build_announcement(identity, "http://10.0.0.1:8001")
        time.sleep(0.01)
        ann2 = build_announcement(identity, "http://10.0.0.1:8001")
        assert ann1["timestamp_utc"] != ann2["timestamp_utc"]


# ---------------------------------------------------------------------------
# verify_announcement
# ---------------------------------------------------------------------------

class TestVerifyAnnouncement:
    def test_valid_announcement_returns_endpoint(self):
        identity = NodeIdentity.generate()
        ep = "http://10.0.0.5:8001"
        ann = build_announcement(identity, ep)
        assert verify_announcement(ann) == ep

    def test_roundtrip_with_different_endpoint(self):
        identity = NodeIdentity.generate()
        ep = "https://203.0.113.10:443"
        ann = build_announcement(identity, ep)
        assert verify_announcement(ann) == ep

    def test_missing_field_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        del ann["signature"]
        with pytest.raises(AnnouncementError, match="missing fields"):
            verify_announcement(ann)

    def test_tampered_signature_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        ann["signature"] = "ff" * 64
        with pytest.raises(AnnouncementError, match="signature"):
            verify_announcement(ann)

    def test_tampered_endpoint_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        ann["endpoint"] = "http://attacker.example.com"
        with pytest.raises(AnnouncementError, match="signature"):
            verify_announcement(ann)

    def test_tampered_node_id_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        ann["node_id"] = "peer:fake"
        with pytest.raises(AnnouncementError, match="signature"):
            verify_announcement(ann)

    def test_stale_timestamp_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        stale_ts = (datetime.now(timezone.utc) - timedelta(seconds=VERIFY_WINDOW_SECONDS + 60)).isoformat()
        ann["timestamp_utc"] = stale_ts
        # Re-sign with stale timestamp so only the timestamp check fires
        from llmesh.discovery.encrypted_announce import _signed_message
        msg = _signed_message(ann["node_id"], ann["endpoint"], stale_ts)
        ann["signature"] = identity.sign(msg).hex()
        with pytest.raises(AnnouncementError, match="timestamp"):
            verify_announcement(ann)

    def test_invalid_timestamp_format_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        ann["timestamp_utc"] = "not-a-date"
        with pytest.raises(AnnouncementError, match="timestamp"):
            verify_announcement(ann)

    def test_timestamp_without_tz_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        ann["timestamp_utc"] = "2026-05-05T12:00:00"  # no tz
        with pytest.raises(AnnouncementError, match="timezone"):
            verify_announcement(ann)

    def test_signature_not_hex_raises(self):
        identity = NodeIdentity.generate()
        ann = build_announcement(identity, "http://10.0.0.1:8001")
        ann["signature"] = "not-hex!!"
        with pytest.raises(AnnouncementError, match="hex"):
            verify_announcement(ann)


# ---------------------------------------------------------------------------
# derive_encryption_key
# ---------------------------------------------------------------------------

class TestDeriveEncryptionKey:
    def test_returns_32_bytes(self):
        key = derive_encryption_key(b"\x00" * 32)
        assert len(key) == 32

    def test_deterministic_without_salt(self):
        secret = b"\xab" * 32
        k1 = derive_encryption_key(secret)
        k2 = derive_encryption_key(secret)
        assert k1 == k2

    def test_different_secrets_different_keys(self):
        k1 = derive_encryption_key(b"\x01" * 32)
        k2 = derive_encryption_key(b"\x02" * 32)
        assert k1 != k2

    def test_different_salts_different_keys(self):
        secret = b"\xab" * 32
        k1 = derive_encryption_key(secret, salt=b"salt1")
        k2 = derive_encryption_key(secret, salt=b"salt2")
        assert k1 != k2

    def test_different_info_different_keys(self):
        secret = b"\xab" * 32
        k1 = derive_encryption_key(secret, info=b"context-a")
        k2 = derive_encryption_key(secret, info=b"context-b")
        assert k1 != k2


# ---------------------------------------------------------------------------
# encrypt_endpoint / decrypt_endpoint
# ---------------------------------------------------------------------------

class TestEncryptDecryptEndpoint:
    def _secret(self) -> bytes:
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        return ecdh_shared_secret(id1, id2.public_key_hex)

    def test_decrypt_returns_original_endpoint(self):
        secret = self._secret()
        ep = "http://10.0.0.5:8001"
        ciphertext, nonce = encrypt_endpoint(ep, secret)
        assert decrypt_endpoint(ciphertext, nonce, secret) == ep

    def test_ciphertext_is_bytes(self):
        secret = self._secret()
        ct, nonce = encrypt_endpoint("http://10.0.0.1:8001", secret)
        assert isinstance(ct, bytes)
        assert isinstance(nonce, bytes)

    def test_nonce_is_12_bytes(self):
        secret = self._secret()
        _, nonce = encrypt_endpoint("http://10.0.0.1:8001", secret)
        assert len(nonce) == 12

    def test_different_encryptions_produce_different_nonces(self):
        secret = self._secret()
        _, n1 = encrypt_endpoint("http://10.0.0.1:8001", secret)
        _, n2 = encrypt_endpoint("http://10.0.0.1:8001", secret)
        assert n1 != n2

    def test_wrong_secret_raises(self):
        secret1 = self._secret()
        secret2 = self._secret()
        ct, nonce = encrypt_endpoint("http://10.0.0.1:8001", secret1)
        with pytest.raises(InvalidTag):
            decrypt_endpoint(ct, nonce, secret2)

    def test_tampered_ciphertext_raises(self):
        secret = self._secret()
        ct, nonce = encrypt_endpoint("http://10.0.0.1:8001", secret)
        bad_ct = bytes([ct[0] ^ 0xFF]) + ct[1:]
        with pytest.raises(InvalidTag):
            decrypt_endpoint(bad_ct, nonce, secret)

    def test_salt_must_match(self):
        secret = self._secret()
        salt = b"my-salt"
        ct, nonce = encrypt_endpoint("http://10.0.0.1:8001", secret, salt=salt)
        assert decrypt_endpoint(ct, nonce, secret, salt=salt) == "http://10.0.0.1:8001"
        with pytest.raises(InvalidTag):
            decrypt_endpoint(ct, nonce, secret, salt=b"wrong-salt")

    def test_unicode_endpoint_roundtrip(self):
        secret = self._secret()
        ep = "https://node-α.local:8443"
        ct, nonce = encrypt_endpoint(ep, secret)
        assert decrypt_endpoint(ct, nonce, secret) == ep

    def test_ecdh_symmetric_secret_decrypts(self):
        """Encryption with id1→id2 secret must decrypt with id2→id1 secret."""
        id1 = NodeIdentity.generate()
        id2 = NodeIdentity.generate()
        s1 = ecdh_shared_secret(id1, id2.public_key_hex)
        s2 = ecdh_shared_secret(id2, id1.public_key_hex)
        ep = "http://10.0.0.1:8001"
        ct, nonce = encrypt_endpoint(ep, s1)
        assert decrypt_endpoint(ct, nonce, s2) == ep
