"""Tests for UDPAdapter — datagram framing and request-response."""
from __future__ import annotations

import asyncio
import socket

import pytest

from llmesh.protocol import NodeAddress, TransportError, UDPAdapter, UnifiedMessage
from llmesh.protocol.message import MessageType
from llmesh.protocol.udp_adapter import _HDR_SIZE, _MAGIC, _pack, _unpack

from helpers import _alloc_port


def _udp() -> int:
    return _alloc_port(socket.SOCK_DGRAM)


# ------------------------------------------------------------------
# Wire format helpers
# ------------------------------------------------------------------

class TestFraming:
    def test_pack_magic(self):
        assert _pack(0, b"hello")[:2] == _MAGIC

    def test_unpack_roundtrip(self):
        body = b'{"test": 1}'
        seq, unpacked = _unpack(_pack(42, body))
        assert seq == 42
        assert unpacked == body

    def test_bad_magic_raises(self):
        with pytest.raises(ValueError, match="bad magic"):
            _unpack(b"\x00\x00\x00\x00" + b"body")

    def test_too_short_raises(self):
        with pytest.raises(ValueError, match="too short"):
            _unpack(b"\x4c")

    def test_seq_wraps_at_65535(self):
        seq, _ = _unpack(_pack(0xFFFF, b"x"))
        assert seq == 0xFFFF

    def test_header_size(self):
        assert _HDR_SIZE == 8


# ------------------------------------------------------------------
# UDPAdapter unit tests
# ------------------------------------------------------------------

class TestUDPAdapter:
    def test_protocol_name(self):
        assert UDPAdapter().protocol_name == "udp"

    def test_not_running_before_start(self):
        assert not UDPAdapter().is_running

    async def test_start_stop(self, free_udp_port, sender):
        adapter = UDPAdapter()
        await adapter.start("127.0.0.1", free_udp_port)
        assert adapter.is_running
        await adapter.stop()
        assert not adapter.is_running

    async def test_handler_receives_broadcast(self, free_udp_port, sender):
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> None:
            received.append(msg)

        server = UDPAdapter()
        server.on_message(handler)
        await server.start("127.0.0.1", free_udp_port)

        client = UDPAdapter()
        await client.start("127.0.0.1", _udp())
        await client.send(
            UnifiedMessage.broadcast({"event": "ping"}, sender),
            NodeAddress("127.0.0.1", free_udp_port),
        )
        await asyncio.sleep(0.1)
        await client.stop()
        await server.stop()

        assert len(received) == 1
        assert received[0].payload == {"event": "ping"}

    async def test_request_response_roundtrip(self, sender):
        server_port, client_port = _udp(), _udp()
        server_addr = NodeAddress("127.0.0.1", server_port)

        async def echo(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"echo": msg.payload}, sender=server_addr)

        server = UDPAdapter(reply_timeout=2.0)
        server.on_message(echo)
        await server.start("127.0.0.1", server_port)

        client = UDPAdapter(reply_timeout=2.0)
        await client.start("127.0.0.1", client_port)
        msg = UnifiedMessage.request(
            {"q": "hello"}, NodeAddress("127.0.0.1", client_port), server_addr
        )
        response = await client.send(msg, server_addr)
        await client.stop()
        await server.stop()

        assert response is not None
        assert response.type == MessageType.RESPONSE
        assert response.payload["echo"] == {"q": "hello"}
        assert response.correlation_id == msg.id

    async def test_send_returns_none_on_timeout(self, sender):
        target_port = _udp()
        client = UDPAdapter(reply_timeout=0.1)
        await client.start("127.0.0.1", _udp())
        result = await client.send(
            UnifiedMessage.request({}, sender, NodeAddress("127.0.0.1", target_port)),
            NodeAddress("127.0.0.1", target_port),
        )
        await client.stop()
        assert result is None

    async def test_non_request_is_fire_and_forget(self, sender, free_udp_port):
        server = UDPAdapter()
        await server.start("127.0.0.1", free_udp_port)
        client = UDPAdapter()
        await client.start("127.0.0.1", _udp())
        result = await client.send(
            UnifiedMessage.broadcast({}, sender),
            NodeAddress("127.0.0.1", free_udp_port),
        )
        await client.stop()
        await server.stop()
        assert result is None

    async def test_broadcast_multiple_targets(self, sender):
        ports = [_udp(), _udp()]
        received: list[str] = []

        async def record(msg: UnifiedMessage) -> None:
            received.append(str(msg.payload.get("n")))

        servers = []
        for p in ports:
            s = UDPAdapter()
            s.on_message(record)
            await s.start("127.0.0.1", p)
            servers.append(s)

        client = UDPAdapter()
        await client.start("127.0.0.1", _udp())
        await client.broadcast(
            UnifiedMessage.broadcast({"n": 99}, sender),
            [NodeAddress("127.0.0.1", p) for p in ports],
        )
        await asyncio.sleep(0.1)
        for s in servers:
            await s.stop()
        await client.stop()

        assert len(received) == 2
        assert all(v == "99" for v in received)

    async def test_broadcast_empty_targets_noop(self, sender):
        await UDPAdapter().broadcast(UnifiedMessage.broadcast({}, sender), [])

    async def test_payload_too_large_raises(self, sender):
        with pytest.raises(TransportError, match="payload_too_large"):
            await UDPAdapter().broadcast(
                UnifiedMessage.broadcast({"data": "x" * 70000}, sender),
                [NodeAddress("127.0.0.1", 9999)],
            )
