"""Tests for TelnetAdapter — double opt-in, L3/L4 rejection, option negotiation."""
from __future__ import annotations

import asyncio
import pytest

from llmesh.protocol.telnet_adapter import (
    TelnetAdapter,
    _strip_telnet_options,
    _build_refuse_option,
    _check_double_optin,
    _IAC, _WILL, _DO, _DONT, _WONT,
)
from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_msg(level: int = 0) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.REQUEST,
        payload={"prompt": "hello", "data_level": level},
        sender=NodeAddress("127.0.0.1", 9999),
        target=NodeAddress("127.0.0.1", 0),
    )


# ---------------------------------------------------------------------------
# Double opt-in guard
# ---------------------------------------------------------------------------

class TestDoubleOptin:
    def test_missing_both_raises(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_TELNET", raising=False)
        monkeypatch.delenv("LLMESH_UNSAFE_TELNET_NO_TLS", raising=False)
        with pytest.raises(RuntimeError, match="LLMESH_ENABLE_TELNET"):
            _check_double_optin()

    def test_only_enable_raises(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_TELNET", "1")
        monkeypatch.delenv("LLMESH_UNSAFE_TELNET_NO_TLS", raising=False)
        with pytest.raises(RuntimeError):
            _check_double_optin()

    def test_only_no_tls_raises(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_TELNET", raising=False)
        monkeypatch.setenv("LLMESH_UNSAFE_TELNET_NO_TLS", "1")
        with pytest.raises(RuntimeError):
            _check_double_optin()

    def test_both_set_passes(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_TELNET", "1")
        monkeypatch.setenv("LLMESH_UNSAFE_TELNET_NO_TLS", "1")
        _check_double_optin()   # should not raise

    def test_wrong_value_raises(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_TELNET", "true")
        monkeypatch.setenv("LLMESH_UNSAFE_TELNET_NO_TLS", "yes")
        with pytest.raises(RuntimeError):
            _check_double_optin()


# ---------------------------------------------------------------------------
# Telnet option stripping
# ---------------------------------------------------------------------------

class TestStripTelnetOptions:
    def test_plain_text_unchanged(self):
        data = b'{"hello": "world"}\n'
        assert _strip_telnet_options(data) == data

    def test_strips_will_option(self):
        # IAC WILL ECHO + payload
        data = bytes([_IAC, _WILL, 0x01]) + b'payload'
        assert _strip_telnet_options(data) == b'payload'

    def test_strips_do_option(self):
        data = bytes([_IAC, _DO, 0x03]) + b'data'
        assert _strip_telnet_options(data) == b'data'

    def test_strips_wont_option(self):
        data = bytes([_IAC, _WONT, 0x03]) + b'ok'
        assert _strip_telnet_options(data) == b'ok'

    def test_strips_dont_option(self):
        data = bytes([_IAC, _DONT, 0x01]) + b'ok'
        assert _strip_telnet_options(data) == b'ok'

    def test_multiple_options_stripped(self):
        data = (
            bytes([_IAC, _WILL, 0x01])
            + bytes([_IAC, _DO, 0x03])
            + b'clean'
        )
        assert _strip_telnet_options(data) == b'clean'

    def test_iac_iac_escaped(self):
        # IAC IAC = literal 0xFF
        data = bytes([_IAC, _IAC]) + b'x'
        result = _strip_telnet_options(data)
        assert result == b'x'

    def test_empty_bytes(self):
        assert _strip_telnet_options(b"") == b""

    def test_trailing_iac_ignored(self):
        # Incomplete sequence at end — should not crash
        data = b'hello' + bytes([_IAC])
        result = _strip_telnet_options(data)
        assert result == b'hello'


# ---------------------------------------------------------------------------
# Option refusal builder
# ---------------------------------------------------------------------------

class TestBuildRefuseOption:
    def test_will_returns_dont(self):
        resp = _build_refuse_option(_WILL, 0x01)
        assert resp == bytes([_IAC, _DONT, 0x01])

    def test_do_returns_wont(self):
        resp = _build_refuse_option(_DO, 0x03)
        assert resp == bytes([_IAC, _WONT, 0x03])

    def test_other_cmd_returns_empty(self):
        assert _build_refuse_option(_WONT, 0x01) == b""
        assert _build_refuse_option(_DONT, 0x01) == b""


# ---------------------------------------------------------------------------
# Adapter properties (no network)
# ---------------------------------------------------------------------------

class TestAdapterProperties:
    def test_protocol_name(self):
        assert TelnetAdapter().protocol_name == "telnet"

    def test_not_running_initially(self):
        assert not TelnetAdapter().is_running

    def test_on_message_sets_handler(self):
        adapter = TelnetAdapter()
        async def handler(msg): return None
        adapter.on_message(handler)
        assert adapter._handler is handler


# ---------------------------------------------------------------------------
# start() requires double opt-in
# ---------------------------------------------------------------------------

class TestStartRequiresOptin:
    @pytest.mark.asyncio
    async def test_start_without_optin_raises(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_TELNET", raising=False)
        monkeypatch.delenv("LLMESH_UNSAFE_TELNET_NO_TLS", raising=False)
        adapter = TelnetAdapter()
        with pytest.raises(RuntimeError, match="LLMESH_ENABLE_TELNET"):
            await adapter.start("127.0.0.1", 0)


# ---------------------------------------------------------------------------
# Integration: start / stop / message exchange
# ---------------------------------------------------------------------------

@pytest.fixture
def telnet_env(monkeypatch):
    monkeypatch.setenv("LLMESH_ENABLE_TELNET", "1")
    monkeypatch.setenv("LLMESH_UNSAFE_TELNET_NO_TLS", "1")


class TestTelnetIntegration:
    @pytest.mark.asyncio
    async def test_start_stop(self, telnet_env):
        adapter = TelnetAdapter()
        await adapter.start("127.0.0.1", 0)
        assert adapter.is_running
        await adapter.stop()
        assert not adapter.is_running

    @pytest.mark.asyncio
    async def test_echo_handler(self, telnet_env):
        adapter = TelnetAdapter()

        async def echo(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"echo": msg.payload.get("prompt")}, msg.target or msg.sender)

        adapter.on_message(echo)
        await adapter.start("127.0.0.1", 0)
        port = adapter._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            target = NodeAddress("127.0.0.1", port)
            msg = _make_msg(level=0)
            response = await adapter.send(msg, target)
            assert response is not None
            assert response.payload.get("echo") == "hello"
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_l3_prompt_rejected(self, telnet_env):
        adapter = TelnetAdapter()
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage | None:
            received.append(msg)
            return None

        adapter.on_message(handler)
        await adapter.start("127.0.0.1", 0)
        port = adapter._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            target = NodeAddress("127.0.0.1", port)
            msg = _make_msg(level=3)
            response = await adapter.send(msg, target)
            await asyncio.sleep(0.05)   # let server process
            assert not received, "L3 prompt must not reach handler"
            assert response is not None
            assert response.type == MessageType.ERROR
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_l4_prompt_rejected(self, telnet_env):
        adapter = TelnetAdapter()
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage | None:
            received.append(msg)
            return None

        adapter.on_message(handler)
        await adapter.start("127.0.0.1", 0)
        port = adapter._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            target = NodeAddress("127.0.0.1", port)
            msg = _make_msg(level=4)
            response = await adapter.send(msg, target)
            await asyncio.sleep(0.05)
            assert not received
            assert response is not None
            assert response.type == MessageType.ERROR
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_l2_prompt_passes(self, telnet_env):
        adapter = TelnetAdapter()
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage | None:
            received.append(msg)
            return msg.make_response({"ok": True}, msg.target or msg.sender)

        adapter.on_message(handler)
        await adapter.start("127.0.0.1", 0)
        port = adapter._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            target = NodeAddress("127.0.0.1", port)
            msg = _make_msg(level=2)
            response = await adapter.send(msg, target)
            await asyncio.sleep(0.05)
            assert len(received) == 1
            assert response is not None and response.payload.get("ok")
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_no_handler_no_crash(self, telnet_env):
        adapter = TelnetAdapter()
        await adapter.start("127.0.0.1", 0)
        port = adapter._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            target = NodeAddress("127.0.0.1", port)
            msg = _make_msg()
            # No handler registered — server processes silently, returns None
            response = await adapter.send(msg, target)
            assert response is None
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_oversized_message_dropped(self, telnet_env):
        """Messages exceeding _MAX_MSG_BYTES are silently dropped."""
        adapter = TelnetAdapter()
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage | None:
            received.append(msg)
            return None

        adapter.on_message(handler)
        await adapter.start("127.0.0.1", 0)
        port = adapter._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            oversized = b"x" * (1024 * 1024 + 1) + b"\n"
            writer.write(oversized)
            await writer.drain()
            await asyncio.sleep(0.1)
            assert not received
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
        finally:
            await adapter.stop()

    @pytest.mark.asyncio
    async def test_telnet_options_negotiated_away(self, telnet_env):
        """Client sending IAC WILL ECHO gets IAC DONT ECHO back."""
        adapter = TelnetAdapter()

        async def echo(msg: UnifiedMessage) -> UnifiedMessage | None:
            return msg.make_response({"ok": True}, msg.target or msg.sender)

        adapter.on_message(echo)
        await adapter.start("127.0.0.1", 0)
        port = adapter._server.sockets[0].getsockname()[1]  # type: ignore[union-attr]

        try:
            # Build a message with IAC WILL ECHO prepended
            msg = _make_msg()
            raw_msg = bytes([_IAC, _WILL, 0x01]) + msg.to_bytes() + b"\n"
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(raw_msg)
            await writer.drain()
            resp_line = await asyncio.wait_for(reader.readline(), timeout=3.0)
            writer.close()
            try:
                await writer.wait_closed()
            except OSError:
                pass
            # Response line should contain IAC DONT ECHO prefix, then JSON
            assert _IAC in resp_line[0:3]
        finally:
            await adapter.stop()
