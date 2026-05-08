"""JSON / MessagePack codec abstraction for UnifiedMessage wire serialization.

Auto-detection on decode: bytes whose first byte is ``{`` (0x7B) are treated as
JSON; all other byte sequences are treated as MessagePack.  This is safe because
MessagePack map headers use 0x80–0xDF, which never overlap with ASCII ``{``.

Usage::

    from llmesh.protocol.codec import encode, decode, is_msgpack_available

    wire = encode(msg.to_dict(), codec="msgpack")   # bytes
    data = decode(wire)                              # dict
"""
from __future__ import annotations

import json
from typing import Any

try:
    import msgpack as _msgpack  # type: ignore[import]
    _MSGPACK_AVAILABLE = True
except ImportError:
    _msgpack = None  # type: ignore[assignment]
    _MSGPACK_AVAILABLE = False

JSON = "json"
MSGPACK = "msgpack"
CODECS = (JSON, MSGPACK)

_INSTALL_HINT = "pip install llmesh[msgpack]"


def encode(data: dict[str, Any], codec: str = JSON) -> bytes:
    """Serialize *data* to bytes using *codec* (``"json"`` or ``"msgpack"``)."""
    if codec == MSGPACK:
        if not _MSGPACK_AVAILABLE:
            raise RuntimeError(
                f"msgpack is not installed — install it with: {_INSTALL_HINT}"
            )
        return _msgpack.packb(data, use_bin_type=True)
    if codec == JSON:
        return json.dumps(data).encode()
    raise ValueError(f"unknown codec {codec!r}; valid: {CODECS}")


def decode(data: bytes) -> dict[str, Any]:
    """Deserialize *data*, auto-detecting JSON vs MessagePack from the first byte."""
    if not data:
        raise ValueError("cannot decode empty frame")
    if data[0] == ord("{"):
        return json.loads(data)
    if not _MSGPACK_AVAILABLE:
        raise RuntimeError(
            f"Received MessagePack data but msgpack is not installed — {_INSTALL_HINT}"
        )
    return _msgpack.unpackb(data, raw=False)


def is_msgpack_available() -> bool:
    """Return True if the ``msgpack`` package is importable."""
    return _MSGPACK_AVAILABLE
