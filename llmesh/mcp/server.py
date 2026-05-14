"""FastAPI-based MCP HTTP server for LLMesh nodes (v0.2.0+).

v0.2.0 privacy pipeline enforcement:
  L0/L1 → pass to backend directly.
  L3    → PromptFirewall classifies as SUMMARIZE; PrivacySummarizer reduces
           to L1 summary; only the summary reaches the LLM backend.
  L4    → always BLOCK; never reaches summarizer or backend.
  Any exception in summarization → fail closed (422).

Security constraints enforced:
- shell=True is NEVER used anywhere in this module.
- pickle, yaml.load (unsafe), marshal, eval, exec are NEVER used.
- All subprocess calls (if any) must use list-based arguments.
- LLM responses are treated as untrusted until OutputValidator clears them.
- Raw L3/L4 prompt text is never passed to the LLM backend.
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
from ..classifier.data_level import DataLevel
from ..identity.node_id import NodeIdentity
from ..llm.backend import BackendError, LLMBackend
from ..llm.llamacpp import LlamaCppBackend
from ..llm.ollama import OllamaBackend
from ..privacy.firewall import PromptFirewall
from ..privacy.summarizer import PrivacySummarizer
from ..discovery.router import registry_router
from ..fairness import ContributionLedger, FairnessPolicy
from ..security.rate_limiter import PerNodeRateLimiter, RateLimitExceeded
from ..timeline.store import TimelineStore
from .nonce_store import NonceStore, SqliteNonceStore
from .schemas import TOOL_SCHEMAS
from .validator import OutputValidator, ValidationError

app = FastAPI(title="LLMesh MCP Node", version="0.2.0")
app.include_router(registry_router)

# --- Auth middleware ---
_trusted_peers_path = os.environ.get("LLMESH_TRUSTED_PEERS_PATH", "")
if _trusted_peers_path and Path(_trusted_peers_path).exists():
    _trusted_peers = TrustedPeers(_trusted_peers_path)
    app.middleware("http")(make_auth_middleware(_trusted_peers))

# --- Node identity ---
_identity: NodeIdentity | None = None
_identity_path = os.environ.get("LLMESH_NODE_IDENTITY_PATH", "")
if _identity_path and Path(_identity_path).exists():
    _raw = Path(_identity_path).read_bytes()
    _identity = NodeIdentity.from_private_bytes(_raw)

# --- Nonce store: SQLite (durable) or in-memory ---
_nonce_db_path = os.environ.get("LLMESH_NONCE_DB_PATH", "")
if _nonce_db_path:
    _nonce_store: NonceStore | SqliteNonceStore = SqliteNonceStore(
        db_path=_nonce_db_path,
        ttl_seconds=300,
    )
else:
    _nonce_store = NonceStore(ttl_seconds=300)

# --- Audit trace ---
_audit: AuditTrace | None = None
_audit_log_path = os.environ.get("LLMESH_AUDIT_LOG_PATH", "")
_audit_hmac_key_hex = os.environ.get("LLMESH_AUDIT_HMAC_KEY", "")
_unsafe_no_lock = os.environ.get("LLMESH_UNSAFE_AUDIT_NO_LOCK", "").strip() == "1"
if _audit_log_path and _audit_hmac_key_hex:
    _audit = AuditTrace(
        _audit_log_path,
        bytes.fromhex(_audit_hmac_key_hex),
        unsafe_no_lock=_unsafe_no_lock,
    )

# --- Privacy pipeline ---
_firewall = PromptFirewall(audit_trace=_audit)
_summarizer = PrivacySummarizer()

# OutputValidator has no nonce_store: handle_tool already consumed the nonce.
_validator = OutputValidator(audit_trace=_audit)


def _select_backend() -> LLMBackend:
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

# --- Timeline store: disabled unless LLMESH_TIMELINE_DB_PATH is set ---
_timeline: TimelineStore | None = None
_timeline_db_path = os.environ.get("LLMESH_TIMELINE_DB_PATH", "")
if _timeline_db_path:
    _timeline = TimelineStore(_timeline_db_path)

# --- Rate limiter (per node_id, token bucket) ---
_rate_limiter = PerNodeRateLimiter(rate=10.0, burst=20.0)

# --- Fairness enforcement (opt-in via env vars) ---
_fairness_ledger: ContributionLedger | None = None
_fairness_policy: FairnessPolicy | None = None
_fairness_hmac_hex = os.environ.get("LLMESH_FAIRNESS_HMAC_KEY", "")
if _fairness_hmac_hex and os.environ.get("LLMESH_FAIRNESS_ENABLED", "").strip() == "1":
    _fairness_ledger = ContributionLedger(bytes.fromhex(_fairness_hmac_hex))
    _fairness_policy = FairnessPolicy(_fairness_ledger)

# --- Request size / field-length limits ---
_MAX_REQUEST_BODY_BYTES = 65_536   # 64 KB; blocks body-flood before JSON parse
_MAX_NONCE_LEN = 128
_MAX_NODE_ID_LEN = 128

# --- OWASP-recommended security response headers ---
_SECURITY_HEADERS: dict[str, str] = {
    "X-Content-Type-Options":  "nosniff",
    "X-Frame-Options":         "DENY",
    "X-XSS-Protection":        "1; mode=block",
    "Content-Security-Policy": "default-src 'none'",
    "Referrer-Policy":         "no-referrer",
    "Cache-Control":           "no-store",
    "Permissions-Policy":      "interest-cohort=()",
}


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Attach OWASP security headers to every response."""
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers[header] = value
    return response


