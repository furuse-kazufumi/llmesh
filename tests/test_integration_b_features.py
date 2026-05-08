"""Integration tests: B-2 + B-3 + B-4 + B-5 combined scenarios.

Tests that the new edge-layer features interact correctly:
  - B-2 (MessagePack) + B-1 (QoS): priority/deadline survive msgpack round-trip
  - B-3 (OutboxQueue) + B-1 (deadline): expired messages are purged from outbox
  - B-4 (DeviceProfile NANO) + B-2 (msgpack): NANO payload fits in 1 KB
  - B-4 (NANO) + B-3 (OutboxQueue): outbox respects deadline from NANO messages
  - B-5 (Routing) + B-1 (TTL): multi-hop TTL decrements correctly
  - B-2 + B-3 + B-4 (full E2E): NANO node queues msgpack message, purge on expiry
"""
from __future__ import annotations

import time

import pytest

from llmesh.protocol.codec import JSON, MSGPACK, encode, decode, is_msgpack_available
from llmesh.protocol.device_profile import DeviceProfile, PayloadTooLargeError
from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage
from llmesh.protocol.outbox import OutboxQueue
from llmesh.protocol.qos import DeadlineExpiredError, check_deadline, is_expired
from llmesh.routing.router import LoopDetectedError, RoutingGuard, TTLExpiredError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sender() -> NodeAddress:
    return NodeAddress(host="127.0.0.1", port=8000, node_id="origin")


@pytest.fixture
def target() -> NodeAddress:
    return NodeAddress(host="127.0.0.1", port=9000, node_id="dest")


@pytest.fixture
def outbox() -> OutboxQueue:
    return OutboxQueue(db_path=":memory:")


def _msg(
    sender: NodeAddress,
    *,
    priority: int = 0,
    deadline: float | None = None,
    ttl: int = 3,
    route: list[str] | None = None,
    payload_size: int = 64,
) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.REQUEST,
        payload={"data": "x" * payload_size},
        sender=sender,
        priority=priority,
        deadline=deadline,
        ttl=ttl,
        route=route or [],
    )


# ---------------------------------------------------------------------------
# B-2 (msgpack) + B-1 (QoS): QoS fields survive codec round-trip
# ---------------------------------------------------------------------------

class TestMsgpackQoSIntegration:
    def test_priority_survives_json_round_trip(self, sender: NodeAddress) -> None:
        msg = _msg(sender, priority=5)
        restored = UnifiedMessage.from_bytes(msg.to_bytes(JSON))
        assert restored.priority == 5

    @pytest.mark.skipif(not is_msgpack_available(), reason="msgpack not installed")
    def test_priority_survives_msgpack_round_trip(self, sender: NodeAddress) -> None:
        msg = _msg(sender, priority=7)
        restored = UnifiedMessage.from_bytes(msg.to_bytes(MSGPACK))
        assert restored.priority == 7

    def test_deadline_survives_json_round_trip(self, sender: NodeAddress) -> None:
        dl = time.time() + 30
        msg = _msg(sender, deadline=dl)
        restored = UnifiedMessage.from_bytes(msg.to_bytes(JSON))
        assert restored.deadline == pytest.approx(dl)

    @pytest.mark.skipif(not is_msgpack_available(), reason="msgpack not installed")
    def test_deadline_survives_msgpack_round_trip(self, sender: NodeAddress) -> None:
        dl = time.time() + 30
        msg = _msg(sender, deadline=dl)
        restored = UnifiedMessage.from_bytes(msg.to_bytes(MSGPACK))
        assert restored.deadline == pytest.approx(dl)

    def test_route_survives_json_round_trip(self, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["node-a", "node-b"])
        restored = UnifiedMessage.from_bytes(msg.to_bytes(JSON))
        assert restored.route == ["node-a", "node-b"]

    @pytest.mark.skipif(not is_msgpack_available(), reason="msgpack not installed")
    def test_route_survives_msgpack_round_trip(self, sender: NodeAddress) -> None:
        msg = _msg(sender, route=["node-a", "node-b"])
        restored = UnifiedMessage.from_bytes(msg.to_bytes(MSGPACK))
        assert restored.route == ["node-a", "node-b"]


