"""Tests for llmesh.protocol.outbox — SQLite-backed store-and-forward queue."""
from __future__ import annotations

import time

import pytest

from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage
from llmesh.protocol.outbox import OutboxQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def queue() -> OutboxQueue:
    return OutboxQueue(db_path=":memory:")


@pytest.fixture
def sender() -> NodeAddress:
    return NodeAddress(host="127.0.0.1", port=8000, node_id="s")


@pytest.fixture
def target() -> NodeAddress:
    return NodeAddress(host="127.0.0.1", port=9000)


def _msg(sender: NodeAddress, *, priority: int = 0, deadline: float | None = None) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.REQUEST,
        payload={"tool": "ping"},
        sender=sender,
        priority=priority,
        deadline=deadline,
    )


# ---------------------------------------------------------------------------
# Basic enqueue / dequeue
# ---------------------------------------------------------------------------

class TestEnqueueDequeue:
    def test_empty_queue_returns_nothing(self, queue: OutboxQueue) -> None:
        assert queue.dequeue() == []

    def test_enqueue_increases_count(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        queue.enqueue(_msg(sender), target)
        assert queue.pending_count() == 1

    def test_dequeue_returns_message_and_target(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        msg = _msg(sender)
        queue.enqueue(msg, target)
        results = queue.dequeue()
        assert len(results) == 1
        restored_msg, restored_target = results[0]
        assert restored_msg.id == msg.id
        assert restored_target.host == target.host
        assert restored_target.port == target.port

    def test_dequeue_does_not_remove_message(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        queue.enqueue(_msg(sender), target)
        queue.dequeue()
        assert queue.pending_count() == 1

    def test_dequeue_respects_n_limit(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        for _ in range(5):
            queue.enqueue(_msg(sender), target)
        assert len(queue.dequeue(3)) == 3

    def test_dequeue_increments_attempts(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        msg = _msg(sender)
        queue.enqueue(msg, target)
        queue.dequeue()
        row = queue._conn.execute(
            "SELECT attempts FROM outbox WHERE id = ?", (msg.id,)
        ).fetchone()
        assert row[0] == 1


# ---------------------------------------------------------------------------
# mark_sent
# ---------------------------------------------------------------------------

class TestMarkSent:
    def test_mark_sent_removes_message(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        msg = _msg(sender)
        queue.enqueue(msg, target)
        queue.mark_sent(msg.id)
        assert queue.pending_count() == 0

    def test_mark_sent_unknown_id_is_noop(self, queue: OutboxQueue) -> None:
        queue.mark_sent("nonexistent-id")  # must not raise


# ---------------------------------------------------------------------------
# purge_expired
# ---------------------------------------------------------------------------

class TestPurgeExpired:
    def test_no_expiry_nothing_purged(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        queue.enqueue(_msg(sender), target)
        purged = queue.purge_expired()
        assert purged == 0
        assert queue.pending_count() == 1

    def test_future_deadline_not_purged(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        queue.enqueue(_msg(sender, deadline=time.time() + 60), target)
        assert queue.purge_expired() == 0

    def test_past_deadline_purged(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        queue.enqueue(_msg(sender, deadline=time.time() - 1), target)
        assert queue.purge_expired() == 1
        assert queue.pending_count() == 0

    def test_mixed_expiry(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        queue.enqueue(_msg(sender, deadline=time.time() - 1), target)
        queue.enqueue(_msg(sender, deadline=time.time() + 60), target)
        queue.enqueue(_msg(sender), target)
        assert queue.purge_expired() == 1
        assert queue.pending_count() == 2


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriorityOrdering:
    def test_high_priority_dequeued_first(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        low = _msg(sender, priority=0)
        high = _msg(sender, priority=10)
        queue.enqueue(low, target)
        queue.enqueue(high, target)
        results = queue.dequeue(2)
        assert results[0][0].id == high.id
        assert results[1][0].id == low.id

    def test_same_priority_fifo(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        first = _msg(sender)
        time.sleep(0.01)
        second = _msg(sender)
        queue.enqueue(first, target)
        queue.enqueue(second, target)
        results = queue.dequeue(2)
        assert results[0][0].id == first.id


# ---------------------------------------------------------------------------
# QoS fields survive round-trip through outbox
# ---------------------------------------------------------------------------

class TestQoSRoundTrip:
    def test_priority_preserved(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        msg = _msg(sender, priority=7)
        queue.enqueue(msg, target)
        restored, _ = queue.dequeue()[0]
        assert restored.priority == 7

    def test_deadline_preserved(self, queue: OutboxQueue, sender: NodeAddress, target: NodeAddress) -> None:
        dl = time.time() + 30
        msg = _msg(sender, deadline=dl)
        queue.enqueue(msg, target)
        restored, _ = queue.dequeue()[0]
        assert restored.deadline == pytest.approx(dl)
