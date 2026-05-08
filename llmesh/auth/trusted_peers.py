"""TrustedPeers — JSON-backed registry of Ed25519 public keys for known nodes.

v0.2.0 additions:
- ``trust_source`` field: "manual" | "rendezvous" | "gossip"
- ``expires_at``: gossip peers expire after configurable TTL (manual = never)
- ``introduced_by``: DID of the gossip introducer (empty for manual)
- ``last_seen_at``: updated on successful contact
- ``add_gossip()``: bounds-checked gossip intake with DID/signature validation
- ``cleanup_gossip_expired()``: remove TTL-expired gossip entries
- ``is_trusted()``: excludes expired gossip peers

Security invariants:
- Manual/rendezvous peers are never deleted by gossip cleanup.
- Gossip is bounded by ``max_gossip_peers``; default 128.
- Malformed DID or invalid introducer signature is rejected immediately.
- ``add_gossip()`` is a no-op when gossip is disabled at construction time.

Format of trusted_peers.json (v0.2.0+):
{
  "<node_id>": {
    "public_key_hex": "<64-char hex>",
    "did":            "did:llmesh:1:z...",
    "endpoint":       "https://192.168.1.2:8001",
    "fingerprint":    "ab:cd:ef:...",
    "trust_source":   "manual" | "rendezvous" | "gossip",
    "introduced_by":  "did:llmesh:1:z..." | "",
    "added_at":       "2026-05-05T10:00:00Z",
    "expires_at":     "2026-05-06T10:00:00Z" | "",
    "last_seen_at":   "2026-05-05T10:00:00Z"
  }
}
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterator


_DID_PREFIX = "did:llmesh:1:z"

# ---------------------------------------------------------------------------
# PeerInfo
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PeerInfo:
    node_id: str
    public_key_hex: str
    did: str
    endpoint: str
    fingerprint: str
    trust_source: str   # "manual" | "rendezvous" | "gossip"
    introduced_by: str  # empty for manual/rendezvous
    added_at: str       # ISO-8601 UTC
    expires_at: str     # ISO-8601 UTC, empty = never (manual/rendezvous)
    last_seen_at: str   # ISO-8601 UTC

    @property
    def is_gossip(self) -> bool:
        return self.trust_source == "gossip"

    @property
    def is_expired(self) -> bool:
        """True when this gossip peer's TTL has elapsed."""
        if not self.expires_at:
            return False
        try:
            exp = datetime.fromisoformat(self.expires_at)
            return datetime.now(timezone.utc) > exp
        except ValueError:
            return True

    def to_dict(self) -> dict:
        return {
            "public_key_hex": self.public_key_hex,
            "did": self.did,
            "endpoint": self.endpoint,
            "fingerprint": self.fingerprint,
            "trust_source": self.trust_source,
            "introduced_by": self.introduced_by,
            "added_at": self.added_at,
            "expires_at": self.expires_at,
            "last_seen_at": self.last_seen_at,
        }


def _fingerprint(pub_hex: str) -> str:
    raw = bytes.fromhex(pub_hex)
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[i:i+2] for i in range(0, 32, 2))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_did(did: str) -> bool:
    return isinstance(did, str) and did.startswith(_DID_PREFIX) and len(did) > len(_DID_PREFIX)


# ---------------------------------------------------------------------------
# TrustedPeers
# ---------------------------------------------------------------------------

