"""ContributionLedger — per-node HMAC-chained deque for fairness ratio computation.

Design:
  - Per-node deque:     dict[node_id, deque[_Entry]] — O(1) append, O(1) front-eviction.
  - Per-node lock:      concurrent writes from different nodes do not contend.
  - _meta_lock:         serialises node-registry mutations (creating new deques/locks).
  - get_ratio():        iterates newest-first, stops at first entry older than window —
                        O(entries within window) instead of O(all entries).
  - Auto-compaction:    triggered per-node when deque exceeds max_entries_per_node;
                        popleft() from the front is O(1) per removed entry.
  - HMAC chain:         per-node, re-rooted at the HMAC of the last removed entry after
                        compaction so verify_chain() stays valid across compactions.
  - record_receipt():   acquires two node-locks in sorted-ID order to prevent deadlock.

Long-running safety:
  Old entries accumulate only until auto-compact fires.  At 1000 req/h per node,
  50_000 entries cover ~50 hours before the first compaction.  After compaction,
  only entries within the last 2*default_window are retained.
"""
from __future__ import annotations

import collections
import hashlib
import hmac
import json
import threading
import time
from typing import NamedTuple

from .receipt import ServiceReceipt

_DEFAULT_MAX_ENTRIES_PER_NODE: int = 50_000


class _Entry(NamedTuple):
    seq: int       # per-node sequence number
    node_id: str
    event: str     # "served" | "consumed"
    task_id: str
    timestamp: float
    entry_hmac: str


