"""SpeculativeManifest — Ed25519-signed prediction of a future branch.

A node doing main inference predicts the **next branches** it is likely to
explore (candidate ChangeOps / Briefs) and ships them to idle mesh peers for
*speculative* execution. Each prediction is captured as a
:class:`SpeculativeManifest`, signed with the origin node's Ed25519 key so that
a receiving peer can prove the manifest really came from the claimed origin
(tamper-evidence over an untrusted mesh).

Signing scheme mirrors :mod:`llmesh.auth.signer`: a deterministic canonical byte
string is signed, and the signature binds to the exact manifest contents.

    manifest = SpeculativeManifest.new(origin_node_id=ident.node_id, branch={...})
    signed = sign_manifest(manifest, ident)
    assert signed.verify()                       # Ed25519 over canonical bytes
    cache_key = signed.manifest_hash             # sha256(canonical) — mesh cache key
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from ..identity.node_id import NodeIdentity


class SignatureError(Exception):
    """Raised when an Ed25519 signature is missing or fails verification."""


def _canonical_json(payload: dict[str, Any]) -> bytes:
    """Deterministic JSON encoding used as the signed / hashed representation."""
    return json.dumps(
        payload,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class SpeculativeManifest:
    """A single predicted branch awaiting speculative execution.

    Parameters
    ----------
    manifest_id:
        Unique id of this prediction (origin-local).
    origin_node_id:
        ``peer:...`` id of the node that produced the prediction.
    branch:
        Opaque payload describing the predicted work (e.g. a ChangeOp / Brief
        dict). The coordinator does not interpret it — it is forwarded to the
        executing peer verbatim.
    created_at_ms:
        Unix milliseconds at prediction time.
    priority:
        Scheduling hint. Speculative work is **low priority** by convention
        (``<= 0``) so it never preempts a peer's confirmed tasks.
    """

    manifest_id: str
    origin_node_id: str
    branch: dict[str, Any]
    created_at_ms: int
    priority: int = 0

    @classmethod
    def new(
        cls,
        *,
        origin_node_id: str,
        branch: dict[str, Any],
        priority: int = 0,
        manifest_id: str | None = None,
        created_at_ms: int | None = None,
    ) -> SpeculativeManifest:
        return cls(
            manifest_id=manifest_id or uuid.uuid4().hex,
            origin_node_id=origin_node_id,
            branch=dict(branch),
            created_at_ms=int(created_at_ms if created_at_ms is not None else time.time() * 1000),
            priority=int(priority),
        )

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SpeculativeManifest:
        """Reconstruct a manifest from its :meth:`to_dict` form (wire decode).

        fail-closed: missing/malformed fields raise (``KeyError`` / ``TypeError`` /
        ``ValueError``); callers decoding untrusted peer payloads must catch and
        reject. The reconstructed manifest still has to pass signature
        verification before it is trusted.
        """
        return cls(
            manifest_id=str(d["manifest_id"]),
            origin_node_id=str(d["origin_node_id"]),
            branch=dict(d["branch"]),
            created_at_ms=int(d["created_at_ms"]),
            priority=int(d.get("priority", 0)),
        )

    def _canonical_payload(self) -> dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "origin_node_id": self.origin_node_id,
            "branch": self.branch,
            "created_at_ms": self.created_at_ms,
            "priority": self.priority,
        }

    def canonical_bytes(self) -> bytes:
        """Deterministic byte string that is signed and hashed."""
        return _canonical_json(self._canonical_payload())

    @property
    def manifest_hash(self) -> str:
        """sha256 of the canonical bytes — the mesh-wide cache key."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return self._canonical_payload()


@dataclass(frozen=True)
class SignedManifest:
    """A :class:`SpeculativeManifest` plus its Ed25519 signature + origin pubkey.

    The signature is over :meth:`SpeculativeManifest.canonical_bytes`, so any
    mutation of the manifest invalidates it.
    """

    manifest: SpeculativeManifest
    origin_pub_hex: str
    signature_hex: str
    speculative: bool = field(default=True)

    @property
    def manifest_hash(self) -> str:
        return self.manifest.manifest_hash

    def verify(self) -> bool:
        """Return True iff the signature is valid for the manifest + pubkey.

        fail-closed: any error (bad hex, wrong key, tampered manifest) → False.
        """
        try:
            sig = bytes.fromhex(self.signature_hex)
        except ValueError:
            return False
        return NodeIdentity.verify_with_public_hex(
            self.manifest.canonical_bytes(), sig, self.origin_pub_hex
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "manifest": self.manifest.to_dict(),
            "origin_pub_hex": self.origin_pub_hex,
            "signature_hex": self.signature_hex,
            "speculative": self.speculative,
        }


def sign_manifest(
    manifest: SpeculativeManifest, identity: NodeIdentity, *, speculative: bool = True
) -> SignedManifest:
    """Sign a manifest with the origin node's Ed25519 key.

    Raises
    ------
    SignatureError
        If ``manifest.origin_node_id`` does not match ``identity.node_id``
        (a node may only sign its own predictions — fail-closed).
    """
    if manifest.origin_node_id != identity.node_id:
        raise SignatureError(
            f"origin_node_id {manifest.origin_node_id!r} != signer {identity.node_id!r}"
        )
    sig = identity.sign(manifest.canonical_bytes())
    return SignedManifest(
        manifest=manifest,
        origin_pub_hex=identity.public_key_hex,
        signature_hex=sig.hex(),
        speculative=speculative,
    )


__all__ = [
    "SignatureError",
    "SignedManifest",
    "SpeculativeManifest",
    "sign_manifest",
]
