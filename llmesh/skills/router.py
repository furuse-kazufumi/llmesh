"""FastAPI router for skill chunk replication endpoints (Phase 3.3 + 3.6c).

Wires `SkillReplica` to HTTP so peer nodes can pull / push / gossip chunks.
Phase 3.6c adds optional ``PeerReputation`` glue (corrupt reports feed
the reputation tracker) and a per-peer rate limiter on the write
endpoints to blunt abuse from a single chatty / hostile DID.

Endpoints (under prefix ``/skills``):

  * ``GET  /<skill_id>``               — fetch a chunk (404 if absent)
  * ``GET  /index``                    — list all known chunks (gossip)
  * ``POST /notify``                   — accept a notification of new chunk
                                         (lazy: client must follow-up GET)
  * ``POST /<skill_id>/report-corrupt`` — report integrity failure, forwarded
                                          to ``PeerReputation`` when wired

Usage::

    from llmesh.skills.router import skills_router, set_replica, set_reputation
    set_replica(my_replica)
    set_reputation(PeerReputation())   # optional
    app.include_router(skills_router)
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from llmesh.skills.replica import SkillReplica
from llmesh.skills.reputation import PeerReputation

skills_router = APIRouter(prefix="/skills", tags=["skills"])

# Module-level singletons (set via set_*()). Mirrors discovery/router.py.
_replica: SkillReplica | None = None
_reputation: PeerReputation | None = None
_corrupt_reports: list[dict[str, Any]] = []
_notifications: list[dict[str, Any]] = []


class RateLimiter:
    """In-memory sliding-window rate limit, keyed by peer identifier.

    Approximates a token bucket without external state: per key, retains
    the timestamps of the last ``max_events`` calls and rejects when more
    than ``max_events`` calls fell inside ``window_s``. Cheap and exact
    for small windows; not intended for cross-process coordination.
    """

    def __init__(
        self,
        *,
        max_events: int = 60,
        window_s: float = 60.0,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if max_events <= 0:
            raise ValueError("max_events must be positive")
        if window_s <= 0:
            raise ValueError("window_s must be positive")
        self._max = int(max_events)
        self._window = float(window_s)
        self._clock = clock or time.monotonic
        self._lock = threading.RLock()
        self._events: dict[str, deque[float]] = {}

    def check(self, key: str) -> bool:
        """Return ``True`` if the call is allowed; record it as a side effect."""
        now = self._clock()
        cutoff = now - self._window
        with self._lock:
            bucket = self._events.setdefault(key, deque())
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                return False
            bucket.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


_rate_limiter: RateLimiter | None = None


def get_replica() -> SkillReplica:
    if _replica is None:
        raise HTTPException(status_code=503, detail="replica_not_configured")
    return _replica


def set_replica(replica: SkillReplica | None) -> None:
    global _replica
    _replica = replica


def set_reputation(reputation: PeerReputation | None) -> None:
    """Wire a ``PeerReputation`` instance; report-corrupt then feeds it."""
    global _reputation
    _reputation = reputation


def set_rate_limiter(limiter: RateLimiter | None) -> None:
    """Install a rate limiter on the write endpoints (notify / report-corrupt).

    Pass ``None`` to disable. Tests can swap in a limiter with a controlled
    ``clock`` and small ``max_events`` to verify gating without sleeping.
    """
    global _rate_limiter
    _rate_limiter = limiter


def get_corrupt_reports() -> list[dict[str, Any]]:
    """Test / introspection helper; production code would persist these."""
    return list(_corrupt_reports)


def get_notifications() -> list[dict[str, Any]]:
    return list(_notifications)


def reset_state() -> None:
    """Clear in-process notification + corrupt-report queues. Test helper."""
    _corrupt_reports.clear()
    _notifications.clear()
    if _rate_limiter is not None:
        _rate_limiter.reset()


def _request_id(request: Request) -> str:
    """Pick a stable per-peer identifier for rate limiting.

    Preference order:

    1. ``X-Peer-Id`` header (caller's DID; explicit and forgeable but cheap)
    2. ``X-Forwarded-For`` first hop
    3. ``request.client.host``

    Forgeability is acceptable: this is a fairness control, not a security
    boundary. Real authentication lives in ``llmesh.auth`` / Ed25519
    signature verification, which the reputation layer also leans on.
    """
    pid = request.headers.get("X-Peer-Id", "").strip()
    if pid:
        return f"peer:{pid}"
    fwd = request.headers.get("X-Forwarded-For", "").strip()
    if fwd:
        return f"fwd:{fwd.split(',')[0].strip()}"
    if request.client is not None:
        return f"ip:{request.client.host}"
    return "anon"


def _rate_check(request: Request) -> None:
    if _rate_limiter is None:
        return
    if not _rate_limiter.check(_request_id(request)):
        raise HTTPException(status_code=429, detail="rate_limited")


@skills_router.get("/index")
async def list_index() -> JSONResponse:
    """Return all known chunks for gossip / discovery."""
    rep = get_replica()
    return JSONResponse(content={"chunks": rep.index()})


@skills_router.get("/{skill_id:path}")
async def get_chunk(skill_id: str) -> JSONResponse:
    """Fetch a skill chunk by id. Path supports slashes (e.g. ``a/b/c``)."""
    rep = get_replica()
    chunk = rep.get(skill_id)
    if chunk is None:
        raise HTTPException(status_code=404, detail=f"unknown_skill:{skill_id}")
    return JSONResponse(content=chunk.to_json())


@skills_router.post("/notify")
async def notify(request: Request) -> JSONResponse:
    """Accept a notification that a peer has a new chunk available.

    Body::

        {
          "skill_id":      "<required>",
          "version":       "<optional>",
          "merkle_root":   "<optional hex>",
          "peer_endpoint": "<optional url>",
          "license":       "<optional>"
        }

    The server records the notification but does NOT pull the chunk
    automatically (that step is gated by `@govern` in Phase 3.5).
    """
    _rate_check(request)
    try:
        body = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="body_must_be_json") from exc
    if not isinstance(body, dict) or "skill_id" not in body:
        raise HTTPException(status_code=400, detail="skill_id_required")
    _notifications.append({k: str(v) for k, v in body.items()})
    return JSONResponse(content={"accepted": True, "queued": len(_notifications)})


@skills_router.post("/{skill_id:path}/report-corrupt")
async def report_corrupt(skill_id: str, request: Request) -> JSONResponse:
    """Record an integrity failure report against a peer.

    Body (optional)::

        {
          "against":   "<reported peer id>",     # required to feed reputation
          "by":        "<reporting peer id>",
          "rationale": "<short text>"
        }

    When a ``PeerReputation`` is wired via ``set_reputation``, ``against``
    is forwarded to ``record_corruption``. Without ``against`` the report
    is still recorded in the in-memory queue (backward compatible) but
    cannot be attributed to a specific peer.
    """
    _rate_check(request)
    body: dict[str, Any] = {}
    try:
        if request.headers.get("content-length", "0") not in ("", "0"):
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
    except Exception:
        body = {}
    against = str(body.get("against", "")).strip()
    by = str(body.get("by", ""))
    report = {
        "skill_id": skill_id,
        "against": against,
        "by": by,
        "rationale": str(body.get("rationale", "")),
    }
    _corrupt_reports.append(report)
    if _reputation is not None and against:
        _reputation.record_corruption(against, reporter=by, skill_id=skill_id)
    return JSONResponse(
        content={
            "recorded": True,
            "total_reports": len(_corrupt_reports),
            "reputation_updated": bool(_reputation is not None and against),
        }
    )


__all__ = [
    "RateLimiter",
    "get_corrupt_reports",
    "get_notifications",
    "get_replica",
    "reset_state",
    "set_rate_limiter",
    "set_replica",
    "set_reputation",
    "skills_router",
]
