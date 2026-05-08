"""ServiceReceipt — recipient-signed proof of service for fairness accounting."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass


@dataclass
class ServiceReceipt:
    server_node_id: str   # who served
    client_node_id: str   # who consumed (signer)
    tool_name: str
    task_id: str
    timestamp: float
    client_pub_hex: str   # client's Ed25519 public key for offline verification
    signature: bytes      # client's Ed25519 signature over canonical payload

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        server_node_id: str,
        client_node_id: str,
        tool_name: str,
        task_id: str,
        client_identity,  # NodeIdentity
        timestamp: float | None = None,
    ) -> "ServiceReceipt":
        """Create and sign a receipt. client_identity must be the consuming node."""
        ts = timestamp if timestamp is not None else time.time()
        payload = _canonical_payload(server_node_id, client_node_id, tool_name, task_id, ts)
        sig = client_identity.sign(payload)
        return cls(
            server_node_id=server_node_id,
            client_node_id=client_node_id,
            tool_name=tool_name,
            task_id=task_id,
            timestamp=ts,
            client_pub_hex=client_identity.public_key_hex,
            signature=sig,
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    def verify(self) -> bool:
        """Verify the client's Ed25519 signature over this receipt."""
        from ..identity.node_id import NodeIdentity
        payload = _canonical_payload(
            self.server_node_id, self.client_node_id,
            self.tool_name, self.task_id, self.timestamp,
        )
        return NodeIdentity.verify_with_public_hex(payload, self.signature, self.client_pub_hex)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "server_node_id": self.server_node_id,
            "client_node_id": self.client_node_id,
            "tool_name": self.tool_name,
            "task_id": self.task_id,
            "timestamp": self.timestamp,
            "client_pub_hex": self.client_pub_hex,
            "signature": self.signature.hex(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ServiceReceipt":
        return cls(
            server_node_id=d["server_node_id"],
            client_node_id=d["client_node_id"],
            tool_name=d["tool_name"],
            task_id=d["task_id"],
            timestamp=float(d["timestamp"]),
            client_pub_hex=d["client_pub_hex"],
            signature=bytes.fromhex(d["signature"]),
        )


def _canonical_payload(
    server_node_id: str,
    client_node_id: str,
    tool_name: str,
    task_id: str,
    timestamp: float,
) -> bytes:
    """Deterministic canonical bytes for signing — must never change."""
    doc = {
        "client_node_id": client_node_id,
        "server_node_id": server_node_id,
        "task_id": task_id,
        "timestamp": timestamp,
        "tool_name": tool_name,
    }
    return json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()
