"""Tests for SMTPAdapter — SMTP intake server."""
from __future__ import annotations

import asyncio
import smtplib
import textwrap
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmesh.protocol import (
    AdapterRegistry,
    NodeAddress,
    SMTPAdapter,
    TransportError,
    UnifiedMessage,
)
from llmesh.protocol.message import MessageType
from llmesh.protocol.smtp_adapter import _LLMeshSMTPHandler, _extract_text_body

from helpers import _alloc_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_raw_email(
    from_addr: str = "sender@example.com",
    subject: str = "summarize",
    body: str = "Hello world",
    to_addr: str = "llmesh@localhost",
) -> bytes:
    return textwrap.dedent(f"""\
        From: {from_addr}\r
        To: {to_addr}\r
        Subject: {subject}\r
        \r
        {body}\r
    """).encode()


def _make_envelope(
    mail_from: str = "sender@example.com",
    content: bytes | None = None,
) -> MagicMock:
    env = MagicMock()
    env.mail_from = mail_from
    env.content = content or _make_raw_email(from_addr=mail_from)
    return env


def _make_session(peer: tuple = ("127.0.0.1", 12345)) -> MagicMock:
    sess = MagicMock()
    sess.peer = peer
    return sess


async def _echo_handler(msg: UnifiedMessage) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.RESPONSE,
        payload={"result": f"echo:{msg.payload['prompt']}"},
        sender=NodeAddress("0.0.0.0", 0, "server"),
        correlation_id=msg.id,
    )


# ---------------------------------------------------------------------------
# Unit tests — _extract_text_body
# ---------------------------------------------------------------------------

class TestExtractTextBody:
    def test_plain_email(self):
        import email
        import email.policy
        raw = b"Content-Type: text/plain\r\n\r\nHello"
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        assert _extract_text_body(msg) == "Hello"

    def test_no_text_plain_returns_none(self):
        import email
        import email.policy
        raw = b"Content-Type: text/html\r\n\r\n<b>Hello</b>"
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        assert _extract_text_body(msg) is None

    def test_multipart_extracts_plain(self):
        import email
        import email.policy
        raw = (
            b"Content-Type: multipart/mixed; boundary=boundary\r\n\r\n"
            b"--boundary\r\n"
            b"Content-Type: text/plain\r\n\r\n"
            b"Plain part\r\n"
            b"--boundary\r\n"
            b"Content-Type: text/html\r\n\r\n"
            b"<b>HTML</b>\r\n"
            b"--boundary--\r\n"
        )
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        body = _extract_text_body(msg)
        assert body is not None and "Plain part" in body


# ---------------------------------------------------------------------------
# Unit tests — SMTPAdapter properties
# ---------------------------------------------------------------------------

class TestSMTPAdapterUnit:
    def test_protocol_name(self):
        assert SMTPAdapter().protocol_name == "smtp"

    def test_not_running_by_default(self):
        assert SMTPAdapter().is_running is False

    def test_registry_registered(self):
        assert "smtp" in AdapterRegistry.available()
        adapter = AdapterRegistry.create("smtp")
        assert isinstance(adapter, SMTPAdapter)

    def test_on_message_sets_handler(self):
        adapter = SMTPAdapter()
        handler = AsyncMock()
        adapter.on_message(handler)
        assert adapter._handler is handler

    def test_trusted_senders_stored(self):
        adapter = SMTPAdapter(trusted_senders={"alice@example.com"})
        assert adapter._trusted_senders == {"alice@example.com"}

    def test_relay_config_stored(self):
        adapter = SMTPAdapter(relay_host="mail.example.com", relay_port=587)
        assert adapter._relay_host == "mail.example.com"
        assert adapter._relay_port == 587


# ---------------------------------------------------------------------------
# Unit tests — _LLMeshSMTPHandler
# ---------------------------------------------------------------------------

