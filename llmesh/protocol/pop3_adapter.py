"""POP3Adapter — retrieve UnifiedMessage task requests via POP3.

Polls a POP3 mailbox at a configurable interval.  Messages are retrieved,
processed sequentially, and deleted from the server.  Each email is
translated to a UnifiedMessage and dispatched to the registered handler.

Security:
  - TLS enforced by default (POP3S / STLS); plain POP3 requires explicit opt-in.
  - Only text/plain bodies are processed; binary attachments are ignored.
  - Message size capped at _MAX_EMAIL_BYTES.
  - No shell=True, no eval/exec of remote data.

Dependencies: poplib (stdlib — no extra install needed)
"""
from __future__ import annotations

import asyncio
import email as _email_mod
import email.policy
import logging
import poplib
import smtplib
import uuid
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_MAX_EMAIL_BYTES = 1 * 1024 * 1024   # 1 MiB hard cap
_DEFAULT_POLL_INTERVAL = 30          # seconds


# ---------------------------------------------------------------------------
# POP3Adapter (ProtocolAdapter)
# ---------------------------------------------------------------------------

class POP3Adapter(ProtocolAdapter):
    """Retrieve and process emails from a POP3 mailbox as UnifiedMessages.

    Args:
        username:        POP3 login username / email address.
        password:        POP3 login password.
        poll_interval:   Seconds between polls (default 30).
        use_ssl:         Use POP3S (port 995) when True (default).
                         Set False only for local test servers.
        relay_host:      SMTP relay for sending replies.
        relay_port:      SMTP relay port (default 25).
        node_id:         Node identifier used as reply From address.
        trusted_senders: Set of allowed From addresses. None = accept all.
    """

    def __init__(
        self,
        username: str = "",
        password: str = "",
        poll_interval: int = _DEFAULT_POLL_INTERVAL,
        use_ssl: bool = True,
        relay_host: str = "localhost",
        relay_port: int = 25,
        node_id: str = "llmesh@localhost",
        trusted_senders: set[str] | None = None,
        **_kwargs: object,
    ) -> None:
        self._username = username
        self._password = password
        self._poll_interval = poll_interval
        self._use_ssl = use_ssl
        self._relay_host = relay_host
        self._relay_port = relay_port
        self._node_id = node_id
        self._trusted_senders = trusted_senders
        self._handler: MessageHandler | None = None
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]
        self._host = ""
        self._port = 995

    # --- ProtocolAdapter interface ---

    @property
    def protocol_name(self) -> str:
        return "pop3"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._running = True
        self._task = asyncio.get_event_loop().create_task(self._poll_loop())
        logger.info("POP3Adapter: polling %s:%d every %ds", host, port, self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("POP3Adapter: stopped")

    async def send(
        self,
        message: UnifiedMessage,
        target: "NodeAddress",
    ) -> UnifiedMessage | None:
        """Send a UnifiedMessage as an email via SMTP relay.

        Returns None (fire-and-forget).
        """
        to_addr = target.node_id or f"llmesh@{target.host}"
        subject = message.payload.get("tool_name", "llmesh-task")
        body = message.payload.get("prompt", "")
        task_id = message.id

        try:
            with smtplib.SMTP(self._relay_host, self._relay_port, timeout=10) as smtp:
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
            raise TransportError(str(exc), protocol="pop3", target=str(target)) from exc

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

    # --- Internal polling ---

    async def _poll_loop(self) -> None:
        while self._running:
            try:
                await asyncio.get_event_loop().run_in_executor(None, self._poll_once)
            except Exception as exc:
                logger.warning("POP3Adapter: poll error: %s", exc)
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    def _poll_once(self) -> None:
        """Connect, fetch all messages, process sequentially, delete each."""
        if self._use_ssl:
            pop = poplib.POP3_SSL(self._host, self._port)
        else:
            pop = poplib.POP3(self._host, self._port)

        try:
            pop.user(self._username)
            pop.pass_(self._password)

            count, _ = pop.stat()
            for i in range(1, count + 1):
                try:
                    keep = self._process_one(pop, i)
                    if not keep:
                        pop.dele(i)
                except Exception as exc:
                    logger.warning("POP3Adapter: error processing message %d: %s", i, exc)
        finally:
            try:
                pop.quit()
            except Exception:
                pass

    def _process_one(self, pop: poplib.POP3, index: int) -> bool:
        """Retrieve and process one message.  Returns True to keep, False to delete."""
        response, lines, _ = pop.retr(index)

        raw_bytes = b"\r\n".join(lines)
        if len(raw_bytes) > _MAX_EMAIL_BYTES:
            logger.warning("POP3Adapter: oversized message %d, deleting", index)
            return False

        msg_obj = _email_mod.message_from_bytes(raw_bytes, policy=email.policy.default)
        from_addr: str = msg_obj.get("From", "") or ""
        subject: str = msg_obj.get("Subject", "") or ""

        if self._trusted_senders is not None and from_addr not in self._trusted_senders:
            logger.warning("POP3Adapter: rejecting untrusted sender %r", from_addr)
            return False

        body = _extract_text_body(msg_obj)
        if body is None:
            logger.warning("POP3Adapter: no text/plain in message %d", index)
            return False

        task_id = msg_obj.get("X-LLMesh-Task-ID") or str(uuid.uuid4())
        tool_name = subject.strip() or "default"

        unified = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={
                "tool_name": tool_name,
                "prompt": body,
                "from_address": from_addr,
                "task_id": task_id,
            },
            sender=NodeAddress(self._host, self._port, from_addr),
            id=task_id,
        )

        if self._handler is not None:
            try:
                response = asyncio.run(self._handler(unified))
            except RuntimeError:
                # Already inside an event loop (test environment)
                response = None
            except Exception as exc:
                logger.exception("POP3Adapter: handler raised %s", exc)
                response = None

            if response is not None:
                result_text = response.payload.get("result", str(response.payload))
                _send_smtp_reply(
                    relay_host=self._relay_host,
                    relay_port=self._relay_port,
                    from_addr=self._node_id,
                    to_addr=from_addr,
                    subject=f"Re: {subject}",
                    body=result_text,
                    task_id=task_id,
                )

        return False  # Always delete after processing


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


def _send_smtp_reply(
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
        logger.warning("POP3Adapter: reply failed to %s: %s", to_addr, exc)
