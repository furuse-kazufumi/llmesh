"""Protocol-agnostic message envelope for LLMesh node-to-node communication."""
from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class MessageType(str, Enum):
    REQUEST = "request"
    RESPONSE = "response"
    STREAM_CHUNK = "stream_chunk"
    STREAM_END = "stream_end"
    STREAM_ACK = "stream_ack"       # receiver → sender: all chunks received, buffer can be dropped
    RETRANSMIT = "retransmit"       # receiver → sender: missing chunks, sent once on timeout
    BROADCAST = "broadcast"
    ERROR = "error"


@dataclass(frozen=True)
class NodeAddress:
    host: str
    port: int
    node_id: str = ""

    def __str__(self) -> str:
        return f"{self.host}:{self.port}"

    def to_dict(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "node_id": self.node_id}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> NodeAddress:
        return cls(host=d["host"], port=d["port"], node_id=d.get("node_id", ""))


@dataclass
class UnifiedMessage:
    """Transport-agnostic message. Serializes to/from JSON bytes.

    All adapters (HTTP, TCP, UDP) exchange UnifiedMessage objects.
    Callers never interact with protocol-specific wire formats.
    """

    type: MessageType
    payload: dict[str, Any]
    sender: NodeAddress
    target: NodeAddress | None = None
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    correlation_id: str | None = None
    timestamp: float = field(default_factory=time.time)
    ttl: int = 3
    # Stream ordering: set on STREAM_CHUNK / STREAM_END messages.
    # sequence_no=0 is the first chunk; STREAM_END carries total_chunks.
    sequence_no: int | None = None
    total_chunks: int | None = None
    # QoS (B-1)
    priority: int = 0          # higher = more urgent; 0 = normal
    deadline: float | None = None  # Unix timestamp; None = no expiry
    # Routing (B-5): ordered list of node_ids this message has already passed through
    route: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # Convenience constructors
    # ------------------------------------------------------------------

    @classmethod
    def request(
        cls,
        payload: dict[str, Any],
        sender: NodeAddress,
        target: NodeAddress | None = None,
    ) -> UnifiedMessage:
        return cls(type=MessageType.REQUEST, payload=payload, sender=sender, target=target)

    @classmethod
    def broadcast(
        cls,
        payload: dict[str, Any],
        sender: NodeAddress,
        ttl: int = 3,
    ) -> UnifiedMessage:
        return cls(
            type=MessageType.BROADCAST,
            payload=payload,
            sender=sender,
            ttl=ttl,
        )

    # ------------------------------------------------------------------
    # Reply helper
    # ------------------------------------------------------------------

    def make_response(
        self,
        payload: dict[str, Any],
        sender: NodeAddress,
        *,
        error: bool = False,
    ) -> UnifiedMessage:
        """Return a response correlated to this message."""
        return UnifiedMessage(
            type=MessageType.ERROR if error else MessageType.RESPONSE,
            payload=payload,
            sender=sender,
            target=self.sender,
            correlation_id=self.id,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Stream chunk constructor
    # ------------------------------------------------------------------

    @classmethod
    def chunk(
        cls,
        payload: dict[str, Any],
        sender: NodeAddress,
        *,
        stream_id: str,
        sequence_no: int,
        total_chunks: int | None = None,
    ) -> UnifiedMessage:
        """Create a STREAM_CHUNK (or STREAM_END for the final chunk).

        Args:
            stream_id:    Shared correlation_id that groups all chunks.
            sequence_no:  Zero-based position within the stream.
            total_chunks: Set only on the final chunk (becomes STREAM_END).
        """
        msg_type = MessageType.STREAM_END if total_chunks is not None else MessageType.STREAM_CHUNK
        return cls(
            type=msg_type,
            payload=payload,
            sender=sender,
            correlation_id=stream_id,
            sequence_no=sequence_no,
            total_chunks=total_chunks,
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "id": self.id,
            "type": self.type.value,
            "payload": self.payload,
            "sender": self.sender.to_dict(),
            "target": self.target.to_dict() if self.target else None,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "ttl": self.ttl,
        }
        if self.sequence_no is not None:
            d["sequence_no"] = self.sequence_no
        if self.total_chunks is not None:
            d["total_chunks"] = self.total_chunks
        if self.priority != 0:
            d["priority"] = self.priority
        if self.deadline is not None:
            d["deadline"] = self.deadline
        if self.route:
            d["route"] = self.route
        return d

    def to_bytes(self, codec: str = "json") -> bytes:
        from .codec import encode
        return encode(self.to_dict(), codec)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> UnifiedMessage:
        target_d = d.get("target")
        return cls(
            id=d["id"],
            type=MessageType(d["type"]),
            payload=d["payload"],
            sender=NodeAddress.from_dict(d["sender"]),
            target=NodeAddress.from_dict(target_d) if target_d else None,
            correlation_id=d.get("correlation_id"),
            timestamp=d["timestamp"],
            ttl=d.get("ttl", 3),
            sequence_no=d.get("sequence_no"),
            total_chunks=d.get("total_chunks"),
            priority=d.get("priority", 0),
            deadline=d.get("deadline"),
            route=d.get("route", []),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> UnifiedMessage:
        from .codec import decode
        return cls.from_dict(decode(data))
