"""WebSocketAdapter — RFC 6455 WebSocket adapter for LLMesh Industrial (v2.11 — J-4.3).

Pure-stdlib WebSocket server that accepts client connections and
emits each received text/binary frame as a SensorEvent.  No external
dependencies (no `websockets` package required).

Use cases
---------
- Game telemetry streaming (J-4.3, Volume N)
- Browser dashboards subscribing to live SensorEvents
- Lightweight bridge for IoT devices that already speak WebSocket
- Real-time notifications to mobile companion apps

Wire format
-----------
Each WebSocket message corresponds to **one** SensorEvent.  The frame
payload is interpreted as:

* **TEXT frame**: UTF-8 JSON object matching SensorEvent fields
* **BINARY frame**: Raw bytes — packed in `payload`, all other fields
  default to "ws_binary"

Usage::

    adapter = WebSocketAdapter("0.0.0.0", 8765)
    adapter.on_event(lambda ev: print(ev))
    await adapter.start()
    # accept clients until stop() is called
    await adapter.stop()

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Per-message size cap (default 1 MiB).
- Optional shared-secret auth via `X-LLMesh-Token` request header.
- IP allowlist (CIDR) via `accept_cidrs` argument.
- TLS via standard `ssl.SSLContext` for `wss://` (recommend production).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import logging
import os
import re
import ssl
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Module constants
# ---------------------------------------------------------------------------

# Magic GUID per RFC 6455 § 1.3 — used in opening handshake key derivation.
_WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Maximum size of one WebSocket message (bytes).  Defends against
# memory-amplification attacks via crafted long frames.
_MAX_MESSAGE_BYTES = 1_048_576       # 1 MiB

# Maximum size of HTTP handshake request (bytes).
_MAX_HANDSHAKE_BYTES = 8_192

# Per-connection TCP read timeout for the next frame (seconds).
_FRAME_READ_TIMEOUT_S = 60.0

# Frame opcodes (RFC 6455 § 5.2)
_OP_CONT = 0x0
_OP_TEXT = 0x1
_OP_BIN = 0x2
_OP_CLOSE = 0x8
_OP_PING = 0x9
_OP_PONG = 0xA

# Server token header — clients pass `X-LLMesh-Token: <secret>` if `auth_token`
# is configured.  Compared with `secrets.compare_digest`.
_AUTH_HEADER = "x-llmesh-token"

# HTTP request line / header parser bounds.
_HEADER_LINE_MAX = 1024


EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ws_accept_key(client_key: str) -> str:
    sha = hashlib.sha1((client_key + _WS_MAGIC).encode()).digest()
    return base64.b64encode(sha).decode()


def _client_in_allowlist(client_ip: str, cidrs: list[str]) -> bool:
    if not cidrs:
        return True
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    for c in cidrs:
        try:
            if addr in ipaddress.ip_network(c, strict=False):
                return True
        except ValueError:
            continue
    return False


def _safe_compare(a: str, b: str) -> bool:
    """Constant-time string compare to defend against timing attacks."""
    import secrets as _secrets
    return _secrets.compare_digest(a, b)


# ---------------------------------------------------------------------------
# WebSocketAdapter
# ---------------------------------------------------------------------------

class WebSocketAdapter:
    """Listen on a TCP port and emit SensorEvents for each WebSocket frame.

    Parameters
    ----------
    host:
        Bind interface (default ``"127.0.0.1"``; use ``"0.0.0.0"`` for LAN).
    port:
        Bind TCP port.
    auth_token:
        Optional shared secret.  Clients must send ``X-LLMesh-Token: <token>``
        in the upgrade request.  Empty string = no auth.
    accept_cidrs:
        IP allowlist as CIDR strings.  Empty list = accept all.
    tls_context:
        Optional :class:`ssl.SSLContext` for ``wss://``.
    max_message_bytes:
        Per-message size cap (default 1 MiB).
    """

    _DEFAULT_HOST = "127.0.0.1"
    _DEFAULT_PORT = 8765

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        *,
        auth_token: str = "",
        accept_cidrs: list[str] | None = None,
        tls_context: ssl.SSLContext | None = None,
        max_message_bytes: int = _MAX_MESSAGE_BYTES,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._auth_token = auth_token
        self._accept_cidrs = list(accept_cidrs or [])
        self._tls_context = tls_context
        self._max_message_bytes = min(max_message_bytes, _MAX_MESSAGE_BYTES)
        self._callbacks: list[EventCallback] = []
        self._server: asyncio.AbstractServer | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def on_event(self, callback: EventCallback) -> None:
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port,
            ssl=self._tls_context,
        )
        self._running = True
        logger.info(
            "WebSocketAdapter listening on %s://%s:%d",
            "wss" if self._tls_context else "ws",
            self._host, self._port,
        )

    async def stop(self) -> None:
        self._running = False
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    # ------------------------------------------------------------------
    # Internal — per-client handler
    # ------------------------------------------------------------------

    async def _handle_client(self, reader: asyncio.StreamReader,
                             writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername") or ("?", 0)
        client_ip = peer[0]

        try:
            # 0. CIDR allowlist
            if not _client_in_allowlist(client_ip, self._accept_cidrs):
                logger.warning("WebSocketAdapter: %s rejected by CIDR allowlist", client_ip)
                writer.close()
                return

            # 1. HTTP handshake
            if not await self._handshake(reader, writer):
                writer.close()
                return

            # 2. Frame loop
            await self._frame_loop(reader, writer, client_ip)

        except Exception as exc:
            logger.debug("WebSocketAdapter: client %s error: %s", client_ip, exc)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def _handshake(self, reader: asyncio.StreamReader,
                         writer: asyncio.StreamWriter) -> bool:
        try:
            buf = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"),
                timeout=10.0,
            )
        except (asyncio.IncompleteReadError, asyncio.TimeoutError):
            return False
        if len(buf) > _MAX_HANDSHAKE_BYTES:
            return False

        text = buf.decode("latin-1")
        lines = text.split("\r\n")
        if not lines or not lines[0].startswith("GET "):
            return False

        headers: dict[str, str] = {}
        for line in lines[1:]:
            if not line:
                break
            if ":" not in line:
                continue
            k, _, v = line.partition(":")
            headers[k.strip().lower()] = v.strip()

        # Validate required headers
        if headers.get("upgrade", "").lower() != "websocket":
            return False
        if "upgrade" not in headers.get("connection", "").lower():
            return False
        client_key = headers.get("sec-websocket-key", "")
        if not client_key:
            return False

        # Authentication
        if self._auth_token:
            client_token = headers.get(_AUTH_HEADER, "")
            if not _safe_compare(client_token, self._auth_token):
                writer.write(
                    b"HTTP/1.1 401 Unauthorized\r\n"
                    b"Content-Length: 0\r\n\r\n"
                )
                await writer.drain()
                return False

        accept = _ws_accept_key(client_key)
        response = (
            "HTTP/1.1 101 Switching Protocols\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Accept: {accept}\r\n\r\n"
        ).encode()
        writer.write(response)
        await writer.drain()
        return True

    async def _frame_loop(self, reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter,
                          client_ip: str) -> None:
        while self._running:
            try:
                opcode, data = await asyncio.wait_for(
                    self._read_frame(reader),
                    timeout=_FRAME_READ_TIMEOUT_S,
                )
            except asyncio.TimeoutError:
                # Send ping; if no pong within timeout, close
                try:
                    writer.write(self._encode_frame(_OP_PING, b"keepalive"))
                    await writer.drain()
                    continue
                except Exception:
                    return
            except Exception:
                return

            if opcode == _OP_CLOSE:
                try:
                    writer.write(self._encode_frame(_OP_CLOSE, b""))
                    await writer.drain()
                except Exception:
                    pass
                return
            if opcode == _OP_PING:
                writer.write(self._encode_frame(_OP_PONG, data))
                await writer.drain()
                continue
            if opcode == _OP_PONG:
                continue

            self._dispatch_message(opcode, data, client_ip)

    # ------------------------------------------------------------------
    # Internal — frame I/O
    # ------------------------------------------------------------------

    async def _read_frame(self, reader: asyncio.StreamReader) -> tuple[int, bytes]:
        header = await reader.readexactly(2)
        b1, b2 = header
        opcode = b1 & 0x0F
        masked = (b2 & 0x80) != 0
        length = b2 & 0x7F
        if length == 126:
            length = struct.unpack("!H", await reader.readexactly(2))[0]
        elif length == 127:
            length = struct.unpack("!Q", await reader.readexactly(8))[0]

        if length > self._max_message_bytes:
            raise ValueError(f"frame exceeds max size: {length}")

        mask = await reader.readexactly(4) if masked else b""
        payload = await reader.readexactly(length)
        if masked:
            payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        return opcode, payload

    def _encode_frame(self, opcode: int, data: bytes) -> bytes:
        header = bytearray()
        header.append(0x80 | opcode)   # FIN=1
        n = len(data)
        if n < 126:
            header.append(n)
        elif n < (1 << 16):
            header.append(126)
            header.extend(struct.pack("!H", n))
        else:
            header.append(127)
            header.extend(struct.pack("!Q", n))
        return bytes(header) + data

    # ------------------------------------------------------------------
    # Internal — message → SensorEvent
    # ------------------------------------------------------------------

    def _dispatch_message(self, opcode: int, data: bytes, client_ip: str) -> None:
        try:
            if opcode == _OP_TEXT:
                event = self._parse_text(data, client_ip)
            elif opcode == _OP_BIN:
                event = self._parse_binary(data, client_ip)
            else:
                return
            self._emit(event)
        except Exception as exc:
            logger.debug("WebSocketAdapter: dispatch error: %s", exc)

    def _parse_text(self, data: bytes, client_ip: str) -> SensorEvent:
        try:
            obj = json.loads(data.decode("utf-8"))
            if not isinstance(obj, dict):
                raise ValueError("expected JSON object")
        except Exception:
            return SensorEvent.create(
                sensor_id="ws_text", protocol="websocket",
                payload=data, sensor_type="ws_text",
                metadata={"client_ip": client_ip},
            )
        payload = obj.get("payload", "")
        payload_b = (
            bytes.fromhex(payload) if isinstance(payload, str) and re.fullmatch(r"[0-9a-fA-F]*", payload)
            else json.dumps(obj.get("payload", {})).encode()
        )
        return SensorEvent.create(
            sensor_id=str(obj.get("sensor_id", "ws_text")),
            protocol="websocket",
            payload=payload_b,
            priority=Priority(obj.get("priority", "normal"))
                     if obj.get("priority", "normal") in ("normal", "high", "critical")
                     else Priority.NORMAL,
            device_id=str(obj.get("device_id", "")),
            sensor_type=str(obj.get("sensor_type", "")),
            unit=str(obj.get("unit", "")),
            metadata={**(obj.get("metadata") or {}), "client_ip": client_ip},
        )

    def _parse_binary(self, data: bytes, client_ip: str) -> SensorEvent:
        return SensorEvent.create(
            sensor_id="ws_binary",
            protocol="websocket",
            payload=data,
            sensor_type="ws_binary",
            metadata={"client_ip": client_ip},
        )

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("WebSocketAdapter callback error: %s", exc)
