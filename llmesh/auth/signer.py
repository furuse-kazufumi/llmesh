"""RequestSigner — Ed25519 signing for outgoing MCP requests.

Canonical string signed:
    "{METHOD}\\n{PATH}\\n{NODE_ID}\\n{TIMESTAMP_MS}\\n{BODY_SHA256}"

Headers added:
    X-LLMesh-Node-Id:   <caller node_id>
    X-LLMesh-Timestamp: <unix milliseconds>
    X-LLMesh-Signature: ed25519:<hex>

body_sha256 binds the signature to the exact request body, preventing
an attacker from replaying a valid header set with a different payload.
"""
from __future__ import annotations

import hashlib
import time

from ..identity.node_id import NodeIdentity


def make_canonical(
    method: str,
    path: str,
    node_id: str,
    timestamp_ms: int,
    body_sha256: str,
) -> str:
    """Deterministic string that is signed.  Exported for use by verifier.

    body_sha256 must be sha256(request_body_bytes).hexdigest().
    For requests with no body, pass sha256(b"").hexdigest().
    """
    return f"{method.upper()}\n{path}\n{node_id}\n{timestamp_ms}\n{body_sha256}"


class RequestSigner:
    """Signs outgoing HTTP requests with the node's Ed25519 private key."""

    def __init__(self, identity: NodeIdentity) -> None:
        self._identity = identity

    @property
    def node_id(self) -> str:
        return self._identity.node_id

    def auth_headers(self, method: str, path: str, body: bytes = b"") -> dict[str, str]:
        """Return the three auth headers to add to an outgoing request.

        Args:
            method: HTTP method (case-insensitive).
            path:   URL path (e.g. ``/tools/generate_code``).
            body:   Raw request body bytes.  Defaults to ``b""`` for requests
                    with no body (GET, HEAD, etc.).
        """
        timestamp_ms = int(time.time() * 1000)
        body_sha256 = hashlib.sha256(body).hexdigest()
        canonical = make_canonical(method, path, self._identity.node_id, timestamp_ms, body_sha256)
        sig_bytes = self._identity.sign(canonical.encode())
        return {
            "X-LLMesh-Node-Id":   self._identity.node_id,
            "X-LLMesh-Timestamp": str(timestamp_ms),
            "X-LLMesh-Signature": "ed25519:" + sig_bytes.hex(),
        }
