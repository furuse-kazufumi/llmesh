"""DID Resolver for the did:llmesh:1: method.

Resolves did:llmesh:1: identifiers (Ed25519) to W3C DID Documents.
Also maintains a local registry for peer-registered documents
(used by P2P discovery and web-of-trust layers).

Spec references:
  - W3C DID Core 1.0: https://www.w3.org/TR/did-core/
  - did:key method:   https://w3c-ccg.github.io/did-method-key/ (basis for did:llmesh:1:)

Security invariants:
  - No network calls — all resolution is local / deterministic
  - Shell=True, eval, exec, pickle are never used
  - Inputs validated before any key material is extracted
"""
from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

# Multicodec prefix for Ed25519 public key (matches node_id.py)
_ED25519_MULTICODEC = b"\xed\x01"
_ED25519_MULTICODEC_LEN = len(_ED25519_MULTICODEC)
_ED25519_PUBKEY_LEN = 32  # bytes

# Base58 alphabet (Bitcoin) — must match node_id._b58encode
_B58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_MAP = {ch: i for i, ch in enumerate(_B58_ALPHABET)}

_DID_LLMESH_PREFIX = "did:llmesh:1:z"

_DID_CONTEXT = [
    "https://www.w3.org/ns/did/v1",
    "https://w3id.org/security/suites/ed25519-2020/v1",
]


class DIDResolutionError(Exception):
    """Raised when a DID cannot be resolved."""


# ---------------------------------------------------------------------------
# DID Document
# ---------------------------------------------------------------------------

@dataclass
class VerificationMethod:
    id: str
    type: str
    controller: str
    public_key_multibase: str  # "z" + base58btc(raw pubkey)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.type,
            "controller": self.controller,
            "publicKeyMultibase": self.public_key_multibase,
        }


@dataclass
class DIDDocument:
    """W3C DID Document for a did:key Ed25519 identity."""

    id: str
    context: list[str] = field(default_factory=lambda: list(_DID_CONTEXT))
    verification_method: list[VerificationMethod] = field(default_factory=list)
    authentication: list[str] = field(default_factory=list)
    assertion_method: list[str] = field(default_factory=list)
    capability_invocation: list[str] = field(default_factory=list)
    capability_delegation: list[str] = field(default_factory=list)

    @property
    def public_key_bytes(self) -> bytes:
        """Raw Ed25519 public key bytes extracted from the first verification method."""
        if not self.verification_method:
            raise DIDResolutionError("no verification method in document")
        pkm = self.verification_method[0].public_key_multibase
        if not pkm.startswith("z"):
            raise DIDResolutionError("publicKeyMultibase must start with 'z' (base58btc)")
        return _b58decode(pkm[1:])

    def to_dict(self) -> dict[str, Any]:
        return {
            "@context": self.context,
            "id": self.id,
            "verificationMethod": [vm.to_dict() for vm in self.verification_method],
            "authentication": self.authentication,
            "assertionMethod": self.assertion_method,
            "capabilityInvocation": self.capability_invocation,
            "capabilityDelegation": self.capability_delegation,
        }


# ---------------------------------------------------------------------------
# Base58 decode (stdlib only)
# ---------------------------------------------------------------------------

def _b58decode(s: str) -> bytes:
    """Decode a Base58 (Bitcoin alphabet) string to bytes."""
    num = 0
    for ch in s:
        if ch not in _B58_MAP:
            raise DIDResolutionError(f"invalid base58 character: {ch!r}")
        num = num * 58 + _B58_MAP[ch]

    # Count leading '1' characters → leading zero bytes
    pad = 0
    for ch in s:
        if ch == "1":
            pad += 1
        else:
            break

    result = num.to_bytes((num.bit_length() + 7) // 8, "big") if num else b""
    return b"\x00" * pad + result


# ---------------------------------------------------------------------------
# Core resolution logic
# ---------------------------------------------------------------------------

def _resolve_did_llmesh(did: str) -> DIDDocument:
    """Deterministically resolve a did:llmesh:1: identifier to a DID Document.

    Supports Ed25519 keys (multicodec prefix 0xed01) only.
    Raises DIDResolutionError for any invalid or unsupported input.
    """
    if not did.startswith(_DID_LLMESH_PREFIX):
        raise DIDResolutionError(f"not a did:llmesh:1: identifier: {did!r}")

    encoded = did[len(_DID_LLMESH_PREFIX):]  # strip "did:llmesh:1:z"
    if not encoded:
        raise DIDResolutionError("did:llmesh:1: identifier has empty key material")

    try:
        multicodec_key = _b58decode(encoded)
    except DIDResolutionError:
        raise
    except Exception as exc:
        raise DIDResolutionError(f"base58 decode failed: {exc}") from exc

    if not multicodec_key.startswith(_ED25519_MULTICODEC):
        prefix = multicodec_key[:2].hex() if len(multicodec_key) >= 2 else multicodec_key.hex()
        raise DIDResolutionError(
            f"unsupported key type (multicodec prefix 0x{prefix}, expected 0xed01)"
        )

    pub_bytes = multicodec_key[_ED25519_MULTICODEC_LEN:]
    if len(pub_bytes) != _ED25519_PUBKEY_LEN:
        raise DIDResolutionError(
            f"Ed25519 public key must be 32 bytes, got {len(pub_bytes)}"
        )

    # publicKeyMultibase = "z" + base58btc(raw pubkey) — single-key multibase
    from .node_id import _b58encode
    public_key_multibase = "z" + _b58encode(pub_bytes)

    vm_id = f"{did}#{did[len('did:llmesh:1:'):]}"  # fragment = full key identifier
    vm = VerificationMethod(
        id=vm_id,
        type="Ed25519VerificationKey2020",
        controller=did,
        public_key_multibase=public_key_multibase,
    )

    return DIDDocument(
        id=did,
        verification_method=[vm],
        authentication=[vm_id],
        assertion_method=[vm_id],
        capability_invocation=[vm_id],
        capability_delegation=[vm_id],
    )


# ---------------------------------------------------------------------------
# Resolver (with local registry for P2P)
# ---------------------------------------------------------------------------

class DIDResolver:
    """Resolve did:key identifiers, with an optional peer registry.

    The registry allows remote nodes to pre-register their DID Documents
    (e.g. received via a capability manifest) so that signature verification
    can be performed without re-deriving from the DID string alone.

    For did:key the registry is redundant (resolution is deterministic), but
    it provides the hook for future did:web or did:peer support.
    """

    def __init__(self) -> None:
        self._registry: dict[str, DIDDocument] = {}

    def resolve(self, did: str) -> DIDDocument:
        """Resolve a DID to a DIDDocument.

        Checks the local registry first; falls back to deterministic resolution
        for did:key identifiers.

        Raises DIDResolutionError if the DID cannot be resolved.
        """
        if did in self._registry:
            return self._registry[did]
        if did.startswith(_DID_LLMESH_PREFIX):
            doc = _resolve_did_llmesh(did)
            self._registry[did] = doc  # cache for subsequent calls
            return doc
        raise DIDResolutionError(f"unsupported DID method: {did!r}")

    def register(self, doc: DIDDocument) -> None:
        """Pre-register a DIDDocument received from a peer node.

        The document is stored as-is; callers are responsible for verifying
        the manifest signature before calling register().
        """
        self._registry[doc.id] = doc

    def is_registered(self, did: str) -> bool:
        """Return True if the DID is in the local registry."""
        return did in self._registry

    def public_key_bytes(self, did: str) -> bytes:
        """Convenience: resolve and return raw Ed25519 public key bytes."""
        return self.resolve(did).public_key_bytes
