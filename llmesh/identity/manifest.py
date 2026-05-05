"""Capability Manifest — signed, TTL-enforced node advertisement."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .node_id import NodeIdentity


class ManifestVerificationError(Exception):
    """Raised when manifest signature or TTL check fails."""


@dataclass
class CapabilityManifest:
    """Signed Capability Manifest for a LLMesh node.

    Fields mirror the spec schema.  Call ``sign()`` before publishing.
    Verify remote manifests with ``CapabilityManifest.verify()``.
    """

    schema_version: str
    node_id: str
    did: str
    issued_at: str          # ISO-8601 UTC
    expires_at: str         # ISO-8601 UTC
    display_name: str
    owner_type: str         # "individual" | "org" | "anonymous"
    subnets: list[str]
    tools: list[str]
    models: list[dict[str, Any]] = field(default_factory=list)
    privacy_policy: dict[str, Any] = field(default_factory=dict)
    performance: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    revocation_endpoint: str = ""
    revocation_token_hash: str = ""
    signature: str = ""     # "ed25519:<hex>" — populated by sign()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        identity: NodeIdentity,
        display_name: str,
        tools: list[str],
        subnets: list[str] | None = None,
        ttl_seconds: int = 86_400,
        owner_type: str = "individual",
    ) -> "CapabilityManifest":
        now = datetime.now(timezone.utc)
        expires = datetime.fromtimestamp(
            now.timestamp() + ttl_seconds, tz=timezone.utc
        )
        return cls(
            schema_version="0.1.0",
            node_id=identity.node_id,
            did=identity.did_key,
            issued_at=now.isoformat(),
            expires_at=expires.isoformat(),
            display_name=display_name,
            owner_type=owner_type,
            subnets=subnets or ["code-dev"],
            tools=tools,
            privacy_policy={
                "accepts_data_levels": ["L0", "L1"],
                "stores_prompts": False,
                "stores_outputs": False,
                "supports_tee": False,
            },
        )

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def _signable_bytes(self) -> bytes:
        """Canonical bytes that are signed — excludes the signature field."""
        payload = {k: v for k, v in self.__dict__.items() if k != "signature"}
        return json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CapabilityManifest":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def sign(self, identity: NodeIdentity) -> None:
        """Sign in-place using the node's Ed25519 private key."""
        sig_bytes = identity.sign(self._signable_bytes())
        self.signature = "ed25519:" + sig_bytes.hex()

    # ------------------------------------------------------------------
    # Verification (fail-closed: any error raises)
    # ------------------------------------------------------------------

    def verify(self, pub_hex: str | None = None) -> None:
        """Verify signature and TTL.

        Raises ManifestVerificationError on any failure.
        """
        self._check_expiry()
        self._check_signature(pub_hex)

    def _check_expiry(self) -> None:
        try:
            expires = datetime.fromisoformat(self.expires_at)
        except ValueError as exc:
            raise ManifestVerificationError(f"invalid expires_at: {exc}") from exc
        now = datetime.now(timezone.utc)
        if now > expires:
            raise ManifestVerificationError(
                f"manifest expired at {self.expires_at}"
            )

    def _check_signature(self, pub_hex: str | None) -> None:
        if not self.signature.startswith("ed25519:"):
            raise ManifestVerificationError("missing or malformed signature")
        if pub_hex is None:
            return  # caller chose to skip sig verification (local node)
        sig_hex = self.signature.removeprefix("ed25519:")
        try:
            sig_bytes = bytes.fromhex(sig_hex)
        except ValueError as exc:
            raise ManifestVerificationError(f"bad signature hex: {exc}") from exc

        ok = NodeIdentity.verify_with_public_hex(
            self._signable_bytes(), sig_bytes, pub_hex
        )
        if not ok:
            raise ManifestVerificationError("signature verification failed")
