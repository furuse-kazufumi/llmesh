"""Rendezvous server — FastAPI app for signed node endpoint registration.

Endpoints:
  POST /announce          Register or refresh a node's endpoint.
  GET  /lookup/{node_id}  Retrieve a registered node's endpoint.

Security invariants:
  - Every announcement must carry a valid Ed25519 signature.
  - Signature covers: "<node_id>|<endpoint>|<timestamp_utc>" (UTF-8).
  - Timestamp must be within ANNOUNCE_WINDOW_SECONDS of server time.
  - shell=True, eval, exec, pickle are never used.
  - Public key is trusted on first use (TOFU); re-announcements must
    carry the same public_key_hex as the original registration.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, field_validator

from ..identity.node_id import NodeIdentity
from ..security.endpoint_validator import EndpointValidator, EndpointValidationError

_endpoint_validator = EndpointValidator(allow_private=True)

# Maximum clock skew tolerated for announcements (seconds)
ANNOUNCE_WINDOW_SECONDS = 300


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class AnnounceRequest(BaseModel):
    node_id: str
    did: str
    endpoint: str
    public_key_hex: str
    timestamp_utc: str   # ISO-8601, e.g. "2026-05-05T12:00:00+00:00"
    signature: str       # hex-encoded Ed25519 signature

    @field_validator("endpoint")
    @classmethod
    def endpoint_must_be_http(cls, v: str) -> str:
        if not (v.startswith("http://") or v.startswith("https://")):
            raise ValueError("endpoint must start with http:// or https://")
        return v

    @field_validator("public_key_hex")
    @classmethod
    def pubkey_must_be_64_hex(cls, v: str) -> str:
        if len(v) != 64:
            raise ValueError("public_key_hex must be 64 hex characters")
        try:
            bytes.fromhex(v)
        except ValueError:
            raise ValueError("public_key_hex must be valid hex")
        return v


class NodeRecord(BaseModel):
    node_id: str
    did: str
    endpoint: str
    public_key_hex: str
    registered_at: str


# ---------------------------------------------------------------------------
# In-memory registry (thread-safe)
# ---------------------------------------------------------------------------

class _Registry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, NodeRecord] = {}

    def get(self, node_id: str) -> NodeRecord | None:
        with self._lock:
            return self._records.get(node_id)

    def put(self, record: NodeRecord) -> None:
        with self._lock:
            self._records[node_id := record.node_id] = record

    def all(self) -> list[NodeRecord]:
        with self._lock:
            return list(self._records.values())


# ---------------------------------------------------------------------------
# Signature verification helper
# ---------------------------------------------------------------------------

def _signed_message(node_id: str, endpoint: str, timestamp_utc: str,
                    public_key_hex: str = "", did: str = "") -> bytes:
    """Canonical signed payload: binds key material to prevent MITM substitution."""
    return f"{node_id}|{endpoint}|{timestamp_utc}|{public_key_hex}|{did}".encode("utf-8")


def _verify_timestamp(timestamp_utc: str) -> None:
    """Raise HTTPException 400 if timestamp is outside the allowed window."""
    try:
        ts = datetime.fromisoformat(timestamp_utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="invalid timestamp format")
    now = datetime.now(timezone.utc)
    # Normalise to UTC
    if ts.tzinfo is None:
        raise HTTPException(status_code=400, detail="timestamp must include timezone")
    skew = abs((now - ts).total_seconds())
    if skew > ANNOUNCE_WINDOW_SECONDS:
        raise HTTPException(
            status_code=400,
            detail=f"timestamp outside allowed window ({skew:.0f}s > {ANNOUNCE_WINDOW_SECONDS}s)",
        )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def make_app(registry: _Registry | None = None) -> FastAPI:
    """Return a configured FastAPI app.  Pass a registry for testing."""
    _reg = registry or _Registry()
    app = FastAPI(title="LLMesh Rendezvous", version="0.1.0")

    @app.post("/announce", status_code=201)
    def announce(req: AnnounceRequest) -> dict[str, Any]:
        # 0. Endpoint SSRF check
        try:
            _endpoint_validator.validate(req.endpoint)
        except EndpointValidationError as exc:
            raise HTTPException(status_code=422, detail=f"invalid_endpoint:{exc}")

        # 1. Timestamp freshness check
        _verify_timestamp(req.timestamp_utc)

        # 2. Signature verification
        msg = _signed_message(req.node_id, req.endpoint, req.timestamp_utc,
                              req.public_key_hex, req.did)
        try:
            sig_bytes = bytes.fromhex(req.signature)
        except ValueError:
            raise HTTPException(status_code=400, detail="signature must be valid hex")

        if not NodeIdentity.verify_with_public_hex(msg, sig_bytes, req.public_key_hex):
            raise HTTPException(status_code=403, detail="signature verification failed")

        # 3. TOFU: existing record must not change public key
        existing = _reg.get(req.node_id)
        if existing and existing.public_key_hex != req.public_key_hex:
            raise HTTPException(
                status_code=409,
                detail="public_key_hex mismatch for known node_id (TOFU violation)",
            )

        # 4. Store
        record = NodeRecord(
            node_id=req.node_id,
            did=req.did,
            endpoint=req.endpoint,
            public_key_hex=req.public_key_hex,
            registered_at=datetime.now(timezone.utc).isoformat(),
        )
        _reg.put(record)
        return {"status": "registered", "node_id": req.node_id}

    @app.get("/lookup/{node_id}")
    def lookup(node_id: str) -> dict[str, Any]:
        record = _reg.get(node_id)
        if record is None:
            raise HTTPException(status_code=404, detail=f"node {node_id!r} not found")
        return record.model_dump()

    @app.get("/nodes")
    def list_nodes() -> list[dict[str, Any]]:
        return [r.model_dump() for r in _reg.all()]

    return app
