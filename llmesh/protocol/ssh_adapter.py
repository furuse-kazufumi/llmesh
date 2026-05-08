"""SSHAdapter — UnifiedMessage over SSH using paramiko 4.x.

Server-side:
  Listens as an SSH server; each connection maps to one request-response
  exchange via an exec channel.  JSON-encoded UnifiedMessage bytes are
  written to stdin by the client and the response is sent on stdout.

Client-side:
  Opens an SSH connection, runs the sentinel command ``llmesh``, writes
  the serialised message to stdin, reads the response from stdout.

Authentication:
  Public-key only (Ed25519).  Pass trusted_keys={node_id: hex_pubkey}
  to restrict access; None disables key checking (dev/test only).

Security:
  - No shell=True, no eval/exec of remote data.
  - Message size capped at _MAX_MSG_BYTES.
  - Auth failures are logged and the connection is dropped.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import threading
from typing import TYPE_CHECKING

import paramiko

from ._key_utils import generate_ed25519_key, key_from_hex
from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import UnifiedMessage

if TYPE_CHECKING:
    from .message import NodeAddress

logger = logging.getLogger(__name__)

_MAX_MSG_BYTES = 4 * 1024 * 1024   # 4 MiB hard cap
_AUTH_TIMEOUT = 30                  # seconds for SSH handshake
_RECV_TIMEOUT = 30                  # seconds to read full request
_SENTINEL_CMD = "llmesh"


# ---------------------------------------------------------------------------
# Internal SSH server implementation
# ---------------------------------------------------------------------------

class _LLMeshServerInterface(paramiko.ServerInterface):
    """Per-connection ServerInterface: public-key auth + exec channel."""

    def __init__(self, trusted_keys: dict[str, str] | None) -> None:
        self._trusted_keys = trusted_keys
        self.authenticated_node_id: str | None = None
        self.exec_event = threading.Event()

    # --- Authentication ---

    def get_allowed_auths(self, username: str) -> str:
        return "publickey"

    def check_auth_publickey(
        self, username: str, key: paramiko.PKey
    ) -> int:
        if self._trusted_keys is None:
            # Dev mode: any Ed25519 key accepted
            self.authenticated_node_id = username
            return paramiko.AUTH_SUCCESSFUL

        for node_id, hex_pub in self._trusted_keys.items():
            known = key_from_hex(hex_pub)
            if known is None:
                continue
            if key.get_fingerprint() == known.get_fingerprint():
                self.authenticated_node_id = node_id
                return paramiko.AUTH_SUCCESSFUL

        logger.warning("SSHAdapter: rejected unknown key from %r", username)
        return paramiko.AUTH_FAILED

    # --- Channel negotiation ---

    def check_channel_request(self, kind: str, chanid: int) -> int:
        if kind == "session":
            return paramiko.OPEN_SUCCEEDED
        return paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_channel_exec_request(
        self, channel: paramiko.Channel, command: bytes
    ) -> bool:
        if command.strip() != _SENTINEL_CMD.encode():
            logger.warning("SSHAdapter: unexpected command %r", command)
            return False
        self.exec_event.set()
        return True


class _SSHClientSession:
    """Handles one accepted TCP connection as a single SSH request-response."""

    def __init__(
        self,
        conn: socket.socket,
        addr: tuple[str, int],
        host_key: paramiko.PKey,
        trusted_keys: dict[str, str] | None,
        handler: MessageHandler | None,
    ) -> None:
        self._conn = conn
        self._addr = addr
        self._host_key = host_key
        self._trusted_keys = trusted_keys
        self._handler = handler

    def run(self) -> None:
        transport: paramiko.Transport | None = None
        try:
            transport = paramiko.Transport(self._conn)
            transport.add_server_key(self._host_key)
            server = _LLMeshServerInterface(self._trusted_keys)
            transport.start_server(server=server)

            chan = transport.accept(timeout=_AUTH_TIMEOUT)
            if chan is None:
                logger.debug("SSHAdapter: no channel from %s", self._addr)
                return

            if not server.exec_event.wait(timeout=10):
                chan.close()
                return

            # Read the full request (client closes write side when done)
            buf = bytearray()
            chan.settimeout(_RECV_TIMEOUT)
            try:
                while True:
                    chunk = chan.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                    if len(buf) > _MAX_MSG_BYTES:
                        self._send_error(chan, "message_too_large")
                        return
            except TimeoutError:
                self._send_error(chan, "recv_timeout")
                return

            # Deserialise
            try:
                msg = UnifiedMessage.from_bytes(bytes(buf))
            except Exception as exc:
                self._send_error(chan, f"parse_error:{exc}")
                return

            # Dispatch
            response: UnifiedMessage | None = None
            if self._handler is not None:
                try:
                    response = asyncio.run(self._handler(msg))
                except Exception as exc:
                    logger.exception("SSHAdapter: handler raised %s", exc)
                    self._send_error(chan, f"handler_error:{exc}")
                    return

            if response is not None:
                chan.sendall(response.to_bytes())

            chan.send_exit_status(0)
            chan.close()

        except Exception:
            logger.exception("SSHAdapter: session error from %s", self._addr)
        finally:
            try:
                if transport is not None:
                    transport.close()
            except Exception:
                pass
            try:
                self._conn.close()
            except OSError:
                pass

    @staticmethod
    def _send_error(chan: paramiko.Channel, reason: str) -> None:
        import json  # noqa: PLC0415
        chan.sendall(json.dumps({"error": reason}).encode())
        chan.send_exit_status(1)
        chan.close()


# ---------------------------------------------------------------------------
# SSHAdapter (ProtocolAdapter)
# ---------------------------------------------------------------------------

class SSHAdapter(ProtocolAdapter):
    """UnifiedMessage over SSH.

    Args:
        host_key:     Server Ed25519 key (paramiko.PKey).
                      Auto-generated via cryptography if None.
        trusted_keys: Mapping {node_id: hex_pubkey_32bytes} for pubkey auth.
                      None = accept any public key (dev/test mode).
        timeout:      Client-side connect + read timeout in seconds.
    """

    def __init__(
        self,
        host_key: paramiko.PKey | None = None,
        trusted_keys: dict[str, str] | None = None,
        timeout: int = 30,
        **_kwargs: object,
    ) -> None:
        self._host_key: paramiko.PKey = host_key or generate_ed25519_key()
        self._trusted_keys = trusted_keys
        self._timeout = timeout
        self._handler: MessageHandler | None = None
        self._server_sock: socket.socket | None = None
        self._running = False

    # --- ProtocolAdapter interface ---

    @property
    def protocol_name(self) -> str:
        return "ssh"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

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
            session = _SSHClientSession(
                conn, addr, self._host_key, self._trusted_keys, self._handler
            )
            t = threading.Thread(target=session.run, daemon=True)
            t.start()

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
    ) -> UnifiedMessage | None:
        """Send *message* to the SSH server at *target* and return the response.

        Args:
            client_key: Ed25519 key used for authentication.
                        Auto-generated if None (accepted when server is in dev mode).
        """
        if client_key is None:
            client_key = generate_ed25519_key()

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        try:
            client.connect(
                target.host,
                port=target.port,
                username=target.node_id or "llmesh",
                pkey=client_key,
                timeout=self._timeout,
                look_for_keys=False,
                allow_agent=False,
            )
            stdin, stdout, _stderr = client.exec_command(
                _SENTINEL_CMD, timeout=self._timeout
            )
            stdin.write(message.to_bytes())
            stdin.channel.shutdown_write()

            raw = stdout.read(_MAX_MSG_BYTES + 1)
            if len(raw) > _MAX_MSG_BYTES:
                raise TransportError(
                    f"response_too_large:{len(raw)}",
                    protocol="ssh",
                    target=str(target),
                )
            if not raw:
                return None
            return UnifiedMessage.from_bytes(raw)

        except paramiko.AuthenticationException as exc:
            raise TransportError(
                f"ssh:auth_failed:{exc}", protocol="ssh", target=str(target)
            ) from exc
        except (paramiko.SSHException, OSError, TimeoutError) as exc:
            raise TransportError(
                str(exc), protocol="ssh", target=str(target)
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
