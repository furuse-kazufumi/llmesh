"""Ed25519 Node ID and did:key derivation."""
from __future__ import annotations


from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    PublicFormat,
    PrivateFormat,
    NoEncryption,
)

# Multicodec prefix for Ed25519 public key: 0xed01
_ED25519_MULTICODEC = b"\xed\x01"

# base58 alphabet (Bitcoin)
_B58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def _b58encode(data: bytes) -> str:
    """Base58 encode without external dependency (Bitcoin alphabet)."""
    count = 0
    for byte in data:
        if byte == 0:
            count += 1
        else:
            break
    num = int.from_bytes(data, "big")
    result = []
    while num > 0:
        num, rem = divmod(num, 58)
        result.append(_B58_ALPHABET[rem:rem + 1])
    result.reverse()
    return (b"1" * count + b"".join(result)).decode("ascii")


class NodeIdentity:
    """Ed25519 keypair with stable Node ID and did:key derivation.

    Usage:
        identity = NodeIdentity.generate()
        print(identity.node_id)   # "peer:12D3KooW..."
        print(identity.did_key)   # "did:llmesh:1:z6Mk..."
    """

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self._public_key: Ed25519PublicKey = private_key.public_key()
        self._pub_bytes: bytes = self._public_key.public_bytes(
            Encoding.Raw, PublicFormat.Raw
        )

    @classmethod
    def generate(cls) -> "NodeIdentity":
        """Generate a new random Ed25519 keypair."""
        return cls(Ed25519PrivateKey.generate())

    @classmethod
    def from_private_bytes(cls, raw: bytes) -> "NodeIdentity":
        """Restore identity from raw 32-byte private key."""
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        return cls(Ed25519PrivateKey.from_private_bytes(raw))

    # ------------------------------------------------------------------
    # Identifiers
    # ------------------------------------------------------------------

    @property
    def did_key(self) -> str:
        """did:llmesh:1: identifier derived from Ed25519 public key.

        Format: did:llmesh:1:z<base58btc(0xed01 || pubkey_bytes)>
        Version segment '1' allows future key algorithm migration.
        """
        multicodec_key = _ED25519_MULTICODEC + self._pub_bytes
        return "did:llmesh:1:z" + _b58encode(multicodec_key)

    @property
    def node_id(self) -> str:
        """Stable node identifier prefixed with 'peer:'."""
        return "peer:" + _b58encode(self._pub_bytes)

    @property
    def public_key_hex(self) -> str:
        return self._pub_bytes.hex()

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def sign(self, message: bytes) -> bytes:
        """Sign arbitrary bytes. Returns 64-byte Ed25519 signature."""
        return self._private_key.sign(message)

    def verify(self, message: bytes, signature: bytes) -> bool:
        """Verify signature against this identity's public key."""
        try:
            self._public_key.verify(signature, message)
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Serialisation (private key — keep secret)
    # ------------------------------------------------------------------

    def private_bytes(self) -> bytes:
        """Raw 32-byte private key for secure storage."""
        return self._private_key.private_bytes(
            Encoding.Raw, PrivateFormat.Raw, NoEncryption()
        )

    # ------------------------------------------------------------------
    # Class-level signature verification (public key only)
    # ------------------------------------------------------------------

    @staticmethod
    def verify_with_public_hex(message: bytes, signature: bytes, pub_hex: str) -> bool:
        """Verify without needing a full NodeIdentity (for remote nodes)."""
        try:
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
            pub = Ed25519PublicKey.from_public_bytes(bytes.fromhex(pub_hex))
            pub.verify(signature, message)
            return True
        except Exception:
            return False

    @staticmethod
    def node_id_from_public_hex(pub_hex: str) -> str:
        """Derive the stable ``peer:`` node id from a hex Ed25519 public key.

        Mirrors :pyattr:`node_id` for a remote peer whose only known identifier is
        its public key (e.g. a signed result's ``executor_pub_hex``). Lets a caller
        bind a signature to the dispatched peer's node id without a registry lookup.

        Raises ``ValueError`` on malformed hex (fail-closed: caller must catch).
        """
        return "peer:" + _b58encode(bytes.fromhex(pub_hex))
