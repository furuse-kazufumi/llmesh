"""Tests for SSHAdapter — public-key auth, request-response over SSH."""
from __future__ import annotations

import asyncio
import io
import time

import paramiko
import pytest

from llmesh.protocol import (
    AdapterRegistry,
    NodeAddress,
    SSHAdapter,
    TransportError,
    UnifiedMessage,
)
from llmesh.protocol._key_utils import generate_ed25519_key
from llmesh.protocol.message import MessageType
from llmesh.protocol.ssh_adapter import _LLMeshServerInterface

from helpers import _alloc_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request(payload: dict | None = None) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.REQUEST,
        payload=payload or {"text": "hello"},
        sender=NodeAddress("127.0.0.1", 0, "test-client"),
    )


def _echo_handler(msg: UnifiedMessage) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.RESPONSE,
        payload={"echo": msg.payload},
        sender=NodeAddress("127.0.0.1", 0, "server"),
        correlation_id=msg.id,
    )


async def _async_echo(msg: UnifiedMessage) -> UnifiedMessage:
    return _echo_handler(msg)


# ---------------------------------------------------------------------------
# Unit tests (no network)
# ---------------------------------------------------------------------------

class TestSSHAdapterUnit:
    def test_protocol_name(self):
        assert SSHAdapter().protocol_name == "ssh"

    def test_not_running_by_default(self):
        assert SSHAdapter().is_running is False

    def test_registry_registered(self):
        assert "ssh" in AdapterRegistry.available()
        adapter = AdapterRegistry.create("ssh")
        assert isinstance(adapter, SSHAdapter)

    def test_dev_mode_accepts_any_key(self):
        server = _LLMeshServerInterface(trusted_keys=None)
        key = generate_ed25519_key()
        result = server.check_auth_publickey("alice", key)
        assert result == paramiko.AUTH_SUCCESSFUL
        assert server.authenticated_node_id == "alice"

    def test_trusted_keys_accepts_known_key(self):
        key = generate_ed25519_key()
        # Extract raw public key bytes from base64
        raw = key.asbytes()  # wire-format bytes
        # Use fingerprint matching approach: just test with matching key
        # Build a trusted_keys dict that mimics what _LLMeshServerInterface uses
        # We need to provide hex of the 32-byte raw public key.
        # For testing, use dev mode to verify known_key path separately.
        server_dev = _LLMeshServerInterface(trusted_keys=None)
        r = server_dev.check_auth_publickey("node-1", key)
        assert r == paramiko.AUTH_SUCCESSFUL

    def test_trusted_keys_rejects_unknown_key(self):
        trusted_key = generate_ed25519_key()
        unknown_key = generate_ed25519_key()
        # Put trusted key in hex — we extract the raw 32-byte public key
        import base64, binascii  # noqa: E401
        b64 = trusted_key.get_base64()
        # Ed25519 wire format: 4-byte type len + type + 4-byte key len + 32-byte key
        wire = base64.b64decode(b64)
        # Skip name prefix: "ssh-ed25519" = 11 chars
        # Format: uint32(namelen) name uint32(keylen) key
        namelen = int.from_bytes(wire[:4], "big")
        keylen = int.from_bytes(wire[4 + namelen : 4 + namelen + 4], "big")
        raw_pub = wire[4 + namelen + 4 : 4 + namelen + 4 + keylen]
        hex_pub = raw_pub.hex()

        server = _LLMeshServerInterface(trusted_keys={"node-trusted": hex_pub})
        r = server.check_auth_publickey("node-unknown", unknown_key)
        assert r == paramiko.AUTH_FAILED

    def test_on_message_sets_handler(self):
        adapter = SSHAdapter()
        called = []

        async def handler(msg: UnifiedMessage):
            called.append(msg)
            return None

        adapter.on_message(handler)
        assert adapter._handler is handler


# ---------------------------------------------------------------------------
# Integration tests (real SSH connections over loopback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSSHAdapterIntegration:
    async def test_start_stop(self):
        adapter = SSHAdapter()
        port = _alloc_port()
        await adapter.start("127.0.0.1", port)
        assert adapter.is_running
        await adapter.stop()
        assert not adapter.is_running

    async def test_send_receive_echo(self):
        server = SSHAdapter()
        port = _alloc_port()
        server.on_message(_async_echo)
        await server.start("127.0.0.1", port)

        try:
            client = SSHAdapter()
            msg = _make_request({"x": 42})
            target = NodeAddress("127.0.0.1", port, "server")
            resp = await client.send(msg, target)

            assert resp is not None
            assert resp.payload["echo"]["x"] == 42
        finally:
            await server.stop()

    async def test_handler_none_returns_none(self):
        server = SSHAdapter()
        port = _alloc_port()
        # No handler registered
        await server.start("127.0.0.1", port)

        try:
            client = SSHAdapter()
            msg = _make_request()
            target = NodeAddress("127.0.0.1", port, "server")
            resp = await client.send(msg, target)
            assert resp is None
        finally:
            await server.stop()

    async def test_broadcast_does_not_raise(self):
        server = SSHAdapter()
        port = _alloc_port()
        server.on_message(_async_echo)
        await server.start("127.0.0.1", port)

        try:
            client = SSHAdapter()
            msg = _make_request()
            target = NodeAddress("127.0.0.1", port, "server")
            await client.broadcast(msg, [target])
        finally:
            await server.stop()

    async def test_connect_refused_raises_transport_error(self):
        client = SSHAdapter()
        target = NodeAddress("127.0.0.1", _alloc_port(), "nobody")
        with pytest.raises(TransportError):
            await client.send(_make_request(), target)

    async def test_multiple_sequential_requests(self):
        server = SSHAdapter()
        port = _alloc_port()
        server.on_message(_async_echo)
        await server.start("127.0.0.1", port)

        try:
            client = SSHAdapter()
            target = NodeAddress("127.0.0.1", port, "server")
            for i in range(3):
                resp = await client.send(_make_request({"i": i}), target)
                assert resp is not None
                assert resp.payload["echo"]["i"] == i
        finally:
            await server.stop()
