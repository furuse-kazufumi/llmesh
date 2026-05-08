"""Tests for llmesh.protocol.codec — JSON/MessagePack serialization."""
from __future__ import annotations

import json

import pytest

from llmesh.protocol.codec import (
    JSON,
    MSGPACK,
    decode,
    encode,
    is_msgpack_available,
)
from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage


# ------------------------------------------------------------------
# JSON codec (always available)
# ------------------------------------------------------------------

class TestJsonCodec:
    def test_encode_returns_bytes(self):
        assert isinstance(encode({"k": "v"}, JSON), bytes)

    def test_encode_is_valid_json(self):
        data = {"x": 1, "y": [1, 2, 3]}
        assert json.loads(encode(data, JSON)) == data

    def test_decode_json_roundtrip(self):
        data = {"hello": "world", "n": 42}
        assert decode(encode(data, JSON)) == data

    def test_decode_auto_detects_json(self):
        raw = b'{"foo": "bar"}'
        assert decode(raw) == {"foo": "bar"}

    def test_decode_empty_raises(self):
        with pytest.raises(ValueError, match="empty"):
            decode(b"")

    def test_default_codec_is_json(self):
        msg = UnifiedMessage.request({"a": 1}, NodeAddress("127.0.0.1", 0))
        assert msg.to_bytes().startswith(b"{")

    def test_unified_message_roundtrip_json(self):
        src = NodeAddress("127.0.0.1", 1234)
        msg = UnifiedMessage.request({"tool": "gen"}, src)
        restored = UnifiedMessage.from_bytes(msg.to_bytes(JSON))
        assert restored.id == msg.id
        assert restored.payload == msg.payload
        assert restored.type == MessageType.REQUEST


# ------------------------------------------------------------------
# MessagePack codec (conditional on availability)
# ------------------------------------------------------------------

@pytest.mark.skipif(not is_msgpack_available(), reason="msgpack not installed")
class TestMsgpackCodec:
    def test_encode_returns_bytes(self):
        data = {"k": "v"}
        raw = encode(data, MSGPACK)
        assert isinstance(raw, bytes)
        assert raw[0] != ord("{")

    def test_decode_msgpack_roundtrip(self):
        data = {"hello": "world", "n": 42, "nested": {"a": [1, 2]}}
        assert decode(encode(data, MSGPACK)) == data

    def test_auto_detect_msgpack(self):
        data = {"x": 99}
        raw = encode(data, MSGPACK)
        assert decode(raw) == data

    def test_unified_message_roundtrip_msgpack(self):
        src = NodeAddress("127.0.0.1", 5678)
        msg = UnifiedMessage.request({"tool": "review"}, src)
        wire = msg.to_bytes(MSGPACK)
        assert wire[0] != ord("{")
        restored = UnifiedMessage.from_bytes(wire)
        assert restored.id == msg.id
        assert restored.payload == {"tool": "review"}

    def test_msgpack_smaller_than_json(self):
        data = {str(i): "value" * 10 for i in range(20)}
        assert len(encode(data, MSGPACK)) < len(encode(data, JSON))


class TestMsgpackUnavailable:
    def test_is_msgpack_available_returns_bool(self):
        assert isinstance(is_msgpack_available(), bool)

    def test_encode_unknown_codec_raises(self):
        with pytest.raises((RuntimeError, Exception)):
            encode({"x": 1}, "badcodec")
