"""DiscoveryClient — HTTP client for P2P node discovery.

Communicates with remote NodeRegistry endpoints using urllib (stdlib only).
All HTTP calls are non-shell, list-based, and fail-closed on error.

Security invariants:
- URLs from untrusted sources are never interpolated into shell commands
- All responses are treated as untrusted until parsed and validated
- No shell=True, eval, exec, pickle anywhere
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

from ..identity.manifest import CapabilityManifest

_DEFAULT_TIMEOUT = 10  # seconds


class DiscoveryError(Exception):
    """Raised when a discovery operation fails."""


class DiscoveryClient:
    """HTTP client for registering with and querying remote node registries.

    Args:
        timeout: Per-request HTTP timeout in seconds.
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        registry_url: str,
        manifest: CapabilityManifest,
        endpoint: str,
        public_key_hex: str,
    ) -> dict[str, Any]:
        """Register this node with a remote registry.

        Args:
            registry_url: Base URL of the remote registry server.
            manifest: Signed CapabilityManifest for this node.
            endpoint: This node's HTTP base URL (advertised to peers).
            public_key_hex: Ed25519 public key hex for remote sig verification.

        Returns:
            The registry's response dict (the created NodeEntry).

        Raises:
            DiscoveryError: On connectivity failure, HTTP error, or bad response.
        """
        payload = {
            "manifest": manifest.to_dict(),
            "endpoint": endpoint,
            "public_key_hex": public_key_hex,
        }
        return self._post(registry_url.rstrip("/") + "/registry/register", payload)

    def deregister(self, registry_url: str, node_id: str) -> dict[str, Any]:
        """Deregister a node from the remote registry."""
        url = f"{registry_url.rstrip('/')}/registry/nodes/{node_id}"
        return self._delete(url)

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def discover(
        self,
        registry_url: str,
        subnet: str | None = None,
        tool: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch the list of live nodes from a remote registry.

        Args:
            registry_url: Base URL of the remote registry server.
            subnet: Optional filter — only nodes in this subnet.
            tool: Optional filter — only nodes advertising this tool.

        Returns:
            List of NodeEntry dicts.

        Raises:
            DiscoveryError: On connectivity failure or bad response.
        """
        params: list[str] = []
        if subnet:
            params.append(f"subnet={urllib.parse.quote(subnet)}")
        if tool:
            params.append(f"tool={urllib.parse.quote(tool)}")
        url = registry_url.rstrip("/") + "/registry/nodes"
        if params:
            url += "?" + "&".join(params)

        resp = self._get(url)
        if not isinstance(resp, list):
            raise DiscoveryError(f"discover: expected list, got {type(resp).__name__}")
        return resp

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def health_check(self, node_url: str) -> bool:
        """Return True if the node at node_url responds to GET /health."""
        try:
            resp = self._get(node_url.rstrip("/") + "/health")
            return isinstance(resp, dict) and resp.get("status") == "ok"
        except DiscoveryError:
            return False

    # ------------------------------------------------------------------
    # HTTP helpers (urllib only)
    # ------------------------------------------------------------------

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        return self._send(req)

    def _get(self, url: str) -> Any:
        req = urllib.request.Request(url, method="GET")
        return self._send(req)

    def _delete(self, url: str) -> Any:
        req = urllib.request.Request(url, method="DELETE")
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> Any:
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                raw = resp.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            raise DiscoveryError(
                f"http_error:{exc.code}:{exc.reason}:{body[:200]}"
            ) from exc
        except urllib.error.URLError as exc:
            raise DiscoveryError(f"url_error:{exc.reason}") from exc
        except TimeoutError as exc:
            raise DiscoveryError("timeout") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DiscoveryError(f"response_not_json:{exc}") from exc


# Lazy import to avoid circular dependency in _send
import urllib.parse  # noqa: E402
