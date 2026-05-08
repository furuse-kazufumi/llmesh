"""Ed25519 → X25519 conversion and ECDH shared-secret derivation.

Ed25519 and X25519 share the same underlying curve (Curve25519).
The birational map allows converting an Ed25519 keypair to an X25519
keypair for Diffie-Hellman key agreement without generating a separate key.

Security invariants:
  - No shell=True, eval, exec, or pickle
  - Private key bytes are never logged or serialised by this module
  - The shared secret is a raw 32-byte value; callers must derive a
    symmetric key via a KDF (e.g. HKDF-SHA256) before use
"""
from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.x25519 import (
    X25519PrivateKey,
    X25519PublicKey,
)

from .node_id import NodeIdentity

# Curve25519 prime
_P = 2**255 - 19


def ed25519_private_to_x25519(identity: NodeIdentity) -> X25519PrivateKey:
    """Derive an X25519 private key from an Ed25519 NodeIdentity.

    Uses the standard SHA-512 clamping method described in RFC 8032 §5.1.5.
    The resulting X25519 key is deterministic for a given identity.
    """
    seed = identity.private_bytes()
    h = hashlib.sha512(seed).digest()
    scalar = bytearray(h[:32])
    scalar[0] &= 248
    scalar[31] &= 127
    scalar[31] |= 64
    return X25519PrivateKey.from_private_bytes(bytes(scalar))


def ed25519_pub_to_x25519_pub_bytes(pub_hex: str) -> bytes:
    """Convert a hex-encoded Ed25519 public key to X25519 Montgomery-form bytes.

    Uses the birational map u = (1+y)/(1-y) mod p, where y is the
    Ed25519 y-coordinate extracted from the compressed point.

    Returns 32 bytes suitable for X25519PublicKey.from_public_bytes().
    """
    pub_bytes = bytes.fromhex(pub_hex)
    if len(pub_bytes) != 32:
        raise ValueError(f"Ed25519 public key must be 32 bytes, got {len(pub_bytes)}")

    # Strip the sign bit to get the y-coordinate
    y_bytes = bytearray(pub_bytes)
    y_bytes[31] &= 0x7F
    y = int.from_bytes(y_bytes, "little")

    # Birational map: u = (1+y) / (1-y) mod p
    denom = (1 - y) % _P
    u = (1 + y) * pow(denom, _P - 2, _P) % _P
    return u.to_bytes(32, "little")


def ecdh_shared_secret(local_identity: NodeIdentity, remote_pub_hex: str) -> bytes:
    """Compute a 32-byte X25519 shared secret.

    Converts both the local Ed25519 private key and the remote Ed25519
    public key to their X25519 equivalents, then performs ECDH.

    The returned bytes are the raw shared secret. Callers MUST derive a
    symmetric key via HKDF or similar before using it for encryption.

    Args:
        local_identity: This node's Ed25519 identity.
        remote_pub_hex: The remote peer's Ed25519 public key as hex.

    Returns:
        32-byte ECDH shared secret.
    """
    local_x25519 = ed25519_private_to_x25519(local_identity)
    remote_x25519_bytes = ed25519_pub_to_x25519_pub_bytes(remote_pub_hex)
    remote_x25519_pub = X25519PublicKey.from_public_bytes(remote_x25519_bytes)
    return local_x25519.exchange(remote_x25519_pub)
