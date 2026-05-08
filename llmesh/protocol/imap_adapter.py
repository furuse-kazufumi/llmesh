"""IMAPAdapter — poll a mailbox for UnifiedMessage task requests.

Polles an IMAP mailbox at a configurable interval.  Each unread email is
translated to a UnifiedMessage, dispatched to the registered handler, and
marked as \\Seen.  The task_id is stored in the X-LLMesh-Task-ID header so
replies can be correlated.

Security:
  - TLS enforced by default (IMAPS); plain IMAP requires explicit opt-in.
  - Only text/plain bodies are processed; binary attachments are ignored.
  - No shell=True, no eval/exec of remote data.

Dependencies: imaplib (stdlib — no extra install needed)
"""
from __future__ import annotations

import asyncio
import email as _email_mod
import email.policy
import imaplib
import logging
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
# IMAPAdapter (ProtocolAdapter)
# ---------------------------------------------------------------------------

class IMAPAdapter(ProtocolAdapter):
    """Poll an IMAP mailbox and dispatch emails as UnifiedMessages.

    Args:
        username:       IMAP login username / email address.
        password:       IMAP login password.
        mailbox:        Mailbox to poll (default "INBOX").
        poll_interval:  Seconds between polls (default 30).
        use_ssl:        Use IMAPS (port 993) when True (default).
                        Set False only for local test servers.
        relay_host:     SMTP relay for sending replies.
        relay_port:     SMTP relay port (default 25).
        node_id:        Node identifier used as reply From address.
        trusted_senders: Set of allowed From addresses. None = accept all.
    """

    def __init__(
        self,
        username: str = "",
        password: str = "",
        mailbox: str = "INBOX",
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
        self._mailbox = mailbox
        self._poll_interval = poll_interval
        self._use_ssl = use_ssl
        self._relay_host = relay_host
        self._relay_port = relay_port
        self._node_id = node_id
        self._trusted_senders = trusted_senders
        self._handler: MessageHandler | None = None
        self._running = False
        self._task: asyncio.Task | None = None  # type: ignore[type-arg]

    # --- ProtocolAdapter interface ---

    @property
    def protocol_name(self) -> str:
        return "imap"

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
        logger.info("IMAPAdapter: polling %s:%d every %ds", host, port, self._poll_interval)

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("IMAPAdapter: stopped")

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
            raise TransportError(str(exc), protocol="imap", target=str(target)) from exc

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
                logger.warning("IMAPAdapter: poll error: %s", exc)
            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

    def _poll_once(self) -> None:
        """Connect, fetch unseen messages, dispatch, mark seen."""
        if self._use_ssl:
            imap = imaplib.IMAP4_SSL(self._host, self._port)
        else:
            imap = imaplib.IMAP4(self._host, self._port)

        try:
            imap.login(self._username, self._password)
            imap.select(self._mailbox)

            # Search for unseen messages
            status, data = imap.search(None, "UNSEEN")
            if status != "OK":
                return

            msg_ids: list[bytes] = data[0].split() if data[0] else []

            for msg_id in msg_ids:
                try:
                    self._process_one(imap, msg_id)
                except Exception as exc:
                    logger.warning("IMAPAdapter: error processing message %s: %s", msg_id, exc)
        finally:
            try:
                imap.close()
                imap.logout()
            except Exception:
                pass

    def _process_one(self, imap: imaplib.IMAP4, msg_id: bytes) -> None:
        status, msg_data = imap.fetch(msg_id, "(RFC822)")
        if status != "OK" or not msg_data or not msg_data[0]:
            return

        raw_bytes = msg_data[0][1]  # type: ignore[index]
        if len(raw_bytes) > _MAX_EMAIL_BYTES:
            logger.warning("IMAPAdapter: oversized message %s, skipping", msg_id)
            imap.store(msg_id, "+FLAGS", "\\Seen")
            return

        msg_obj = _email_mod.message_from_bytes(raw_bytes, policy=email.policy.default)
        from_addr: str = msg_obj.get("From", "") or ""
        subject: str = msg_obj.get("Subject", "") or ""

        if self._trusted_senders is not None and from_addr not in self._trusted_senders:
            logger.warning("IMAPAdapter: rejecting untrusted sender %r", from_addr)
            imap.store(msg_id, "+FLAGS", "\\Seen")
            return

        body = _extract_text_body(msg_obj)
        if body is None:
            logger.warning("IMAPAdapter: no text/plain in message %s", msg_id)
            imap.store(msg_id, "+FLAGS", "\\Seen")
            return

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
                logger.exception("IMAPAdapter: handler raised %s", exc)
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

        # Mark as seen
        imap.store(msg_id, "+FLAGS", "\\Seen")


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
        logger.warning("IMAPAdapter: reply failed to %s: %s", to_addr, exc)
