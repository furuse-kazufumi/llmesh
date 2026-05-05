"""RequestVerifier — FastAPI middleware for Ed25519 request authentication.

Verification steps (fail-closed):
1. X-LLMesh-Node-Id header present and in TrustedPeers
2. X-LLMesh-Timestamp within ±TOLERANCE_MS of server clock
3. X-LLMesh-Signature is valid Ed25519 over the canonical string

Unauthenticated paths (bypass list):
    GET /identity          — public key advertisement (TOFU bootstrap)
    GET /health            — health check
    POST /registry/register — registration uses manifest Ed25519 instead
    GET  /registry/peers   — gossip pull (nodes are already verified via manifest)
"""
from __future__ import annotations

import hashlib
import os
import time

from fastapi import Request
from fastapi.responses import JSONResponse

from ..identity.node_id import NodeIdentity
from .signer import make_canonical
from .trusted_peers import TrustedPeers

TOLERANCE_MS = 30_000  # ±30 seconds

_BYPASS_PREFIXES = (
    "/identity",
    "/health",
    "/registry/register",   # manifest Ed25519 handles auth
    "/docs",
    "/openapi",
    # NOTE: /registry/peers is NOT here — trusted peers only
)


class SignatureVerificationError(Exception):
    pass


def _verify_request(
    node_id: str,
    timestamp_str: str,
    sig_header: str,
    method: str,
    path: str,
    peers: TrustedPeers,
    body: bytes = b"",
) -> None:
    """Raise SignatureVerificationError on any failure.

    Args:
        body: Raw request body bytes used to compute body_sha256.
              Must match the bytes the signer used when calling auth_headers().
              Defaults to ``b""`` for bodyless requests.
    """
    peer = peers.get(node_id)
    if peer is None:
        raise SignatureVerificationError(f"untrusted_node:{node_id}")

    try:
        timestamp_ms = int(timestamp_str)
    except (ValueError, TypeError):
        raise SignatureVerificationError("invalid_timestamp")

    delta = abs(int(time.time() * 1000) - timestamp_ms)
    if delta > TOLERANCE_MS:
        raise SignatureVerificationError(f"timestamp_stale:{delta}ms")

    if not sig_header.startswith("ed25519:"):
        raise SignatureVerificationError("malformed_signature")
    try:
        sig_bytes = bytes.fromhex(sig_header.removeprefix("ed25519:"))
    except ValueError:
        raise SignatureVerificationError("invalid_signature_hex")

    body_sha256 = hashlib.sha256(body).hexdigest()
    canonical = make_canonical(method, path, node_id, timestamp_ms, body_sha256)
    ok = NodeIdentity.verify_with_public_hex(canonical.encode(), sig_bytes, peer.public_key_hex)
    if not ok:
        raise SignatureVerificationError(f"bad_signature:node={node_id}")


def make_auth_middleware(peers: TrustedPeers):
    """Return a FastAPI middleware function bound to the given TrustedPeers."""

    async def _auth_middleware(request: Request, call_next):
        path = request.url.path

        # Bypass list
        if any(path.startswith(p) for p in _BYPASS_PREFIXES):
            return await call_next(request)

        node_id   = request.headers.get("X-LLMesh-Node-Id", "")
        timestamp = request.headers.get("X-LLMesh-Timestamp", "")
        signature = request.headers.get("X-LLMesh-Signature", "")

        if not node_id or not timestamp or not signature:
            return JSONResponse(
                status_code=401,
                content={"error": "missing_auth_headers"},
            )

        req_body = await request.body()

        try:
            _verify_request(
                node_id, timestamp, signature,
                request.method, path, peers,
                body=req_body,
            )
        except SignatureVerificationError as exc:
            return JSONResponse(
                status_code=403,
                content={"error": "auth_failed", "detail": str(exc)},
            )

        return await call_next(request)

    return _auth_middleware
