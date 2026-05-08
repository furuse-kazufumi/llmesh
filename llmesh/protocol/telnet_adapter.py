"""TelnetAdapter — asyncio-based Telnet server (EXPLICITLY DEPRECATED).

SECURITY WARNING
----------------
Telnet is unencrypted and unauthenticated. This adapter exists only for
legacy system interoperability. It must **never** be used in production or
over untrusted networks.

Double opt-in required:
  LLMESH_ENABLE_TELNET=1
  LLMESH_UNSAFE_TELNET_NO_TLS=1

Both environment variables must be set to "1" or the adapter refuses to start.

Restrictions enforced at the protocol boundary:
  - L3 / L4 prompts are rejected unconditionally.
  - Each connection is isolated; no auth state is persisted.
  - Message size is capped at _MAX_MSG_BYTES.

Wire protocol:
  Newline-delimited JSON (same as raw TCP) with minimal Telnet option
  negotiation: IAC DONT <OPTION> is sent for any option the client tries
  to enable, and IAC WONT <OPTION> for any DO request, so clients quickly
  discover that no options are supported and fall back to plain text.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_MAX_MSG_BYTES = 1 * 1024 * 1024   # 1 MiB hard cap
_READ_TIMEOUT = 30                  # seconds to read a full line

# Telnet control bytes (RFC 854)
_IAC  = 0xFF   # Interpret As Command
_DONT = 0xFE
_DO   = 0xFD
_WONT = 0xFC
_WILL = 0xFB
_SB   = 0xFA   # Subnegotiation Begin
_SE   = 0xF0   # Subnegotiation End

# Single-byte commands (no option byte follows)
_SINGLE_BYTE_CMDS = {0xF1, 0xF2, 0xF3, 0xF4, 0xF5, 0xF6, 0xF9}

# Rejected message types at L3/L4 boundary
_LEVEL_KEY = "data_level"
_BLOCKED_LEVELS = {3, 4}


def _check_double_optin() -> None:
    """Raise RuntimeError unless both opt-in vars are set to '1'."""
    enable  = os.environ.get("LLMESH_ENABLE_TELNET", "")
    no_tls  = os.environ.get("LLMESH_UNSAFE_TELNET_NO_TLS", "")
    if enable != "1" or no_tls != "1":
        raise RuntimeError(
            "TelnetAdapter requires LLMESH_ENABLE_TELNET=1 AND "
            "LLMESH_UNSAFE_TELNET_NO_TLS=1. Telnet is unencrypted "
            "and must never be used in production."
        )


def _strip_telnet_options(data: bytes) -> bytes:
    """Remove IAC option sequences from raw bytes, return printable remainder."""
    out: list[int] = []
    i = 0
    while i < len(data):
        b = data[i]
        if b != _IAC:
            out.append(b)
            i += 1
            continue
        # IAC byte — need at least one more byte
        if i + 1 >= len(data):
            break
        cmd = data[i + 1]
        if cmd in _SINGLE_BYTE_CMDS or cmd == _IAC:
            i += 2
        elif cmd == _SB:
            # Skip until IAC SE
            i += 2
            while i < len(data) - 1:
                if data[i] == _IAC and data[i + 1] == _SE:
                    i += 2
                    break
                i += 1
        elif cmd in (_WILL, _WONT, _DO, _DONT):
            # Three-byte sequence: IAC CMD OPTION
            i += 3
        else:
            i += 2
    return bytes(out)


def _build_refuse_option(cmd: int, option: int) -> bytes:
    """Return IAC DONT/WONT in response to a client WILL/DO."""
    if cmd == _WILL:
        return bytes([_IAC, _DONT, option])
    if cmd == _DO:
        return bytes([_IAC, _WONT, option])
    return b""


class TelnetAdapter(ProtocolAdapter):
    """Telnet server adapter — DEPRECATED, opt-in only.

    Use only for legacy system integration. Provides newline-delimited JSON
    framing over plain Telnet. All security properties of the LLMesh privacy
    pipeline are preserved; only the transport is plaintext.
    """

    def __init__(self, **_kwargs: object) -> None:
        self._handler: MessageHandler | None = None
        self._server: asyncio.AbstractServer | None = None
        self._running = False

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "telnet"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        _check_double_optin()
        logger.warning(
            "TelnetAdapter: TELNET IS UNENCRYPTED — NOT FOR PRODUCTION USE "
            "(listening on %s:%d)",
            host, port,
        )
        self._server = await asyncio.start_server(
            self._handle_connection, host, port
        )
        self._running = True
        asyncio.create_task(self._server.serve_forever())

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._running = False

    async def send(
        self,
        message: UnifiedMessage,
        target: NodeAddress,
    ) -> UnifiedMessage | None:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(target.host, target.port),
                timeout=10.0,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            raise TransportError(str(exc), protocol="telnet", target=str(target)) from exc
        try:
            data = message.to_bytes() + b"\n"
            writer.write(data)
            await writer.drain()

            raw = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT)
            clean = _strip_telnet_options(raw).strip()
            if not clean:
                return None
            return UnifiedMessage.from_bytes(clean)
        except (OSError, asyncio.TimeoutError, KeyError, json.JSONDecodeError) as exc:
            raise TransportError(str(exc), protocol="telnet", target=str(target)) from exc
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def broadcast(
        self,
        message: UnifiedMessage,
        targets: list[NodeAddress] | None = None,
    ) -> None:
        if not targets:
            return
        await asyncio.gather(
            *(self.send(message, t) for t in targets),
            return_exceptions=True,
        )

    # ------------------------------------------------------------------
    # Internal connection handler
    # ------------------------------------------------------------------

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        peer = writer.get_extra_info("peername", ("?", 0))
        logger.debug("TelnetAdapter: connection from %s:%s", *peer)
        try:
            await self._process_connection(reader, writer)
        except Exception as exc:  # noqa: BLE001
            logger.debug("TelnetAdapter: connection error from %s: %s", peer, exc)
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass

    async def _process_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        raw = await asyncio.wait_for(reader.readline(), timeout=_READ_TIMEOUT)
        if len(raw) > _MAX_MSG_BYTES:
            logger.warning("TelnetAdapter: oversized message, dropping")
            return

        # Negotiate away all Telnet options
        negotiate = self._extract_and_refuse_options(raw, writer)
        if negotiate:
            await writer.drain()

        clean = _strip_telnet_options(raw).strip()
        if not clean:
            return

        try:
            msg = UnifiedMessage.from_bytes(clean)
        except (KeyError, json.JSONDecodeError, ValueError) as exc:
            logger.warning("TelnetAdapter: malformed message: %s", exc)
            return

        # Reject L3/L4 prompts unconditionally
        level = msg.payload.get(_LEVEL_KEY, 0)
        if level in _BLOCKED_LEVELS:
            logger.warning(
                "TelnetAdapter: L%d prompt rejected — Telnet cannot carry L3/L4 data",
                level,
            )
            error_msg = UnifiedMessage(
                type=MessageType.ERROR,
                payload={"error": f"L{level} prompts rejected over Telnet"},
                sender=msg.target or NodeAddress("127.0.0.1", 0),
                target=msg.sender,
                correlation_id=msg.id,
            )
            writer.write(error_msg.to_bytes() + b"\n")
            await writer.drain()
            return

        if self._handler is None:
            return

        response = await self._handler(msg)
        if response is not None:
            writer.write(response.to_bytes() + b"\n")
            await writer.drain()

    def _extract_and_refuse_options(
        self,
        data: bytes,
        writer: asyncio.StreamWriter,
    ) -> bool:
        """Write IAC DONT/WONT for each IAC WILL/DO in data. Returns True if any."""
        i = 0
        found = False
        while i < len(data) - 1:
            if data[i] != _IAC:
                i += 1
                continue
            cmd = data[i + 1]
            if cmd in (_WILL, _DO) and i + 2 < len(data):
                option = data[i + 2]
                writer.write(_build_refuse_option(cmd, option))
                found = True
                i += 3
            elif cmd in (_WONT, _DONT) and i + 2 < len(data):
                i += 3
            elif cmd in _SINGLE_BYTE_CMDS:
                i += 2
            else:
                i += 2
        return found
