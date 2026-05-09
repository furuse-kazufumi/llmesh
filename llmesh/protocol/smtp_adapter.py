"""SMTPAdapter — receive UnifiedMessage task requests via email.

Server-side:
  Listens as an SMTP server (aiosmtpd).  Incoming email is translated:
    Subject   → payload["tool_name"]
    Body      → payload["prompt"]
    From addr → payload["from_address"]  (and metadata)
  The handler response is sent back to the sender via SMTP relay.

Security:
  - Only text/plain parts are accepted; binary attachments are rejected.
  - Message size capped at _MAX_EMAIL_BYTES.
  - Sender validated against trusted_senders allowlist when provided.
  - No shell=True, no eval/exec of remote data.

Dependencies: aiosmtpd>=1.4  (pip install llmesh[email])
"""
from __future__ import annotations

import email as _email_mod
import email.policy
import logging
import smtplib
import uuid
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

try:
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import SMTP as _SMTP, Envelope, Session
    _AIOSMTPD_AVAILABLE = True
except ImportError:
    _AIOSMTPD_AVAILABLE = False
    Controller = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_MAX_EMAIL_BYTES = 1 * 1024 * 1024   # 1 MiB hard cap
_LLMESH_NODE = NodeAddress("0.0.0.0", 0, "smtp-server")


# ---------------------------------------------------------------------------
# aiosmtpd handler
# ---------------------------------------------------------------------------

class _LLMeshSMTPHandler:
    """aiosmtpd message handler — converts email → UnifiedMessage."""

    def __init__(
        self,
        message_handler: MessageHandler | None,
        trusted_senders: set[str] | None,
        relay_host: str,
        relay_port: int,
        node_address: NodeAddress,
    ) -> None:
        self._message_handler = message_handler
        self._trusted_senders = trusted_senders
        self._relay_host = relay_host
        self._relay_port = relay_port
        self._node_address = node_address

    async def handle_DATA(
        self,
        server: "_SMTP",
        session: "Session",
        envelope: "Envelope",
    ) -> str:
        raw: bytes = envelope.content  # type: ignore[assignment]
        if len(raw) > _MAX_EMAIL_BYTES:
            logger.warning("SMTPAdapter: oversized email from %s", envelope.mail_from)
            return "552 Message too large"

        from_addr: str = envelope.mail_from or ""

        if self._trusted_senders is not None:
            if from_addr not in self._trusted_senders:
                logger.warning("SMTPAdapter: rejected untrusted sender %r", from_addr)
                return "550 Sender not authorized"

        # Parse email
        msg_obj = _email_mod.message_from_bytes(raw, policy=email.policy.default)
        subject: str = msg_obj.get("Subject", "") or ""
        body = _extract_text_body(msg_obj)
        if body is None:
            logger.warning("SMTPAdapter: no text/plain body from %s", from_addr)
            return "550 Only text/plain accepted"

        tool_name = subject.strip() or "default"
        task_id = str(uuid.uuid4())

        unified = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={
                "tool_name": tool_name,
                "prompt": body.strip(),
                "from_address": from_addr,
                "task_id": task_id,
            },
            sender=NodeAddress(session.peer[0] if session.peer else "unknown", 0, from_addr),
            id=task_id,
        )

        response: UnifiedMessage | None = None
        if self._message_handler is not None:
            try:
                response = await self._message_handler(unified)
            except Exception as exc:
                logger.exception("SMTPAdapter: handler raised %s", exc)
                return "451 Internal processing error"

        if response is not None:
            result_text = response.payload.get("result", str(response.payload))
            _send_reply(
                relay_host=self._relay_host,
                relay_port=self._relay_port,
                from_addr=self._node_address.node_id or "llmesh@localhost",
                to_addr=from_addr,
                subject=f"Re: {subject}",
                body=result_text,
                task_id=task_id,
            )

        return "250 OK"


