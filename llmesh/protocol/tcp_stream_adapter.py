"""TCPStreamAdapter — pooled persistent TCP connections with bidirectional ReliableStream.

Unlike TCPAdapter (one connection per request), TCPStreamAdapter:
- Maintains a per-(host, port) connection pool (default size 4)
- Uses ReliableStream to transparently chunk large payloads in both directions
- Allows up to pool_size concurrent requests to the same target in parallel;
  further callers block until a connection becomes available

Wire format: same 4-byte big-endian length-prefix framing as TCPAdapter.
Payload: full UnifiedMessage.to_dict() serialized via ReliableStream (supports
payloads of any size; single-chunk for small messages).

Server side: handles each accepted connection as a long-lived stream session —
reads frames in a loop, feeds them to ReliableStream, responds to each complete
request via the same stream (ReliableStream handles ACK/RETRANSMIT for both
directions on one instance per connection).

Client side: acquires a connection from the pool (creating one if under pool_size),
sends the request via ReliableStream.send(), reads frames until the response stream
completes, then returns the connection to the pool for reuse.
"""
from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass
from typing import Any

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import NodeAddress, UnifiedMessage
from .outbox import OutboxQueue
from .reliable_stream import ReliableStream
from .tcp_adapter import _CONNECT_TIMEOUT, _write_frame

_SERVER_IDLE_TIMEOUT  = 300.0  # seconds; server closes idle connections
_CLIENT_READ_TIMEOUT  = 120.0  # seconds; client waits for a complete response
_BODY_READ_TIMEOUT    = 30.0   # seconds; reading a single frame body
_TICK_INTERVAL        = 1.0    # seconds; server-side stream.tick() cadence
_DEFAULT_POOL_SIZE    = 4      # max connections per (host, port)
_POOL_ACQUIRE_TIMEOUT = 30.0   # seconds to wait when pool is at capacity

_FRAME_HEADER = struct.Struct("!I")   # 4-byte big-endian uint32
_MAX_FRAME    = 16 * 1024 * 1024      # 16 MiB hard cap


async def _tick_loop(stream: ReliableStream, adapter: "_ConnAdapter") -> None:
    """Periodic maintenance for a server-side ReliableStream connection.

    Calls stream.tick() every _TICK_INTERVAL seconds to trigger retransmit
    for stalled chunks and evict expired sender-side buffers.
    Exits cleanly on cancellation or if the adapter write fails (connection gone).
    """
    try:
        while True:
            await asyncio.sleep(_TICK_INTERVAL)
            await stream.tick(adapter=adapter)
    except (asyncio.CancelledError, OSError, TransportError):
        pass