@app.middleware("http")
async def _body_size_limit(request: Request, call_next):
    """Reject requests whose Content-Length exceeds the hard cap before buffering."""
    cl = request.headers.get("content-length")
    if cl is not None:
        try:
            if int(cl) > _MAX_REQUEST_BODY_BYTES:
                return JSONResponse(
                    status_code=413,
                    content={"error": "request_too_large",
                             "detail": f"body must not exceed {_MAX_REQUEST_BODY_BYTES} bytes"},
                )
        except ValueError:
            pass  # malformed Content-Length; let downstream handle it
    return await call_next(request)


@app.middleware("http")
async def _json_only(request: Request, call_next):
    if request.method == "POST":
        ct = request.headers.get("content-type", "")
        if "application/json" not in ct:
            return JSONResponse(
                status_code=415,
                content={"error": "unsupported_media_type", "detail": "application/json required"},
            )
    return await call_next(request)


def _tl(task_id: str, node_id: str, event_type: str, **kw: Any) -> None:
    """Record a timeline event when the store is configured. Never raises."""
    if _timeline is not None:
        _timeline.record(task_id, node_id, event_type, **kw)


@app.post("/tools/{tool_name}")
async def handle_tool(tool_name: str, request: Request) -> JSONResponse:
    """Handle a tool invocation request.

    Steps:
    1. Validate tool_name, parse JSON body
    2. Validate task_id (UUID v4) and nonce freshness
    3. Classify prompt via PromptFirewall
    4. L4 → reject; L3 → summarize (raw never reaches backend); L0/L1 → pass
    5. Invoke LLM backend with effective prompt
    6. Validate LLM response via OutputValidator
    """
    if tool_name not in _ALLOWED_TOOLS:
        raise HTTPException(status_code=404, detail=f"unknown_tool:{tool_name}")

    # --- Rate limiting (per node, before any parsing work) ---
    node_id = request.headers.get("X-Node-Id", "")
    if len(node_id) > _MAX_NODE_ID_LEN:
        raise HTTPException(status_code=400, detail="node_id_too_long")
    try:
        _rate_limiter.check(node_id or (request.client.host if request.client else "anonymous"))
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")

    # --- Fairness gate (optional — only active when LLMESH_FAIRNESS_ENABLED=1) ---
    if _fairness_policy is not None and node_id:
        if not _fairness_policy.is_allowed(node_id):
            raise HTTPException(status_code=429, detail="fairness_blocked")

    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json_parse_error")

    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request_must_be_object")

    task_id = body.get("task_id", "")
    if not task_id:
        raise HTTPException(status_code=422, detail="missing_task_id")
    try:
        parsed_uuid = uuid.UUID(task_id, version=4)
        if parsed_uuid.version != 4:
            raise ValueError("not_v4")
    except (ValueError, AttributeError):
        raise HTTPException(status_code=422, detail=f"invalid_task_id_uuid4:{task_id!r}")

    caller_nonce = body.get("caller_nonce", "")
    if not caller_nonce:
        raise HTTPException(status_code=422, detail="missing_caller_nonce")
    if len(caller_nonce) > _MAX_NONCE_LEN:
        raise HTTPException(status_code=422, detail="caller_nonce_too_long")

    try:
        fresh = _nonce_store.check_and_store(node_id or "anonymous", caller_nonce)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid_nonce:{exc}")
    if not fresh:
        raise HTTPException(status_code=409, detail="replay_attack_detected")

    # Nonce accepted — task officially received.
    import time as _time
    _t0 = _time.monotonic()
    _tl(task_id, node_id, "received", tool=tool_name)

    # --- Privacy pipeline ---
    prompt = str(body.get("prompt", "") or "")
    fw_decision = _firewall.classify(prompt, node_id=node_id, task_id=task_id)

    if fw_decision.blocked:
        if _audit is not None:
            _audit.log(
                event_type="l4_blocked",
                node_id=node_id,
                task_id=task_id,
                policy_decision="BLOCK",
                output_sha256="0" * 64,
                data_level=4,
                prompt_sha256=hashlib.sha256(prompt.encode()).hexdigest(),
            )
        _tl(task_id, node_id, "failed",
            reason="firewall_blocked", detail=fw_decision.reason,
            elapsed_ms=int((_time.monotonic() - _t0) * 1000))
        raise HTTPException(status_code=422, detail=f"firewall_blocked:{fw_decision.reason}")

    fw_evt = ("firewall_summarize" if fw_decision.requires_summarization
              else "firewall_allow")
    _tl(task_id, node_id, fw_evt,
        layer=fw_decision.triggered_layer, reason=fw_decision.reason)

    effective_prompt = prompt

    if fw_decision.requires_summarization:
        try:
            sum_result = _summarizer.summarize_text(prompt, DataLevel(fw_decision.level))
            effective_prompt = sum_result.summary
            _tl(task_id, node_id, "summarized")
        except Exception:
            if _audit is not None:
                _audit.log(
                    event_type="summarization_failed",
                    node_id=node_id,
                    task_id=task_id,
                    policy_decision="BLOCK",
                    output_sha256="0" * 64,
                    data_level=int(fw_decision.level),
                )
            _tl(task_id, node_id, "failed", reason="summarization_error",
                elapsed_ms=int((_time.monotonic() - _t0) * 1000))
            raise HTTPException(status_code=422, detail="l3_summarization_failed_closed")

    # --- LLM invocation ---
    backend_body = {**body, "prompt": effective_prompt}
    _tl(task_id, node_id, "llm_invoked", tool=tool_name)
    _t_llm = _time.monotonic()
    try:
        llm_result = _llm_backend.invoke(tool_name, backend_body)
    except BackendError as exc:
        if _audit is not None:
            _audit.log(
                event_type="backend_error",
                node_id=node_id,
                task_id=task_id,
                policy_decision="BLOCK",
                output_sha256="0" * 64,
            )
        _tl(task_id, node_id, "failed", reason="backend_error",
            llm_ms=int((_time.monotonic() - _t_llm) * 1000),
            elapsed_ms=int((_time.monotonic() - _t0) * 1000))
        raise HTTPException(status_code=502, detail=f"llm_backend_error:{exc}")

    _tl(task_id, node_id, "llm_responded",
        llm_ms=int((_time.monotonic() - _t_llm) * 1000))

    llm_result.setdefault("task_id", task_id)
    llm_result.setdefault("caller_nonce_echo", caller_nonce)

    try:
        validated = _validator.validate(
            json.dumps(llm_result),
            tool_name,
            caller_nonce,
            node_id=node_id,
            task_id=task_id,
        )
    except ValidationError as exc:
        _tl(task_id, node_id, "failed", reason="validation_error",
            elapsed_ms=int((_time.monotonic() - _t0) * 1000))
        raise HTTPException(status_code=502, detail=f"llm_output_invalid:{exc.reason}")

    _tl(task_id, node_id, "completed",
        tool=tool_name, elapsed_ms=int((_time.monotonic() - _t0) * 1000))

    # --- Fairness accounting: record that this node consumed one service ---
    if _fairness_ledger is not None and node_id:
        _fairness_ledger.record_consumed(node_id, task_id)

    return JSONResponse(content=validated)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "tools": sorted(_ALLOWED_TOOLS)}


