"""NodeClient — single-node MCP tool invocation over HTTP(S).

Calls POST {endpoint}/tools/{tool_name} and returns the raw response dict.
The caller is responsible for passing the result through OutputValidator.

Security invariants:
- URL is never interpolated into shell commands
- All HTTP calls use urllib (stdlib only) — no shell=True
- Response is treated as untrusted until OutputValidator clears it
- Optional RequestSigner adds Ed25519 auth headers to every request
- Optional ssl_context enables TLS with custom CA verification
- max_response_bytes limits response size to prevent memory exhaustion
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..auth.signer import RequestSigner

_DEFAULT_TIMEOUT = 60       # seconds
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024   # 4 MiB


class NodeCallError(Exception):
    """Raised when a remote node call fails."""

    def __init__(self, message: str, node_id: str = "", endpoint: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id
        self.endpoint = endpoint


class NodeClient:
    """HTTP client for calling MCP tools on a remote LLMesh node.

    Args:
        timeout:     Request timeout in seconds.
        signer:      Optional RequestSigner — adds X-LLMesh-* auth headers.
        ssl_context: Optional SSLContext — enables HTTPS with CA verification.
                     Pass ssl.create_default_context(cafile="certs/ca.crt").
    """

    def __init__(
        self,
        timeout: int = _DEFAULT_TIMEOUT,
        signer: "RequestSigner | None" = None,
        ssl_context: ssl.SSLContext | None = None,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
    ) -> None:
        self._timeout = timeout
        self._signer = signer
        self._ssl_ctx = ssl_context
        self._max_response_bytes = max_response_bytes

    def call(
        self,
        endpoint: str,
        tool_name: str,
        body: dict[str, Any],
        node_id: str = "",
    ) -> dict[str, Any]:
        """Call POST {endpoint}/tools/{tool_name} and return the response dict.

        Args:
            endpoint: Node base URL, e.g. "https://192.168.1.5:8080".
            tool_name: MCP tool name, e.g. "generate_code".
            body: Request payload (must include task_id and caller_nonce).
            node_id: Optional node identifier for error reporting.

        Returns:
            Parsed response dict (unvalidated — caller must run OutputValidator).

        Raises:
            NodeCallError: On connectivity failure, HTTP error, timeout,
                           or non-JSON response.
        """
        path = f"/tools/{tool_name}"
        url = endpoint.rstrip("/") + path
        payload = json.dumps(body).encode()

        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "X-Node-Id": node_id or "fanout-client",
        }
        if self._signer:
            headers.update(self._signer.auth_headers("POST", path))

        req = urllib.request.Request(
            url,
            data=payload,
            headers=headers,
            method="POST",
        )

        try:
            with urllib.request.urlopen(
                req, timeout=self._timeout, context=self._ssl_ctx
            ) as resp:
                raw = resp.read(self._max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            body_text = exc.read().decode(errors="replace")[:200]
            raise NodeCallError(
                f"http_error:{exc.code}:{exc.reason}:{body_text}",
                node_id=node_id,
                endpoint=endpoint,
            ) from exc
        except urllib.error.URLError as exc:
            raise NodeCallError(
                f"url_error:{exc.reason}",
                node_id=node_id,
                endpoint=endpoint,
            ) from exc
        except TimeoutError as exc:
            raise NodeCallError(
                "timeout",
                node_id=node_id,
                endpoint=endpoint,
            ) from exc

        if len(raw) > self._max_response_bytes:
            raise NodeCallError(
                f"response_too_large:{len(raw)}>{self._max_response_bytes}",
                node_id=node_id,
                endpoint=endpoint,
            )

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise NodeCallError(
                f"response_not_json:{exc}",
                node_id=node_id,
                endpoint=endpoint,
            ) from exc

        if not isinstance(data, dict):
            raise NodeCallError(
                "response_not_an_object",
                node_id=node_id,
                endpoint=endpoint,
            )

        return data
