"""FastAPI router for the P2P node registry endpoints.

Endpoints:
  POST   /registry/register          — register a node (manifest + endpoint)
  GET    /registry/nodes             — list live nodes (optional subnet/tool filter)
  GET    /registry/nodes/{node_id}   — get a specific node
  DELETE /registry/nodes/{node_id}   — deregister a node

Mount into the main app with:
    from llmesh.discovery.router import registry_router, get_registry
    app.include_router(registry_router)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from .registry import NodeRegistry, RegistryError
from ..security.endpoint_validator import EndpointValidator, EndpointValidationError

# allow_private=True: LAN nodes use RFC1918 addresses; localhost still blocked
_endpoint_validator = EndpointValidator(allow_private=True)

registry_router = APIRouter(prefix="/registry", tags=["discovery"])

# Module-level singleton — shared across all requests
_registry = NodeRegistry()


def get_registry() -> NodeRegistry:
    """Return the module-level NodeRegistry (for testing override)."""
    return _registry


def set_registry(reg: NodeRegistry) -> None:
    """Replace the module-level registry (used in tests)."""
    global _registry
    _registry = reg


@registry_router.post("/register")
async def register_node(request: Request) -> JSONResponse:
    """Register a node by submitting its signed CapabilityManifest.

    Request body (JSON):
      {
        "manifest":       { ...CapabilityManifest fields... },
        "endpoint":       "http://192.168.1.5:8080",
        "public_key_hex": "<64-char hex>"
      }
    """
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json_parse_error")

    manifest_dict = body.get("manifest")
    endpoint = body.get("endpoint", "")
    public_key_hex = body.get("public_key_hex", "")

    if not manifest_dict or not isinstance(manifest_dict, dict):
        raise HTTPException(status_code=422, detail="missing_or_invalid_manifest")
    if not endpoint:
        raise HTTPException(status_code=422, detail="missing_endpoint")
    if not public_key_hex:
        raise HTTPException(status_code=422, detail="missing_public_key_hex")

    try:
        endpoint = _endpoint_validator.validate(endpoint)
    except EndpointValidationError as exc:
        raise HTTPException(status_code=422, detail=f"invalid_endpoint:{exc}")

    try:
        entry = _registry.register(manifest_dict, endpoint, public_key_hex)
    except RegistryError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return JSONResponse(status_code=201, content=entry.to_dict())


@registry_router.get("/nodes")
async def list_nodes(
    subnet: str | None = Query(default=None),
    tool: str | None = Query(default=None),
) -> JSONResponse:
    """Return live registered nodes, optionally filtered by subnet or tool."""
    nodes = _registry.list_nodes(subnet=subnet, tool=tool)
    return JSONResponse(content=[n.to_dict() for n in nodes])


@registry_router.get("/nodes/{node_id}")
async def get_node(node_id: str) -> JSONResponse:
    """Return a specific node by node_id."""
    entry = _registry.get(node_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"node_not_found:{node_id}")
    return JSONResponse(content=entry.to_dict())


@registry_router.delete("/nodes/{node_id}")
async def deregister_node(node_id: str) -> JSONResponse:
    """Deregister a node (self-deregistration)."""
    removed = _registry.deregister(node_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"node_not_found:{node_id}")
    return JSONResponse(content={"removed": node_id})


@registry_router.get("/peers")
async def list_peers() -> JSONResponse:
    """Return all live nodes with signed manifests for gossip propagation.

    This endpoint is unauthenticated — peers are verified by their manifest
    signatures on the receiving side, not by transport auth.
    """
    nodes = _registry.list_nodes()
    return JSONResponse(content={"peers": [n.to_peer_dict() for n in nodes]})


@registry_router.post("/query")
async def query_matching_peers(request: Request) -> JSONResponse:
    """Capability-aware peer matching (RFC Phase 2a).

    Request body::

        {
          "required_tools":      ["chat"],            // optional
          "preferred_domains":   ["code", "math"],    // optional
          "preferred_languages": ["ja", "en"],        // optional
          "min_data_level":      0,                    // optional, default 0
          "k":                   3                     // optional, default 3
        }

    Response::

        {
          "matches": [
            {"score": 1.0, "node_id": "...", "endpoint": "...", "did": "..."},
            ...
          ]
        }
    """
    from llmesh.discovery.clustering import CapabilityQuery

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body_must_be_object")

    try:
        query = CapabilityQuery(
            required_tools=frozenset(body.get("required_tools") or []),
            preferred_domains=frozenset(body.get("preferred_domains") or []),
            preferred_languages=frozenset(body.get("preferred_languages") or []),
            min_data_level=int(body.get("min_data_level") or 0),
        )
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"invalid_query:{exc}") from exc

    raw_k = body.get("k")
    if raw_k is None:
        k = 3
    else:
        try:
            k = int(raw_k)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"invalid_k:{exc}") from exc
    if k < 1 or k > 100:
        raise HTTPException(status_code=400, detail=f"k_out_of_range:{k}")

    matches = _registry.find_matching(query, k=k)
    return JSONResponse(
        content={
            "matches": [
                {
                    "score": score,
                    "node_id": entry.node_id,
                    "endpoint": entry.endpoint,
                    "did": entry.did,
                }
                for score, entry in matches
            ]
        }
    )
