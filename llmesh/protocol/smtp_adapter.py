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
import socket
import time
import uuid
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

try:
    from aiosmtpd.controller import Controller
    from aiosmtpd.smtp import SMTP as _SMTP
    from aiosmtpd.smtp import Envelope, Session
    _AIOSMTPD_AVAILABLE = True
except ImportError:
    _AIOSMTPD_AVAILABLE = False
    Controller = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_MAX_EMAIL_BYTES = 1 * 1024 * 1024   # 1 MiB hard cap
# 自前の readiness 探り。aiosmtpd の引き金が 1 回・1 秒しか引かれないので
# ここは何度も引く。試験が差し替えられるよう module 定数に出してある。
_PROBE_ATTEMPTS = 12
_PROBE_TIMEOUT = 1.0                 # seconds per connect + recv
_PROBE_PAUSE = 0.25                  # seconds between attempts
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
        server: _SMTP,
        session: Session,
        envelope: Envelope,
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


def _extract_text_body(msg: _email_mod.message.Message) -> str | None:  # type: ignore[type-arg]
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
        self._controller: Controller | None = None
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
        # ★ready_timeout を 30 秒に伸ばす(aiosmtpd の既定は 1 秒)。ただし
        #   **これは macOS の失敗の原因ではなかった** —— 伸ばしても落ちた。
        #   aiosmtpd の文面は「システムが混んでいる、ready_timeout を増やせ」と
        #   言うが、実際に落ちているのは「起動はした、**応答しない**」段
        #   (`_factory_invoked` が立たない = 自分への試験接続が届かない)で、
        #   待ち時間の話ではない。メッセージを額面どおり受け取ると原因を見失う。
        self._controller = Controller(
            smtp_handler, hostname=host, port=port, ready_timeout=30.0
        )
        try:
            self._controller.start()
        except (TimeoutError, OSError) as exc:
            # ★aiosmtpd の第 2 段("started, but not responding")は、引き金を
            #   **1 回・1 秒だけ**引いて諦める造りになっている
            #   (controller.py: `_trigger_server()` の socket_timeout を捨て、
            #   その後 `_factory_invoked` を待つだけ)。だから ready_timeout を
            #   伸ばしても効かない —— 30 秒でも macOS CI は落ちた(2026-09-26 実測)。
            #   同じ理由で、この経路の `_thread_exception` は必ず None。
            #   サーバ自体は第 1 段を通って立っている可能性が高いので、**捨てる前に
            #   自分で何度も繋いで確かめる**。
            inner = getattr(self._controller, "_thread_exception", None)
            banner, probe_note = self._probe_smtp(host, port)
            if banner and inner is None:
                logger.warning(
                    "SMTPAdapter: aiosmtpd's one-shot readiness trigger timed out on "
                    "%s:%d, but the server answered our own retried probe (%s). "
                    "Treating it as started; the library's message about a busy system "
                    "does not apply (its trigger is a single 1 s connect).",
                    host, port, banner,
                )
            else:
                self._shutdown_controller_quietly()
                from_thread = (
                    "" if inner is None
                    else f" (server thread raised {type(inner).__name__}: {inner})"
                )
                raise OSError(
                    f"SMTPAdapter: could not bring up the SMTP server on {host}:{port}"
                    f" — {type(exc).__name__}: {exc}{from_thread}. Our own retried "
                    f"probe saw: {probe_note}."
                ) from exc
        self._running = True
        logger.info("SMTPAdapter: listening on %s:%d", host, port)

    # --- readiness ---

    @staticmethod
    def _probe_smtp(
        host: str,
        port: int,
        attempts: int | None = None,
        per_try: float | None = None,
    ) -> tuple[str, str]:
        """自分で繋いで SMTP の挨拶を読む。`(挨拶, 探りの所見)` を返す。

        aiosmtpd の引き金が 1 回・1 秒しか引かれないので、ここは**何度も**引く。
        所見は失敗時の文面に入れる —— 「接続拒否」と「繋がるが無言」は原因が
        別物(前者はサーバが立っていない、後者は別の何かが港を持っている)。
        """
        last = "no attempt was made"
        attempts = _PROBE_ATTEMPTS if attempts is None else attempts
        per_try = _PROBE_TIMEOUT if per_try is None else per_try
        for _ in range(attempts):
            # ★接続の失敗と「繋がったが無言」を**別の枝**で捕まえる。1 つの
            #   try にまとめると、無言の港は recv の timeout として接続失敗と
            #   同じ所見になり、見分けがつかなくなる(2026-09-27 に踏んだ)。
            try:
                conn = socket.create_connection((host, port), per_try)
            except OSError as err:
                last = f"could not connect ({type(err).__name__}: {err})"
                time.sleep(_PROBE_PAUSE)
                continue
            try:
                conn.settimeout(per_try)
                greeting = conn.recv(1024).decode("utf-8", "replace").strip()
            except OSError as err:
                last = f"connected but nothing arrived ({type(err).__name__})"
                greeting = ""
            finally:
                conn.close()
            if greeting.startswith("220"):
                return greeting[:120], f"SMTP greeting {greeting[:60]!r}"
            if greeting:
                last = f"connected but the greeting was not 220: {greeting[:60]!r}"
            time.sleep(_PROBE_PAUSE)
        return "", last

    def _shutdown_controller_quietly(self) -> None:
        """立ち上げに失敗した controller を片付ける。

        ★`self._controller = None` だけで済ませてはいけない —— サーバスレッドと
        listen ソケットが残る(同じ港を次に使うテストが不可解に落ちる)。
        """
        controller, self._controller = self._controller, None
        if controller is None:
            return
        try:
            controller.stop(no_assert=True)
        except Exception as err:      # noqa: BLE001 - 片付けで新しい失敗を被せない
            logger.debug("SMTPAdapter: controller.stop() during cleanup: %s", err)

    async def stop(self) -> None:
        if self._controller is not None:
            self._controller.stop()
            self._controller = None
        self._running = False
        logger.info("SMTPAdapter: stopped")

    async def send(
        self,
        message: UnifiedMessage,
        target: NodeAddress,
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
        targets: list[NodeAddress] | None = None,
    ) -> None:
        if not targets:
            return
        for target in targets:
            try:
                await self.send(message, target)
            except TransportError:
                pass
