"""FastAPI-based MCP HTTP server for LLMesh nodes.

Each node exposes POST /tools/{tool_name} which:
1. Accepts JSON-only requests (Content-Type: application/json)
2. Validates task_id (UUID v4), nonce, and schema via OutputValidator
3. Passes the validated payload to the configured LLM backend
4. Validates the LLM response via OutputValidator before returning

Security constraints enforced:
- shell=True is NEVER used anywhere in this module
- pickle, yaml.load (unsafe), marshal, eval, exec are NEVER used
- All subprocess calls (if any) must use list-based arguments
- LLM responses are treated as untrusted until OutputValidator clears them
"""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from ..audit import AuditTrace
from ..auth.trusted_peers import TrustedPeers
from ..auth.verifier import make_auth_middleware
from ..identity.node_id import NodeIdentity
from ..llm.backend import BackendError, LLMBackend
from ..llm.llamacpp import LlamaCppBackend
from ..llm.ollama import OllamaBackend
from ..privacy.firewall import PromptFirewall
from ..discovery.router import registry_router
from .nonce_store import NonceStore
from .schemas import TOOL_SCHEMAS
from .validator import OutputValidator, ValidationError

app = FastAPI(title="LLMesh MCP Node", version="0.1.0")
app.include_router(registry_router)

# --- Auth middleware (opt-in via LLMESH_TRUSTED_PEERS_PATH env var) ---
_trusted_peers_path = os.environ.get("LLMESH_TRUSTED_PEERS_PATH", "")
if _trusted_peers_path and Path(_trusted_peers_path).exists():
    _trusted_peers = TrustedPeers(_trusted_peers_path)
    app.middleware("http")(make_auth_middleware(_trusted_peers))

# --- Node identity (opt-in via LLMESH_NODE_IDENTITY_PATH env var) ---
_identity: NodeIdentity | None = None
_identity_path = os.environ.get("LLMESH_NODE_IDENTITY_PATH", "")
if _identity_path and Path(_identity_path).exists():
    _raw = Path(_identity_path).read_bytes()
    _identity = NodeIdentity.from_private_bytes(_raw)

_nonce_store = NonceStore(ttl_seconds=300)

# --- Audit trace (opt-in via LLMESH_AUDIT_LOG_PATH + LLMESH_AUDIT_HMAC_KEY) ---
_audit: AuditTrace | None = None
_audit_log_path = os.environ.get("LLMESH_AUDIT_LOG_PATH", "")
_audit_hmac_key_hex = os.environ.get("LLMESH_AUDIT_HMAC_KEY", "")
if _audit_log_path and _audit_hmac_key_hex:
    _audit = AuditTrace(_audit_log_path, bytes.fromhex(_audit_hmac_key_hex))

# --- Prompt firewall (always active; audit-wired when _audit is set) ---
_firewall = PromptFirewall(audit_trace=_audit)

# Validator has no nonce_store: handle_tool already consumed the nonce above,
# so passing it here would trigger a false replay_attack on the echo check.
_validator = OutputValidator(audit_trace=_audit)


def _select_backend() -> LLMBackend:
    # LLMESH_BACKEND=ollama|llamacpp  LLMESH_BACKEND_URL=http://...  LLMESH_MODEL=<name>
    name = os.environ.get("LLMESH_BACKEND", "ollama").lower()
    url = os.environ.get("LLMESH_BACKEND_URL", "")
    model = os.environ.get("LLMESH_MODEL", "")
    kw: dict[str, Any] = {}
    if url:
        kw["base_url"] = url
    if model:
        kw["model"] = model
    if name == "llamacpp":
        return LlamaCppBackend(**kw)
    return OllamaBackend(**kw)


_llm_backend: LLMBackend = _select_backend()

_ALLOWED_TOOLS = set(TOOL_SCHEMAS.keys())


@app.middleware("http")
async def _json_only(request: Request, call_next):
    """Reject non-JSON content types before reaching route handlers."""
    if request.method == "POST":
        ct = request.headers.get("content-type", "")
        if "application/json" not in ct:
            return JSONResponse(
                status_code=415,
                content={"error": "unsupported_media_type", "detail": "application/json required"},
            )
    return await call_next(request)