async def _open_connection(host: str, port: int) -> "_PersistentConn":
    """Open one TCP connection and return it as a _PersistentConn."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=_CONNECT_TIMEOUT,
        )
    except (OSError, asyncio.TimeoutError) as exc:
        raise TransportError(
            str(exc), protocol="tcp_stream", target=f"{host}:{port}"
        ) from exc
    sockname = writer.get_extra_info("sockname", ("localhost", 0))
    local = NodeAddress(host=sockname[0], port=sockname[1])
    return _PersistentConn(reader=reader, writer=writer, local=local)


async def _read_frame_timeout(
    reader: asyncio.StreamReader,
    header_timeout: float,
) -> bytes:
    """Read one length-prefixed frame. Uses header_timeout for the header read."""
    raw = await asyncio.wait_for(reader.readexactly(4), timeout=header_timeout)
    length = _FRAME_HEADER.unpack(raw)[0]
    if length > _MAX_FRAME:
        raise TransportError(f"frame_too_large:{length}", protocol="tcp_stream")
    return await asyncio.wait_for(reader.readexactly(length), timeout=_BODY_READ_TIMEOUT)


# ------------------------------------------------------------------
# Write-only inline adapter used by ReliableStream inside a connection
# ------------------------------------------------------------------

class _ConnAdapter(ProtocolAdapter):
    """Wraps an asyncio.StreamWriter for use with ReliableStream inside a connection.

    Only send() is meaningful; all other ProtocolAdapter methods are no-ops.
    """

    def __init__(self, writer: asyncio.StreamWriter, codec: str = "json") -> None:
        self._writer = writer
        self._codec = codec

    @property
    def protocol_name(self) -> str:
        return "_conn"

    @property
    def is_running(self) -> bool:
        return not self._writer.is_closing()

    def on_message(self, handler: MessageHandler) -> None:
        pass

    async def start(self, host: str, port: int) -> None:
        pass

    async def stop(self) -> None:
        pass

    async def send(
        self,
        message: UnifiedMessage,
        target: Any = None,
    ) -> None:  # type: ignore[override]
        await _write_frame(self._writer, message.to_bytes(self._codec))

    async def broadcast(
        self,
        message: UnifiedMessage,
        targets: Any = None,
    ) -> None:
        pass


# ------------------------------------------------------------------
# Persistent connection descriptor (client side)
# ------------------------------------------------------------------

@dataclass
class _PersistentConn:
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter
    local: NodeAddress

    def is_alive(self) -> bool:
        return not self.writer.is_closing()


# ------------------------------------------------------------------
# Per-(host, port) connection pool
# ------------------------------------------------------------------

class _ConnPool:
    """Pool of persistent TCP connections to a single (host, port).

    Connections are checked out via acquire() and returned via release().
    Up to *max_size* connections are created on demand; callers block on
    acquire() when the pool is at capacity until one is returned.
    """

    def __init__(self, max_size: int) -> None:
        self._max_size = max_size
        self._queue: asyncio.Queue[_PersistentConn] = asyncio.Queue()
        self._total = 0  # connections created (idle + in-use)
        self._create_lock = asyncio.Lock()

    async def acquire(self, host: str, port: int) -> _PersistentConn:
        """Check out a connection, creating one if under the size limit."""
        while True:
            # Fast path: grab an idle connection
            try:
                conn = self._queue.get_nowait()
                if conn.is_alive():
                    return conn
                self._total -= 1  # dead connection; discard and try again
                continue
            except asyncio.QueueEmpty:
                pass

            # Try to open a new connection if under the limit
            async with self._create_lock:
                if self._total < self._max_size:
                    conn = await _open_connection(host, port)
                    self._total += 1
                    return conn

            # Pool at capacity — wait for a connection to be returned
            try:
                conn = await asyncio.wait_for(
                    self._queue.get(), timeout=_POOL_ACQUIRE_TIMEOUT
                )
            except asyncio.TimeoutError as exc:
                raise TransportError(
                    "pool_exhausted", protocol="tcp_stream", target=f"{host}:{port}"
                ) from exc

            if conn.is_alive():
                return conn
            self._total -= 1

    def release(self, conn: _PersistentConn) -> None:
        """Return a healthy connection to the pool; discard a dead one."""
        if conn.is_alive():
            self._queue.put_nowait(conn)
        else:
            self._total -= 1

    def discard(self) -> None:
        """Account for a connection that was closed outside the pool."""
        self._total -= 1

    async def close_all(self) -> None:
        """Close all idle connections. In-use connections are orphaned."""
        while True:
            try:
                conn = self._queue.get_nowait()
                conn.writer.close()
                try:
                    await conn.writer.wait_closed()
                except OSError:
                    pass
            except asyncio.QueueEmpty:
                break


# ------------------------------------------------------------------
# TCPStreamAdapter
# ------------------------------------------------------------------

class TCPStreamAdapter(ProtocolAdapter):
    """Pooled persistent TCP connections with bidirectional ReliableStream framing.

    Args:
        timeout:   Client read timeout in seconds (default 120).
        pool_size: Max concurrent connections per (host, port) (default 4).
    """

    def __init__(
        self,
        timeout: float = _CLIENT_READ_TIMEOUT,
        pool_size: int = _DEFAULT_POOL_SIZE,
        codec: str = "json",
        outbox: OutboxQueue | None = None,
        retry_interval: float = 5.0,
        **_kwargs: object,
    ) -> None:
        self._codec = codec
        self._handler: MessageHandler | None = None
        self._server: asyncio.AbstractServer | None = None
        self._running = False
        self._port = 0
        self._read_timeout = float(timeout)
        self._pool_size = int(pool_size)
        self._pools: dict[tuple[str, int], _ConnPool] = {}
        self._handler_tasks: set[asyncio.Task[None]] = set()
        self._outbox = outbox
        self._retry_interval = retry_interval
        self._retry_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "tcp_stream"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        self._port = port
        self._server = await asyncio.start_server(
            self._handle_connection, host, port
        )
        self._running = True
        asyncio.create_task(self._server.serve_forever())
        if self._outbox is not None:
            self._retry_task = asyncio.ensure_future(self._retry_loop())

    async def stop(self) -> None:
        if self._retry_task is not None:
            self._retry_task.cancel()
            try:
                await self._retry_task
            except asyncio.CancelledError:
                pass
            self._retry_task = None
        for task in list(self._handler_tasks):
            task.cancel()
        if self._handler_tasks:
            await asyncio.gather(*self._handler_tasks, return_exceptions=True)
        self._handler_tasks.clear()
        for pool in list(self._pools.values()):
            await pool.close_all()
        self._pools.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._running = False

    async def send(
        self,
        message: UnifiedMessage,
        target: NodeAddress,
    ) -> UnifiedMessage | None:
        """Send *message* to *target*.

        On TransportError, if an OutboxQueue is attached the message is
        persisted for later retry and None is returned (transparent to caller).
        Without an outbox the error propagates as usual.
        """
        try:
            return await self._send_inner(message, target)
        except TransportError:
            if self._outbox is not None:
                await asyncio.to_thread(self._outbox.enqueue, message, target)
                return None
            raise

    async def _send_inner(
        self,
        message: UnifiedMessage,
        target: NodeAddress,
    ) -> UnifiedMessage | None:
        """Raw send — acquires pool connection, exchanges one request/response."""
        pool = self._get_or_create_pool(target.host, target.port)
        conn = await pool.acquire(target.host, target.port)
        try:
            conn_adapter = _ConnAdapter(conn.writer, codec=self._codec)
            # Fresh ReliableStream per request: handles outgoing request chunks,
            # incoming STREAM_ACK, and incoming response chunks.
            stream = ReliableStream(sender=conn.local, watchdog_timeout_s=None)

            try:
                await stream.send(message.to_dict(), target=target, adapter=conn_adapter)
            except OSError as exc:
                conn.writer.close()
                raise TransportError(
                    str(exc), protocol="tcp_stream", target=str(target)
                ) from exc

            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._read_timeout

            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    conn.writer.close()
                    raise TransportError(
                        "read_timeout", protocol="tcp_stream", target=str(target)
                    )

                try:
                    body = await _read_frame_timeout(conn.reader, header_timeout=remaining)
                except (asyncio.IncompleteReadError, asyncio.TimeoutError) as exc:
                    conn.writer.close()
                    raise TransportError(
                        f"connection_closed:{exc}",
                        protocol="tcp_stream",
                        target=str(target),
                    ) from exc
                except TransportError:
                    conn.writer.close()
                    raise

                try:
                    msg = UnifiedMessage.from_bytes(body)
                except (ValueError, KeyError) as exc:
                    conn.writer.close()
                    raise TransportError(
                        f"invalid_frame:{exc}",
                        protocol="tcp_stream",
                        target=str(target),
                    ) from exc

                payloads = await stream.on_message(msg, adapter=conn_adapter)
                if payloads:
                    data = payloads[0]
                    if isinstance(data, dict):
                        return UnifiedMessage.from_dict(data)
                    return None
        finally:
            if conn.is_alive():
                pool.release(conn)
            else:
                pool.discard()

    async def broadcast(
        self,
        message: UnifiedMessage,
        targets: list[NodeAddress] | None = None,
    ) -> None:
        if not targets:
            return
        for target in targets:
            try:
                await self.send(message, target)
            except TransportError:
                pass

    # ------------------------------------------------------------------
    # Outbox retry loop
    # ------------------------------------------------------------------

    async def _retry_loop(self) -> None:
        """Periodically replay queued messages from the outbox after failures."""
        assert self._outbox is not None
        while True:
            await asyncio.sleep(self._retry_interval)
            await asyncio.to_thread(self._outbox.purge_expired)
            pending = await asyncio.to_thread(self._outbox.dequeue, 10)
            for msg, target in pending:
                try:
                    await self._send_inner(msg, target)
                    await asyncio.to_thread(self._outbox.mark_sent, msg.id)
                except TransportError:
                    pass  # leave in outbox; retry on next interval

    # ------------------------------------------------------------------
    # Connection pool management
    # ------------------------------------------------------------------

    def _get_or_create_pool(self, host: str, port: int) -> _ConnPool:
        key = (host, port)
        pool = self._pools.get(key)
        if pool is None:
            pool = _ConnPool(self._pool_size)
            self._pools[key] = pool
        return pool

    # ------------------------------------------------------------------
    # Server-side connection handler
    # ------------------------------------------------------------------

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        task = asyncio.current_task()
        if task is not None:
            self._handler_tasks.add(task)
        peername = writer.get_extra_info("peername", ("?", 0))
        sockname = writer.get_extra_info("sockname", ("localhost", self._port))
        local  = NodeAddress(host=sockname[0], port=sockname[1])
        client = NodeAddress(host=peername[0], port=peername[1])

        conn_adapter = _ConnAdapter(writer)
        # Single stream for the full connection: handles incoming request chunks
        # and ACKs for our outgoing response chunks.
        stream = ReliableStream(sender=local, watchdog_timeout_s=None)
        tick_task = asyncio.create_task(_tick_loop(stream, conn_adapter))

        try:
            while True:
                try:
                    body = await _read_frame_timeout(
                        reader, header_timeout=_SERVER_IDLE_TIMEOUT
                    )
                except (asyncio.IncompleteReadError, asyncio.TimeoutError, TransportError):
                    break

                try:
                    msg = UnifiedMessage.from_bytes(body)
                except (ValueError, KeyError):
                    continue

                payloads = await stream.on_message(msg, adapter=conn_adapter)

                for payload in payloads:
                    if self._handler is None or not isinstance(payload, dict):
                        continue
                    try:
                        request_msg = UnifiedMessage.from_dict(payload)
                    except (ValueError, KeyError):
                        continue

                    response = await self._handler(request_msg)
                    if response is not None:
                        await stream.send(
                            response.to_dict(),
                            target=client,
                            adapter=conn_adapter,
                        )

        except (TransportError, OSError):
            pass
        finally:
            if task is not None:
                self._handler_tasks.discard(task)
            try:
                tick_task.cancel()
                await tick_task
            except (asyncio.CancelledError, RuntimeError):
                pass
            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, RuntimeError):
                pass
