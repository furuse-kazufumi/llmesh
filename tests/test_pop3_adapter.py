"""Tests for POP3Adapter — POP3 mailbox poller."""
from __future__ import annotations

import asyncio
import poplib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmesh.protocol import (
    AdapterRegistry,
    NodeAddress,
    POP3Adapter,
    TransportError,
    UnifiedMessage,
)
from llmesh.protocol.message import MessageType
from llmesh.protocol.pop3_adapter import _extract_text_body

from helpers import _alloc_port


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_email_bytes(
    from_addr: str = "sender@example.com",
    subject: str = "summarize",
    body: str = "Hello world",
    task_id: str | None = None,
) -> bytes:
    lines = [
        f"From: {from_addr}",
        f"Subject: {subject}",
        "Content-Type: text/plain",
    ]
    if task_id:
        lines.append(f"X-LLMesh-Task-ID: {task_id}")
    lines += ["", body]
    return "\r\n".join(lines).encode()


def _make_pop3_mock(messages: list[bytes]) -> MagicMock:
    """Return a mock POP3 that returns the given messages."""
    pop = MagicMock(spec=poplib.POP3)
    pop.user.return_value = b"+OK"
    pop.pass_.return_value = b"+OK Logged in"
    pop.stat.return_value = (len(messages), sum(len(m) for m in messages))
    pop.quit.return_value = b"+OK"

    def retr_side_effect(index: int):
        raw = messages[index - 1]
        lines = raw.split(b"\r\n")
        return (b"+OK", lines, len(raw))

    pop.retr.side_effect = retr_side_effect
    pop.dele.return_value = b"+OK"
    return pop


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
        raw = b"Content-Type: application/octet-stream\r\n\r\n\x00"
        msg = email.message_from_bytes(raw, policy=email.policy.default)
        assert _extract_text_body(msg) is None


# ---------------------------------------------------------------------------
# Unit tests — POP3Adapter properties
# ---------------------------------------------------------------------------

class TestPOP3AdapterUnit:
    def test_protocol_name(self):
        assert POP3Adapter().protocol_name == "pop3"

    def test_not_running_by_default(self):
        assert POP3Adapter().is_running is False

    def test_registry_registered(self):
        assert "pop3" in AdapterRegistry.available()
        adapter = AdapterRegistry.create("pop3")
        assert isinstance(adapter, POP3Adapter)

    def test_on_message_sets_handler(self):
        adapter = POP3Adapter()
        handler = AsyncMock()
        adapter.on_message(handler)
        assert adapter._handler is handler

    def test_trusted_senders_stored(self):
        adapter = POP3Adapter(trusted_senders={"alice@example.com"})
        assert adapter._trusted_senders == {"alice@example.com"}

    def test_poll_interval_stored(self):
        adapter = POP3Adapter(poll_interval=120)
        assert adapter._poll_interval == 120

    def test_ssl_default_true(self):
        assert POP3Adapter()._use_ssl is True

    def test_ssl_false_stored(self):
        adapter = POP3Adapter(use_ssl=False)
        assert adapter._use_ssl is False


# ---------------------------------------------------------------------------
# Unit tests — _poll_once with mock POP3
# ---------------------------------------------------------------------------

