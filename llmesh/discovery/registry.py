"""NodeRegistry — in-memory peer registry with TTL eviction and subnet filtering.

Security invariants:
- Manifest signature is verified before registration
- TTL is enforced: expired entries are invisible and evicted lazily
- No shell=True, eval, exec, pickle anywhere in this module
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from ..identity.manifest import CapabilityManifest, ManifestVerificationError


class RegistryError(Exception):
    """Raised when a registration or lookup fails."""


@dataclass
class NodeEntry:
    """A registered peer node."""

    node_id: str
    did: str
    endpoint: str          # HTTP base URL, e.g. "http://192.168.1.5:8080"
    subnets: list[str]
    tools: list[str]
    public_key_hex: str    # Ed25519 pubkey for signature verification
    registered_at: float   # Unix timestamp
    expires_at: float      # Unix timestamp
    display_name: str = ""
    manifest_dict: dict = field(default_factory=dict)  # full signed manifest for gossip

    def is_expired(self) -> bool:
        return time.time() > self.expires_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "did": self.did,
            "endpoint": self.endpoint,
            "subnets": self.subnets,
            "tools": self.tools,
            "display_name": self.display_name,
            "registered_at": self.registered_at,
            "expires_at": self.expires_at,
        }

    def to_peer_dict(self) -> dict[str, Any]:
        """Full representation for gossip (includes signed manifest)."""
        return {
            "node_id": self.node_id,
            "public_key_hex": self.public_key_hex,
            "endpoint": self.endpoint,
            "manifest": self.manifest_dict,
        }


class NodeRegistry:
    """In-memory registry of peer LLMesh nodes.

    Nodes self-register by submitting a signed CapabilityManifest.
    The registry verifies the manifest signature and TTL before storing.
    Expired entries are evicted lazily on each read operation.

    Args:
        max_nodes: Hard cap on registered nodes. Oldest entry is evicted
                   when the cap is reached.
        verify_signatures: If True (default), manifest Ed25519 signatures
                           are verified before registration.
    """

    def __init__(
        self,
        max_nodes: int = 256,
        verify_signatures: bool = True,
    ) -> None:
        self._max_nodes = max_nodes
        self._verify = verify_signatures
        self._nodes: dict[str, NodeEntry] = {}  # node_id → NodeEntry

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        manifest_dict: dict[str, Any],
        endpoint: str,
        public_key_hex: str,
    ) -> NodeEntry:
        """Register a node from its CapabilityManifest dict.

        Args:
            manifest_dict: The manifest as a plain dict (from JSON body).
            endpoint: The node's HTTP base URL.
            public_key_hex: Hex-encoded Ed25519 public key for sig verification.

        Returns:
            The created NodeEntry.

        Raises:
            RegistryError: On invalid manifest, bad signature, or expired TTL.
        """
        try:
            manifest = CapabilityManifest.from_dict(manifest_dict)
        except Exception as exc:
            raise RegistryError(f"manifest_parse_error:{exc}") from exc

        if self._verify:
            try:
                manifest.verify(pub_hex=public_key_hex)
            except ManifestVerificationError as exc:
                raise RegistryError(f"manifest_verification_failed:{exc}") from exc
        # verify_signatures=False skips ALL checks (TTL included) — test-only mode

        self._evict_expired()

        if len(self._nodes) >= self._max_nodes:
            self._evict_oldest()

        import time as _time
        from datetime import datetime
        try:
            expires_at = datetime.fromisoformat(manifest.expires_at).timestamp()
        except ValueError as exc:
            raise RegistryError(f"invalid_expires_at:{exc}") from exc

        entry = NodeEntry(
            node_id=manifest.node_id,
            did=manifest.did,
            endpoint=endpoint.rstrip("/"),
            subnets=list(manifest.subnets),
            tools=list(manifest.tools),
            public_key_hex=public_key_hex,
            registered_at=_time.time(),
            expires_at=expires_at,
            display_name=manifest.display_name,
            manifest_dict=manifest_dict,
        )
        self._nodes[entry.node_id] = entry
        return entry

    def deregister(self, node_id: str) -> bool:
        """Remove a node from the registry. Returns True if it existed."""
        return self._nodes.pop(node_id, None) is not None

    # ------------------------------------------------------------------
    # Lookup
    # ------------------------------------------------------------------

    def get(self, node_id: str) -> NodeEntry | None:
        """Return a NodeEntry by node_id, or None if absent/expired."""
        self._evict_expired()
        return self._nodes.get(node_id)

    def list_nodes(
        self,
        subnet: str | None = None,
        tool: str | None = None,
    ) -> list[NodeEntry]:
        """Return live nodes, optionally filtered by subnet or tool.

        Args:
            subnet: If given, only return nodes in this subnet.
            tool: If given, only return nodes that advertise this tool.
        """
        self._evict_expired()
        nodes = list(self._nodes.values())
        if subnet:
            nodes = [n for n in nodes if subnet in n.subnets]
        if tool:
            nodes = [n for n in nodes if tool in n.tools]
        return nodes

    @property
    def count(self) -> int:
        self._evict_expired()
        return len(self._nodes)

    def find_matching(
        self,
        query: "CapabilityQuery",
        *,
        k: int = 3,
    ) -> list[tuple[float, NodeEntry]]:
        """Return up to k live nodes that match the capability query, ranked
        by matching score (descending).

        Each node's CapabilityProfile is built from its stored manifest_dict.
        Peers with score 0.0 (hard-filter rejected) are excluded.
        """
        # Local import to avoid pulling clustering at module top (keeps
        # registry.py importable without clustering needing zero deps).
        from llmesh.discovery.clustering import CapabilityProfile, pick_top_peers

        self._evict_expired()
        pairs: list[tuple[CapabilityProfile, NodeEntry]] = [
            (CapabilityProfile.from_manifest(entry.manifest_dict), entry)
            for entry in self._nodes.values()
        ]
        return pick_top_peers(pairs, query, k=k)

    # ------------------------------------------------------------------
    # Eviction
    # ------------------------------------------------------------------

    def _evict_expired(self) -> None:
        expired = [nid for nid, e in self._nodes.items() if e.is_expired()]
        for nid in expired:
            del self._nodes[nid]

    def _evict_oldest(self) -> None:
        if not self._nodes:
            return
        oldest = min(self._nodes.values(), key=lambda e: e.registered_at)
        del self._nodes[oldest.node_id]
