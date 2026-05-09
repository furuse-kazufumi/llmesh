"""GossipClient — periodic peer exchange over /registry/peers.

Flow (every interval_s seconds):
  For each known peer endpoint:
    1. GET {endpoint}/registry/peers
    2. For each returned peer:
       a. Verify the signed CapabilityManifest using the provided public_key_hex
       b. public_key_hex is self-certifying: the manifest signature proves key ownership
       c. If new → add to TrustedPeers (source="gossip:<introducer>") + NodeRegistry
"""
from __future__ import annotations

import json
import logging
import ssl
import threading
import urllib.error
import urllib.request
from typing import TYPE_CHECKING

from ..auth.trusted_peers import TrustedPeers
from ..identity.manifest import CapabilityManifest, ManifestVerificationError
from .registry import NodeRegistry, RegistryError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_DEFAULT_INTERVAL = 60   # seconds
_DEFAULT_TIMEOUT  = 10   # seconds per HTTP call


class GossipClient:
    """Background thread that periodically pulls peers from known nodes.

    Security model:
    - Each shared peer advertisement includes a signed CapabilityManifest.
    - The manifest is signed with the peer's Ed25519 private key.
    - Verification uses only the public_key_hex embedded in the advertisement
      (self-certifying: proves the signer owns the key, not that the key is
      legitimate).
    - Trust transitivity: A trusts B (manual) → B shares C → A auto-trusts C.
      This is intentional for convenience; operators who want strict control
      should set gossip_enabled=False and manage trusted_peers.json manually.
    """

    def __init__(
        self,
        peers: TrustedPeers,
        registry: NodeRegistry,
        interval_s: int = _DEFAULT_INTERVAL,
        timeout_s: int = _DEFAULT_TIMEOUT,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._peers = peers
        self._registry = registry
        self._interval = interval_s
        self._timeout = timeout_s
        self._ssl_ctx = ssl_context
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the gossip background thread."""
        self._thread = threading.Thread(
            target=self._loop, name="llmesh-gossip", daemon=True
        )
        self._thread.start()
        logger.info("GossipClient started (interval=%ds)", self._interval)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.wait(timeout=self._interval):
            self._run_once()

    def run_once(self) -> None:
        """Public hook for tests / manual triggering."""
        self._run_once()

    def _run_once(self) -> None:
        for peer in self._peers.all_peers():
            try:
                self._pull_from(peer.node_id, peer.endpoint)
            except Exception as exc:
                logger.debug("gossip pull from %s failed: %s", peer.node_id, exc)

    def _pull_from(self, introducer_id: str, endpoint: str) -> None:
        from llmesh.security.http_limits import (
            DEFAULT_GOSSIP_RESPONSE_BYTES,
            ResponseTooLargeError,
            read_capped,
        )
        url = endpoint.rstrip("/") + "/registry/peers"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(  # nosec B310 - peer URL is signed/verified upstream; response capped.
                req, timeout=self._timeout, context=self._ssl_ctx
            ) as resp:
                body = json.loads(
                    read_capped(resp, max_bytes=DEFAULT_GOSSIP_RESPONSE_BYTES)
                )
        except ResponseTooLargeError as exc:
            logger.warning("gossip: %s response too large (cap=%d)", endpoint, exc.cap)
            return
        except urllib.error.URLError as exc:
            logger.debug("gossip: cannot reach %s: %s", endpoint, exc)
            return

        peers_list = body.get("peers", [])
        new_count = 0
        for item in peers_list:
            if self._ingest(item, introducer_id):
                new_count += 1
        if new_count:
            logger.info("gossip: learned %d new peer(s) from %s", new_count, introducer_id)

    def _ingest(self, item: dict, introducer_id: str) -> bool:
        """Return True if a genuinely new peer was added."""
        node_id      = item.get("node_id", "")
        pub_hex      = item.get("public_key_hex", "")
        endpoint     = item.get("endpoint", "")
        manifest_raw = item.get("manifest", {})

        if not (node_id and pub_hex and endpoint and manifest_raw):
            return False

        # Already known → skip
        if self._peers.is_trusted(node_id):
            return False

        # Verify manifest signature — proves the node owns the key
        try:
            manifest = CapabilityManifest.from_dict(manifest_raw)
            manifest.verify(pub_hex=pub_hex)
        except (ManifestVerificationError, Exception) as exc:
            logger.warning("gossip: rejected %s (bad manifest: %s)", node_id, exc)
            return False

        # Add to TrustedPeers (persisted)
        self._peers.add_gossip(
            node_id=node_id,
            public_key_hex=pub_hex,
            did=manifest_raw.get("did", ""),
            endpoint=endpoint,
            introduced_by=manifest_raw.get("did", introducer_id),
        )

        # Register in in-memory registry (best-effort)
        try:
            self._registry.register(manifest_raw, endpoint, pub_hex)
        except RegistryError as exc:
            logger.debug("gossip: registry.register(%s) failed: %s", node_id, exc)

        logger.info("gossip: added new peer %s via %s", node_id, introducer_id)
        return True
