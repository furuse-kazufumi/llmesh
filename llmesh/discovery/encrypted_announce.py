"""Signed and (Phase 2) encrypted endpoint announcements.

Phase 1 — plaintext endpoint, Ed25519 signature:
  - build_announcement(identity, endpoint) → dict
  - verify_announcement(ann)              → endpoint str

Phase 2 building blocks — AES-256-GCM encrypted endpoint:
  - encrypt_endpoint(endpoint, shared_secret) → (ciphertext, nonce)
  - decrypt_endpoint(ciphertext, nonce, shared_secret) → endpoint str

The shared_secret fed to encrypt/decrypt should be derived via ECDH
(llmesh.identity.x25519.ecdh_shared_secret) and then further derived
with HKDF before passing here.  A convenience wrapper is provided:

  - derive_encryption_key(shared_secret, *, salt, info) → 32-byte key

Security invariants:
  - shell=True, eval, exec, pickle are never used
  - AES-256-GCM provides confidentiality and integrity
  - Nonce is 12 bytes, generated fresh per encryption (never reused)
  - HKDF-SHA256 used to derive a symmetric key from the ECDH secret
  - Timestamp window check prevents replay of old announcements
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..identity.node_id import NodeIdentity

# Maximum clock skew accepted when verifying announcements (seconds)
VERIFY_WINDOW_SECONDS = 300

_NONCE_LEN = 12   # GCM recommended nonce length
_KEY_LEN = 32     # AES-256


class AnnouncementError(Exception):
    """Raised when an announcement fails signature or structure validation."""


# ---------------------------------------------------------------------------
# Signed announcement — Phase 1
# ---------------------------------------------------------------------------

def _signed_message(node_id: str, endpoint: str, timestamp_utc: str,
                    public_key_hex: str = "", did: str = "") -> bytes:
    """Canonical signed payload — must match rendezvous.server._signed_message."""
    return f"{node_id}|{endpoint}|{timestamp_utc}|{public_key_hex}|{did}".encode("utf-8")


def build_announcement(
    identity: NodeIdentity,
    endpoint: str,
) -> dict:
    """Build a signed announcement dict (Phase 1: plaintext endpoint).

    The returned dict is compatible with the rendezvous server's
    ``POST /announce`` request schema.

    Args:
        identity: This node's Ed25519 identity (used for signing).
        endpoint: HTTP/HTTPS URL where this node accepts connections.

    Returns:
        Dict with keys: node_id, did, endpoint, public_key_hex,
        timestamp_utc, signature.
    """
    timestamp_utc = datetime.now(timezone.utc).isoformat()
    message = _signed_message(identity.node_id, endpoint, timestamp_utc,
                              identity.public_key_hex, identity.did_key)
    signature_hex = identity.sign(message).hex()
    return {
        "node_id": identity.node_id,
        "did": identity.did_key,
        "endpoint": endpoint,
        "public_key_hex": identity.public_key_hex,
        "timestamp_utc": timestamp_utc,
        "signature": signature_hex,
    }


def verify_announcement(ann: dict) -> str:
    """Verify a signed announcement and return the plaintext endpoint.

    Checks:
      1. Required fields are present.
      2. Timestamp is within VERIFY_WINDOW_SECONDS of now.
      3. Ed25519 signature is valid.

    Args:
        ann: Dict as returned by build_announcement() or the rendezvous server.

    Returns:
        The verified endpoint string.

    Raises:
        AnnouncementError: If any check fails.
    """
    required = {"node_id", "endpoint", "public_key_hex", "timestamp_utc", "signature"}
    missing = required - ann.keys()
    if missing:
        raise AnnouncementError(f"missing fields: {missing}")

    node_id: str = ann["node_id"]
    endpoint: str = ann["endpoint"]
    public_key_hex: str = ann["public_key_hex"]
    timestamp_utc: str = ann["timestamp_utc"]
    signature_hex: str = ann["signature"]

    # Timestamp freshness
    try:
        ts = datetime.fromisoformat(timestamp_utc)
    except ValueError as exc:
        raise AnnouncementError(f"invalid timestamp format: {exc}") from exc
    if ts.tzinfo is None:
        raise AnnouncementError("timestamp must include timezone info")
    skew = abs((datetime.now(timezone.utc) - ts).total_seconds())
    if skew > VERIFY_WINDOW_SECONDS:
        raise AnnouncementError(
            f"announcement timestamp too old or too far in future ({skew:.0f}s)"
        )

    # Signature
    try:
        sig_bytes = bytes.fromhex(signature_hex)
    except ValueError as exc:
        raise AnnouncementError(f"signature is not valid hex: {exc}") from exc

    message = _signed_message(node_id, endpoint, timestamp_utc, public_key_hex,
                              ann.get("did", ""))
    if not NodeIdentity.verify_with_public_hex(message, sig_bytes, public_key_hex):
        raise AnnouncementError("Ed25519 signature verification failed")

    return endpoint


# ---------------------------------------------------------------------------
# Key derivation helper — Phase 2
# ---------------------------------------------------------------------------

def derive_encryption_key(
    shared_secret: bytes,
    *,
    salt: bytes | None = None,
    info: bytes = b"llmesh-endpoint-encryption-v1",
) -> bytes:
    """Derive a 32-byte AES-256 key from an ECDH shared secret via HKDF-SHA256.

    Args:
        shared_secret: Raw 32-byte output of ecdh_shared_secret().
        salt:          Optional random salt (16–32 bytes recommended).
                       If None, HKDF uses a zero-filled salt internally.
        info:          Context string binding the key to its purpose.

    Returns:
        32-byte symmetric encryption key.
    """
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=_KEY_LEN,
        salt=salt,
        info=info,
    )
    return hkdf.derive(shared_secret)


# ---------------------------------------------------------------------------
# Authenticated encryption — Phase 2
# ---------------------------------------------------------------------------

def encrypt_endpoint(
    endpoint: str,
    shared_secret: bytes,
    *,
    salt: bytes | None = None,
) -> tuple[bytes, bytes]:
    """Encrypt an endpoint URL with AES-256-GCM.

    A fresh 12-byte nonce is generated per call.  The shared_secret is
    first derived via HKDF-SHA256 to produce the actual encryption key.

    Args:
        endpoint:      Plaintext endpoint URL string.
        shared_secret: 32-byte ECDH shared secret (from ecdh_shared_secret()).
        salt:          Optional HKDF salt (pass the same value to decrypt).

    Returns:
        (ciphertext, nonce) — both bytes objects.
        Store nonce alongside ciphertext; it is not secret.
    """
    key = derive_encryption_key(shared_secret, salt=salt)
    nonce = os.urandom(_NONCE_LEN)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, endpoint.encode("utf-8"), None)
    return ciphertext, nonce


def decrypt_endpoint(
    ciphertext: bytes,
    nonce: bytes,
    shared_secret: bytes,
    *,
    salt: bytes | None = None,
) -> str:
    """Decrypt an AES-256-GCM encrypted endpoint URL.

    Args:
        ciphertext:    Output of encrypt_endpoint() (includes GCM auth tag).
        nonce:         The nonce returned by encrypt_endpoint().
        shared_secret: The same 32-byte ECDH shared secret used for encryption.
        salt:          The same HKDF salt used during encryption (or None).

    Returns:
        Plaintext endpoint URL string.

    Raises:
        cryptography.exceptions.InvalidTag: If decryption or authentication fails.
    """
    key = derive_encryption_key(shared_secret, salt=salt)
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
