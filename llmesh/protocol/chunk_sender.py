"""ChunkSender — sender-side chunk buffer for reliable stream delivery.

The sender buffers every chunk it dispatches.  When the receiver confirms
full delivery (STREAM_ACK) the buffer is freed.  If the receiver detects a
gap and sends a RETRANSMIT request, the sender re-delivers only the missing
chunks.  A TTL-based safety net prevents unbounded memory growth when ACKs
are never received.

Usage::

    sender = ChunkSender(ttl_s=120)

    # Before sending each chunk, register it
    chunks = [UnifiedMessage.chunk(...) for ...]
    sender.buffer(stream_id, chunks)
    for c in chunks:
        await adapter.send(c, target)

    # When a RETRANSMIT message arrives from the receiver
    async def on_incoming(msg):
        if msg.type == MessageType.RETRANSMIT:
            for chunk in sender.handle_retransmit(msg):
                await adapter.send(chunk, msg.sender)
        elif msg.type == MessageType.STREAM_ACK:
            sender.handle_ack(msg)

    # Periodically evict stale buffers (e.g. in a background task)
    sender.expire_old()
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .message import MessageType, UnifiedMessage

_DEFAULT_TTL = 300.0  # seconds


@dataclass
class _SenderBuffer:
    stream_id: str
    chunks: dict[int, UnifiedMessage]   # seq_no → chunk
    created_at: float = field(default_factory=time.monotonic)


class ChunkSender:
    """Sender-side chunk buffer.

    Thread-unsafe; use one instance per async task / connection.

    Args:
        ttl_s: How long to keep a buffer after creation even if no ACK
               arrives.  Acts as a safety net for lost ACKs.
    """

    def __init__(self, ttl_s: float = _DEFAULT_TTL) -> None:
        self._ttl = ttl_s
        self._buffers: dict[str, _SenderBuffer] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def buffer(self, stream_id: str, chunks: list[UnifiedMessage]) -> None:
        """Store *chunks* so they can be re-delivered on demand.

        Call this before (or immediately after) dispatching the chunks.
        Registering the same stream_id twice replaces the existing buffer.
        """
        self._buffers[stream_id] = _SenderBuffer(
            stream_id=stream_id,
            chunks={c.sequence_no: c for c in chunks if c.sequence_no is not None},
        )

    def handle_retransmit(self, msg: UnifiedMessage) -> list[UnifiedMessage]:
        """Return the chunks listed in a RETRANSMIT message.

        The message payload must contain:
          {"stream_id": "<id>", "missing": [<seq_no>, ...]}

        Returns only the chunks that are still buffered; missing entries are
        silently skipped (they may have already been ACK-ed and evicted).
        """
        if msg.type != MessageType.RETRANSMIT:
            return []
        stream_id: str = msg.payload.get("stream_id", "")
        missing: list[int] = msg.payload.get("missing", [])
        buf = self._buffers.get(stream_id)
        if buf is None:
            return []
        return [buf.chunks[s] for s in missing if s in buf.chunks]

    def handle_ack(self, msg: UnifiedMessage) -> bool:
        """Discard the buffer for the stream in a STREAM_ACK message.

        The message payload must contain: {"stream_id": "<id>"}

        Returns True if a buffer was found and freed, False otherwise.
        """
        if msg.type != MessageType.STREAM_ACK:
            return False
        stream_id: str = msg.payload.get("stream_id", "")
        return self._buffers.pop(stream_id, None) is not None

    def expire_old(self, now: float | None = None) -> list[str]:
        """Discard buffers older than ttl_s.

        Returns the stream IDs that were evicted.
        Call periodically (e.g. every 30 s) to prevent memory leaks when
        the receiver never sends a STREAM_ACK.
        """
        t = now if now is not None else time.monotonic()
        expired = [
            sid for sid, buf in self._buffers.items()
            if (t - buf.created_at) >= self._ttl
        ]
        for sid in expired:
            del self._buffers[sid]
        return expired

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def buffered_streams(self) -> list[str]:
        """Stream IDs currently held in the buffer."""
        return list(self._buffers.keys())

    def chunk_count(self, stream_id: str) -> int:
        """Number of buffered chunks for *stream_id* (0 if not found)."""
        buf = self._buffers.get(stream_id)
        return len(buf.chunks) if buf else 0

    def __len__(self) -> int:
        """Total number of buffered chunks across all streams."""
        return sum(len(b.chunks) for b in self._buffers.values())
