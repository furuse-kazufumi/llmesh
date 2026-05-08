"""Tests for WebSocketAdapter (v2.11 — J-4.3)."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import struct
import pytest

from llmesh.industrial.websocket_adapter import (
    WebSocketAdapter,
    _ws_accept_key, _client_in_allowlist, _safe_compare,
    _WS_MAGIC, _OP_TEXT, _OP_BIN, _OP_CLOSE, _OP_PING,
)
from llmesh.industrial.sensor_event import SensorEvent


def _free_port() -> int:
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]; s.close()
    return p


# ---------------------------------------------------------------------------
# Pure-function helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_accept_key_matches_rfc6455_example(self):
        # RFC 6455 § 1.3 example
        assert _ws_accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo="

    def test_cidr_allowlist_empty_accepts_all(self):
        assert _client_in_allowlist("192.168.1.5", []) is True

    def test_cidr_allowlist_match(self):
        assert _client_in_allowlist("192.168.1.5", ["192.168.1.0/24"]) is True
        assert _client_in_allowlist("10.0.0.1", ["192.168.1.0/24"]) is False

    def test_cidr_invalid_ip(self):
        assert _client_in_allowlist("not-an-ip", ["192.168.1.0/24"]) is False

    def test_safe_compare(self):
        assert _safe_compare("abc", "abc") is True
        assert _safe_compare("abc", "abd") is False


# ---------------------------------------------------------------------------
# Adapter construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_defaults(self):
        a = WebSocketAdapter()
        assert a._host == "127.0.0.1"
        assert a._port == 8765

    def test_max_message_clamped(self):
        a = WebSocketAdapter(max_message_bytes=10**12)
        assert a._max_message_bytes == 1_048_576

    def test_on_event(self):
        a = WebSocketAdapter()
        cb = lambda ev: None
        a.on_event(cb)
        assert cb in a._callbacks


# ---------------------------------------------------------------------------
# Frame encoding
# ---------------------------------------------------------------------------

class TestFrameEncoding:
    def test_short_frame_encoding(self):
        a = WebSocketAdapter()
        f = a._encode_frame(_OP_TEXT, b"hello")
        assert f[0] == 0x81   # FIN+TEXT
        assert f[1] == 5
        assert f[2:] == b"hello"

    def test_long_frame_encoding_16bit(self):
        a = WebSocketAdapter()
        payload = b"x" * 200
        f = a._encode_frame(_OP_BIN, payload)
        assert f[0] == 0x82   # FIN+BIN
        assert f[1] == 126    # 16-bit length follows
        assert struct.unpack("!H", f[2:4])[0] == 200


# ---------------------------------------------------------------------------
# Lifecycle (real socket on ephemeral port)
# ---------------------------------------------------------------------------

class TestLifecycle:
    @pytest.mark.asyncio
    async def test_start_stop(self):
        port = _free_port()
        a = WebSocketAdapter("127.0.0.1", port)
        await a.start()
        assert a._running is True
        await a.stop()
        assert a._running is False

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self):
        port = _free_port()
        a = WebSocketAdapter("127.0.0.1", port)
        await a.start()
        srv1 = a._server
        await a.start()
        assert a._server is srv1
        await a.stop()


# ---------------------------------------------------------------------------
# Handshake protocol
# ---------------------------------------------------------------------------

class TestHandshake:
    async def _do_handshake(self, port: int, *,
                             extra_headers: dict[str, str] | None = None) -> bytes:
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        try:
            key = base64.b64encode(b"0" * 16).decode()
            req = (
                f"GET / HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{port}\r\n"
                f"Upgrade: websocket\r\n"
                f"Connection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\n"
                f"Sec-WebSocket-Version: 13\r\n"
            )
            for k, v in (extra_headers or {}).items():
                req += f"{k}: {v}\r\n"
            req += "\r\n"
            writer.write(req.encode())
            await writer.drain()
            response = await asyncio.wait_for(
                reader.readuntil(b"\r\n\r\n"), timeout=2.0,
            )
            return response
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    @pytest.mark.asyncio
    async def test_valid_handshake(self):
        port = _free_port()
        a = WebSocketAdapter("127.0.0.1", port)
        await a.start()
        try:
            response = await self._do_handshake(port)
            assert b"101 Switching Protocols" in response
            assert b"Sec-WebSocket-Accept:" in response
        finally:
            await a.stop()

    @pytest.mark.asyncio
    async def test_auth_token_required_when_set(self):
        port = _free_port()
        a = WebSocketAdapter("127.0.0.1", port, auth_token="s3cret")
        await a.start()
        try:
            r1 = await self._do_handshake(port)   # no token
            assert b"401" in r1
            r2 = await self._do_handshake(
                port, extra_headers={"X-LLMesh-Token": "s3cret"},
            )
            assert b"101 Switching Protocols" in r2
        finally:
            await a.stop()


# ---------------------------------------------------------------------------
# Event dispatch (helper-direct)
# ---------------------------------------------------------------------------

class TestDispatch:
    def test_text_json_parsed(self):
        a = WebSocketAdapter()
        events: list[SensorEvent] = []
        a.on_event(events.append)

        msg = json.dumps({
            "sensor_id": "temp_01",
            "sensor_type": "temperature",
            "unit": "C",
            "payload": "deadbeef",
        }).encode()
        a._dispatch_message(_OP_TEXT, msg, client_ip="127.0.0.1")

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_id == "temp_01"
        assert ev.sensor_type == "temperature"
        assert ev.payload == bytes.fromhex("deadbeef")

    def test_text_invalid_json_falls_back_to_raw(self):
        a = WebSocketAdapter()
        events = []
        a.on_event(events.append)
        a._dispatch_message(_OP_TEXT, b"not json", client_ip="127.0.0.1")
        assert events[0].sensor_type == "ws_text"

    def test_binary_passthrough(self):
        a = WebSocketAdapter()
        events = []
        a.on_event(events.append)
        a._dispatch_message(_OP_BIN, b"\xde\xad\xbe\xef", client_ip="127.0.0.1")
        assert events[0].payload == b"\xde\xad\xbe\xef"
        assert events[0].sensor_type == "ws_binary"

    def test_callback_exception_does_not_crash(self):
        a = WebSocketAdapter()
        a.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
        a._dispatch_message(_OP_BIN, b"ok", client_ip="127.0.0.1")  # must not raise

    def test_client_ip_in_metadata(self):
        a = WebSocketAdapter()
        events = []
        a.on_event(events.append)
        a._dispatch_message(_OP_BIN, b"x", client_ip="10.0.0.5")
        assert events[0].metadata["client_ip"] == "10.0.0.5"


# ---------------------------------------------------------------------------
# CIDR allowlist (real socket)
# ---------------------------------------------------------------------------

class TestCIDR:
    @pytest.mark.asyncio
    async def test_loopback_allowed_when_listed(self):
        port = _free_port()
        a = WebSocketAdapter(
            "127.0.0.1", port,
            accept_cidrs=["127.0.0.0/8"],
        )
        await a.start()
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            key = base64.b64encode(b"k" * 16).decode()
            writer.write(
                (f"GET / HTTP/1.1\r\n"
                 f"Upgrade: websocket\r\nConnection: Upgrade\r\n"
                 f"Sec-WebSocket-Key: {key}\r\n\r\n").encode()
            )
            await writer.drain()
            r = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=2.0)
            assert b"101 Switching" in r
            writer.close()
            await writer.wait_closed()
        finally:
            await a.stop()