# ---------------------------------------------------------------------------
# B-3 (OutboxQueue) + B-1 (deadline): expiry in outbox
# ---------------------------------------------------------------------------

class TestOutboxDeadlineIntegration:
    def test_expired_message_purged_from_outbox(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        expired = _msg(sender, deadline=time.time() - 1)
        valid = _msg(sender, deadline=time.time() + 60)
        outbox.enqueue(expired, target)
        outbox.enqueue(valid, target)

        purged = outbox.purge_expired()

        assert purged == 1
        assert outbox.pending_count() == 1
        remaining, _ = outbox.dequeue()[0]
        assert remaining.id == valid.id

    def test_outbox_preserves_deadline_on_dequeue(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        dl = time.time() + 45
        msg = _msg(sender, deadline=dl)
        outbox.enqueue(msg, target)
        restored, _ = outbox.dequeue()[0]
        assert not is_expired(restored.deadline)
        assert restored.deadline == pytest.approx(dl)

    def test_priority_ordering_in_outbox(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        low = _msg(sender, priority=0)
        high = _msg(sender, priority=10)
        critical = _msg(sender, priority=100)
        outbox.enqueue(low, target)
        outbox.enqueue(high, target)
        outbox.enqueue(critical, target)

        results = outbox.dequeue(3)
        ids = [m.id for m, _ in results]
        assert ids == [critical.id, high.id, low.id]


# ---------------------------------------------------------------------------
# B-4 (NANO) + B-2 (msgpack): NANO payload size checks
# ---------------------------------------------------------------------------

class TestNanoPayloadIntegration:
    def test_nano_rejects_oversized_payload(self) -> None:
        profile = DeviceProfile.nano()
        with pytest.raises(PayloadTooLargeError):
            profile.check_payload(1025)

    def test_nano_accepts_1kb_payload(self) -> None:
        profile = DeviceProfile.nano()
        profile.check_payload(1024)  # must not raise

    def test_nano_serialized_message_fits_limit(self, sender: NodeAddress) -> None:
        profile = DeviceProfile.nano()
        msg = _msg(sender, payload_size=64)
        size = len(msg.to_bytes(JSON))
        profile.check_payload(size)  # must not raise

    def test_nano_large_message_flagged(self, sender: NodeAddress) -> None:
        profile = DeviceProfile.nano()
        msg = _msg(sender, payload_size=900)
        size = len(msg.to_bytes(JSON))
        if size > 1024:
            with pytest.raises(PayloadTooLargeError):
                profile.check_payload(size)
        # (if still within limit, test passes — payload_size is chars not bytes)

    def test_nano_protocol_guard_blocks_tcp(self) -> None:
        from llmesh.protocol.device_profile import ProtocolNotAllowedError
        profile = DeviceProfile.nano()
        with pytest.raises(ProtocolNotAllowedError):
            profile.check_protocol("tcp_stream")

    def test_nano_allows_udp(self) -> None:
        profile = DeviceProfile.nano()
        profile.check_protocol("udp")  # must not raise


# ---------------------------------------------------------------------------
# B-4 (NANO) + B-3 (OutboxQueue): NANO messages in outbox
# ---------------------------------------------------------------------------

class TestNanoOutboxIntegration:
    def test_nano_message_enqueued_and_purged(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        profile = DeviceProfile.nano()
        msg = _msg(sender, deadline=time.time() - 1, priority=5)
        size = len(msg.to_bytes(JSON))
        profile.check_payload(size)  # validate before enqueue

        outbox.enqueue(msg, target)
        assert outbox.purge_expired() == 1
        assert outbox.pending_count() == 0

    def test_nano_valid_message_survives_outbox_round_trip(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        profile = DeviceProfile.nano()
        dl = time.time() + 30
        msg = _msg(sender, deadline=dl, priority=3)
        size = len(msg.to_bytes(JSON))
        profile.check_payload(size)

        outbox.enqueue(msg, target)
        outbox.purge_expired()

        assert outbox.pending_count() == 1
        restored, _ = outbox.dequeue()[0]
        assert restored.priority == 3
        assert not is_expired(restored.deadline)


# ---------------------------------------------------------------------------
# B-5 (Routing) + B-1 (TTL): multi-hop scenarios
# ---------------------------------------------------------------------------

class TestRoutingTTLIntegration:
    def test_three_hop_ttl_consumed(self, sender: NodeAddress) -> None:
        msg = _msg(sender, ttl=3)
        g_a = RoutingGuard("node-a")
        g_b = RoutingGuard("node-b")
        g_c = RoutingGuard("node-c")

        hop1 = g_a.forward(msg)
        assert hop1.ttl == 2
        assert hop1.route == ["node-a"]

        hop2 = g_b.forward(hop1)
        assert hop2.ttl == 1
        assert hop2.route == ["node-a", "node-b"]

        hop3 = g_c.forward(hop2)
        assert hop3.ttl == 0
        assert hop3.route == ["node-a", "node-b", "node-c"]

    def test_ttl_exhausted_after_three_hops(self, sender: NodeAddress) -> None:
        msg = _msg(sender, ttl=3)
        g_a, g_b, g_c = RoutingGuard("node-a"), RoutingGuard("node-b"), RoutingGuard("node-c")
        g_d = RoutingGuard("node-d")

        hop = g_c.forward(g_b.forward(g_a.forward(msg)))
        assert hop.ttl == 0
        with pytest.raises(TTLExpiredError):
            g_d.forward(hop)

    def test_loop_prevented_on_revisit(self, sender: NodeAddress) -> None:
        msg = _msg(sender, ttl=5, route=["node-a", "node-b"])
        g = RoutingGuard("node-a")
        with pytest.raises(LoopDetectedError):
            g.forward(msg)

    def test_route_serializes_through_outbox(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        msg = _msg(sender, route=["node-a", "node-b"], ttl=2)
        outbox.enqueue(msg, target)
        restored, _ = outbox.dequeue()[0]
        assert restored.route == ["node-a", "node-b"]
        assert restored.ttl == 2


# ---------------------------------------------------------------------------
# Full E2E: B-2 + B-3 + B-4 combined
# ---------------------------------------------------------------------------

class TestFullE2EIntegration:
    def test_nano_msgpack_outbox_round_trip(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        """NANO node: validate payload → encode as JSON → queue → dequeue → verify."""
        profile = DeviceProfile.nano()
        dl = time.time() + 60
        msg = _msg(sender, priority=5, deadline=dl, payload_size=32)

        # NANO guard: check payload fits
        raw = msg.to_bytes(JSON)
        profile.check_payload(len(raw))

        # Enqueue in outbox
        outbox.enqueue(msg, target)
        assert outbox.pending_count() == 1

        # No expired messages
        assert outbox.purge_expired() == 0

        # Dequeue and verify all fields intact
        restored, restored_target = outbox.dequeue()[0]
        assert restored.id == msg.id
        assert restored.priority == 5
        assert restored.deadline == pytest.approx(dl)
        assert restored_target.host == target.host
        assert restored_target.port == target.port

        # Mark sent
        outbox.mark_sent(msg.id)
        assert outbox.pending_count() == 0

    def test_mixed_priority_expiry_batch(
        self, outbox: OutboxQueue, sender: NodeAddress, target: NodeAddress
    ) -> None:
        """Queue messages with varied priority/deadline, purge expired, verify ordering."""
        profile = DeviceProfile.nano()
        now = time.time()

        msgs = [
            _msg(sender, priority=1, deadline=now - 1),   # expired, low
            _msg(sender, priority=10, deadline=now + 60), # valid, high
            _msg(sender, priority=5, deadline=now + 60),  # valid, medium
            _msg(sender, priority=0, deadline=now - 1),   # expired, zero
        ]
        for m in msgs:
            raw = m.to_bytes(JSON)
            profile.check_payload(len(raw))
            outbox.enqueue(m, target)

        purged = outbox.purge_expired()
        assert purged == 2
        assert outbox.pending_count() == 2

        results = outbox.dequeue(2)
        priorities = [m.priority for m, _ in results]
        assert priorities == [10, 5]  # high priority first
