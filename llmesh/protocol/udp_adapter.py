"""UDPAdapter — UnifiedMessage over UDP datagrams.

Wire format:
  [2 bytes magic 0x4C 0x4D ("LM")][2 bytes uint16 sequence][4 bytes reserved][JSON body]

Max payload: ~65499 bytes (UDP datagram limit minus 8-byte header).
Use-cases: gossip peer exchange, capability broadcasts, heartbeats.

send() with a REQUEST message: sends datagram, waits up to *reply_timeout*
seconds for a RESPONSE with matching correlation_id. Returns None on timeout
(fire-and-forget semantics — caller decides whether to retry).

broadcast() always fire-and-forget; no reply is expected.
"""
from __future__ import annotations

import asyncio
import struct
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, UnifiedMessage

if TYPE_CHECKING:
    from .message import NodeAddress

_MAGIC = b"\x4c\x4d"           # "LM"
_HEADER = struct.Struct("!2sHI")  # magic(2) + seq(2) + reserved(4)  = 8 bytes
_HDR_SIZE = _HEADER.size        # 8
_MAX_DATAGRAM = 65499           # UDP payload cap after 8-byte header
_DEFAULT_REPLY_TIMEOUT = 5.0   # seconds


def _pack(seq: int, body: bytes) -> bytes:
    return _HEADER.pack(_MAGIC, seq & 0xFFFF, 0) + body


def _unpack(data: bytes) -> tuple[int, bytes]:
    if len(data) < _HDR_SIZE:
        raise ValueError("datagram too short")
    magic, seq, _ = _HEADER.unpack(data[:_HDR_SIZE])
    if magic != _MAGIC:
        raise ValueError(f"bad magic: {magic!r}")
    return seq, data[_HDR_SIZE:]


class _UDPProtocol(asyncio.DatagramProtocol):
    """asyncio DatagramProtocol that dispatches to UDPAdapter._on_datagram."""

    def __init__(self, adapter: "UDPAdapter") -> None:
        self._adapter = adapter
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        asyncio.ensure_future(self._adapter._on_datagram(data, addr))

    def error_received(self, exc: Exception) -> None:
        pass

    def connection_lost(self, exc: Exception | None) -> None:
        pass


class UDPAdapter(ProtocolAdapter):
    """UnifiedMessage over UDP.

    Unreliable, connectionless. Suitable for gossip, capability announcements,
    and heartbeats. TTL field in UnifiedMessage limits broadcast hops.
    """

    def __init__(self, reply_timeout: float = _DEFAULT_REPLY_TIMEOUT, codec: str = "json", **_kwargs: object) -> None:
        self._codec = codec
        self._handler: MessageHandler | None = None
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _UDPProtocol | None = None
        self._running = False
        self._reply_timeout = reply_timeout
        self._seq: int = 0
        # correlation_id → asyncio.Future[UnifiedMessage]
        self._pending: dict[str, asyncio.Future[UnifiedMessage]] = {}
        # Priority queue for fire-and-forget datagrams: (-priority, counter, datagram, addr)
        # Higher priority messages are dequeued first (min-heap via negation).
        self._send_queue: asyncio.PriorityQueue[tuple[int, int, bytes, tuple[str, int]]] = asyncio.PriorityQueue()
        self._send_counter: int = 0
        self._worker_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "udp"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        loop = asyncio.get_running_loop()
        self._protocol = _UDPProtocol(self)
        self._transport, _ = await loop.create_datagram_endpoint(  # type: ignore[assignment]
            lambda: self._protocol,
            local_addr=(host, port),
        )
        self._worker_task = asyncio.ensure_future(self._send_worker())
        self._running = True

    async def stop(self) -> None:
        if self._worker_task is not None:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                pass
            self._worker_task = None
        if self._transport is not None:
            self._transport.close()
        for fut in self._pending.values():
            if not fut.done():
                fut.cancel()
        self._pending.clear()
        self._running = False

    async def send(
        self,
        message: UnifiedMessage,
        target: "NodeAddress",
    ) -> UnifiedMessage | None:
        """Send *message* to *target*.

        For REQUEST messages: waits up to reply_timeout seconds for a correlated
        RESPONSE. Returns None on timeout (caller may retry).
        For other types: fire-and-forget, returns None immediately.
        """
        body = message.to_bytes(self._codec)
        if len(body) > _MAX_DATAGRAM:
            raise TransportError(
                f"payload_too_large:{len(body)}", protocol="udp", target=str(target)
            )

        datagram = _pack(self._seq, body)
        self._seq = (self._seq + 1) & 0xFFFF

        if self._transport is not None:
            self._transport.sendto(datagram, (target.host, target.port))
        else:
            # Client-only mode: send via temporary socket
            loop = asyncio.get_running_loop()
            sock_protocol = _UDPProtocol(self)
            transport, _ = await loop.create_datagram_endpoint(  # type: ignore[assignment]
                lambda: sock_protocol,
                remote_addr=(target.host, target.port),
            )
            try:
                transport.sendto(datagram)
            finally:
                transport.close()

        if message.type != MessageType.REQUEST:
            return None

        # Wait for correlated response
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[UnifiedMessage] = loop.create_future()
        self._pending[message.id] = fut
        try:
            return await asyncio.wait_for(fut, timeout=self._reply_timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._pending.pop(message.id, None)

    async def broadcast(
        self,
        message: UnifiedMessage,
        targets: "list[NodeAddress] | None" = None,
    ) -> None:
        if not targets:
            return
        body = message.to_bytes(self._codec)
        if len(body) > _MAX_DATAGRAM:
            raise TransportError(
                f"payload_too_large:{len(body)}", protocol="udp"
            )
        datagram = _pack(self._seq, body)
        self._seq = (self._seq + 1) & 0xFFFF

        if self._transport is not None:
            for target in targets:
                self._enqueue_datagram(datagram, (target.host, target.port), message.priority)
        else:
            for target in targets:
                try:
                    await self.send(message, target)
                except TransportError:
                    pass

    # ------------------------------------------------------------------
    # Priority queue helpers
    # ------------------------------------------------------------------

    def _enqueue_datagram(self, datagram: bytes, addr: tuple[str, int], priority: int = 0) -> None:
        """Queue a datagram for delivery ordered by priority (higher value = sent first)."""
        self._send_queue.put_nowait((-priority, self._send_counter, datagram, addr))
        self._send_counter += 1

    async def _send_worker(self) -> None:
        """Background worker: dequeue and send datagrams in priority order."""
        while True:
            _, _, datagram, addr = await self._send_queue.get()
            try:
                if self._transport is not None:
                    self._transport.sendto(datagram, addr)
            finally:
                self._send_queue.task_done()

    # ------------------------------------------------------------------
    # Internal datagram handler
    # ------------------------------------------------------------------

    async def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        try:
            _, body = _unpack(data)
            msg = UnifiedMessage.from_bytes(body)
        except (ValueError, KeyError):
            return

        # Resolve pending request if this is a correlated response
        if msg.correlation_id and msg.correlation_id in self._pending:
            fut = self._pending[msg.correlation_id]
            if not fut.done():
                fut.set_result(msg)
            return

        if self._handler is not None:
            response = await self._handler(msg)
            if response is not None and self._transport is not None:
                resp_body = response.to_bytes(self._codec)
                resp_datagram = _pack(self._seq, resp_body)
                self._seq = (self._seq + 1) & 0xFFFF
                self._transport.sendto(resp_datagram, addr)