class ContributionLedger:
    """Per-node HMAC-chained ledger for fairness ratio computation.

    Args:
        hmac_key:              Secret for chain integrity. Must stay constant.
        default_window:        Time window in seconds for ratio queries (default: 1 h).
        max_entries_per_node:  Auto-compact threshold per node. Set to 0 to disable.
    """

    def __init__(
        self,
        hmac_key: bytes,
        default_window: float = 3600.0,
        max_entries_per_node: int = _DEFAULT_MAX_ENTRIES_PER_NODE,
    ) -> None:
        self._key = hmac_key
        self._default_window = default_window
        self._max_per_node = max_entries_per_node

        # Per-node storage
        self._by_node: dict[str, collections.deque[_Entry]] = {}
        # Per-node HMAC chain state
        self._prev_hmac: dict[str, str] = {}
        self._compaction_root: dict[str, str] = {}
        # Per-node sequence counters (monotonic, never reset)
        self._seq: dict[str, int] = {}
        # Per-node locks: concurrent nodes don't contend on each other
        self._node_locks: dict[str, threading.Lock] = {}
        # Meta-lock: serialises node-registry creation only
        self._meta_lock = threading.Lock()

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record_served(
        self, node_id: str, task_id: str, timestamp: float | None = None
    ) -> None:
        """Record that node_id served a request."""
        lock = self._node_lock(node_id)
        with lock:
            self._append_locked(node_id, "served", task_id, timestamp)

    def record_consumed(
        self, node_id: str, task_id: str, timestamp: float | None = None
    ) -> None:
        """Record that node_id consumed a request."""
        lock = self._node_lock(node_id)
        with lock:
            self._append_locked(node_id, "consumed", task_id, timestamp)

    def record_receipt(self, receipt: ServiceReceipt) -> None:
        """Record both sides of a receipt; acquire node locks in sorted order
        to prevent deadlock when server_node_id == client_node_id is impossible
        but still guarantees consistent ordering for distinct IDs.
        """
        server_id = receipt.server_node_id
        client_id = receipt.client_node_id
        ts = receipt.timestamp

        # Ensure both node locks exist before acquiring
        self._node_lock(server_id)
        self._node_lock(client_id)

        # Acquire in sorted order to prevent deadlock
        first_id, second_id = sorted([server_id, client_id])
        lock1 = self._node_lock(first_id)
        lock2 = self._node_lock(second_id)

        with lock1:
            with lock2:
                self._append_locked(server_id, "served",   receipt.task_id, ts)
                self._append_locked(client_id, "consumed", receipt.task_id, ts)

    # ------------------------------------------------------------------
    # Ratio computation
    # ------------------------------------------------------------------

    def get_ratio(self, node_id: str, window: float | None = None) -> float:
        """Return contribution_ratio = served / consumed within the time window.

        Iterates the deque newest-first and stops at the first entry older than
        the window cutoff — O(entries within window) per call.

        Returns 1.0 (neutral) when the node has no consumed events in the window.
        """
        w = window if window is not None else self._default_window
        cutoff = time.time() - w
        served = consumed = 0

        lock = self._node_lock(node_id)
        with lock:
            dq = self._by_node.get(node_id)
            if dq is None:
                return 1.0
            for e in reversed(dq):
                if e.timestamp < cutoff:
                    break
                if e.event == "served":
                    served += 1
                else:
                    consumed += 1

        if consumed == 0:
            return 1.0
        return served / consumed

    # ------------------------------------------------------------------
    # Compaction
    # ------------------------------------------------------------------

    def compact(
        self, max_age_seconds: float | None = None, node_id: str | None = None
    ) -> int:
        """Remove entries older than max_age_seconds. Returns total count removed.

        Args:
            max_age_seconds: Defaults to default_window * 2.
            node_id:         Compact only this node. Compacts all nodes when None.
        """
        age = max_age_seconds if max_age_seconds is not None else self._default_window * 2
        if node_id is not None:
            lock = self._node_lock(node_id)
            with lock:
                return self._compact_node_locked(node_id, age)
        # Compact all nodes — acquire each lock individually (no cross-node deadlock risk)
        total = 0
        with self._meta_lock:
            node_ids = list(self._by_node.keys())
        for nid in node_ids:
            lock = self._node_lock(nid)
            with lock:
                total += self._compact_node_locked(nid, age)
        return total

    # ------------------------------------------------------------------
    # Chain verification
    # ------------------------------------------------------------------

    def verify_chain(self, node_id: str | None = None) -> bool:
        """Verify HMAC chain integrity.

        Args:
            node_id: Verify only this node's chain. Verifies all when None.
        """
        if node_id is not None:
            lock = self._node_lock(node_id)
            with lock:
                return self._verify_node_locked(node_id)
        with self._meta_lock:
            node_ids = list(self._by_node.keys())
        for nid in node_ids:
            lock = self._node_lock(nid)
            with lock:
                if not self._verify_node_locked(nid):
                    return False
        return True

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def entry_count(self, node_id: str | None = None) -> int:
        """Return entry count for one node, or total across all nodes."""
        if node_id is not None:
            lock = self._node_lock(node_id)
            with lock:
                dq = self._by_node.get(node_id)
                return len(dq) if dq else 0
        with self._meta_lock:
            node_ids = list(self._by_node.keys())
        total = 0
        for nid in node_ids:
            lock = self._node_lock(nid)
            with lock:
                dq = self._by_node.get(nid)
                if dq:
                    total += len(dq)
        return total

    def known_nodes(self) -> list[str]:
        """Return all node IDs currently tracked."""
        with self._meta_lock:
            return list(self._by_node.keys())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _node_lock(self, node_id: str) -> threading.Lock:
        """Return the per-node lock, creating it under meta_lock if needed."""
        # Fast path: lock already exists (no meta_lock needed)
        lock = self._node_locks.get(node_id)
        if lock is not None:
            return lock
        with self._meta_lock:
            if node_id not in self._node_locks:
                self._node_locks[node_id] = threading.Lock()
                self._by_node[node_id] = collections.deque()
                self._prev_hmac[node_id] = "0" * 64
                self._compaction_root[node_id] = "0" * 64
                self._seq[node_id] = 0
            return self._node_locks[node_id]

    def _append_locked(
        self, node_id: str, event: str, task_id: str, timestamp: float | None
    ) -> None:
        """Append one entry. Caller must hold this node's lock."""
        ts = timestamp if timestamp is not None else time.time()
        seq = self._seq[node_id]
        self._seq[node_id] = seq + 1
        body = self._entry_body(seq, node_id, event, task_id, ts)
        prev = self._prev_hmac[node_id]
        entry_hmac = hmac.new(
            self._key, (prev + body).encode(), hashlib.sha256
        ).hexdigest()
        entry = _Entry(seq, node_id, event, task_id, ts, entry_hmac)
        self._by_node[node_id].append(entry)
        self._prev_hmac[node_id] = entry_hmac

        # Auto-compact when deque exceeds per-node limit
        if self._max_per_node > 0 and len(self._by_node[node_id]) > self._max_per_node:
            self._compact_node_locked(node_id, self._default_window * 2)

    def _compact_node_locked(self, node_id: str, max_age_seconds: float) -> int:
        """Evict front entries older than max_age_seconds. Caller holds node's lock."""
        dq = self._by_node.get(node_id)
        if not dq:
            return 0
        cutoff = time.time() - max_age_seconds
        removed = 0
        while dq and dq[0].timestamp < cutoff:
            evicted = dq.popleft()
            self._compaction_root[node_id] = evicted.entry_hmac
            removed += 1
        return removed

    def _verify_node_locked(self, node_id: str) -> bool:
        """Verify HMAC chain for one node. Caller holds node's lock."""
        dq = self._by_node.get(node_id)
        if not dq:
            return True
        prev = self._compaction_root.get(node_id, "0" * 64)
        for entry in dq:
            body = self._entry_body(
                entry.seq, entry.node_id, entry.event, entry.task_id, entry.timestamp
            )
            expected = hmac.new(
                self._key, (prev + body).encode(), hashlib.sha256
            ).hexdigest()
            if not hmac.compare_digest(expected, entry.entry_hmac):
                return False
            prev = entry.entry_hmac
        return True

    @staticmethod
    def _entry_body(
        seq: int, node_id: str, event: str, task_id: str, timestamp: float
    ) -> str:
        return json.dumps(
            {
                "event": event,
                "node_id": node_id,
                "seq": seq,
                "task_id": task_id,
                "timestamp": timestamp,
            },
            sort_keys=True,
            ensure_ascii=False,
        )
