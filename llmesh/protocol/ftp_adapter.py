"""FTPAdapter — file-based LLM tasks via FTP/FTPS (pyftpdlib 2.x).

File naming convention:
  Client uploads: <task_id>.prompt.txt
  Server writes:  <task_id>.result.txt  (available for download)

Each authenticated user gets an isolated directory under the adapter's
working directory.  Only .prompt.txt uploads and .result.txt downloads
are allowed.

FTPS (TLS) is enabled by default when pyOpenSSL is installed.  A
self-signed certificate is auto-generated when certfile/keyfile are
not provided.  Plain FTP requires explicit opt-in: ``allow_plain_ftp=True``.

Security:
  - FTPS (STARTTLS) by default; plain FTP is explicitly opt-in.
  - Only .prompt.txt uploads are processed; .result.txt is server-written.
  - Prompt size capped at _MAX_PROMPT_BYTES.
  - No shell=True, no eval/exec of remote data.

Dependencies: pyftpdlib>=2.0  (pip install llmesh[ftp])
              pyOpenSSL        (for FTPS; auto-installed with llmesh[ftp])
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import tempfile
import threading
import time
import uuid
from typing import TYPE_CHECKING

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    _PYFTPDLIB_AVAILABLE = True
except ImportError:
    _PYFTPDLIB_AVAILABLE = False
    FTPHandler = None   # type: ignore[assignment,misc]
    FTPServer = None    # type: ignore[assignment,misc]
    DummyAuthorizer = None  # type: ignore[assignment,misc]

try:
    from pyftpdlib.handlers.ftps.control import TLS_FTPHandler
    _FTPS_AVAILABLE = True
except (ImportError, ModuleNotFoundError):
    _FTPS_AVAILABLE = False
    TLS_FTPHandler = None  # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_MAX_PROMPT_BYTES = 1 * 1024 * 1024   # 1 MiB hard cap
_RESULT_TTL = 300                       # 5 minutes; older results are pruned
_DEFAULT_PASSIVE_PORTS = range(60000, 60100)
_FTP_PERMS = "elradfmwMT"               # full access inside home dir


# ---------------------------------------------------------------------------
# TLS certificate helpers
# ---------------------------------------------------------------------------

def _generate_self_signed_cert(dest_dir: str) -> tuple[str, str]:
    """Generate a self-signed RSA cert and return (certfile, keyfile) paths."""
    import datetime
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COMMON_NAME, "llmesh-ftp"),
    ])
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.datetime.utcnow())
        .not_valid_after(datetime.datetime.utcnow() + datetime.timedelta(days=365))
        .sign(key, hashes.SHA256())
    )
    keyfile = os.path.join(dest_dir, "llmesh-ftp.key")
    certfile = os.path.join(dest_dir, "llmesh-ftp.crt")
    with open(keyfile, "wb") as f:
        f.write(key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ))
    with open(certfile, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return certfile, keyfile


# ---------------------------------------------------------------------------
# Handler factory
# ---------------------------------------------------------------------------

def _make_handler_class(
    message_handler: MessageHandler | None,
    use_tls: bool,
    certfile: str,
    keyfile: str,
    passive_ports: range,
) -> type:
    """Return a customised FTPHandler subclass with the given configuration."""
    if use_tls and _FTPS_AVAILABLE:
        base: type = TLS_FTPHandler  # type: ignore[assignment]
    else:
        base = FTPHandler  # type: ignore[assignment]

    class _LLMeshFTPHandler(base):  # type: ignore[valid-type]
        # Wrap in list to prevent Python from treating the coroutine as a
        # bound method when accessed via an instance.
        _llmesh_handler_box: list = [message_handler]

        def on_file_received(self, file: str) -> None:
            filename = os.path.basename(file)
            if filename.endswith(".prompt.txt"):
                _handle_prompt(file, type(self)._llmesh_handler_box[0])

        def on_incomplete_file_received(self, file: str) -> None:
            try:
                os.remove(file)
            except OSError:
                pass

    _LLMeshFTPHandler.passive_ports = passive_ports  # type: ignore[attr-defined]

    if use_tls and _FTPS_AVAILABLE:
        _LLMeshFTPHandler.certfile = certfile  # type: ignore[attr-defined]
        _LLMeshFTPHandler.keyfile = keyfile    # type: ignore[attr-defined]

    return _LLMeshFTPHandler


def _handle_prompt(filepath: str, handler: MessageHandler | None) -> None:
    """Read prompt file, call handler, write result file."""
    try:
        with open(filepath, "rb") as f:
            raw = f.read(_MAX_PROMPT_BYTES + 1)
        if len(raw) > _MAX_PROMPT_BYTES:
            logger.warning("FTPAdapter: oversized prompt %s, ignoring", filepath)
            return

        prompt_text = raw.decode("utf-8", errors="replace")
        filename = os.path.basename(filepath)
        task_id = filename[: -len(".prompt.txt")]

        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": prompt_text, "task_id": task_id},
            sender=NodeAddress(host="ftp", port=0, node_id="ftp-client"),
            id=task_id,
        )

        if handler is None:
            return

        try:
            response: UnifiedMessage | None = asyncio.run(handler(msg))
        except RuntimeError:
            # Already in an event loop (test environment)
            response = None
        except Exception as exc:
            logger.exception("FTPAdapter: handler error for task %s: %s", task_id, exc)
            return

        if response is not None:
            result_text = str(response.payload.get("result", ""))
            result_path = os.path.join(
                os.path.dirname(filepath),
                task_id + ".result.txt",
            )
            with open(result_path, "w", encoding="utf-8") as f:
                f.write(result_text)

    except Exception as exc:
        logger.exception("FTPAdapter: error handling prompt %s: %s", filepath, exc)


# ---------------------------------------------------------------------------
# FTPAdapter (ProtocolAdapter)
# ---------------------------------------------------------------------------

class FTPAdapter(ProtocolAdapter):
    """UnifiedMessage via FTP/FTPS file-based prompt/result exchange.

    Args:
        username:        FTP login username (default "llmesh").
        password:        FTP login password (default "").
        allow_plain_ftp: Enable plain (unencrypted) FTP. Default False.
        certfile:        Path to TLS certificate for FTPS.
                         Auto-generated self-signed if None.
        keyfile:         Path to TLS private key for FTPS.
                         Auto-generated self-signed if None.
        passive_ports:   Port range for passive data connections.
        node_id:         Node identifier used in outgoing messages.
    """

    def __init__(
        self,
        username: str = "llmesh",
        password: str = "",
        allow_plain_ftp: bool = False,
        certfile: str | None = None,
        keyfile: str | None = None,
        passive_ports: range = _DEFAULT_PASSIVE_PORTS,
        node_id: str = "ftp-server",
        **_kwargs: object,
    ) -> None:
        if not _PYFTPDLIB_AVAILABLE:
            raise ImportError(
                "pyftpdlib is required for FTPAdapter: pip install llmesh[ftp]"
            )
        self._username = username
        self._password = password
        self._allow_plain_ftp = allow_plain_ftp
        self._certfile = certfile
        self._keyfile = keyfile
        self._passive_ports = passive_ports
        self._node_id = node_id
        self._handler: MessageHandler | None = None
        self._server: "FTPServer | None" = None
        self._server_thread: threading.Thread | None = None
        self._tmpdir: str | None = None
        self._running = False

    # --- ProtocolAdapter interface ---

    @property
    def protocol_name(self) -> str:
        return "ftp"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str, port: int) -> None:
        self._tmpdir = tempfile.mkdtemp(prefix="llmesh-ftp-")
        user_dir = os.path.join(self._tmpdir, self._username)
        os.makedirs(user_dir, exist_ok=True)

        use_tls = not self._allow_plain_ftp and _FTPS_AVAILABLE
        certfile = self._certfile or ""
        keyfile = self._keyfile or ""

        if use_tls and not (certfile and keyfile):
            certfile, keyfile = _generate_self_signed_cert(self._tmpdir)

        authorizer = DummyAuthorizer()  # type: ignore[misc]
        authorizer.add_user(
            self._username, self._password, user_dir, perm=_FTP_PERMS
        )

        handler_cls = _make_handler_class(
            self._handler, use_tls, certfile, keyfile, self._passive_ports
        )
        handler_cls.authorizer = authorizer  # type: ignore[attr-defined]

        self._server = FTPServer((host, port), handler_cls)  # type: ignore[misc]
        self._running = True

        self._server_thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"handle_exit": False},
            daemon=True,
        )
        self._server_thread.start()
        logger.info(
            "FTPAdapter: listening on %s:%d (tls=%s)", host, port, use_tls
        )

    async def stop(self) -> None:
        self._running = False
        if self._server is not None:
            self._server.close_all()
            self._server = None
        if self._server_thread is not None:
            self._server_thread.join(timeout=5)
            self._server_thread = None
        if self._tmpdir is not None:
            shutil.rmtree(self._tmpdir, ignore_errors=True)
            self._tmpdir = None
        logger.info("FTPAdapter: stopped")

    async def send(
        self,
        message: UnifiedMessage,
        target: "NodeAddress",
    ) -> UnifiedMessage | None:
        """Upload prompt and poll for result via FTP.

        Payload must contain ``{"prompt": "..."}``; ``task_id`` defaults to
        ``message.id``.  Returns the result as a UnifiedMessage, or None on
        fire-and-forget (no result available within timeout).
        """
        import ftplib  # noqa: PLC0415

        task_id = str(message.payload.get("task_id", message.id))
        prompt_file = f"{task_id}.prompt.txt"
        result_file = f"{task_id}.result.txt"
        prompt_text = message.payload.get("prompt", "")
        if isinstance(prompt_text, str):
            prompt_bytes = prompt_text.encode("utf-8")
        else:
            prompt_bytes = prompt_text

        username = target.node_id or self._username
        password = message.payload.get("password", self._password)

        try:
            import io as _io  # noqa: PLC0415

            with ftplib.FTP() as ftp:  # noqa: S321
                ftp.connect(target.host, target.port, timeout=10)
                ftp.login(username, password)
                ftp.storbinary(f"STOR {prompt_file}", _io.BytesIO(prompt_bytes))

                # Poll for result
                deadline = time.monotonic() + 30
                while time.monotonic() < deadline:
                    try:
                        buf = _io.BytesIO()
                        ftp.retrbinary(f"RETR {result_file}", buf.write)
                        try:
                            ftp.delete(result_file)
                        except (ftplib.Error, OSError):
                            pass
                        return UnifiedMessage(
                            type=MessageType.RESPONSE,
                            payload={
                                "result": buf.getvalue().decode("utf-8", errors="replace"),
                                "task_id": task_id,
                            },
                            sender=target,
                            correlation_id=message.id,
                        )
                    except ftplib.error_perm:
                        time.sleep(0.5)

        except (ftplib.Error, OSError, EOFError, TimeoutError) as exc:
            raise TransportError(str(exc), protocol="ftp", target=str(target)) from exc

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

    # --- Helpers for testing ---

    @property
    def working_dir(self) -> str | None:
        """Return the adapter's working directory (for tests)."""
        if self._tmpdir is None:
            return None
        return os.path.join(self._tmpdir, self._username)
