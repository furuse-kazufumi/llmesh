"""TrustedPeers — JSON-backed registry of Ed25519 public keys for known nodes.

Format of trusted_peers.json:
{
  "<node_id>": {
    "public_key_hex": "<64-char hex>",
    "did":            "did:llmesh:1:z...",
    "endpoint":       "https://192.168.1.2:8001",
    "fingerprint":    "ab:cd:ef:...",
    "source":         "manual" | "gossip:<node_id>",
    "added_at":       "2026-05-05T10:00:00Z"
  }
}
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class PeerInfo:
    node_id: str
    public_key_hex: str
    did: str
    endpoint: str
    fingerprint: str
    source: str   # "manual" or "gossip:<node_id>"
    added_at: str

    def to_dict(self) -> dict:
        return {
            "public_key_hex": self.public_key_hex,
            "did": self.did,
            "endpoint": self.endpoint,
            "fingerprint": self.fingerprint,
            "source": self.source,
            "added_at": self.added_at,
        }


def _fingerprint(pub_hex: str) -> str:
    """SHA-256 fingerprint of public key bytes, colon-separated hex pairs."""
    raw = bytes.fromhex(pub_hex)
    digest = hashlib.sha256(raw).hexdigest()
    return ":".join(digest[i:i+2] for i in range(0, 32, 2))  # first 16 bytes


class TrustedPeers:
    """Thread-safe, JSON-persisted trusted peer registry.

    New peers (from gossip) are appended atomically via write-then-rename
    so a crash mid-write never corrupts the file.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        self._peers: dict[str, PeerInfo] = {}
        if self._path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Read API
    # ------------------------------------------------------------------

    def get(self, node_id: str) -> PeerInfo | None:
        with self._lock:
            return self._peers.get(node_id)

    def is_trusted(self, node_id: str) -> bool:
        with self._lock:
            return node_id in self._peers

    def all_peers(self) -> list[PeerInfo]:
        with self._lock:
            return list(self._peers.values())

    def __iter__(self) -> Iterator[PeerInfo]:
        return iter(self.all_peers())

    def __len__(self) -> int:
        with self._lock:
            return len(self._peers)

    # ------------------------------------------------------------------
    # Write API
    # ------------------------------------------------------------------

    def add(
        self,
        node_id: str,
        public_key_hex: str,
        did: str,
        endpoint: str,
        source: str = "manual",
    ) -> PeerInfo:
        """Add or overwrite a peer entry and persist to disk."""
        peer = PeerInfo(
            node_id=node_id,
            public_key_hex=public_key_hex,
            did=did,
            endpoint=endpoint,
            fingerprint=_fingerprint(public_key_hex),
            source=source,
            added_at=datetime.now(timezone.utc).isoformat(),
        )
        with self._lock:
            self._peers[node_id] = peer
            self._save()
        return peer

    def remove(self, node_id: str) -> bool:
        with self._lock:
            removed = self._peers.pop(node_id, None) is not None
            if removed:
                self._save()
        return removed

    def reload(self) -> None:
        """Re-read the JSON file (e.g. after manual edit)."""
        with self._lock:
            self._load()

    # ------------------------------------------------------------------
    # Persistence (must be called with self._lock held)
    # ------------------------------------------------------------------

    def _load(self) -> None:
        raw: dict = json.loads(self._path.read_text(encoding="utf-8"))
        self._peers = {}
        for node_id, info in raw.items():
            self._peers[node_id] = PeerInfo(
                node_id=node_id,
                public_key_hex=info["public_key_hex"],
                did=info.get("did", ""),
                endpoint=info.get("endpoint", ""),
                fingerprint=info.get("fingerprint", _fingerprint(info["public_key_hex"])),
                source=info.get("source", "manual"),
                added_at=info.get("added_at", ""),
            )

    def _save(self) -> None:
        """Atomic write via temp-file rename."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(".tmp")
        data = {nid: p.to_dict() for nid, p in self._peers.items()}
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create_empty(cls, path: str | Path) -> "TrustedPeers":
        """Create a new empty TrustedPeers file."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}\n", encoding="utf-8")
        return cls(p)