def _extract_text_body(msg: "_email_mod.message.Message") -> str | None:  # type: ignore[type-arg]
    """Return first text/plain part, or None if no plaintext found."""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        if msg.get_content_type() == "text/plain":
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return None


def _send_reply(
    relay_host: str,
    relay_port: int,
    from_addr: str,
    to_addr: str,
    subject: str,
    body: str,
    task_id: str,
) -> None:
    try:
        with smtplib.SMTP(relay_host, relay_port, timeout=10) as smtp:
            message = (
                f"From: {from_addr}\r\n"
                f"To: {to_addr}\r\n"
                f"Subject: {subject}\r\n"
                f"X-LLMesh-Task-ID: {task_id}\r\n"
                f"\r\n"
                f"{body}"
            )
            smtp.sendmail(from_addr, [to_addr], message)
    except Exception as exc:
        logger.warning("SMTPAdapter: reply failed to %s: %s", to_addr, exc)


# ---------------------------------------------------------------------------
# SMTPAdapter (ProtocolAdapter)
# ---------------------------------------------------------------------------

class SMTPAdapter(ProtocolAdapter):
    """UnifiedMessage intake via SMTP.

    Args:
        trusted_senders: Set of allowed From addresses. None = accept all.
        relay_host:      SMTP relay for sending replies (default localhost).
        relay_port:      Relay port (default 25).
        node_id:         Node identifier used as reply From address.
    """

    def __init__(
        self,
        trusted_senders: set[str] | None = None,
        relay_host: str = "localhost",
        relay_port: int = 25,
        node_id: str = "llmesh@localhost",
        **_kwargs: object,
    ) -> None:
        if not _AIOSMTPD_AVAILABLE:
            raise ImportError(
                "aiosmtpd is required for SMTPAdapter: pip install llmesh[email]"
            )
        self._trusted_senders = trusted_senders
        self._relay_host = relay_host
        self._relay_port = relay_port
        self._node_id = node_id
        self._handler: MessageHandler | None = None
        self._controller: "Controller | None" = None
        self._running = False
        self._host = "0.0.0.0"
        self._port = 8025

    # --- ProtocolAdapter interface ---

    @property
    def protocol_name(self) -> str:
        return "smtp"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        node_addr = NodeAddress(host, port, self._node_id)
        smtp_handler = _LLMeshSMTPHandler(
            message_handler=self._handler,
            trusted_senders=self._trusted_senders,
            relay_host=self._relay_host,
            relay_port=self._relay_port,
            node_address=node_addr,
        )
        self._controller = Controller(smtp_handler, hostname=host, port=port)
        self._controller.start()
        self._running = True
        logger.info("SMTPAdapter: listening on %s:%d", host, port)

    async def stop(self) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._controller = None
        self._running = False
        logger.info("SMTPAdapter: stopped")

    async def send(
        self,
        message: UnifiedMessage,
        target: "NodeAddress",
    ) -> UnifiedMessage | None:
        """Send a UnifiedMessage as an email to target (SMTP relay).

        The payload must contain 'prompt' (body) and optionally 'tool_name' (subject).
        Returns None (fire-and-forget).
        """
        to_addr = target.node_id or f"llmesh@{target.host}"
        subject = message.payload.get("tool_name", "llmesh-task")
        body = message.payload.get("prompt", "")
        task_id = message.id

        try:
            with smtplib.SMTP(target.host, target.port, timeout=10) as smtp:
                raw = (
                    f"From: {self._node_id}\r\n"
                    f"To: {to_addr}\r\n"
                    f"Subject: {subject}\r\n"
                    f"X-LLMesh-Task-ID: {task_id}\r\n"
                    f"\r\n"
                    f"{body}"
                )
                smtp.sendmail(self._node_id, [to_addr], raw)
        except (smtplib.SMTPException, OSError, TimeoutError) as exc:
            raise TransportError(str(exc), protocol="smtp", target=str(target)) from exc

        return None

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
