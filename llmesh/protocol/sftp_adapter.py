"""SFTPAdapter — file-based LLM tasks via SFTP (paramiko 4.x).

File naming convention:
  Client uploads: <task_id>.prompt.txt
  Server writes:  <task_id>.result.txt  (available for download)

All prompt/result files live in a virtual in-memory filesystem — nothing
is written to disk.  The privacy pipeline is applied to the prompt through
the normal MessageHandler mechanism.

Client workflow:
  1. sftp.put(local_prompt, "<task_id>.prompt.txt")
  2. Poll sftp.stat("<task_id>.result.txt") until it exists
  3. sftp.get("<task_id>.result.txt", local_result)
  4. sftp.remove("<task_id>.result.txt")   # optional cleanup

Security:
  - Public-key only auth (Ed25519).
  - Prompt size capped at _MAX_PROMPT_BYTES.
  - Results pruned after _RESULT_TTL seconds (default 5 min).
  - No shell=True, no eval/exec.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket
import stat
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any

import paramiko

from ._key_utils import generate_ed25519_key, key_from_hex
from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

_MAX_PROMPT_BYTES = 1 * 1024 * 1024   # 1 MiB
_RESULT_TTL = 300                      # 5 minutes
_POLL_INTERVAL = 0.5                   # seconds between result polls
_POLL_TIMEOUT = 120                    # seconds before giving up


# ---------------------------------------------------------------------------
# Virtual in-memory filesystem
# ---------------------------------------------------------------------------

class _InMemoryFile:
    """Writable/readable in-memory file."""

    def __init__(self, content: bytes = b"") -> None:
        self._data = bytearray(content)
        self.mtime = time.time()

    def write_at(self, offset: int, data: bytes) -> int:
        end = offset + len(data)
        if end > len(self._data):
            self._data.extend(b"\x00" * (end - len(self._data)))
        self._data[offset:end] = data
        self.mtime = time.time()
        return len(data)

    def read(self, offset: int, length: int) -> bytes:
        return bytes(self._data[offset : offset + length])

    def size(self) -> int:
        return len(self._data)

    def to_bytes(self) -> bytes:
        return bytes(self._data)

    def make_attr(self, name: str = "") -> paramiko.SFTPAttributes:
        attr = paramiko.SFTPAttributes()
        attr.st_size = len(self._data)
        attr.st_mode = stat.S_IFREG | 0o644
        attr.st_mtime = int(self.mtime)
        attr.st_atime = int(self.mtime)
        attr.filename = name
        return attr


class _VirtualFS:
    """Thread-safe in-memory filesystem for SFTP sessions."""

    def __init__(self) -> None:
        self._files: dict[str, _InMemoryFile] = {}
        self._lock = threading.Lock()

    def put(self, name: str, content: bytes) -> None:
        with self._lock:
            self._files[name] = _InMemoryFile(content)

    def get(self, name: str) -> _InMemoryFile | None:
        with self._lock:
            return self._files.get(name)

    def write_at(self, name: str, offset: int, data: bytes) -> int:
        with self._lock:
            if name not in self._files:
                self._files[name] = _InMemoryFile()
            return self._files[name].write_at(offset, data)

    def exists(self, name: str) -> bool:
        with self._lock:
            return name in self._files

    def stat(self, name: str) -> paramiko.SFTPAttributes | None:
        with self._lock:
            f = self._files.get(name)
            return f.make_attr(name) if f is not None else None

    def list_attrs(self) -> list[paramiko.SFTPAttributes]:
        with self._lock:
            return [f.make_attr(n) for n, f in self._files.items()]

    def remove(self, name: str) -> None:
        with self._lock:
            self._files.pop(name, None)

    def prune_old_results(self, ttl: float = _RESULT_TTL) -> None:
        cutoff = time.time() - ttl
        with self._lock:
            expired = [
                k for k, v in self._files.items()
                if v.mtime < cutoff and k.endswith(".result.txt")
            ]
            for k in expired:
                del self._files[k]


# ---------------------------------------------------------------------------
# paramiko SFTP interfaces
# ---------------------------------------------------------------------------

class _SFTPHandleImpl(paramiko.SFTPHandle):
    """Per-file handle; triggers prompt processing on close."""

    def __init__(
        self,
        name: str,
        vfs: _VirtualFS,
        handler: MessageHandler | None,
        sender_node_id: str,
        flags: int = 0,
    ) -> None:
        super().__init__(flags)
        self._name = name
        self._vfs = vfs
        self._handler = handler
        self._sender_node_id = sender_node_id

    def write(self, offset: int, data: bytes) -> int:
        if self._vfs.write_at(self._name, offset, data) < 0:
            return paramiko.SFTP_FAILURE
        return paramiko.SFTP_OK

    def read(self, offset: int, length: int) -> bytes | int:
        f = self._vfs.get(self._name)
        if f is None:
            return paramiko.SFTP_NO_SUCH_FILE
        return f.read(offset, length)

    def stat(self) -> paramiko.SFTPAttributes | int:
        attr = self._vfs.stat(self._name)
        return attr if attr is not None else paramiko.SFTP_NO_SUCH_FILE

    def close(self) -> None:
        if self._name.endswith(".prompt.txt") and self._handler is not None:
            self._process_prompt()

    def _process_prompt(self) -> None:
        task_id = self._name[: -len(".prompt.txt")]
        f = self._vfs.get(self._name)
        if f is None:
            return
        prompt_text = f.to_bytes().decode("utf-8", errors="replace")
        sender = NodeAddress(host="sftp", port=0, node_id=self._sender_node_id)
        target = NodeAddress(host="local", port=0, node_id="")
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": prompt_text, "task_id": task_id},
            sender=sender,
            target=target,
            id=task_id,
        )
        try:
            response: UnifiedMessage | None = asyncio.run(self._handler(msg))  # type: ignore[arg-type]
        except Exception:
            logger.exception("SFTPAdapter: handler error for task %s", task_id)
            return

        if response is not None:
            result_text = str(response.payload.get("result", ""))
            result_name = task_id + ".result.txt"
            self._vfs.put(result_name, result_text.encode("utf-8"))


class _SFTPServerImpl(paramiko.SFTPServerInterface):
    """Virtual-filesystem SFTP server interface."""

    def __init__(
        self,
        server: paramiko.ServerInterface,
        vfs: _VirtualFS,
        msg_handler: MessageHandler | None = None,
        **_kwargs: Any,
    ) -> None:
        super().__init__(server)
        self._vfs = vfs
        self._handler = msg_handler
        # node_id injected by _LLMeshSFTPServer
        self._node_id: str = getattr(server, "authenticated_node_id", "") or ""

    def _name(self, path: str) -> str:
        return os.path.basename(path.lstrip("/\\"))

    def canonicalize(self, path: str) -> str:
        return "/" + self._name(path)

    def list_folder(
        self, path: str
    ) -> list[paramiko.SFTPAttributes] | int:
        return self._vfs.list_attrs()

    def stat(self, path: str) -> paramiko.SFTPAttributes | int:
        attr = self._vfs.stat(self._name(path))
        return attr if attr is not None else paramiko.SFTP_NO_SUCH_FILE

    lstat = stat  # no symlinks in virtual FS

    def open(
        self,
        path: str,
        flags: int,
        attr: paramiko.SFTPAttributes,
    ) -> paramiko.SFTPHandle | int:
        name = self._name(path)
        if not name.endswith((".prompt.txt", ".result.txt")):
            return paramiko.SFTP_PERMISSION_DENIED
        if (
            name.endswith(".prompt.txt")
            and self._vfs.get(name) is not None
            and len(self._vfs.get(name).to_bytes()) + 1 > _MAX_PROMPT_BYTES  # type: ignore[union-attr]
        ):
            return paramiko.SFTP_FAILURE
        return _SFTPHandleImpl(name, self._vfs, self._handler, self._node_id, flags)

    def remove(self, path: str) -> int:
        self._vfs.remove(self._name(path))
        return paramiko.SFTP_OK

    def mkdir(self, path: str, attr: paramiko.SFTPAttributes) -> int:
        return paramiko.SFTP_OP_UNSUPPORTED

    def rmdir(self, path: str) -> int:
        return paramiko.SFTP_OP_UNSUPPORTED

    def rename(self, oldpath: str, newpath: str) -> int:
        return paramiko.SFTP_OP_UNSUPPORTED


# ---------------------------------------------------------------------------
# SSH server interface for SFTP connections
# ---------------------------------------------------------------------------

class _LLMeshSFTPServer(paramiko.ServerInterface):
    """SSH layer for SFTP connections: pubkey auth + SFTP subsystem only."""

    def __init__(self, trusted_keys: dict[str, str] | None) -> None:
        self._trusted_keys = trusted_keys
        self.authenticated_node_id: str = ""

    def get_allowed_auths(self, username: str) -> str:
        return "publickey"

    def check_auth_publickey(self, username: str, key: paramiko.PKey) -> int:
        if self._trusted_keys is None:
            self.authenticated_node_id = username
            return paramiko.AUTH_SUCCESSFUL
        for node_id, hex_pub in self._trusted_keys.items():
            known = key_from_hex(hex_pub)
            if known is None:
                continue
            if key.get_fingerprint() == known.get_fingerprint():
                self.authenticated_node_id = node_id
                return paramiko.AUTH_SUCCESSFUL
        return paramiko.AUTH_FAILED

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    # check_channel_subsystem_request is intentionally not overridden.
    # The default ServerInterface implementation automatically looks up and
    # starts the handler registered via Transport.set_subsystem_handler().


# ---------------------------------------------------------------------------
# SFTPAdapter (ProtocolAdapter)
# ---------------------------------------------------------------------------

class SFTPAdapter(ProtocolAdapter):
    """UnifiedMessage via SFTP file-based prompt/result exchange.

    Args:
        host_key:     Server Ed25519 key. Auto-generated if None.
        trusted_keys: Mapping {node_id: hex_pubkey} for pubkey auth.
                      None = accept any key (dev/test mode).
    """

    def __init__(
        self,
        host_key: paramiko.PKey | None = None,
        trusted_keys: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> None:
        self._host_key: paramiko.PKey = host_key or generate_ed25519_key()
        self._trusted_keys = trusted_keys
        self._handler: MessageHandler | None = None
        self._vfs = _VirtualFS()
        self._server_sock: socket.socket | None = None
        self._running = False

    # --- ProtocolAdapter interface ---

    @property
    def protocol_name(self) -> str:
        return "sftp"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    @property
    def vfs(self) -> _VirtualFS:
        """Exposed for testing: direct access to the virtual filesystem."""
        return self._vfs

    async def start(self, host: str, port: int) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((host, port))
        sock.listen(10)
        self._server_sock = sock
        self._running = True
        asyncio.get_event_loop().run_in_executor(None, self._accept_loop)

    def _accept_loop(self) -> None:
        assert self._server_sock is not None
        self._server_sock.setblocking(True)
        self._server_sock.settimeout(1.0)
        while self._running:
            try:
                conn, addr = self._server_sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self._handle_connection, args=(conn, addr), daemon=True
            )
            t.start()

    def _handle_connection(
        self, conn: socket.socket, addr: tuple[str, int]
    ) -> None:
        transport: paramiko.Transport | None = None
        try:
            transport = paramiko.Transport(conn)
            transport.add_server_key(self._host_key)
            srv = _LLMeshSFTPServer(self._trusted_keys)
            transport.set_subsystem_handler(
                "sftp",
                paramiko.SFTPServer,
                _SFTPServerImpl,
                vfs=self._vfs,
                msg_handler=self._handler,
            )
            transport.start_server(server=srv)

            # The SFTP subsystem handler manages its own channel.
            # Just wait for the transport thread to finish (client disconnect).
            transport.join(timeout=60)
        except Exception:
            logger.exception("SFTPAdapter: connection error from %s", addr)
        finally:
            try:
                if transport is not None:
                    transport.close()
            except Exception:
                pass
            try:
                conn.close()
            except OSError:
                pass

    async def stop(self) -> None:
        self._running = False
        if self._server_sock is not None:
            try:
                self._server_sock.close()
            except OSError:
                pass
            self._server_sock = None

    async def send(
        self,
        message: UnifiedMessage,
        target: "NodeAddress",
        client_key: paramiko.PKey | None = None,
        poll_timeout: float = _POLL_TIMEOUT,
    ) -> UnifiedMessage | None:
        """Upload prompt and poll for result via SFTP.

        The message payload should contain ``{"prompt": "..."}``
        (``task_id`` is auto-generated from message.id if absent).

        Args:
            client_key:   Ed25519 key for authentication.
            poll_timeout: Seconds to wait for result before raising TransportError.
        """
        if client_key is None:
            client_key = generate_ed25519_key()

        task_id = str(message.payload.get("task_id", message.id))
        prompt_file = f"{task_id}.prompt.txt"
        result_file = f"{task_id}.result.txt"
        prompt_bytes = message.payload.get("prompt", "")
        if isinstance(prompt_bytes, str):
            prompt_bytes = prompt_bytes.encode("utf-8")

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # nosec B507 - peer trust established via Capability Manifest, not host keys.
        try:
            client.connect(
                target.host,
                port=target.port,
                username=target.node_id or "llmesh",
                pkey=client_key,
                timeout=30,
                look_for_keys=False,
                allow_agent=False,
            )
            sftp = client.open_sftp()
            try:
                # Upload prompt
                import io as _io  # noqa: PLC0415
                sftp.putfo(_io.BytesIO(prompt_bytes), prompt_file)

                # Poll for result
                deadline = time.monotonic() + poll_timeout
                while time.monotonic() < deadline:
                    try:
                        sftp.stat(result_file)
                        # File exists — download it
                        buf = _io.BytesIO()
                        sftp.getfo(result_file, buf)
                        result_bytes = buf.getvalue()
                        try:
                            sftp.remove(result_file)
                        except Exception:
                            pass
                        sftp.close()
                        return UnifiedMessage(
                            type=MessageType.RESPONSE,
                            payload={
                                "result": result_bytes.decode("utf-8", errors="replace"),
                                "task_id": task_id,
                            },
                            sender=target,
                            correlation_id=message.id,
                            id=str(uuid.uuid4()),
                        )
                    except FileNotFoundError:
                        time.sleep(_POLL_INTERVAL)

                sftp.close()
                raise TransportError(
                    f"sftp:result_timeout:{task_id}",
                    protocol="sftp",
                    target=str(target),
                )
            except TransportError:
                raise
            except Exception as exc:
                raise TransportError(
                    str(exc), protocol="sftp", target=str(target)
                ) from exc
        except paramiko.AuthenticationException as exc:
            raise TransportError(
                f"sftp:auth_failed:{exc}", protocol="sftp", target=str(target)
            ) from exc
        except (paramiko.SSHException, OSError) as exc:
            raise TransportError(
                str(exc), protocol="sftp", target=str(target)
            ) from exc
        finally:
            client.close()

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