@app.post("/tools/{tool_name}")
async def handle_tool(tool_name: str, request: Request) -> JSONResponse:
    """Handle a tool invocation request.

    Steps:
    1. Validate tool_name is known
    2. Parse JSON body (fail-closed on parse error)
    3. Validate task_id UUID v4 from caller
    4. Validate nonce freshness via NonceStore
    5. Invoke LLM backend
    6. Validate LLM response via OutputValidator before returning
    """
    if tool_name not in _ALLOWED_TOOLS:
        raise HTTPException(status_code=404, detail=f"unknown_tool:{tool_name}")

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json_parse_error")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request_must_be_object")

    # Extract and validate task_id from request envelope
    task_id = body.get("task_id", "")
    if not task_id:
        raise HTTPException(status_code=422, detail="missing_task_id")
    try:
        parsed_uuid = uuid.UUID(task_id, version=4)
        if parsed_uuid.version != 4:
            raise ValueError("not_v4")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail=f"invalid_task_id_uuid4:{task_id!r}")

    # Extract caller_nonce from request envelope
    caller_nonce = body.get("caller_nonce", "")
    if not caller_nonce:
        raise HTTPException(status_code=422, detail="missing_caller_nonce")

    # Node ID from header (optional, used for replay tracking)
    node_id = request.headers.get("X-Node-Id", "")

    # Server-side nonce freshness check
    try:
        fresh = _nonce_store.check_and_store(node_id or "anonymous", caller_nonce)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid_nonce:{exc}")
    if not fresh:
        raise HTTPException(status_code=409, detail="replay_attack_detected")

    # --- Prompt firewall check ---
    prompt = body.get("prompt", "") or ""
    fw_decision = _firewall.classify(str(prompt), node_id=node_id, task_id=task_id)
    if fw_decision.blocked:
        raise HTTPException(status_code=422, detail=f"firewall_blocked:{fw_decision.reason}")

    # --- LLM invocation ---
    try:
        llm_result = _llm_backend.invoke(tool_name, body)
    except BackendError as exc:
        if _audit is not None:
            _audit.log(
                event_type="backend_error",
                node_id=node_id,
                task_id=task_id,
                policy_decision="BLOCK",
                output_sha256="0" * 64,
            )
        raise HTTPException(status_code=502, detail=f"llm_backend_error:{exc}")

    # Inject task_id and caller_nonce_echo if the LLM omitted them.
    # OutputValidator will reject mismatches, so this is safe to set as default.
    llm_result.setdefault("task_id", task_id)
    llm_result.setdefault("caller_nonce_echo", caller_nonce)

    # Validate LLM output before returning to caller (fail-closed)
    try:
        validated = _validator.validate(
            json.dumps(llm_result),
            tool_name,
            caller_nonce,
            node_id=node_id,
            task_id=task_id,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=502, detail=f"llm_output_invalid:{exc.reason}")

    return JSONResponse(content=validated)


@app.get("/health")
async def health() -> dict:
    """Liveness probe."""
    return {"status": "ok", "tools": sorted(_ALLOWED_TOOLS)}


@app.get("/identity")
async def identity() -> JSONResponse:
    """Public key advertisement — unauthenticated, used for TOFU bootstrap.

    Returns node_id, public_key_hex, did, and a human-readable fingerprint
    (SHA-256 of the public key bytes, first 16 bytes as colon-separated hex).
    """
    if _identity is None:
        raise HTTPException(status_code=503, detail="node_identity_not_configured")
    raw = bytes.fromhex(_identity.public_key_hex)
    digest = hashlib.sha256(raw).hexdigest()
    fingerprint = ":".join(digest[i:i+2] for i in range(0, 32, 2))
    return JSONResponse(content={
        "node_id":        _identity.node_id,
        "public_key_hex": _identity.public_key_hex,
        "did":            _identity.did_key,
        "fingerprint":    fingerprint,
    })


def create_app() -> FastAPI:
    """Factory for use with ASGI runners (e.g. uvicorn)."""
    return app
