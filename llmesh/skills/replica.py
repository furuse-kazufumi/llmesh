"""SkillReplica — LRU + popularity tiered local replica store (RFC Phase 3.2).

`SkillReplica` keeps a local copy of received SkillChunks across two
in-process tiers:

  * **hot** — kept in RAM (`_hot`), LRU-evicted to *warm* when total size
    exceeds ``hot_mb``.
  * **warm** — persisted under ``root/warm/<prefix>/<skill_id>.json``,
    LRU-evicted (deleted) when total size exceeds ``warm_gb``.

A SQLite index at ``root/_index.sqlite`` tracks per-chunk metadata
(``last_access``, ``hit_count``, ``tier``, ``size_bytes``). The index also
serves the `index()` query used by gossip.

Popularity = ``hit_count * exp(-age_hours / decay_hours)``. Used by
``evict()`` as a tie-breaker against pure LRU when picking what to drop.

This module deliberately stays simple: no async, no networking. Phase 3.3+
will wrap it in an HTTP layer.
"""
from __future__ import annotations

import json
import math
import sqlite3
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from llmesh.skills.chunk import SkillChunk

_HOT_BYTES_DEFAULT = 100 * 1024 * 1024
_WARM_BYTES_DEFAULT = 1024 * 1024 * 1024
_DECAY_HOURS_DEFAULT = 24.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS chunks (
    skill_id     TEXT PRIMARY KEY,
    version      TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL,
    tier         TEXT NOT NULL,
    last_access  REAL NOT NULL,
    hit_count    INTEGER NOT NULL DEFAULT 0,
    received_at  REAL NOT NULL,
    content_sha  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tier ON chunks(tier);
"""


@dataclass(frozen=True)
class EvictionResult:
    """Summary of one evict() pass."""

    demoted_hot_to_warm: int
    deleted_warm: int
    bytes_freed_hot: int
    bytes_freed_warm: int


class SkillReplica:
    """LRU + popularity store for SkillChunks."""

    def __init__(
        self,
        root: Path | str,
        *,
        hot_bytes: int = _HOT_BYTES_DEFAULT,
        warm_bytes: int = _WARM_BYTES_DEFAULT,
        decay_hours: float = _DECAY_HOURS_DEFAULT,
    ) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._warm_dir = self.root / "warm"
        self._warm_dir.mkdir(parents=True, exist_ok=True)
        self.hot_bytes_cap = hot_bytes
        self.warm_bytes_cap = warm_bytes
        self.decay_hours = decay_hours
        self._lock = threading.RLock()
        self._hot: OrderedDict[str, SkillChunk] = OrderedDict()
        self._hot_size = 0
        self._conn = sqlite3.connect(str(self.root / "_index.sqlite"), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def put(self, chunk: SkillChunk) -> None:
        """Store a chunk in the hot tier. Auto-evicts if hot exceeds cap."""
        now = time.time()
        with self._lock:
            # If already present, refresh; remove from hot/warm first
            if chunk.skill_id in self._hot:
                self._hot_size -= self._hot[chunk.skill_id].size_bytes
                self._hot.pop(chunk.skill_id)
            self._delete_warm_file(chunk.skill_id)

            self._hot[chunk.skill_id] = chunk
            self._hot_size += chunk.size_bytes
            self._upsert_index(
                chunk.skill_id,
                version=chunk.version,
                size_bytes=chunk.size_bytes,
                tier="hot",
                last_access=now,
                received_at=now,
                content_sha=chunk.content_sha256,
            )
            self._enforce_hot_cap()

    # ------------------------------------------------------------------
    # Read path
    # ------------------------------------------------------------------

    def get(self, skill_id: str) -> SkillChunk | None:
        with self._lock:
            now = time.time()
            chunk = self._hot.get(skill_id)
            if chunk is not None:
                # bump LRU recency + hit_count
                self._hot.move_to_end(skill_id)
                self._bump_hit(skill_id, last_access=now)
                return chunk

            warm_path = self._warm_path(skill_id)
            if warm_path.exists():
                try:
                    chunk = SkillChunk.from_json(json.loads(warm_path.read_text(encoding="utf-8")))
                except Exception:
                    # corrupt cache → evict
                    warm_path.unlink(missing_ok=True)
                    self._delete_index(skill_id)
                    return None
                # promote back to hot
                self._hot[skill_id] = chunk
                self._hot.move_to_end(skill_id)
                self._hot_size += chunk.size_bytes
                self._upsert_index(
                    skill_id,
                    version=chunk.version,
                    size_bytes=chunk.size_bytes,
                    tier="hot",
                    last_access=now,
                    received_at=self._received_at(skill_id, fallback=now),
                    content_sha=chunk.content_sha256,
                )
                self._bump_hit(skill_id, last_access=now)
                warm_path.unlink(missing_ok=True)
                self._enforce_hot_cap()
                return chunk
            return None

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def evict(self) -> EvictionResult:
        """Run a single pass of hot-to-warm and warm-deletion if over cap."""
        with self._lock:
            before_hot = self._hot_size
            self._enforce_hot_cap()
            after_hot = self._hot_size

            before_warm_files = list(self._warm_dir.rglob("*.json"))
            before_warm_bytes = sum(p.stat().st_size for p in before_warm_files if p.exists())
            deleted = self._enforce_warm_cap()
            after_warm_files = list(self._warm_dir.rglob("*.json"))
            after_warm_bytes = sum(p.stat().st_size for p in after_warm_files if p.exists())

            return EvictionResult(
                demoted_hot_to_warm=max(0, len(before_warm_files) + len(self._hot) - len(after_warm_files) - len(self._hot)),
                deleted_warm=deleted,
                bytes_freed_hot=max(0, before_hot - after_hot),
                bytes_freed_warm=max(0, before_warm_bytes - after_warm_bytes),
            )

    def index(self) -> list[dict[str, Any]]:
        """Return all known chunks (gossip / discovery payload)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT skill_id, version, size_bytes, tier, last_access, hit_count, content_sha FROM chunks ORDER BY last_access DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def popularity(self, skill_id: str) -> float:
        with self._lock:
            row = self._conn.execute(
                "SELECT hit_count, last_access FROM chunks WHERE skill_id = ?",
                (skill_id,),
            ).fetchone()
            if row is None:
                return 0.0
            age_h = max(0.0, (time.time() - row["last_access"]) / 3600.0)
            return float(row["hit_count"]) * math.exp(-age_h / self.decay_hours)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _warm_path(self, skill_id: str) -> Path:
        # Sanitise: skill_id may contain '/', use it as the subpath
        safe = skill_id.replace("..", "_").replace("\\", "/")
        return self._warm_dir / (safe + ".json")

    def _delete_warm_file(self, skill_id: str) -> None:
        try:
            self._warm_path(skill_id).unlink(missing_ok=True)
        except OSError:
            pass

    def _upsert_index(
        self,
        skill_id: str,
        *,
        version: str,
        size_bytes: int,
        tier: str,
        last_access: float,
        received_at: float,
        content_sha: str,
    ) -> None:
        self._conn.execute(
            "INSERT INTO chunks(skill_id, version, size_bytes, tier, last_access, hit_count, received_at, content_sha) "
            "VALUES(?, ?, ?, ?, ?, 0, ?, ?) "
            "ON CONFLICT(skill_id) DO UPDATE SET "
            "  version=excluded.version, size_bytes=excluded.size_bytes, "
            "  tier=excluded.tier, last_access=excluded.last_access, "
            "  content_sha=excluded.content_sha",
            (skill_id, version, size_bytes, tier, last_access, received_at, content_sha),
        )

    def _bump_hit(self, skill_id: str, *, last_access: float) -> None:
        self._conn.execute(
            "UPDATE chunks SET hit_count = hit_count + 1, last_access = ? WHERE skill_id = ?",
            (last_access, skill_id),
        )

    def _delete_index(self, skill_id: str) -> None:
        self._conn.execute("DELETE FROM chunks WHERE skill_id = ?", (skill_id,))

    def _received_at(self, skill_id: str, *, fallback: float) -> float:
        row = self._conn.execute(
            "SELECT received_at FROM chunks WHERE skill_id = ?",
            (skill_id,),
        ).fetchone()
        return float(row["received_at"]) if row else fallback

    def _enforce_hot_cap(self) -> None:
        """Demote LRU hot chunks to warm until under cap."""
        while self._hot_size > self.hot_bytes_cap and self._hot:
            sid, chunk = self._hot.popitem(last=False)  # FIFO = LRU
            self._hot_size -= chunk.size_bytes
            warm_path = self._warm_path(sid)
            warm_path.parent.mkdir(parents=True, exist_ok=True)
            warm_path.write_text(json.dumps(chunk.to_json()), encoding="utf-8")
            self._conn.execute(
                "UPDATE chunks SET tier='warm' WHERE skill_id=?",
                (sid,),
            )

    def _enforce_warm_cap(self) -> int:
        """Delete oldest warm chunks until total warm bytes <= cap. Returns deleted count."""
        total = 0
        warm_rows = self._conn.execute(
            "SELECT skill_id, size_bytes, last_access FROM chunks WHERE tier='warm' ORDER BY last_access ASC"
        ).fetchall()
        all_bytes = sum(int(r["size_bytes"]) for r in warm_rows)
        if all_bytes <= self.warm_bytes_cap:
            return 0
        for row in warm_rows:
            if all_bytes <= self.warm_bytes_cap:
                break
            sid = row["skill_id"]
            sz = int(row["size_bytes"])
            self._delete_warm_file(sid)
            self._delete_index(sid)
            all_bytes -= sz
            total += 1
        return total


__all__ = ["EvictionResult", "SkillReplica"]