@app.get("/identity")
async def identity() -> JSONResponse:
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


# --- F25 (f): External event ingest from llive / other producers ---
#
# Phase 2 OBS-03 spec frozen in llove's docs/llove_llive_bridge.md.
# Allow-list keeps the surface intentionally narrow — adding a new
# event_type requires a deliberate edit here (not just a producer change).
_ALLOWED_INGEST_EVENT_TYPES: frozenset[str] = frozenset({
    "route_trace",
    "concept_update",
    "bwt_summary",
})

# Body field names that collide with TimelineStore.record positional args.
# We refuse them in metadata so ingesters can't accidentally shadow the
# canonical fields (task_id / node_id / event_type / timestamp_utc).
_RESERVED_METADATA_KEYS: frozenset[str] = frozenset({
    "task_id",
    "node_id",
    "event_type",
    "timestamp_utc",
})


@app.post("/timeline/ingest")
async def timeline_ingest(request: Request) -> JSONResponse:
    """External producers (llive / future MQTT bridge / etc.) push events
    into TimelineStore. Read-side endpoints (``/timeline/recent`` /
    ``/timeline/task/{id}``) consume them transparently.

    Body schema (frozen by ``llove/docs/llove_llive_bridge.md`` v1):

    ::

        {
          "task_id":   "<UUID v4>",
          "node_id":   "<= 128 chars, optional (defaults to X-Node-Id)>",
          "event_type": "route_trace | concept_update | bwt_summary",
          "metadata":  { ... }
        }

    Response 200: ``{"stored": true}``

    timestamp_utc is assigned server-side at receive time (TimelineStore
    convention). Clients should not supply it; if they do, it is ignored.

    Errors:
        400 json_parse_error | request_must_be_object | node_id_too_long
        413 request_too_large (body > 64 KB, handled by middleware)
        415 unsupported_media_type (handled by middleware)
        422 missing_task_id | invalid_task_id_uuid4:<...> |
            unknown_event_type:<...> | metadata_must_be_object |
            reserved_metadata_key:<...>
        429 rate_limit_exceeded
        503 timeline_not_configured
    """
    if _timeline is None:
        raise HTTPException(status_code=503, detail="timeline_not_configured")

    # --- Rate limiting (per node, before any parsing work) ---
    node_id_header = request.headers.get("X-Node-Id", "")
    if len(node_id_header) > _MAX_NODE_ID_LEN:
        raise HTTPException(status_code=400, detail="node_id_too_long")
    try:
        _rate_limiter.check(
            node_id_header
            or (request.client.host if request.client else "anonymous")
        )
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="rate_limit_exceeded")

    # --- Body parse ---
    try:
        body: dict[str, Any] = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="json_parse_error")
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="request_must_be_object")

    # --- task_id (UUID v4) ---
    task_id = str(body.get("task_id", ""))
    if not task_id:
        raise HTTPException(status_code=422, detail="missing_task_id")
    try:
        parsed_uuid = uuid.UUID(task_id, version=4)
        if parsed_uuid.version != 4:
            raise ValueError("not_v4")
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=422, detail=f"invalid_task_id_uuid4:{task_id!r}"
        )

    # --- event_type allow-list ---
    event_type = str(body.get("event_type", ""))
    if event_type not in _ALLOWED_INGEST_EVENT_TYPES:
        raise HTTPException(
            status_code=422, detail=f"unknown_event_type:{event_type}"
        )

    # --- metadata structure check + reserved-key guard ---
    metadata = body.get("metadata", {})
    if not isinstance(metadata, dict):
        raise HTTPException(status_code=422, detail="metadata_must_be_object")
    for k in metadata:
        if k in _RESERVED_METADATA_KEYS:
            raise HTTPException(
                status_code=422, detail=f"reserved_metadata_key:{k}"
            )

    # --- Resolve node_id: body has priority, fall back to header ---
    body_node_id = body.get("node_id", "")
    final_node_id = str(body_node_id) if body_node_id else node_id_header
    if len(final_node_id) > _MAX_NODE_ID_LEN:
        raise HTTPException(status_code=400, detail="node_id_too_long")

    # --- Record. TimelineStore.record assigns timestamp_utc server-side. ---
    _timeline.record(
        task_id,
        final_node_id,
        event_type,
        **metadata,
    )

    return JSONResponse(content={"stored": True})


