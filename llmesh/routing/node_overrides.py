"""NodeOverrides — per-node manual overrides for blocking and priority pinning.

Blocked nodes:
    Filtered out before any other selector logic.  Circuit breaker state and
    fairness scores are ignored — the node never receives requests.

Pinned nodes:
    Bypass fairness filtering and are sorted to the front of the candidate
    list so they are always preferred over unpinned nodes.

Persistence:
    Overrides survive process restarts via an optional JSON file.  The file is
    written atomically (write temp + rename) to avoid corruption on crash.

Security invariants:
- No shell=True, eval, exec, pickle anywhere
- Thread-safe: all state mutations under _lock
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path


class NodeOverrides:
    """Per-node blocking and pinning overrides.

    Args:
        path: Optional path to a JSON file for persistence.
              When None, overrides are in-memory only.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._path = Path(path) if path else None
        self._blocked: dict[str, dict] = {}   # node_id -> {reason, blocked_at}
        self._pinned:  dict[str, dict] = {}   # node_id -> {label, pinned_at}
        self._lock = threading.Lock()
        if self._path and self._path.exists():
            self._load()

    # ------------------------------------------------------------------
    # Blocking
    # ------------------------------------------------------------------

    def block(self, node_id: str, reason: str = "") -> None:
        """Block a node from receiving any requests.

        Unsets any existing pin — a node cannot be both blocked and pinned.
        """
        with self._lock:
            self._blocked[node_id] = {"reason": reason, "blocked_at": time.time()}
            self._pinned.pop(node_id, None)
        self._save()

    def unblock(self, node_id: str) -> None:
        """Remove manual block from a node."""
        with self._lock:
            existed = self._blocked.pop(node_id, None)
        if existed is not None:
            self._save()

    def is_blocked(self, node_id: str) -> bool:
        """Return True if the node is manually blocked."""
        with self._lock:
            return node_id in self._blocked

    def blocked_nodes(self) -> dict[str, dict]:
        """Return a deep snapshot of all blocked nodes and their metadata."""
        with self._lock:
            return {k: dict(v) for k, v in self._blocked.items()}

    # ------------------------------------------------------------------
    # Pinning (priority + fairness bypass)
    # ------------------------------------------------------------------

    def pin(self, node_id: str, label: str = "") -> None:
        """Pin a node as priority — bypasses fairness and sorts first.

        Removes any existing block — a node cannot be both blocked and pinned.
        """
        with self._lock:
            self._blocked.pop(node_id, None)
            self._pinned[node_id] = {"label": label, "pinned_at": time.time()}
        self._save()

    def unpin(self, node_id: str) -> None:
        """Remove priority pin from a node."""
        with self._lock:
            existed = self._pinned.pop(node_id, None)
        if existed is not None:
            self._save()

    def is_pinned(self, node_id: str) -> bool:
        """Return True if the node has a priority pin."""
        with self._lock:
            return node_id in self._pinned

    def pinned_nodes(self) -> dict[str, dict]:
        """Return a deep snapshot of all pinned nodes and their metadata."""
        with self._lock:
            return {k: dict(v) for k, v in self._pinned.items()}

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Load overrides from JSON file (called once at init, lock not held)."""
        try:
            data = json.loads(self._path.read_text())  # type: ignore[union-attr]
            self._blocked = data.get("blocked", {})
            self._pinned  = data.get("pinned", {})
        except (json.JSONDecodeError, OSError):
            pass

    def _save(self) -> None:
        """Write overrides to JSON file atomically (lock not held; data copied before)."""
        if self._path is None:
            return
        with self._lock:
            data = {
                "blocked": dict(self._blocked),
                "pinned":  dict(self._pinned),
            }
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, self._path)
        except OSError:
            tmp.unlink(missing_ok=True)
