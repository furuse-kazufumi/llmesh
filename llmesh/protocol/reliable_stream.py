"""ReliableStream — high-level send/receive for arbitrary data over any adapter.

Combines ChunkSender (sender-side buffer + retransmit) and
MessageAssembler (receiver-side ordering + ACK/RETRANSMIT) into a
single object that handles:

- Automatic chunking of large payloads
- bytes, dict, and str payloads (binary via base64 in the chunk envelope)
- Reassembly on the receive side with in-order delivery
- ACK / RETRANSMIT reliability handshake

Usage (send side)::

    stream = ReliableStream(sender=my_addr)
    stream_id = await stream.send(b"large binary blob", target=peer, adapter=tcp)

Usage (receive side)::

    stream = ReliableStream(sender=my_addr)
    async for msg in adapter:
        for payload in await stream.on_message(msg, adapter=adapter):
            handle(payload)   # bytes | dict | str, fully reassembled

Usage (background maintenance)::

    while running:
        await asyncio.sleep(1)
        await stream.tick(adapter=adapter)
        if stream.is_peer_silent():
            break   # peer stopped sending; caller should close connection
"""
from __future__ import annotations

import base64
import json
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .assembler import MessageAssembler
from .chunk_sender import ChunkSender
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    from .adapter import ProtocolAdapter

_DTYPE_BYTES = "bytes"
_DTYPE_DICT = "dict"
_DTYPE_STR = "str"

Payload = bytes | dict[str, Any] | str


@dataclass
class _Accumulator:
    pieces: list[str] = field(default_factory=list)
    dtype: str = _DTYPE_BYTES