@app.get("/timeline/task/{task_id}")
async def timeline_task(task_id: str) -> JSONResponse:
    """Return the full event timeline for a single task_id."""
    if _timeline is None:
        raise HTTPException(status_code=503, detail="timeline_not_configured")
    events = _timeline.get_task_timeline(task_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"task_not_found:{task_id}")
    first_ts = events[0].timestamp_utc
    rows = []
    for ev in events:
        delta = ev.delta_ms(events[0]) if ev is not events[0] else 0
        rows.append({
            "event_id":     ev.event_id,
            "event_type":   ev.event_type,
            "timestamp_utc": ev.timestamp_utc,
            "delta_ms":     delta,
            "metadata":     ev.metadata,
        })
    return JSONResponse(content={
        "task_id":   task_id,
        "node_id":   events[0].node_id,
        "started":   first_ts,
        "terminal":  events[-1].is_terminal,
        "resumable": not events[-1].is_terminal,
        "events":    rows,
    })


@app.get("/timeline/recent")
async def timeline_recent(limit: int = 50, node_id: str = "") -> JSONResponse:
    """Return the most recent timeline events (newest first)."""
    if _timeline is None:
        raise HTTPException(status_code=503, detail="timeline_not_configured")
    limit = min(max(1, limit), 500)
    events = _timeline.get_recent_events(limit=limit, node_id=node_id)
    return JSONResponse(content={
        "count": len(events),
        "events": [
            {
                "event_id":      ev.event_id,
                "task_id":       ev.task_id,
                "node_id":       ev.node_id,
                "event_type":    ev.event_type,
                "timestamp_utc": ev.timestamp_utc,
                "metadata":      ev.metadata,
            }
            for ev in events
        ],
    })


@app.get("/timeline/resumable")
async def timeline_resumable() -> JSONResponse:
    """Return tasks with no terminal event — these can be safely retried."""
    if _timeline is None:
        raise HTTPException(status_code=503, detail="timeline_not_configured")
    tasks = _timeline.get_resumable_tasks()
    return JSONResponse(content={"count": len(tasks), "tasks": tasks})


def create_app() -> FastAPI:
    return app
