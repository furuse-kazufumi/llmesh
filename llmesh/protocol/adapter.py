"""ProtocolAdapter — abstract base class for all transport implementations."""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .message import NodeAddress, UnifiedMessage

MessageHandler = Callable[["UnifiedMessage"], Awaitable["UnifiedMessage | None"]]


class ProtocolAdapter(ABC):
    """Transport-agnostic communication interface.

    Callers interact only with UnifiedMessage objects and NodeAddress values.
    The underlying wire protocol (HTTP, TCP, UDP, …) is fully encapsulated.

    Lifecycle::
        adapter = AdapterRegistry.create("tcp")
        adapter.on_message(my_handler)
        await adapter.start("0.0.0.0", 9000)
        response = await adapter.send(msg, target)
        await adapter.stop()

    Implementations: HTTPAdapter, TCPAdapter, UDPAdapter.
    Register custom adapters via AdapterRegistry.register().
    """

    @abstractmethod
    async def start(self, host: str, port: int) -> None:
        """Start listening for incoming messages on host:port."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop the adapter and release all resources."""

    @abstractmethod
    async def send(
        self,
        message: "UnifiedMessage",
        target: "NodeAddress",
    ) -> "UnifiedMessage | None":
        """Send *message* to *target* and return the response.

        Returns None for fire-and-forget semantics (e.g. UDP broadcast).
        Raises TransportError on connectivity failure.
        """

    @abstractmethod
    async def broadcast(
        self,
        message: "UnifiedMessage",
        targets: "list[NodeAddress] | None" = None,
    ) -> None:
        """Send *message* to multiple targets (or all known peers if None)."""

    @abstractmethod
    def on_message(self, handler: MessageHandler) -> None:
        """Register a coroutine handler for incoming messages.

        The handler receives a UnifiedMessage and may return a response
        (returned to sender) or None (no reply).
        """

    @property
    @abstractmethod
    def protocol_name(self) -> str:
        """Short protocol identifier, e.g. 'http', 'tcp', 'udp'."""

    @property
    @abstractmethod
    def is_running(self) -> bool:
        """True while the adapter is listening for incoming messages."""


class TransportError(Exception):
    """Raised when a send or broadcast operation fails."""

    def __init__(self, message: str, protocol: str = "", target: str = "") -> None:
        super().__init__(message)
        self.protocol = protocol
        self.target = target
