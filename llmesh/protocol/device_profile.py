"""DeviceProfile — capability descriptor for LLMesh nodes.

Two profiles:
  FULL  — default; all features enabled (Ed25519 signing, TCPStream, any payload size)
  NANO  — constrained devices; signing optional, UDP-only, 1 KB payload cap

Environment variable overrides:
  LLMESH_NANO_NO_CRYPTO=1  — disable Ed25519 signing for NANO nodes
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from enum import Enum


class ProfileType(str, Enum):
    FULL = "full"
    NANO = "nano"


_NANO_MAX_PAYLOAD = 1024          # bytes
_NANO_ALLOWED_PROTOCOLS = {"udp"}


@dataclass
class DeviceProfile:
    """Runtime capability profile for a LLMesh node.

    Args:
        profile_type:   FULL (default) or NANO.
        no_crypto:      If True, Ed25519 signing is skipped (NANO only).
                        Automatically set from LLMESH_NANO_NO_CRYPTO env var.
        max_payload:    Maximum outgoing payload in bytes.
                        NANO default is 1024; FULL has no enforced limit (None).
        allowed_protocols: Set of protocol names this node may use.
    """

    profile_type: ProfileType = ProfileType.FULL
    no_crypto: bool = field(default_factory=lambda: bool(os.environ.get("LLMESH_NANO_NO_CRYPTO", "")))
    max_payload: int | None = None
    allowed_protocols: set[str] = field(default_factory=lambda: {"http", "tcp", "tcp_stream", "udp"})

    def __post_init__(self) -> None:
        if self.profile_type == ProfileType.NANO:
            if self.max_payload is None:
                self.max_payload = _NANO_MAX_PAYLOAD
            self.allowed_protocols = _NANO_ALLOWED_PROTOCOLS.copy()
        else:
            # FULL: no_crypto always False regardless of env var
            self.no_crypto = False

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def full(cls) -> "DeviceProfile":
        return cls(profile_type=ProfileType.FULL)

    @classmethod
    def nano(cls, *, no_crypto: bool | None = None) -> "DeviceProfile":
        """Create a NANO profile.

        Args:
            no_crypto: Override signing behaviour. Defaults to LLMESH_NANO_NO_CRYPTO env var.
        """
        profile = cls(profile_type=ProfileType.NANO)
        if no_crypto is not None:
            profile.no_crypto = no_crypto
        return profile

    # ------------------------------------------------------------------
    # Guard helpers
    # ------------------------------------------------------------------

    def check_payload(self, size: int) -> None:
        """Raise PayloadTooLargeError if *size* exceeds this profile's limit."""
        if self.max_payload is not None and size > self.max_payload:
            raise PayloadTooLargeError(
                f"payload {size} bytes exceeds {self.profile_type.value} limit "
                f"of {self.max_payload} bytes"
            )

    def check_protocol(self, protocol: str) -> None:
        """Raise ProtocolNotAllowedError if *protocol* is not permitted."""
        if protocol not in self.allowed_protocols:
            raise ProtocolNotAllowedError(
                f"protocol {protocol!r} not allowed for "
                f"{self.profile_type.value} profile "
                f"(allowed: {sorted(self.allowed_protocols)})"
            )

    @property
    def is_nano(self) -> bool:
        return self.profile_type == ProfileType.NANO

    @property
    def signing_enabled(self) -> bool:
        return not self.no_crypto


class PayloadTooLargeError(Exception):
    """Raised when a message payload exceeds the DeviceProfile limit."""


class ProtocolNotAllowedError(Exception):
    """Raised when a protocol is not permitted by the DeviceProfile."""
