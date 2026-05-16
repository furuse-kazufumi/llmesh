"""SkillChunk — signed, Merkle-verified unit of replicable knowledge (RFC Phase 3.1).

See `docs/llmesh_p2p_phase3_skill_chunk_rfc.md` for the design contract.
"""
from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field, replace
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

from llmesh.skills.merkle import DEFAULT_CHUNK_SIZE, compute_merkle_root

SCHEMA_VERSION = 1


class SkillChunkError(Exception):
    """Raised on signature, hash, or schema verification failure."""


def _signable_payload(
    *,
    schema_version: int,
    skill_id: str,
    version: str,
    content_sha256: str,
    merkle_root: str,
    created_by: str,
) -> bytes:
    """Canonical bytes that are signed. Field order is fixed."""
    return json.dumps(
        {
            "schema_version": schema_version,
            "skill_id": skill_id,
            "version": version,
            "content_sha256": content_sha256,
            "merkle_root": merkle_root,
            "created_by": created_by,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class SkillChunk:
    """A signed unit of skill knowledge.

    Body is stored as ``bytes`` in memory; ``to_json()`` base64-encodes it.
    Verify integrity with ``verify(public_key_hex)`` — checks content hash,
    Merkle root, and Ed25519 signature in one call.
    """

    schema_version: int
    skill_id: str
    version: str
    body: bytes
    license: str
    license_url: str = ""
    language: str = ""
    domains: tuple[str, ...] = field(default_factory=tuple)
    model_size_hint: str = ""
    data_level: int = 0
    created_by: str = ""
    merkle_chunk_size: int = DEFAULT_CHUNK_SIZE
    signature: str = ""  # 128 hex (Ed25519)

    # Derived / cached
    content_sha256: str = ""
    merkle_root: str = ""
    size_bytes: int = 0

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create_unsigned(
        cls,
        *,
        skill_id: str,
        version: str,
        body: bytes,
        license: str,  # noqa: A002 — domain term
        license_url: str = "",
        language: str = "",
        domains: tuple[str, ...] | list[str] = (),
        model_size_hint: str = "",
        data_level: int = 0,
        created_by: str = "",
        merkle_chunk_size: int = DEFAULT_CHUNK_SIZE,
    ) -> SkillChunk:
        """Build a chunk with derived hashes filled in but no signature yet."""
        content_hash = hashlib.sha256(body).hexdigest()
        m_root = compute_merkle_root(body, merkle_chunk_size)
        return cls(
            schema_version=SCHEMA_VERSION,
            skill_id=skill_id,
            version=version,
            body=body,
            license=license,
            license_url=license_url,
            language=language,
            domains=tuple(domains),
            model_size_hint=model_size_hint,
            data_level=data_level,
            created_by=created_by,
            merkle_chunk_size=merkle_chunk_size,
            signature="",
            content_sha256=content_hash,
            merkle_root=m_root,
            size_bytes=len(body),
        )

    def sign(self, private_key: Ed25519PrivateKey) -> SkillChunk:
        """Return a copy of self with the signature populated."""
        payload = _signable_payload(
            schema_version=self.schema_version,
            skill_id=self.skill_id,
            version=self.version,
            content_sha256=self.content_sha256,
            merkle_root=self.merkle_root,
            created_by=self.created_by,
        )
        sig = private_key.sign(payload).hex()
        return replace(self, signature=sig)

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self, public_key: Ed25519PublicKey) -> None:
        """Raise SkillChunkError if any integrity check fails."""
        if self.schema_version != SCHEMA_VERSION:
            raise SkillChunkError(
                f"unsupported schema_version: {self.schema_version!r}"
            )
        expected_hash = hashlib.sha256(self.body).hexdigest()
        if expected_hash != self.content_sha256:
            raise SkillChunkError(
                f"content_sha256 mismatch (expected {expected_hash!r}, got {self.content_sha256!r})"
            )
        expected_root = compute_merkle_root(self.body, self.merkle_chunk_size)
        if expected_root != self.merkle_root:
            raise SkillChunkError(
                f"merkle_root mismatch (expected {expected_root!r}, got {self.merkle_root!r})"
            )
        if not self.signature:
            raise SkillChunkError("missing signature")
        payload = _signable_payload(
            schema_version=self.schema_version,
            skill_id=self.skill_id,
            version=self.version,
            content_sha256=self.content_sha256,
            merkle_root=self.merkle_root,
            created_by=self.created_by,
        )
        try:
            public_key.verify(bytes.fromhex(self.signature), payload)
        except InvalidSignature as exc:
            raise SkillChunkError(f"Ed25519 signature verification failed: {exc}") from exc

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "skill_id": self.skill_id,
            "version": self.version,
            "size_bytes": self.size_bytes,
            "content_sha256": self.content_sha256,
            "merkle_root": self.merkle_root,
            "merkle_chunk_size": self.merkle_chunk_size,
            "license": self.license,
            "license_url": self.license_url,
            "language": self.language,
            "domains": list(self.domains),
            "model_size_hint": self.model_size_hint,
            "data_level": self.data_level,
            "created_by": self.created_by,
            "signature": self.signature,
            "body_b64": base64.b64encode(self.body).decode("ascii"),
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> SkillChunk:
        try:
            body = base64.b64decode(data["body_b64"])
        except Exception as exc:
            raise SkillChunkError(f"body_b64 decode failed: {exc}") from exc
        return cls(
            schema_version=int(data["schema_version"]),
            skill_id=str(data["skill_id"]),
            version=str(data["version"]),
            body=body,
            license=str(data["license"]),
            license_url=str(data.get("license_url", "")),
            language=str(data.get("language", "")),
            domains=tuple(str(d) for d in data.get("domains", [])),
            model_size_hint=str(data.get("model_size_hint", "")),
            data_level=int(data.get("data_level", 0)),
            created_by=str(data.get("created_by", "")),
            merkle_chunk_size=int(data.get("merkle_chunk_size", DEFAULT_CHUNK_SIZE)),
            signature=str(data.get("signature", "")),
            content_sha256=str(data["content_sha256"]),
            merkle_root=str(data["merkle_root"]),
            size_bytes=int(data.get("size_bytes", len(body))),
        )


__all__ = ["SCHEMA_VERSION", "SkillChunk", "SkillChunkError"]
