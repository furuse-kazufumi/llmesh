"""Internal helper: generate Ed25519 keys for paramiko adapters."""
from __future__ import annotations

import io
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover — annotations only
    import paramiko


def generate_ed25519_key() -> "paramiko.Ed25519Key":
    """Return a fresh paramiko Ed25519Key (paramiko 4.x compatible)."""
    import paramiko  # noqa: PLC0415
    from cryptography.hazmat.primitives.asymmetric.ed25519 import (  # noqa: PLC0415
        Ed25519PrivateKey,
    )
    from cryptography.hazmat.primitives.serialization import (  # noqa: PLC0415
        Encoding,
        NoEncryption,
        PrivateFormat,
    )

    priv = Ed25519PrivateKey.generate()
    pem = priv.private_bytes(Encoding.PEM, PrivateFormat.OpenSSH, NoEncryption())
    return paramiko.Ed25519Key.from_private_key(io.StringIO(pem.decode()))


def key_from_hex(hex_pub: str) -> "paramiko.Ed25519Key | None":
    """Load a paramiko Ed25519Key from a 32-byte raw public key in hex.

    Returns None on any parse error.
    """
    import binascii  # noqa: PLC0415
    import paramiko  # noqa: PLC0415

    try:
        raw = binascii.unhexlify(hex_pub)
    except Exception:
        return None
    try:
        return paramiko.Ed25519Key(data=raw)
    except Exception:
        return None