class ReliableStream:
    """High-level reliable data stream over any ProtocolAdapter.

    Thread-unsafe; use one instance per async task / connection.

    Args:
        sender:               NodeAddress of this node (used in ACK/RETRANSMIT messages).
        chunk_size:           Maximum raw bytes per chunk (default 256 KB).
        retransmit_timeout_s: Gap idle time before a RETRANSMIT is sent.
        ttl_s:                How long the sender-side buffer is kept if no ACK arrives.
        watchdog_timeout_s:   Silence threshold after which is_peer_silent() returns True.
                              None disables watchdog.
    """

    DEFAULT_CHUNK_SIZE = 256 * 1024  # 256 KB

    def __init__(
        self,
        sender: NodeAddress,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        retransmit_timeout_s: float = 5.0,
        ttl_s: float = 300.0,
        watchdog_timeout_s: float | None = 60.0,
    ) -> None:
        self._sender = sender
        self._chunk_size = chunk_size
        self._retransmit_timeout = retransmit_timeout_s
        self._sender_buf = ChunkSender(ttl_s=ttl_s)
        self._assembler = MessageAssembler(watchdog_timeout_s=watchdog_timeout_s)
        self._accumulators: dict[str, _Accumulator] = {}

    # ------------------------------------------------------------------
    # Send side
    # ------------------------------------------------------------------

    async def send(
        self,
        data: Payload,
        *,
        target: NodeAddress,
        adapter: ProtocolAdapter,
    ) -> str:
        """Encode and send *data* as a reliable chunked stream.

        Accepts bytes, dict (JSON-serializable), or str.
        Returns the stream_id shared by all chunks.
        """
        raw, dtype = self._encode(data)
        stream_id = str(uuid.uuid4())
        chunks = self._make_chunks(raw, dtype, stream_id)
        self._sender_buf.buffer(stream_id, chunks)
        for chunk in chunks:
            await adapter.send(chunk, target)
        return stream_id

    # ------------------------------------------------------------------
    # Receive side
    # ------------------------------------------------------------------

    async def on_message(
        self,
        msg: UnifiedMessage,
        *,
        adapter: ProtocolAdapter | None = None,
    ) -> list[Payload]:
        """Feed *msg* into the stream layer.

        Returns a list of fully reassembled payloads (may be empty).
        Sends STREAM_ACK automatically when *adapter* is provided.
        Handles incoming RETRANSMIT and STREAM_ACK control messages.
        """
        # ---- control messages ----------------------------------------
        if msg.type == MessageType.STREAM_ACK:
            self._sender_buf.handle_ack(msg)
            return []
        if msg.type == MessageType.RETRANSMIT:
            if adapter is not None:
                for chunk in self._sender_buf.handle_retransmit(msg):
                    await adapter.send(chunk, msg.sender)
            return []

        # ---- data chunks (pass non-stream messages through as-is) ----
        ready = self._assembler.push(msg)
        for chunk in ready:
            if chunk.sequence_no is None:
                continue
            sid = chunk.correlation_id or chunk.id
            dtype = chunk.payload.get("_type", _DTYPE_BYTES)
            piece = chunk.payload.get("_chunk", "")
            if sid not in self._accumulators:
                self._accumulators[sid] = _Accumulator(dtype=dtype)
            self._accumulators[sid].pieces.append(piece)

        # ---- completed streams: decode and ACK -----------------------
        results: list[Payload] = []
        for completed in self._assembler.pop_completed():
            sid = completed.stream_id
            acc = self._accumulators.pop(sid, _Accumulator())
            raw = base64.b64decode("".join(acc.pieces))
            results.append(self._decode(raw, acc.dtype))

            if adapter is not None:
                ack = UnifiedMessage(
                    type=MessageType.STREAM_ACK,
                    payload={"stream_id": sid},
                    sender=self._sender,
                    target=completed.sender,
                )
                await adapter.send(ack, completed.sender)

        return results

    # ------------------------------------------------------------------
    # Periodic maintenance
    # ------------------------------------------------------------------

    async def tick(
        self,
        *,
        adapter: ProtocolAdapter | None = None,
        now: float | None = None,
    ) -> None:
        """Run periodic maintenance (call every few seconds from a background task).

        - Sends RETRANSMIT for stalled streams past retransmit_timeout_s.
        - Evicts sender-side buffers past ttl_s.
        """
        for info in self._assembler.check_timeouts(self._retransmit_timeout, now=now):
            if adapter is not None:
                retransmit_msg = UnifiedMessage(
                    type=MessageType.RETRANSMIT,
                    payload={"stream_id": info.stream_id, "missing": info.missing},
                    sender=self._sender,
                    target=info.sender,
                )
                await adapter.send(retransmit_msg, info.sender)
        self._sender_buf.expire_old(now=now)

    # ------------------------------------------------------------------
    # Watchdog
    # ------------------------------------------------------------------

    def is_peer_silent(self, now: float | None = None) -> bool:
        """True if the watchdog has expired (peer stopped sending)."""
        return self._assembler.check_watchdog(now=now)

    # ------------------------------------------------------------------
    # Encoding helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode(data: Payload) -> tuple[bytes, str]:
        if isinstance(data, bytes):
            return data, _DTYPE_BYTES
        if isinstance(data, dict):
            return json.dumps(data, separators=(",", ":")).encode(), _DTYPE_DICT
        if isinstance(data, str):
            return data.encode(), _DTYPE_STR
        raise TypeError(f"unsupported payload type: {type(data)!r}")

    @staticmethod
    def _decode(raw: bytes, dtype: str) -> Payload:
        if dtype == _DTYPE_DICT:
            return json.loads(raw)
        if dtype == _DTYPE_STR:
            return raw.decode()
        return raw

    def _make_chunks(
        self,
        raw: bytes,
        dtype: str,
        stream_id: str,
    ) -> list[UnifiedMessage]:
        """Split *raw* into UnifiedMessage chunks, each carrying ≤ chunk_size raw bytes."""
        encoded = base64.b64encode(raw).decode()

        # Number of base64 chars that correspond to chunk_size raw bytes
        # base64: every 3 bytes → 4 chars; ceil(chunk_size/3)*4
        b64_per_chunk = ((self._chunk_size + 2) // 3) * 4
        pieces = [
            encoded[i : i + b64_per_chunk]
            for i in range(0, max(len(encoded), 1), b64_per_chunk)
        ]

        total = len(pieces)
        chunks: list[UnifiedMessage] = []
        for seq, piece in enumerate(pieces):
            is_last = seq == total - 1
            chunks.append(
                UnifiedMessage.chunk(
                    payload={"_chunk": piece, "_type": dtype},
                    sender=self._sender,
                    stream_id=stream_id,
                    sequence_no=seq,
                    total_chunks=total if is_last else None,
                )
            )
        return chunks
