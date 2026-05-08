"""HTTP response size guards (v2.17+).

LLMesh's HTTP clients all read entire response bodies into memory before
parsing them as JSON. Without explicit caps a hostile or runaway server
can pin arbitrary memory and trigger an OOM kill on the LLMesh node.

This module exposes :func:`read_capped` which wraps
``urllib.request.urlopen``-returned response objects with a per-call
upper bound. The default policy lives here so a single CHANGELOG line
adjusts the cap globally.

Usage
-----
    from llmesh.security.http_limits import read_capped, ResponseTooLargeError

    with urllib.request.urlopen(req, timeout=10) as resp:
        body = read_capped(resp, max_bytes=1 << 20)  # 1 MiB cap

    # Or via the dedicated exception type for explicit branching:
    try:
        body = read_capped(resp, max_bytes=4096)
    except ResponseTooLargeError:
        ...
"""
from __future__ import annotations


# Module-level defaults. Per-caller overrides are encouraged; these
# values exist so a downstream that just wants "something sensible" can
# import them.
DEFAULT_MAX_RESPONSE_BYTES = 1 << 20          # 1 MiB — small JSON / control-plane
DEFAULT_LLM_RESPONSE_BYTES = 1 << 24          # 16 MiB — generative output
DEFAULT_GOSSIP_RESPONSE_BYTES = 1 << 18       # 256 KiB — peer discovery
DEFAULT_DISCOVERY_RESPONSE_BYTES = 1 << 18    # 256 KiB — registry list
DEFAULT_RENDEZVOUS_RESPONSE_BYTES = 1 << 16   # 64 KiB — DID lookup
DEFAULT_HTTP_ADAPTER_BYTES = 1 << 22          # 4 MiB — generic HTTP messages


class ResponseTooLargeError(IOError):
    """Raised when an HTTP response exceeds the configured byte cap."""

    def __init__(self, cap: int) -> None:
        super().__init__(f"HTTP response exceeded {cap} bytes")
        self.cap = cap


def read_capped(resp, *, max_bytes: int) -> bytes:
    """Read at most ``max_bytes`` from ``resp`` and raise on overflow.

    Parameters
    ----------
    resp:
        Anything with a ``read(n: int) -> bytes`` method. The standard
        objects returned from ``urllib.request.urlopen`` qualify, as do
        in-memory ``BytesIO`` and most async wrappers (use ``await`` if
        the underlying read is async — this helper is purely synchronous).
    max_bytes:
        Hard upper bound on the body size. Reading attempts to pull
        ``max_bytes + 1`` so the overflow case is detectable in a single
        round-trip.

    Returns
    -------
    bytes
        The full response body (always ``len(...) <= max_bytes``).
    """
    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    raw = resp.read(max_bytes + 1)
    if not isinstance(raw, (bytes, bytearray, memoryview)):
        raise TypeError(
            f"resp.read returned {type(raw).__name__}, expected bytes-like"
        )
    if len(raw) > max_bytes:
        raise ResponseTooLargeError(max_bytes)
    return bytes(raw)
