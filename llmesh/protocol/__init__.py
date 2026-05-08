"""llmesh.protocol — transport-agnostic messaging layer (v0.4.0).

Public API::

    from llmesh.protocol import AdapterRegistry, ReliableStream, UnifiedMessage

    adapter = AdapterRegistry.create("tcp")          # or "http" / "udp" / "tcp_stream"
    stream = ReliableStream(sender=my_addr)

    stream_id = await stream.send(b"any bytes", target=peer, adapter=adapter)
    for payload in await stream.on_message(incoming_msg, adapter=adapter):
        handle(payload)   # bytes | dict | str, fully reassembled

Protocol comparison:
  http        — HTTP/1.1 request-response (default; TLS + auth headers supported)
  tcp         — TCP with 4-byte framing, one connection per request
  tcp_stream  — TCP with persistent connection + ReliableStream chunking;
                transparent for any payload size, auto-reconnects on failure
  udp         — UDP datagrams; unreliable, suitable for gossip / heartbeats
"""
from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .codec import JSON, MSGPACK, CODECS, decode, encode, is_msgpack_available
from .assembler import CompletedStream, MessageAssembler, RetransmitInfo
from .chunk_sender import ChunkSender
from .device_profile import (
    DeviceProfile,
    PayloadTooLargeError,
    ProfileType,
    ProtocolNotAllowedError,
)
from .message import MessageType, NodeAddress, UnifiedMessage
from .outbox import OutboxQueue
from .qos import DeadlineExpiredError, check_deadline, is_expired
from .registry import AdapterRegistry
from .reliable_stream import ReliableStream
from .watchdog import WatchdogTimer

# Register built-in adapters
from .http_adapter import HTTPAdapter
from .tcp_adapter import TCPAdapter
from .tcp_stream_adapter import TCPStreamAdapter
from .udp_adapter import UDPAdapter
from .ssh_adapter import SSHAdapter
from .sftp_adapter import SFTPAdapter
from .smtp_adapter import SMTPAdapter
from .imap_adapter import IMAPAdapter
from .pop3_adapter import POP3Adapter
from .ftp_adapter import FTPAdapter
from .snmp_adapter import SNMPAdapter
from .local_file_adapter import LocalFileAdapter

AdapterRegistry.register("http", HTTPAdapter)
AdapterRegistry.register("tcp", TCPAdapter)
AdapterRegistry.register("tcp_stream", TCPStreamAdapter)
AdapterRegistry.register("udp", UDPAdapter)
AdapterRegistry.register("ssh", SSHAdapter)
AdapterRegistry.register("sftp", SFTPAdapter)
AdapterRegistry.register("smtp", SMTPAdapter)
AdapterRegistry.register("imap", IMAPAdapter)
AdapterRegistry.register("pop3", POP3Adapter)
AdapterRegistry.register("ftp", FTPAdapter)
AdapterRegistry.register("snmp", SNMPAdapter)
AdapterRegistry.register("localfile", LocalFileAdapter)

__all__ = [
    "AdapterRegistry",
    "check_deadline",
    "CODECS",
    "DeadlineExpiredError",
    "DeviceProfile",
    "PayloadTooLargeError",
    "ProfileType",
    "ProtocolNotAllowedError",
    "JSON",
    "MSGPACK",
    "decode",
    "encode",
    "is_expired",
    "is_msgpack_available",
    "ChunkSender",
    "CompletedStream",
    "HTTPAdapter",
    "MessageAssembler",
    "MessageHandler",
    "MessageType",
    "NodeAddress",
    "OutboxQueue",
    "ProtocolAdapter",
    "ReliableStream",
    "RetransmitInfo",
    "TCPAdapter",
    "TCPStreamAdapter",
    "TransportError",
    "UDPAdapter",
    "SSHAdapter",
    "SFTPAdapter",
    "SMTPAdapter",
    "IMAPAdapter",
    "POP3Adapter",
    "FTPAdapter",
    "SNMPAdapter",
    "LocalFileAdapter",
    "UnifiedMessage",
    "WatchdogTimer",
]
