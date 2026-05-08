"""Tests for ROS2Adapter — opt-in guard, L3/L4 rejection, allowlist, sensor passthrough."""
from __future__ import annotations

import asyncio
import json
import os
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from llmesh.protocol.ros2_adapter import (
    ROS2Adapter,
    _check_optin,
    _nonce_from_stamp,
    _LLMeshROS2Node,
    _BLOCKED_LEVELS,
)
from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

def _make_mock_rclpy(node_cls=None):
    """Return a mock rclpy module."""
    m = MagicMock()
    m.init = MagicMock()
    m.shutdown = MagicMock()
    m.spin_once = MagicMock()
    m.spin = MagicMock()
    return m


def _make_adapter(allowlist=None, rclpy_mod=None):
    if rclpy_mod is None:
        rclpy_mod = _make_mock_rclpy()
    return ROS2Adapter(
        node_name="test_node",
        request_topic="/llmesh/request",
        response_topic="/llmesh/response",
        node_allowlist=allowlist,
        _rclpy_mod=rclpy_mod,
    )


# ---------------------------------------------------------------------------
# Opt-in guard
# ---------------------------------------------------------------------------

class TestCheckOptin:
    def test_missing_env_raises(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_ROS2", raising=False)
        with pytest.raises(RuntimeError, match="LLMESH_ENABLE_ROS2"):
            _check_optin()

    def test_wrong_value_raises(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_ROS2", "true")
        with pytest.raises(RuntimeError):
            _check_optin()

    def test_correct_value_passes(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_ROS2", "1")
        _check_optin()  # should not raise


# ---------------------------------------------------------------------------
# Nonce from stamp
# ---------------------------------------------------------------------------

class TestNonceFromStamp:
    def test_dict_stamp(self):
        nonce = _nonce_from_stamp({"sec": 1000, "nanosec": 500})
        assert len(nonce) > 0
        assert isinstance(nonce, str)

    def test_none_stamp(self):
        nonce = _nonce_from_stamp(None)
        assert len(nonce) == 32

    def test_different_stamps_differ(self):
        n1 = _nonce_from_stamp({"sec": 1, "nanosec": 0})
        n2 = _nonce_from_stamp({"sec": 2, "nanosec": 0})
        assert n1 != n2


# ---------------------------------------------------------------------------
# ROS2Adapter construction
# ---------------------------------------------------------------------------

class TestROS2AdapterConstruction:
    def test_no_rclpy_and_no_mock_raises(self):
        with patch("llmesh.protocol.ros2_adapter._RCLPY_AVAILABLE", False):
            with pytest.raises(ImportError, match="rclpy"):
                ROS2Adapter()

    def test_mock_rclpy_accepted(self):
        adapter = _make_adapter()
        assert adapter.protocol_name == "ros2"
        assert adapter.is_running is False

    def test_allowlist_set(self):
        adapter = _make_adapter(allowlist=["node_a", "node_b"])
        assert adapter._is_allowed("node_a")
        assert not adapter._is_allowed("node_c")

    def test_no_allowlist_allows_all(self):
        adapter = _make_adapter(allowlist=None)
        assert adapter._is_allowed("any_node")


# ---------------------------------------------------------------------------
# Start / stop
# ---------------------------------------------------------------------------

class TestROS2AdapterLifecycle:
    @pytest.mark.asyncio
    async def test_start_requires_optin(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_ROS2", raising=False)
        adapter = _make_adapter()
        with pytest.raises(RuntimeError, match="LLMESH_ENABLE_ROS2"):
            await adapter.start()

    @pytest.mark.asyncio
    async def test_start_and_stop(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_ROS2", "1")
        mock_rclpy = _make_mock_rclpy()

        # Patch _LLMeshROS2Node so it doesn't need real ROS
        with patch("llmesh.protocol.ros2_adapter._LLMeshROS2Node") as MockNode:
            MockNode.return_value = MagicMock()
            adapter = ROS2Adapter(_rclpy_mod=mock_rclpy)
            await adapter.start()
            assert adapter.is_running is True
            mock_rclpy.init.assert_called_once()

            await adapter.stop()
            assert adapter.is_running is False
            mock_rclpy.shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_raises(self, monkeypatch):
        from llmesh.protocol.adapter import TransportError
        adapter = _make_adapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={},
            sender=NodeAddress("ros2", 0),
            target=NodeAddress("ros2", 0),
        )
        with pytest.raises(TransportError):
            await adapter.send(msg, NodeAddress("ros2", 0))

    @pytest.mark.asyncio
    async def test_broadcast_raises(self, monkeypatch):
        from llmesh.protocol.adapter import TransportError
        adapter = _make_adapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={},
            sender=NodeAddress("ros2", 0),
            target=NodeAddress("ros2", 0),
        )
        with pytest.raises(TransportError):
            await adapter.broadcast(msg)


# ---------------------------------------------------------------------------
# _LLMeshROS2Node._on_message — unit tests via mock node
# ---------------------------------------------------------------------------

def _make_ros2_node(adapter):
    """Build a _LLMeshROS2Node with mocked rclpy dependencies."""
    mock_string_cls = MagicMock()
    mock_publisher = MagicMock()
    mock_subscription = MagicMock()

    node = MagicMock(spec=_LLMeshROS2Node)
    node._adapter = adapter
    node._response_topic = "/llmesh/response"
    node._String = mock_string_cls
    node._publisher = mock_publisher
    # Bind the real _on_message to this mock node
    node._on_message = _LLMeshROS2Node._on_message.__get__(node, type(node))
    return node, mock_publisher


class TestROS2NodeOnMessage:
    def test_l3_message_dropped(self):
        adapter = _make_adapter()
        node, pub = _make_ros2_node(adapter)
        msg = MagicMock()
        msg.data = json.dumps({"data_level": 3, "prompt": "secret", "node_id": "n1"})
        node._on_message(msg)
        pub.publish.assert_not_called()

    def test_l4_message_dropped(self):
        adapter = _make_adapter()
        node, pub = _make_ros2_node(adapter)
        msg = MagicMock()
        msg.data = json.dumps({"data_level": 4, "prompt": "top secret"})
        node._on_message(msg)
        pub.publish.assert_not_called()

    def test_allowlist_rejection(self):
        adapter = _make_adapter(allowlist=["allowed_node"])
        node, pub = _make_ros2_node(adapter)
        msg = MagicMock()
        msg.data = json.dumps({"prompt": "hi", "node_id": "evil_node"})
        node._on_message(msg)
        pub.publish.assert_not_called()

    def test_oversized_message_dropped(self):
        adapter = _make_adapter()
        node, pub = _make_ros2_node(adapter)
        msg = MagicMock()
        msg.data = "x" * (300 * 1024)
        node._on_message(msg)
        pub.publish.assert_not_called()

    def test_plain_text_parsed_as_prompt(self):
        adapter = _make_adapter()
        received = []

        async def handler(m):
            received.append(m)
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": "ok"},
                sender=NodeAddress("ros2", 0),
            )

        adapter.on_message(handler)
        node, pub = _make_ros2_node(adapter)

        loop = asyncio.new_event_loop()
        adapter._loop = loop
        msg = MagicMock()
        msg.data = "not json, just text"
        # Run with loop
        node._on_message(msg)
        # Give time for coroutine
        loop.run_until_complete(asyncio.sleep(0))
        loop.close()

    def test_valid_l0_message_dispatched(self):
        adapter = _make_adapter()
        received = []

        async def handler(m: UnifiedMessage):
            received.append(m)
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": "done"},
                sender=NodeAddress("ros2", 0),
            )

        adapter.on_message(handler)
        node, pub = _make_ros2_node(adapter)

        loop = asyncio.new_event_loop()
        adapter._loop = loop
        msg = MagicMock()
        msg.data = json.dumps({"prompt": "hello", "data_level": 0, "node_id": "bot1"})

        async def run():
            node._on_message(msg)
            await asyncio.sleep(0.1)

        loop.run_until_complete(run())
        loop.close()
        assert len(received) == 1
        assert received[0].payload["prompt"] == "hello"

    def test_sensor_data_injected_into_prompt(self):
        adapter = _make_adapter()
        received = []

        async def handler(m: UnifiedMessage):
            received.append(m)
            return None

        adapter.on_message(handler)
        node, pub = _make_ros2_node(adapter)

        loop = asyncio.new_event_loop()
        adapter._loop = loop
        msg = MagicMock()
        msg.data = json.dumps({
            "prompt": "analyse this",
            "data_level": 0,
            "sensor_topic": "/imu/data",
            "sensor_data": {"orientation": {"x": 0, "y": 0, "z": 0, "w": 1}},
        })

        async def run():
            node._on_message(msg)
            await asyncio.sleep(0.1)

        loop.run_until_complete(run())
        loop.close()
        assert len(received) == 1
        assert "[sensor]" in received[0].payload["prompt"]

    def test_l4_sensor_blocked(self):
        adapter = _make_adapter()
        received = []

        async def handler(m: UnifiedMessage):
            received.append(m)
            return None

        adapter.on_message(handler)
        node, pub = _make_ros2_node(adapter)

        loop = asyncio.new_event_loop()
        adapter._loop = loop
        msg = MagicMock()
        msg.data = json.dumps({
            "prompt": "process",
            "data_level": 0,
            "sensor_topic": "/face_recognition/output",
            "sensor_data": {"faces": [{"id": "user123"}]},
        })

        async def run():
            node._on_message(msg)
            await asyncio.sleep(0.1)

        loop.run_until_complete(run())
        loop.close()
        # Handler should NOT be called — L4 sensor blocked
        assert len(received) == 0


# ---------------------------------------------------------------------------
# _dispatch with no handler
# ---------------------------------------------------------------------------

class TestROS2AdapterDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_no_handler_returns_none(self):
        adapter = _make_adapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": "hi"},
            sender=NodeAddress("ros2", 0),
        )
        result = await adapter._dispatch(msg)
        assert result is None

    @pytest.mark.asyncio
    async def test_dispatch_handler_exception_returns_none(self):
        adapter = _make_adapter()

        async def bad_handler(m):
            raise ValueError("boom")

        adapter.on_message(bad_handler)
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": "hi"},
            sender=NodeAddress("ros2", 0),
        )
        result = await adapter._dispatch(msg)
        assert result is None


# ---------------------------------------------------------------------------
# is_allowed
# ---------------------------------------------------------------------------

class TestIsAllowed:
    def test_no_allowlist_always_true(self):
        adapter = _make_adapter(allowlist=None)
        for name in ["alice", "bob", "evil"]:
            assert adapter._is_allowed(name)

    def test_with_allowlist(self):
        adapter = _make_adapter(allowlist=["alice"])
        assert adapter._is_allowed("alice")
        assert not adapter._is_allowed("bob")
