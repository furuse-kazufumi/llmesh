"""Peer reputation for skill chunk distribution (RFC Phase 3.6b).

Per-peer trust score derived from a rolling window of:

* ``transfers``    — successful chunk pulls from the peer
* ``corruptions``  — integrity failures reported against the peer

The score is defined as::

    score = 1 - (corruption_count / max(1, transfer_count))   (clamped to [0, 1])

Higher is better. ``verdict()`` maps the score to a triage state:

* ``trusted``  — score >= warn_threshold (default 0.7)
* ``warn``     — warn_threshold > score >= block_threshold (default 0.5)
* ``blocked``  — score < block_threshold

The 30-day default window matches the RFC §Security recommendation.
SQLite is the persistence layer (stdlib only) so the score survives
process restarts. The class is thread-safe via an RLock.

This module deliberately stays decoupled from the router / sync layer:
producers call ``record_transfer`` / ``record_corruption`` explicitly,
and gossip schedulers feed `peer_provider` output through
``reputation_filtered`` to drop blocked peers.
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)

Verdict = Literal["trusted", "warn", "blocked"]

_DEFAULT_WINDOW_S = 30 * 24 * 60 * 60  # 30 days
_DEFAULT_WARN = 0.7
_DEFAULT_BLOCK = 0.5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS transfers (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id     TEXT NOT NULL,
    occurred_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transfers_peer ON transfers(peer_id, occurred_at);

CREATE TABLE IF NOT EXISTS corruptions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    peer_id     TEXT NOT NULL,
    occurred_at REAL NOT NULL,
    reporter    TEXT NOT NULL DEFAULT '',
    skill_id    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_corruptions_peer ON corruptions(peer_id, occurred_at);
"""


@dataclass(frozen=True)
class PeerStats:
    """Snapshot of one peer's tally inside the active window."""

    peer_id: str
    transfers: int
    corruptions: int
    score: float
    verdict: Verdict


class PeerReputation:
    """SQLite-backed rolling-window peer reputation tracker."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        window_s: int = _DEFAULT_WINDOW_S,
        warn_threshold: float = _DEFAULT_WARN,
        block_threshold: float = _DEFAULT_BLOCK,
        clock: "callable[[], float] | None" = None,  # type: ignore[type-arg]
    ) -> None:
        if not 0.0 <= block_threshold <= warn_threshold <= 1.0:
            raise ValueError(
                f"thresholds must satisfy 0 <= block ({block_threshold}) "
                f"<= warn ({warn_threshold}) <= 1"
            )
        self._window_s = int(window_s)
        self._warn = float(warn_threshold)
        self._block = float(block_threshold)
        self._clock = clock or time.time
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            ":memory:" if db_path is None else str(db_path),
            isolation_level=None,
            check_same_thread=False,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record_transfer(self, peer_id: str) -> None:
        """Log one successful chunk pull from ``peer_id``."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO transfers(peer_id, occurred_at) VALUES(?, ?)",
                (peer_id, self._clock()),
            )

    def record_corruption(
        self,
        peer_id: str,
        *,
        reporter: str = "",
        skill_id: str = "",
    ) -> None:
        """Log one integrity failure attributed to ``peer_id``."""
        with self._lock:
            self._conn.execute(
                "INSERT INTO corruptions(peer_id, occurred_at, reporter, skill_id) "
                "VALUES(?, ?, ?, ?)",
                (peer_id, self._clock(), reporter, skill_id),
            )

    def prune(self) -> int:
        """Delete entries older than the active window. Returns rows removed."""
        cutoff = self._clock() - self._window_s
        with self._lock:
            t = self._conn.execute(
                "DELETE FROM transfers WHERE occurred_at < ?", (cutoff,)
            )
            c = self._conn.execute(
                "DELETE FROM corruptions WHERE occurred_at < ?", (cutoff,)
            )
            return int(t.rowcount or 0) + int(c.rowcount or 0)

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def score(self, peer_id: str) -> float:
        """Return ``peer_id``'s in-window reputation score in [0, 1]."""
        stats = self.stats(peer_id)
        return stats.score

    def verdict(self, peer_id: str) -> Verdict:
        return self.stats(peer_id).verdict

    def stats(self, peer_id: str) -> PeerStats:
        cutoff = self._clock() - self._window_s
        with self._lock:
            t_row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM transfers WHERE peer_id = ? AND occurred_at >= ?",
                (peer_id, cutoff),
            ).fetchone()
            c_row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM corruptions WHERE peer_id = ? AND occurred_at >= ?",
                (peer_id, cutoff),
            ).fetchone()
        transfers = int(t_row["n"]) if t_row else 0
        corruptions = int(c_row["n"]) if c_row else 0
        # Unknown peers start out trusted — the alternative (block on no data)
        # would forbid joining the mesh.
        if transfers == 0 and corruptions == 0:
            score = 1.0
        else:
            score = 1.0 - (corruptions / max(1, transfers))
            score = max(0.0, min(1.0, score))
        return PeerStats(
            peer_id=peer_id,
            transfers=transfers,
            corruptions=corruptions,
            score=score,
            verdict=self._verdict_of(score),
        )

    def _verdict_of(self, score: float) -> Verdict:
        if score < self._block:
            return "blocked"
        if score < self._warn:
            return "warn"
        return "trusted"

    def reputation_filtered(self, peers: Iterable[str]) -> list[str]:
        """Drop peers whose verdict is ``blocked``. Order preserved.

        Warns at INFO level for ``warn`` verdicts but keeps them — RFC
        recommends "warn but don't drop" for the middle band so operators
        retain visibility before a peer is fully excluded.
        """
        out: list[str] = []
        for peer in peers:
            v = self.verdict(peer)
            if v == "blocked":
                logger.info("peer %s blocked by reputation", peer)
                continue
            if v == "warn":
                logger.info("peer %s flagged (warn) by reputation", peer)
            out.append(peer)
        return out

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = [
    "PeerReputation",
    "PeerStats",
    "Verdict",
]
