"""Tests for llmesh.routing.router — RoutingGuard (loop detection, TTL, forwarding)."""
from __future__ import annotations

import pytest

from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage
from llmesh.routing.router import LoopDetectedError, RoutingGuard, TTLExpiredError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def guard() -> RoutingGuard:
    return RoutingGuard(local_node_id="node-a")


@pytest.fixture
def sender() -> NodeAddress:
    return NodeAddress(host="127.0.0.1", port=8000, node_id="origin")


def _msg(sender: NodeAddress, *, ttl: int = 3, route: list[str] | None = None) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.BROADCAST,
        payload={"data": "x"},
        sender=sender,
        ttl=ttl,
        route=route or [],
    )


# ---------------------------------------------------------------------------
# RoutingGuard.check
# ---------------------------------------------------------------------------

class TestCheck:
    def test_fresh_message_passes(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        guard.check(_msg(sender))  # must not raise

    def test_ttl_zero_raises(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        with pytest.raises(TTLExpiredError, match="ttl=0"):
            guard.check(_msg(sender, ttl=0))

    def test_ttl_negative_raises(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        with pytest.raises(TTLExpiredError):
            guard.check(_msg(sender, ttl=-1))

    def test_loop_detected_raises(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        with pytest.raises(LoopDetectedError, match="node-a"):
            guard.check(_msg(sender, route=["node-b", "node-a"]))

    def test_route_without_self_passes(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        guard.check(_msg(sender, route=["node-b", "node-c"]))  # must not raise

    def test_route_too_long_raises(self, sender: NodeAddress) -> None:
        g = RoutingGuard(local_node_id="node-a", max_route_len=3)
        with pytest.raises(LoopDetectedError, match="too long"):
            g.check(_msg(sender, route=["x", "y", "z"]))


# ---------------------------------------------------------------------------
# RoutingGuard.forward
# ---------------------------------------------------------------------------

class TestForward:
    def test_forward_appends_local_to_route(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["node-b"])
        fwd = guard.forward(msg)
        assert fwd.route == ["node-b", "node-a"]

    def test_forward_decrements_ttl(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        msg = _msg(sender, ttl=3)
        fwd = guard.forward(msg)
        assert fwd.ttl == 2

    def test_forward_does_not_mutate_original(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["node-b"], ttl=3)
        _ = guard.forward(msg)
        assert msg.route == ["node-b"]
        assert msg.ttl == 3

    def test_forward_preserves_id(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        msg = _msg(sender)
        fwd = guard.forward(msg)
        assert fwd.id == msg.id

    def test_forward_raises_on_ttl_zero(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        with pytest.raises(TTLExpiredError):
            guard.forward(_msg(sender, ttl=0))

    def test_forward_raises_on_loop(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        with pytest.raises(LoopDetectedError):
            guard.forward(_msg(sender, route=["node-a"]))


# ---------------------------------------------------------------------------
# RoutingGuard.is_routable
# ---------------------------------------------------------------------------

class TestIsRoutable:
    def test_valid_message_is_routable(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        assert guard.is_routable(_msg(sender)) is True

    def test_ttl_zero_not_routable(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        assert guard.is_routable(_msg(sender, ttl=0)) is False

    def test_loop_not_routable(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        assert guard.is_routable(_msg(sender, route=["node-a"])) is False


# ---------------------------------------------------------------------------
# RoutingGuard.filter_nodes
# ---------------------------------------------------------------------------

class TestFilterNodes:
    def test_removes_visited_nodes(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["node-b", "node-c"])
        result = guard.filter_nodes(["node-a", "node-b", "node-c", "node-d"], msg)
        assert result == ["node-a", "node-d"]

    def test_empty_route_returns_all(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        msg = _msg(sender)
        nodes = ["node-a", "node-b"]
        assert guard.filter_nodes(nodes, msg) == nodes

    def test_all_visited_returns_empty(self, guard: RoutingGuard, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["node-x", "node-y"])
        assert guard.filter_nodes(["node-x", "node-y"], msg) == []


# ---------------------------------------------------------------------------
# UnifiedMessage route serialization
# ---------------------------------------------------------------------------

class TestMessageRouteSerialization:
    def test_empty_route_omitted_from_dict(self, sender: NodeAddress) -> None:
        msg = _msg(sender)
        assert "route" not in msg.to_dict()

    def test_nonempty_route_in_dict(self, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["a", "b"])
        assert msg.to_dict()["route"] == ["a", "b"]

    def test_route_round_trip(self, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["a", "b", "c"])
        restored = UnifiedMessage.from_dict(msg.to_dict())
        assert restored.route == ["a", "b", "c"]

    def test_missing_route_defaults_to_empty(self, sender: NodeAddress) -> None:
        d = _msg(sender).to_dict()
        d.pop("route", None)
        restored = UnifiedMessage.from_dict(d)
        assert restored.route == []


# ---------------------------------------------------------------------------
# RoutingGuard constructor validation
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_empty_node_id_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty"):
            RoutingGuard(local_node_id="")
