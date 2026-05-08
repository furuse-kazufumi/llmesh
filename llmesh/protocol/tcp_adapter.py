"""TCPAdapter — UnifiedMessage over TCP with 4-byte length-prefix framing.

Wire format per message:
  [4 bytes big-endian uint32: body_length][body_length bytes: UTF-8 JSON]

Each send() opens a new connection to the target, writes one message,
reads one response, then closes the connection. The server handles each
incoming connection as a single request-response exchange.
"""
from __future__ import annotations

import asyncio
import struct
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import UnifiedMessage

if TYPE_CHECKING:
    from .message import NodeAddress

_HEADER = struct.Struct("!I")   # 4-byte big-endian unsigned int
_MAX_FRAME = 16 * 1024 * 1024  # 16 MiB hard cap
_CONNECT_TIMEOUT = 10          # seconds
_READ_TIMEOUT = 30             # seconds


def _pack_frame(data: bytes) -> bytes:
    """Return length-prefixed frame bytes (for testing and internal use)."""
    return _HEADER.pack(len(data)) + data


async def _write_frame(writer: asyncio.StreamWriter, data: bytes) -> None:
    writer.write(_pack_frame(data))
    await writer.drain()


async def _read_frame(reader: asyncio.StreamReader) -> bytes:
    header = await asyncio.wait_for(reader.readexactly(4), timeout=_READ_TIMEOUT)
    length = _HEADER.unpack(header)[0]
    if length > _MAX_FRAME:
        raise TransportError(f"frame_too_large:{length}", protocol="tcp")
    return await asyncio.wait_for(reader.readexactly(length), timeout=_READ_TIMEOUT)


class TCPAdapter(ProtocolAdapter):
    """UnifiedMessage over TCP.

    Reliable, ordered, connection-per-request. Suitable for task submission
    and streaming (STREAM_CHUNK / STREAM_END messages).
    """

    def __init__(self, codec: str = "json", **_kwargs: object) -> None:
        self._codec = codec
        self._handler: MessageHandler | None = None
        self._server: asyncio.AbstractServer | None = None
        self._running = False

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "tcp"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        self._server = await asyncio.start_server(
            self._handle_connection, host, port
        )
        self._running = True
        # Serve in the background without blocking
        asyncio.create_task(self._server.serve_forever())

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._running = False

    async def send(
        self,
        message: UnifiedMessage,
        target: "NodeAddress",
    ) -> UnifiedMessage | None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target.host, target.port),
                timeout=_CONNECT_TIMEOUT,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise TransportError(
                str(exc), protocol="tcp", target=str(target)
            ) from exc

        try:
            await _write_frame(writer, message.to_bytes(self._codec))
            try:
                raw = await _read_frame(reader)
                return UnifiedMessage.from_bytes(raw) if raw else None
            except (asyncio.IncompleteReadError, asyncio.TimeoutError):
                return None
        except TransportError:
            raise
        except OSError as exc:
            raise TransportError(
                str(exc), protocol="tcp", target=str(target)
            ) from exc
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def broadcast(
        self,
        message: UnifiedMessage,
        targets: "list[NodeAddress] | None" = None,
    ) -> None:
        if not targets:
            return
        for target in targets:
            try:
                await self.send(message, target)
            except TransportError:
                pass

    # ------------------------------------------------------------------
    # Internal connection handler
    # ------------------------------------------------------------------

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        try:
            raw = await _read_frame(reader)
            msg = UnifiedMessage.from_bytes(raw)
            if self._handler is not None:
                response = await self._handler(msg)
                if response is not None:
                    await _write_frame(writer, response.to_bytes(self._codec))
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, TransportError,
                ValueError, KeyError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