class TestSMTPHandler:
    @pytest.mark.asyncio
    async def test_handle_data_basic(self):
        received: list[UnifiedMessage] = []

        async def capture(msg: UnifiedMessage) -> None:
            received.append(msg)
            return None

        handler = _LLMeshSMTPHandler(
            message_handler=capture,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        result = await handler.handle_DATA(MagicMock(), _make_session(), _make_envelope())
        assert result.startswith("250")
        assert len(received) == 1
        assert received[0].payload["tool_name"] == "summarize"
        assert received[0].payload["prompt"] == "Hello world"

    @pytest.mark.asyncio
    async def test_handle_data_oversized(self):
        from llmesh.protocol.smtp_adapter import _MAX_EMAIL_BYTES
        env = _make_envelope()
        env.content = b"X" * (_MAX_EMAIL_BYTES + 1)

        handler = _LLMeshSMTPHandler(
            message_handler=None,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        result = await handler.handle_DATA(MagicMock(), _make_session(), env)
        assert result.startswith("552")

    @pytest.mark.asyncio
    async def test_handle_data_untrusted_sender(self):
        handler = _LLMeshSMTPHandler(
            message_handler=None,
            trusted_senders={"allowed@example.com"},
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        env = _make_envelope(mail_from="evil@attacker.com")
        result = await handler.handle_DATA(MagicMock(), _make_session(), env)
        assert result.startswith("550")

    @pytest.mark.asyncio
    async def test_handle_data_trusted_sender_accepted(self):
        received: list[UnifiedMessage] = []

        async def capture(msg: UnifiedMessage) -> None:
            received.append(msg)

        handler = _LLMeshSMTPHandler(
            message_handler=capture,
            trusted_senders={"alice@example.com"},
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        env = _make_envelope(mail_from="alice@example.com")
        result = await handler.handle_DATA(MagicMock(), _make_session(), env)
        assert result.startswith("250")
        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_handle_data_no_text_plain(self):
        env = _make_envelope()
        env.content = b"Content-Type: text/html\r\n\r\n<b>hi</b>"

        handler = _LLMeshSMTPHandler(
            message_handler=None,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        result = await handler.handle_DATA(MagicMock(), _make_session(), env)
        assert result.startswith("550")

    @pytest.mark.asyncio
    async def test_handler_exception_returns_451(self):
        async def bad_handler(msg: UnifiedMessage) -> None:
            raise RuntimeError("boom")

        handler = _LLMeshSMTPHandler(
            message_handler=bad_handler,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        result = await handler.handle_DATA(MagicMock(), _make_session(), _make_envelope())
        assert result.startswith("451")

    @pytest.mark.asyncio
    async def test_reply_sent_on_response(self):
        async def respond(msg: UnifiedMessage) -> UnifiedMessage:
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": "done"},
                sender=NodeAddress("0.0.0.0", 0, "server"),
            )

        handler = _LLMeshSMTPHandler(
            message_handler=respond,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )

        with patch("llmesh.protocol.smtp_adapter._send_reply") as mock_send:
            result = await handler.handle_DATA(MagicMock(), _make_session(), _make_envelope())
            assert result.startswith("250")
            mock_send.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_handler_returns_250(self):
        handler = _LLMeshSMTPHandler(
            message_handler=None,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        result = await handler.handle_DATA(MagicMock(), _make_session(), _make_envelope())
        assert result.startswith("250")

    @pytest.mark.asyncio
    async def test_empty_subject_uses_default_tool_name(self):
        received: list[UnifiedMessage] = []

        async def capture(msg: UnifiedMessage) -> None:
            received.append(msg)

        handler = _LLMeshSMTPHandler(
            message_handler=capture,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        env = _make_envelope()
        env.content = _make_raw_email(subject="", body="test prompt")
        await handler.handle_DATA(MagicMock(), _make_session(), env)
        assert received[0].payload["tool_name"] == "default"

    @pytest.mark.asyncio
    async def test_from_address_in_payload(self):
        received: list[UnifiedMessage] = []

        async def capture(msg: UnifiedMessage) -> None:
            received.append(msg)

        handler = _LLMeshSMTPHandler(
            message_handler=capture,
            trusted_senders=None,
            relay_host="localhost",
            relay_port=25,
            node_address=NodeAddress("0.0.0.0", 8025, "llmesh@localhost"),
        )
        env = _make_envelope(mail_from="node1@mesh.local")
        await handler.handle_DATA(MagicMock(), _make_session(), env)
        assert received[0].payload["from_address"] == "node1@mesh.local"


# ---------------------------------------------------------------------------
# Integration tests — real SMTP server
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSMTPAdapterIntegration:
    async def test_start_stop(self):
        adapter = SMTPAdapter()
        port = _alloc_port()
        await adapter.start("127.0.0.1", port)
        assert adapter.is_running
        await adapter.stop()
        assert not adapter.is_running

    async def test_receive_email(self):
        received: list[UnifiedMessage] = []

        async def capture(msg: UnifiedMessage) -> None:
            received.append(msg)

        adapter = SMTPAdapter()
        port = _alloc_port()
        adapter.on_message(capture)
        await adapter.start("127.0.0.1", port)

        try:
            with smtplib.SMTP("127.0.0.1", port, timeout=5) as smtp:
                raw = _make_raw_email(subject="translate", body="Bonjour")
                smtp.sendmail("sender@example.com", ["llmesh@localhost"], raw)

            # Give handler time to process
            deadline = time.monotonic() + 3.0
            while not received and time.monotonic() < deadline:
                await asyncio.sleep(0.05)

            assert len(received) == 1
            assert received[0].payload["tool_name"] == "translate"
            assert received[0].payload["prompt"] == "Bonjour"
        finally:
            await adapter.stop()

    async def test_send_fire_and_forget(self):
        """send() via SMTP relay returns None."""
        adapter = SMTPAdapter()
        port = _alloc_port()

        # Start a minimal SMTP server to receive the sent message
        sink: list[bytes] = []

        async def capture(msg: UnifiedMessage) -> None:
            sink.append(msg.payload.get("prompt", b"").encode())

        sink_adapter = SMTPAdapter()
        await sink_adapter.start("127.0.0.1", port)
        sink_adapter.on_message(capture)

        try:
            msg = UnifiedMessage(
                type=MessageType.REQUEST,
                payload={"tool_name": "ping", "prompt": "test"},
                sender=NodeAddress("127.0.0.1", 0, "client"),
            )
            target = NodeAddress("127.0.0.1", port, "llmesh@localhost")
            result = await adapter.send(msg, target)
            assert result is None
        finally:
            await sink_adapter.stop()

    async def test_send_connection_refused_raises_transport_error(self):
        adapter = SMTPAdapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"tool_name": "ping", "prompt": "x"},
            sender=NodeAddress("127.0.0.1", 0, "client"),
        )
        target = NodeAddress("127.0.0.1", _alloc_port(), "nobody")
        with pytest.raises(TransportError):
            await adapter.send(msg, target)

    async def test_broadcast_does_not_raise(self):
        adapter = SMTPAdapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"tool_name": "ping", "prompt": "x"},
            sender=NodeAddress("127.0.0.1", 0, "client"),
        )
        # broadcast to unreachable targets silently swallows TransportError
        target = NodeAddress("127.0.0.1", _alloc_port(), "nobody")
        await adapter.broadcast(msg, [target])  # no exception