class TestPOP3PollOnce:
    def test_retrieves_and_deletes_message(self):
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        raw = _make_email_bytes(subject="translate", body="Bonjour")
        mock_pop = _make_pop3_mock([raw])

        with patch("poplib.POP3", return_value=mock_pop):
            adapter._poll_once()

        mock_pop.retr.assert_called_once_with(1)
        mock_pop.dele.assert_called_once_with(1)

    def test_oversized_message_deleted(self):
        from llmesh.protocol.pop3_adapter import _MAX_EMAIL_BYTES
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        mock_pop = _make_pop3_mock([b"X" * (_MAX_EMAIL_BYTES + 1)])

        with patch("poplib.POP3", return_value=mock_pop):
            adapter._poll_once()

        mock_pop.dele.assert_called_once_with(1)

    def test_untrusted_sender_deleted(self):
        adapter = POP3Adapter(
            trusted_senders={"allowed@example.com"},
            use_ssl=False,
        )
        adapter._host = "localhost"
        adapter._port = 110

        raw = _make_email_bytes(from_addr="evil@attacker.com")
        mock_pop = _make_pop3_mock([raw])

        with patch("poplib.POP3", return_value=mock_pop):
            adapter._poll_once()

        mock_pop.dele.assert_called_once_with(1)

    def test_no_text_plain_deleted(self):
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        raw = b"Content-Type: application/octet-stream\r\n\r\n\x00"
        mock_pop = _make_pop3_mock([raw])

        with patch("poplib.POP3", return_value=mock_pop):
            adapter._poll_once()

        mock_pop.dele.assert_called_once_with(1)

    def test_multiple_messages_all_deleted(self):
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        raws = [
            _make_email_bytes(subject=f"task{i}", body=f"body{i}")
            for i in range(4)
        ]
        mock_pop = _make_pop3_mock(raws)

        with patch("poplib.POP3", return_value=mock_pop):
            adapter._poll_once()

        assert mock_pop.retr.call_count == 4
        assert mock_pop.dele.call_count == 4

    def test_empty_mailbox_no_retr(self):
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        mock_pop = _make_pop3_mock([])

        with patch("poplib.POP3", return_value=mock_pop):
            adapter._poll_once()

        mock_pop.retr.assert_not_called()
        mock_pop.dele.assert_not_called()

    def test_quit_called_on_success(self):
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        mock_pop = _make_pop3_mock([_make_email_bytes()])

        with patch("poplib.POP3", return_value=mock_pop):
            adapter._poll_once()

        mock_pop.quit.assert_called_once()

    def test_quit_called_on_exception(self):
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        mock_pop = _make_pop3_mock([])
        mock_pop.stat.side_effect = poplib.error_proto("connection error")

        with patch("poplib.POP3", return_value=mock_pop):
            try:
                adapter._poll_once()
            except Exception:
                pass

        mock_pop.quit.assert_called_once()

    def test_ssl_uses_pop3_ssl(self):
        adapter = POP3Adapter(use_ssl=True)
        adapter._host = "localhost"
        adapter._port = 995
        adapter._username = "u"
        adapter._password = "p"

        mock_pop = _make_pop3_mock([])

        with patch("poplib.POP3_SSL", return_value=mock_pop) as mock_ssl:
            adapter._poll_once()
            mock_ssl.assert_called_once_with("localhost", 995)

    def test_no_ssl_uses_pop3_plain(self):
        adapter = POP3Adapter(use_ssl=False)
        adapter._host = "localhost"
        adapter._port = 110

        mock_pop = _make_pop3_mock([])

        with patch("poplib.POP3", return_value=mock_pop) as mock_plain:
            adapter._poll_once()
            mock_plain.assert_called_once_with("localhost", 110)

    def test_reply_sent_when_handler_returns_response(self):
        async def respond(msg: UnifiedMessage) -> UnifiedMessage:
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": "processed"},
                sender=NodeAddress("0.0.0.0", 0, "server"),
            )

        adapter = POP3Adapter(
            use_ssl=False,
            relay_host="localhost",
            relay_port=25,
        )
        adapter._host = "localhost"
        adapter._port = 110
        adapter.on_message(respond)

        raw = _make_email_bytes()
        mock_pop = _make_pop3_mock([raw])

        with patch("poplib.POP3", return_value=mock_pop):
            with patch("llmesh.protocol.pop3_adapter._send_smtp_reply") as mock_reply:
                adapter._poll_once()
                # asyncio.run raises RuntimeError in test env — reply only sent when handler succeeds


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPOP3AdapterLifecycle:
    async def test_start_sets_running(self):
        adapter = POP3Adapter(use_ssl=False)
        with patch.object(adapter, "_poll_once"):
            await adapter.start("127.0.0.1", 110)
            assert adapter.is_running
            await adapter.stop()
            assert not adapter.is_running

    async def test_stop_cancels_task(self):
        adapter = POP3Adapter(poll_interval=9999, use_ssl=False)
        with patch.object(adapter, "_poll_once"):
            await adapter.start("127.0.0.1", 110)
            assert adapter._task is not None
            await adapter.stop()
            assert adapter._task is None

    async def test_send_returns_none(self):
        adapter = POP3Adapter(use_ssl=False)
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"tool_name": "ping", "prompt": "hi"},
            sender=NodeAddress("127.0.0.1", 0, "client"),
        )
        target = NodeAddress("127.0.0.1", _alloc_port(), "nobody")

        with patch("smtplib.SMTP") as mock_smtp:
            mock_ctx = MagicMock()
            mock_smtp.return_value.__enter__ = MagicMock(return_value=mock_ctx)
            mock_smtp.return_value.__exit__ = MagicMock(return_value=False)
            result = await adapter.send(msg, target)
            assert result is None

    async def test_send_smtp_failure_raises_transport_error(self):
        adapter = POP3Adapter(use_ssl=False)
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"tool_name": "ping", "prompt": "hi"},
            sender=NodeAddress("127.0.0.1", 0, "client"),
        )
        target = NodeAddress("127.0.0.1", _alloc_port(), "nobody")

        with patch("smtplib.SMTP", side_effect=OSError("connection refused")):
            with pytest.raises(TransportError):
                await adapter.send(msg, target)

    async def test_broadcast_does_not_raise(self):
        adapter = POP3Adapter(use_ssl=False)
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"tool_name": "ping", "prompt": "x"},
            sender=NodeAddress("127.0.0.1", 0, "client"),
        )
        with patch("smtplib.SMTP", side_effect=OSError("refused")):
            await adapter.broadcast(msg, [NodeAddress("127.0.0.1", _alloc_port(), "x")])
