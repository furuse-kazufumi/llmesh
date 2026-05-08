"""Tests for TCPAdapter — length-prefix framing and request-response."""
from __future__ import annotations

import asyncio

import pytest

from llmesh.protocol import NodeAddress, TCPAdapter, TransportError, UnifiedMessage
from llmesh.protocol.message import MessageType
from llmesh.protocol.tcp_adapter import _HEADER, _pack_frame, _read_frame

from helpers import _alloc_port


# ------------------------------------------------------------------
# Framing helpers
# ------------------------------------------------------------------

class TestFraming:
    async def test_pack_unpack(self):
        data = b"hello world"
        framed = _HEADER.pack(len(data)) + data
        reader = asyncio.StreamReader()
        reader.feed_data(framed)
        reader.feed_eof()
        assert await _read_frame(reader) == data

    async def test_empty_body(self):
        framed = _HEADER.pack(0)
        reader = asyncio.StreamReader()
        reader.feed_data(framed)
        reader.feed_eof()
        assert await _read_frame(reader) == b""

    async def test_frame_too_large_raises(self):
        framed = _HEADER.pack(20 * 1024 * 1024)
        reader = asyncio.StreamReader()
        reader.feed_data(framed)
        reader.feed_eof()
        with pytest.raises(TransportError, match="frame_too_large"):
            await _read_frame(reader)


# ------------------------------------------------------------------
# TCPAdapter round-trip
# ------------------------------------------------------------------

class TestTCPAdapter:
    def test_protocol_name(self):
        assert TCPAdapter().protocol_name == "tcp"

    def test_not_running_before_start(self):
        assert not TCPAdapter().is_running

    async def test_start_stop(self, free_port):
        adapter = TCPAdapter()
        await adapter.start("127.0.0.1", free_port)
        assert adapter.is_running
        await adapter.stop()
        assert not adapter.is_running

    async def test_request_response_roundtrip(self, free_port, sender):
        server = TCPAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def echo(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"echo": msg.payload}, sender=server_addr)

        server.on_message(echo)
        await server.start("127.0.0.1", free_port)

        msg = UnifiedMessage.request({"data": "ping"}, sender, server_addr)
        response = await TCPAdapter().send(msg, server_addr)
        await server.stop()

        assert response is not None
        assert response.type == MessageType.RESPONSE
        assert response.payload["echo"] == {"data": "ping"}
        assert response.correlation_id == msg.id

    async def test_server_no_handler_returns_no_response(self, free_port, sender):
        server = TCPAdapter()
        await server.start("127.0.0.1", free_port)
        response = await TCPAdapter().send(
            UnifiedMessage.request({}, sender), NodeAddress("127.0.0.1", free_port)
        )
        await server.stop()
        assert response is None

    async def test_send_to_closed_port_raises(self, sender):
        with pytest.raises(TransportError):
            await TCPAdapter().send(
                UnifiedMessage.request({}, sender), NodeAddress("127.0.0.1", 19988)
            )

    async def test_broadcast_multiple_targets(self, sender):
        port1, port2 = _alloc_port(), _alloc_port()
        received: list[str] = []

        async def record(msg: UnifiedMessage) -> None:
            received.append(msg.id)

        s1, s2 = TCPAdapter(), TCPAdapter()
        s1.on_message(record)
        s2.on_message(record)
        await s1.start("127.0.0.1", port1)
        await s2.start("127.0.0.1", port2)

        await TCPAdapter().broadcast(
            UnifiedMessage.broadcast({}, sender),
            [NodeAddress("127.0.0.1", port1), NodeAddress("127.0.0.1", port2)],
        )
        await asyncio.sleep(0.1)
        await s1.stop()
        await s2.stop()
        assert len(received) == 2

    async def test_broadcast_ignores_errors(self, sender):
        await TCPAdapter().broadcast(
            UnifiedMessage.broadcast({}, sender),
            [NodeAddress("127.0.0.1", 19980), NodeAddress("127.0.0.1", 19981)],
        )

    async def test_concurrent_requests(self, free_port, sender):
        server = TCPAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def echo(msg: UnifiedMessage) -> UnifiedMessage:
            await asyncio.sleep(0.01)
            return msg.make_response({"n": msg.payload["n"]}, sender=server_addr)

        server.on_message(echo)
        await server.start("127.0.0.1", free_port)

        client = TCPAdapter()
        results = await asyncio.gather(*[
            client.send(
                UnifiedMessage.request({"n": i}, sender, server_addr), server_addr
            )
            for i in range(5)
        ])
        await server.stop()

        assert all(r is not None for r in results)
        assert {r.payload["n"] for r in results} == set(range(5))  # type: ignore[union-attr]
