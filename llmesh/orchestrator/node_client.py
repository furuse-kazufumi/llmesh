"""NodeClient — single-node MCP tool invocation over HTTP(S) or any protocol adapter.

Default (protocol="http"):
    Calls POST {endpoint}/tools/{tool_name} via urllib and returns the raw response dict.

With protocol="tcp":
    Sends a UnifiedMessage(REQUEST) via TCPAdapter (one connection per request).
    Suitable for small payloads and environments without persistent connections.
    endpoint format: "host:port" (e.g. "192.168.1.5:8080").

With protocol="tcp_stream":
    Sends via TCPStreamAdapter — persistent connection + ReliableStream chunking.
    Transparent for any payload size; auto-reconnects on failure.
    Prefer this over "tcp" for large payloads or high-frequency calls.
    endpoint format: "host:port" (e.g. "192.168.1.5:8080").

With protocol="udp":
    Fire-and-forget datagrams; limited to ~65 KB payloads.

The caller is responsible for passing the result through OutputValidator.

Security invariants:
- URL is never interpolated into shell commands
- All HTTP calls use urllib (stdlib only) — no shell=True
- Non-HTTP calls use AdapterRegistry (no subprocess, no eval)
- Response is treated as untrusted until OutputValidator clears it
- Optional RequestSigner adds Ed25519 auth headers to HTTP requests
- Optional ssl_context enables TLS with custom CA verification
- max_response_bytes limits response size to prevent memory exhaustion
"""
from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from ..protocol import AdapterRegistry, NodeAddress, UnifiedMessage
from ..protocol.qos import DeadlineExpiredError, is_expired

if TYPE_CHECKING:
    from ..auth.signer import RequestSigner

_DEFAULT_TIMEOUT = 60       # seconds
_DEFAULT_MAX_RESPONSE_BYTES = 4 * 1024 * 1024   # 4 MiB
_HTTP_PROTOCOLS = {"http", "https"}


def _parse_host_port(endpoint: str) -> tuple[str, int]:
    """Parse "host:port" into (host, port). Strips scheme if present."""
    # Strip scheme (http://, tcp://, etc.)
    if "://" in endpoint:
        endpoint = endpoint.split("://", 1)[1]
    endpoint = endpoint.rstrip("/")
    host, _, port_s = endpoint.rpartition(":")
    if not host:
        host, port_s = port_s, "8080"
    try:
        return host, int(port_s)
    except ValueError as exc:
        raise ValueError(f"invalid endpoint {endpoint!r}: {exc}") from exc


class NodeCallError(Exception):
    """Raised when a remote node call fails."""

    def __init__(self, message: str, node_id: str = "", endpoint: str = "") -> None:
        super().__init__(message)
        self.node_id = node_id
        self.endpoint = endpoint


def register_adapter(name: str, adapter_cls: type) -> None:
    """Register a custom ProtocolAdapter class under *name*.

    Convenience wrapper around AdapterRegistry.register() so callers
    don't need to import the registry directly::

        from llmesh.orchestrator.node_client import register_adapter
        register_adapter("grpc", MyGRPCAdapter)
        client = NodeClient(protocol="grpc")
    """
    from ..protocol import AdapterRegistry
    AdapterRegistry.register(name, adapter_cls)


class NodeClient:
    """MCP tool client for a remote LLMesh node.

    Supports HTTP(S) (default) and any ProtocolAdapter registered in AdapterRegistry
    (e.g. "tcp", "udp").

    Args:
        timeout:     Request / connection timeout in seconds.
        signer:      Optional RequestSigner — adds X-LLMesh-* auth headers (HTTP only).
        ssl_context: Optional SSLContext — enables HTTPS with CA verification (HTTP only).
        max_response_bytes: Response size limit (HTTP only; adapter path has no limit).
        protocol:    Transport to use: "http" (default), "tcp", "udp", or any name
                     registered with AdapterRegistry.register().
                     "http" and "https" both use the urllib path unchanged.
    """

    def __init__(
        self,
        timeout: int = _DEFAULT_TIMEOUT,
        signer: "RequestSigner | None" = None,
        ssl_context: ssl.SSLContext | None = None,
        max_response_bytes: int = _DEFAULT_MAX_RESPONSE_BYTES,
        protocol: str = "http",
    ) -> None:
        self._timeout = timeout
        self._signer = signer
        self._ssl_ctx = ssl_context
        self._max_response_bytes = max_response_bytes
        self._protocol = protocol

    def call(
        self,
        endpoint: str,
        tool_name: str,
        body: dict[str, Any],
        node_id: str = "",
    ) -> dict[str, Any]:
        """Call a remote MCP tool and return the unvalidated response dict.

        Args:
            endpoint:  For HTTP: base URL e.g. "https://192.168.1.5:8080".
                       For other protocols: "host:port" e.g. "192.168.1.5:8080".
            tool_name: MCP tool name, e.g. "generate_code".
            body:      Request payload (must include task_id and caller_nonce).
            node_id:   Optional node identifier for error reporting.

        Returns:
            Parsed response dict (unvalidated — caller must run OutputValidator).

        Raises:
            NodeCallError: On connectivity failure, timeout, or invalid response.
            DeadlineExpiredError: If body["deadline"] has already passed.
        """
        if is_expired(body.get("deadline")):
            raise DeadlineExpiredError(
                f"call to {tool_name!r} on {node_id or endpoint!r} deadline expired"
            )
        if self._protocol in _HTTP_PROTOCOLS:
            return self._http_call(endpoint, tool_name, body, node_id)
        return self._adapter_call(endpoint, tool_name, body, node_id)

    # ------------------------------------------------------------------
    # HTTP path (unchanged)
    # ------------------------------------------------------------------

    def _http_call(
        self,
        endpoint: str,
        tool_name: str,
        body: dict[str, Any],
        node_id: str,
    ) -> dict[str, Any]:
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
            with urllib.request.urlopen(  # nosec B310 - peer URL verified via Capability Manifest; response capped.
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

    # ------------------------------------------------------------------
    # Adapter path (TCP / UDP / custom)
    # ------------------------------------------------------------------

    def _adapter_call(
        self,
        endpoint: str,
        tool_name: str,
        body: dict[str, Any],
        node_id: str,
    ) -> dict[str, Any]:
        """Send a UnifiedMessage REQUEST via AdapterRegistry and return the result."""
        try:
            host, port = _parse_host_port(endpoint)
        except ValueError as exc:
            raise NodeCallError(str(exc), node_id=node_id, endpoint=endpoint) from exc

        target = NodeAddress(host=host, port=port, node_id=node_id)
        sender = NodeAddress(host="localhost", port=0)

        msg = UnifiedMessage.request(
            payload={"tool": tool_name, "body": body},
            sender=sender,
            target=target,
        )

        async def _go() -> UnifiedMessage | None:
            adapter = AdapterRegistry.create(self._protocol, timeout=self._timeout)
            return await adapter.send(msg, target)

        try:
            response = asyncio.run(_go())
        except Exception as exc:
            raise NodeCallError(
                f"adapter_error:{exc}",
                node_id=node_id,
                endpoint=endpoint,
            ) from exc

        if response is None:
            raise NodeCallError("no_response", node_id=node_id, endpoint=endpoint)

        if response.type.value == "error":
            raise NodeCallError(
                response.payload.get("error", "remote_error"),
                node_id=node_id,
                endpoint=endpoint,
            )

        result = response.payload.get("result")
        if not isinstance(result, dict):
            raise NodeCallError(
                "response_payload_missing_result",
                node_id=node_id,
                endpoint=endpoint,
            )
        return result