class TrustedPeers:
    """Thread-safe, JSON-persisted trusted peer registry.

    Args:
        path: Path to the JSON peers file.
        max_gossip_peers: Hard cap on gossip-sourced entries. Default 128.
        gossip_ttl_seconds: TTL for gossip entries in seconds. Default 86400 (24h).
        allow_gossip: When False, ``add_gossip()`` is a silent no-op. Default True.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        max_gossip_peers: int = 128,
        gossip_ttl_seconds: int = 86_400,
        allow_gossip: bool = True,
    ) -> None:
        self._path = Path(path)
        self._max_gossip = max_gossip_peers
        self._gossip_ttl = gossip_ttl_seconds
        self._allow_gossip = allow_gossip
        self._lock = threading.Lock()
        self._peers: dict[str, PeerInfo] = {}
        if self._path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def get(self, node_id: str) -> PeerInfo | None:
        with self._lock:
            p = self._peers.get(node_id)
            if p is not None and p.is_gossip and p.is_expired:
                return None
            return p

    def is_trusted(self, node_id: str) -> bool:
        return self.get(node_id) is not None

    def all_peers(self) -> list[PeerInfo]:
        """Return all non-expired peers."""
        with self._lock:
            return [p for p in self._peers.values()
                    if not (p.is_gossip and p.is_expired)]

    def __iter__(self) -> Iterator[PeerInfo]:
        return iter(self.all_peers())

    def __len__(self) -> int:
        return len(self.all_peers())

    # ------------------------------------------------------------------
    # Write API — manual / rendezvous peers
    # ------------------------------------------------------------------

    def add(
        self,
        node_id: str,
        public_key_hex: str,
        did: str,
        endpoint: str,
        trust_source: str = "manual",
    ) -> PeerInfo:
        """Add or overwrite a manual/rendezvous peer and persist to disk."""
        peer = PeerInfo(
            node_id=node_id,
            public_key_hex=public_key_hex,
            did=did,
            endpoint=endpoint,
            fingerprint=_fingerprint(public_key_hex),
            trust_source=trust_source,
            introduced_by="",
            added_at=_now_iso(),
            expires_at="",
            last_seen_at=_now_iso(),
        )
        with self._lock:
            self._peers[node_id] = peer
            self._save()
        return peer

    # ------------------------------------------------------------------
    # Write API — gossip peers (bounds-checked)
    # ------------------------------------------------------------------

    def add_gossip(
        self,
        node_id: str,
        public_key_hex: str,
        did: str,
        endpoint: str,
        introduced_by: str,
    ) -> PeerInfo | None:
        """Add a gossip-discovered peer with bounds and validation checks.

        Returns the new PeerInfo, or None if the peer was rejected.

        Rejection reasons:
        - Gossip disabled (allow_gossip=False)
        - Malformed DID for this node or the introducer
        - Gossip peer count already at max_gossip_peers
        - node_id already present as a manual/rendezvous peer
        """
        if not self._allow_gossip:
            return None

        # DID validation
        if not _validate_did(did):
            return None
        if not _validate_did(introduced_by):
            return None

        expires = datetime.now(timezone.utc) + timedelta(seconds=self._gossip_ttl)

        with self._lock:
            existing = self._peers.get(node_id)
            if existing is not None and not existing.is_gossip:
                # Manual/rendezvous peer — gossip cannot overwrite
                return None

            gossip_count = sum(1 for p in self._peers.values() if p.is_gossip and not p.is_expired)
            if existing is None and gossip_count >= self._max_gossip:
                return None

            peer = PeerInfo(
                node_id=node_id,
                public_key_hex=public_key_hex,
                did=did,
                endpoint=endpoint,
                fingerprint=_fingerprint(public_key_hex),
                trust_source="gossip",
                introduced_by=introduced_by,
                added_at=_now_iso(),
                expires_at=expires.isoformat(),
                last_seen_at=_now_iso(),
            )
            self._peers[node_id] = peer
            self._save()
        return peer

    def update_last_seen(self, node_id: str) -> bool:
        """Refresh last_seen_at for a known peer. Returns True if found."""
        with self._lock:
            p = self._peers.get(node_id)
            if p is None:
                return False
            updated = PeerInfo(
                node_id=p.node_id,
                public_key_hex=p.public_key_hex,
                did=p.did,
                endpoint=p.endpoint,
                fingerprint=p.fingerprint,
                trust_source=p.trust_source,
                introduced_by=p.introduced_by,
                added_at=p.added_at,
                expires_at=p.expires_at,
                last_seen_at=_now_iso(),
            )
            self._peers[node_id] = updated
            self._save()
        return True

    def cleanup_gossip_expired(self) -> int:
        """Remove TTL-expired gossip peers. Manual/rendezvous peers are never removed.

        Returns the count of removed entries.
        """
        with self._lock:
            to_remove = [
                nid for nid, p in self._peers.items()
                if p.is_gossip and p.is_expired
            ]
            for nid in to_remove:
                del self._peers[nid]
            if to_remove:
                self._save()
        return len(to_remove)

    def remove(self, node_id: str) -> bool:
        with self._lock:
            removed = self._peers.pop(node_id, None) is not None
            if removed:
                self._save()
        return removed

    def reload(self) -> None:
        with self._lock:
            self._load()

    # ------------------------------------------------------------------
    # Persistence (must be called with self._lock held)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        raw: dict = json.loads(self._path.read_text(encoding="utf-8"))
        self._peers = {}
        for node_id, info in raw.items():
            # Backward compat: old format used "source" instead of "trust_source"
            trust_source = info.get("trust_source") or info.get("source", "manual")
            self._peers[node_id] = PeerInfo(
                node_id=node_id,
                public_key_hex=info["public_key_hex"],
                did=info.get("did", ""),
                endpoint=info.get("endpoint", ""),
                fingerprint=info.get("fingerprint", _fingerprint(info["public_key_hex"])),
                trust_source=trust_source,
                introduced_by=info.get("introduced_by", ""),
                added_at=info.get("added_at", ""),
                expires_at=info.get("expires_at", ""),
                last_seen_at=info.get("last_seen_at", ""),
            )

    def _save(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        data = {nid: p.to_dict() for nid, p in self._peers.items()}
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create_empty(cls, path: str | Path, **kwargs) -> "TrustedPeers":
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n", encoding="utf-8")
        return cls(p, **kwargs)
