"""RoutingGuard — loop detection and TTL enforcement for multi-hop routing.

Usage::

    guard = RoutingGuard(local_node_id="node-a")

    # On receipt of an incoming message:
    try:
        guard.check(msg)
    except (LoopDetectedError, TTLExpiredError):
        return  # drop silently

    # Before forwarding to the next hop:
    forwarded = guard.forward(msg)
    await adapter.send(forwarded, next_target)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..protocol.message import UnifiedMessage


class LoopDetectedError(Exception):
    """Raised when the local node ID already appears in the message route."""


class TTLExpiredError(Exception):
    """Raised when a message's TTL has reached zero."""


class RoutingGuard:
    """Enforces loop prevention and TTL for forwarded messages.

    Args:
        local_node_id: This node's identifier. Used to detect routing loops.
        max_route_len: Hard cap on route list length (defence against spoofed routes).
    """

    def __init__(self, local_node_id: str, max_route_len: int = 32) -> None:
        if not local_node_id:
            raise ValueError("local_node_id must be a non-empty string")
        self._local = local_node_id
        self._max_route_len = max_route_len

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def check(self, msg: "UnifiedMessage") -> None:
        """Validate *msg* before processing or forwarding.

        Raises:
            LoopDetectedError: if this node already appears in msg.route.
            TTLExpiredError:   if msg.ttl <= 0.
        """
        if msg.ttl <= 0:
            raise TTLExpiredError(
                f"message {msg.id!r} TTL exhausted (ttl={msg.ttl})"
            )
        if self._local in msg.route:
            raise LoopDetectedError(
                f"message {msg.id!r} loop detected: "
                f"node {self._local!r} already in route {msg.route}"
            )
        if len(msg.route) >= self._max_route_len:
            raise LoopDetectedError(
                f"message {msg.id!r} route too long ({len(msg.route)} hops)"
            )

    def forward(self, msg: "UnifiedMessage") -> "UnifiedMessage":
        """Return a copy of *msg* stamped for forwarding.

        Appends the local node ID to route and decrements ttl by 1.
        The original message is not mutated.

        Raises:
            TTLExpiredError:   if msg.ttl <= 0 (already exhausted).
            LoopDetectedError: if this node is already in the route.
        """
        self.check(msg)

        from ..protocol.message import UnifiedMessage

        return UnifiedMessage(
            type=msg.type,
            payload=msg.payload,
            sender=msg.sender,
            target=msg.target,
            id=msg.id,
            correlation_id=msg.correlation_id,
            timestamp=msg.timestamp,
            ttl=msg.ttl - 1,
            sequence_no=msg.sequence_no,
            total_chunks=msg.total_chunks,
            priority=msg.priority,
            deadline=msg.deadline,
            route=msg.route + [self._local],
        )

    def is_routable(self, msg: "UnifiedMessage") -> bool:
        """Return True if *msg* passes routing checks, False otherwise."""
        try:
            self.check(msg)
            return True
        except (LoopDetectedError, TTLExpiredError):
            return False

    def filter_nodes(
        self, node_ids: list[str], msg: "UnifiedMessage"
    ) -> list[str]:
        """Remove node IDs already present in *msg.route* from *node_ids*.

        Used by SmartNodeSelector to avoid forwarding back to visited nodes.
        """
        visited = set(msg.route)
        return [n for n in node_ids if n not in visited]
