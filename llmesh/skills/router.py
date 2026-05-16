"""FastAPI router for skill chunk replication endpoints (Phase 3.3).

Wires `SkillReplica` to HTTP so peer nodes can pull / push / gossip chunks.

Endpoints (under prefix ``/skills``):

  * ``GET  /<skill_id>``               — fetch a chunk (404 if absent)
  * ``GET  /index``                    — list all known chunks (gossip)
  * ``POST /notify``                   — accept a notification of new chunk
                                         (lazy: client must follow-up GET)
  * ``POST /<skill_id>/report-corrupt`` — report integrity failure (placeholder
                                          for Phase 3.6 reputation system)

Usage::

    from llmesh.skills.router import skills_router, set_replica
    set_replica(my_replica)
    app.include_router(skills_router)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from llmesh.skills.replica import SkillReplica

skills_router = APIRouter(prefix="/skills", tags=["skills"])

# Module-level singleton (set via set_replica()). Mirrors discovery/router.py
# pattern.
_replica: SkillReplica | None = None
_corrupt_reports: list[dict[str, Any]] = []
_notifications: list[dict[str, Any]] = []


def get_replica() -> SkillReplica:
    if _replica is None:
        raise HTTPException(status_code=503, detail="replica_not_configured")
    return _replica


def set_replica(replica: SkillReplica | None) -> None:
    global _replica
    _replica = replica


def get_corrupt_reports() -> list[dict[str, Any]]:
    """Test / introspection helper; production code would persist these."""
    return list(_corrupt_reports)


def get_notifications() -> list[dict[str, Any]]:
    return list(_notifications)


def reset_state() -> None:
    """Clear in-process notification + corrupt-report queues. Test helper."""
    _corrupt_reports.clear()
    _notifications.clear()


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
          "by":      "<reporting peer id>",
          "rationale": "<short text>"
        }
    """
    body: dict[str, Any] = {}
    try:
        if request.headers.get("content-length", "0") not in ("", "0"):
            body = await request.json()
            if not isinstance(body, dict):
                body = {}
    except Exception:
        body = {}
    report = {
        "skill_id": skill_id,
        "by": str(body.get("by", "")),
        "rationale": str(body.get("rationale", "")),
    }
    _corrupt_reports.append(report)
    return JSONResponse(content={"recorded": True, "total_reports": len(_corrupt_reports)})


__all__ = [
    "get_corrupt_reports",
    "get_notifications",
    "get_replica",
    "reset_state",
    "set_replica",
    "skills_router",
]
