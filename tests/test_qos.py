"""Tests for llmesh.protocol.qos and QoS fields on UnifiedMessage."""
from __future__ import annotations

import time

import pytest

from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage
from llmesh.protocol.qos import DeadlineExpiredError, check_deadline, is_expired


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sender() -> NodeAddress:
    return NodeAddress(host="127.0.0.1", port=8000, node_id="node-a")


def _msg(sender: NodeAddress, *, priority: int = 0, deadline: float | None = None) -> UnifiedMessage:
    return UnifiedMessage(
        type=MessageType.REQUEST,
        payload={"tool": "ping"},
        sender=sender,
        priority=priority,
        deadline=deadline,
    )


# ---------------------------------------------------------------------------
# is_expired
# ---------------------------------------------------------------------------

class TestIsExpired:
    def test_none_deadline_never_expired(self) -> None:
        assert is_expired(None) is False

    def test_future_deadline_not_expired(self) -> None:
        assert is_expired(time.time() + 60) is False

    def test_past_deadline_is_expired(self) -> None:
        assert is_expired(time.time() - 1) is True

    def test_exactly_now_considered_expired(self) -> None:
        # time.time() - epsilon: guaranteed past
        assert is_expired(time.time() - 0.001) is True


# ---------------------------------------------------------------------------
# check_deadline
# ---------------------------------------------------------------------------

class TestCheckDeadline:
    def test_no_deadline_passes(self, sender: NodeAddress) -> None:
        msg = _msg(sender)
        check_deadline(msg)  # must not raise

    def test_future_deadline_passes(self, sender: NodeAddress) -> None:
        msg = _msg(sender, deadline=time.time() + 60)
        check_deadline(msg)  # must not raise

    def test_expired_deadline_raises(self, sender: NodeAddress) -> None:
        msg = _msg(sender, deadline=time.time() - 1)
        with pytest.raises(DeadlineExpiredError, match=msg.id):
            check_deadline(msg)


# ---------------------------------------------------------------------------
# UnifiedMessage serialization — priority & deadline round-trip
# ---------------------------------------------------------------------------

class TestMessageQoSFields:
    def test_default_priority_omitted_from_dict(self, sender: NodeAddress) -> None:
        msg = _msg(sender)
        d = msg.to_dict()
        assert "priority" not in d

    def test_nonzero_priority_in_dict(self, sender: NodeAddress) -> None:
        msg = _msg(sender, priority=5)
        d = msg.to_dict()
        assert d["priority"] == 5

    def test_no_deadline_omitted_from_dict(self, sender: NodeAddress) -> None:
        msg = _msg(sender)
        d = msg.to_dict()
        assert "deadline" not in d

    def test_deadline_in_dict(self, sender: NodeAddress) -> None:
        dl = time.time() + 30
        msg = _msg(sender, deadline=dl)
        d = msg.to_dict()
        assert d["deadline"] == pytest.approx(dl)

    def test_round_trip_priority(self, sender: NodeAddress) -> None:
        msg = _msg(sender, priority=3)
        restored = UnifiedMessage.from_dict(msg.to_dict())
        assert restored.priority == 3

    def test_round_trip_deadline(self, sender: NodeAddress) -> None:
        dl = time.time() + 10
        msg = _msg(sender, deadline=dl)
        restored = UnifiedMessage.from_dict(msg.to_dict())
        assert restored.deadline == pytest.approx(dl)

    def test_round_trip_defaults_when_absent(self, sender: NodeAddress) -> None:
        msg = _msg(sender)
        d = msg.to_dict()
        restored = UnifiedMessage.from_dict(d)
        assert restored.priority == 0
        assert restored.deadline is None

    def test_bytes_round_trip_with_qos(self, sender: NodeAddress) -> None:
        dl = time.time() + 5
        msg = _msg(sender, priority=7, deadline=dl)
        restored = UnifiedMessage.from_bytes(msg.to_bytes())
        assert restored.priority == 7
        assert restored.deadline == pytest.approx(dl)
