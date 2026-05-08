"""Tests for UnifiedMessage, MessageType, NodeAddress."""
from __future__ import annotations

import json
import time

import pytest

from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage


class TestNodeAddress:
    def test_str(self):
        addr = NodeAddress("127.0.0.1", 8080)
        assert str(addr) == "127.0.0.1:8080"

    def test_roundtrip(self):
        addr = NodeAddress("10.0.0.1", 9000, node_id="abc")
        assert NodeAddress.from_dict(addr.to_dict()) == addr

    def test_optional_node_id(self):
        addr = NodeAddress.from_dict({"host": "h", "port": 1})
        assert addr.node_id == ""

    def test_frozen(self):
        addr = NodeAddress("h", 1)
        with pytest.raises((AttributeError, TypeError)):
            addr.host = "x"  # type: ignore[misc]


class TestUnifiedMessage:
    def _sender(self) -> NodeAddress:
        return NodeAddress("127.0.0.1", 8000, node_id="s1")

    def _target(self) -> NodeAddress:
        return NodeAddress("127.0.0.1", 8001, node_id="t1")

    def test_request_factory(self):
        msg = UnifiedMessage.request({"k": "v"}, self._sender(), self._target())
        assert msg.type == MessageType.REQUEST
        assert msg.payload == {"k": "v"}

    def test_broadcast_factory(self):
        msg = UnifiedMessage.broadcast({"event": "up"}, self._sender(), ttl=2)
        assert msg.type == MessageType.BROADCAST
        assert msg.ttl == 2
        assert msg.target is None

    def test_auto_id_and_timestamp(self):
        before = time.time()
        msg = UnifiedMessage.request({}, self._sender())
        after = time.time()
        assert msg.id  # non-empty UUID string
        assert before <= msg.timestamp <= after

    def test_unique_ids(self):
        m1 = UnifiedMessage.request({}, self._sender())
        m2 = UnifiedMessage.request({}, self._sender())
        assert m1.id != m2.id

    def test_to_bytes_is_valid_json(self):
        msg = UnifiedMessage.request({"x": 1}, self._sender())
        raw = msg.to_bytes()
        d = json.loads(raw)
        assert d["type"] == "request"
        assert d["payload"] == {"x": 1}

    def test_roundtrip_bytes(self):
        msg = UnifiedMessage.request(
            {"tool": "gen"}, self._sender(), self._target()
        )
        restored = UnifiedMessage.from_bytes(msg.to_bytes())
        assert restored.id == msg.id
        assert restored.type == msg.type
        assert restored.payload == msg.payload
        assert restored.sender == msg.sender
        assert restored.target == msg.target
        assert restored.timestamp == pytest.approx(msg.timestamp)

    def test_roundtrip_no_target(self):
        msg = UnifiedMessage.broadcast({"ping": True}, self._sender())
        restored = UnifiedMessage.from_bytes(msg.to_bytes())
        assert restored.target is None

    def test_make_response(self):
        req = UnifiedMessage.request({"q": 1}, self._sender(), self._target())
        resp = req.make_response({"r": 2}, sender=self._target())
        assert resp.type == MessageType.RESPONSE
        assert resp.correlation_id == req.id
        assert resp.target == req.sender

    def test_make_error_response(self):
        req = UnifiedMessage.request({}, self._sender(), self._target())
        err = req.make_response({"msg": "fail"}, sender=self._target(), error=True)
        assert err.type == MessageType.ERROR

    def test_all_message_types_roundtrip(self):
        for mt in MessageType:
            msg = UnifiedMessage(
                type=mt,
                payload={},
                sender=self._sender(),
            )
            assert UnifiedMessage.from_bytes(msg.to_bytes()).type == mt

    def test_ttl_preserved(self):
        msg = UnifiedMessage.broadcast({}, self._sender(), ttl=7)
        assert UnifiedMessage.from_bytes(msg.to_bytes()).ttl == 7

    def test_correlation_id_preserved(self):
        msg = UnifiedMessage(
            type=MessageType.RESPONSE,
            payload={},
            sender=self._sender(),
            correlation_id="orig-id",
        )
        assert UnifiedMessage.from_bytes(msg.to_bytes()).correlation_id == "orig-id"
