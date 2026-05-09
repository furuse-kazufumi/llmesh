"""Rendezvous client — announce and lookup helpers.

Uses stdlib urllib only (no extra dependencies).

Security invariants:
  - shell=True, eval, exec, pickle are never used
  - Announcements are signed with the caller's Ed25519 private key
  - Signature covers: "<node_id>|<endpoint>|<timestamp_utc>" (UTF-8)
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from datetime import datetime, timezone

from ..identity.node_id import NodeIdentity


class AnnounceError(Exception):
    """Raised when the rendezvous server rejects an announcement."""


class LookupError(Exception):
    """Raised when a node cannot be found or the server is unreachable."""


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def announce(
    identity: NodeIdentity,
    endpoint: str,
    rendezvous_url: str,
    *,
    timeout: float = 10.0,
) -> None:
    """Sign and POST an endpoint announcement to the rendezvous server.

    Args:
        identity:       This node's Ed25519 identity (used for signing).
        endpoint:       HTTP/HTTPS URL where this node accepts connections.
        rendezvous_url: Base URL of the rendezvous server (no trailing slash).
        timeout:        HTTP request timeout in seconds.

    Raises:
        AnnounceError: Server returned an error or connection failed.
    """
    timestamp_utc = _now_utc_iso()
    message = f"{identity.node_id}|{endpoint}|{timestamp_utc}|{identity.public_key_hex}|{identity.did_key}".encode("utf-8")
    signature_hex = identity.sign(message).hex()

    payload = {
        "node_id": identity.node_id,
        "did": identity.did_key,
        "endpoint": endpoint,
        "public_key_hex": identity.public_key_hex,
        "timestamp_utc": timestamp_utc,
        "signature": signature_hex,
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url=f"{rendezvous_url.rstrip('/')}/announce",
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 - rendezvous URL is operator-configured; response capped.
            if resp.status not in (200, 201):
                body = resp.read().decode("utf-8", errors="replace")
                raise AnnounceError(f"unexpected status {resp.status}: {body}")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise AnnounceError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise AnnounceError(f"connection failed: {exc.reason}") from exc


def lookup(
    node_id: str,
    rendezvous_url: str,
    *,
    timeout: float = 10.0,
) -> str:
    """Return the endpoint URL registered for *node_id*.

    Args:
        node_id:        The target node's identifier (e.g. "peer:...").
        rendezvous_url: Base URL of the rendezvous server.
        timeout:        HTTP request timeout in seconds.

    Returns:
        The endpoint URL string (e.g. "https://10.0.0.5:8001").

    Raises:
        LookupError: Node not found or server unreachable.
    """
    from llmesh.security.http_limits import (
        DEFAULT_RENDEZVOUS_RESPONSE_BYTES,
        ResponseTooLargeError,
        read_capped,
    )
    url = f"{rendezvous_url.rstrip('/')}/lookup/{urllib.request.quote(node_id)}"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310  # nosec B310 - rendezvous URL is operator-configured; response capped.
            body = read_capped(resp, max_bytes=DEFAULT_RENDEZVOUS_RESPONSE_BYTES).decode("utf-8")
            data = json.loads(body)
            return data["endpoint"]
    except ResponseTooLargeError as exc:
        raise LookupError(f"response too large: cap={exc.cap}") from exc
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LookupError(f"node {node_id!r} not found") from exc
        body = exc.read().decode("utf-8", errors="replace")
        raise LookupError(f"HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise LookupError(f"connection failed: {exc.reason}") from exc
    except (KeyError, json.JSONDecodeError) as exc:
        raise LookupError(f"unexpected response format: {exc}") from exc
