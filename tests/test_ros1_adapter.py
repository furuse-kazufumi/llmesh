"""Tests for ROS1Adapter — double opt-in, L3/L4 rejection, EOL warning, allowlist."""
from __future__ import annotations

import asyncio
import json
import pytest
import logging
from unittest.mock import MagicMock, patch

from llmesh.protocol.ros1_adapter import (
    ROS1Adapter,
    _check_double_optin,
    _DEPRECATION_MSG,
)
from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_rospy():
    m = MagicMock()
    m.init_node = MagicMock()
    m.Publisher = MagicMock(return_value=MagicMock())
    m.Subscriber = MagicMock(return_value=MagicMock())
    m.spin = MagicMock()
    m.signal_shutdown = MagicMock()
    return m


def _make_adapter(allowlist=None, rospy_mod=None):
    if rospy_mod is None:
        rospy_mod = _make_mock_rospy()
    return ROS1Adapter(
        node_name="test_ros1_node",
        node_allowlist=allowlist,
        _rospy_mod=rospy_mod,
    )


# ---------------------------------------------------------------------------
# Double opt-in guard
# ---------------------------------------------------------------------------

class TestDoubleOptin:
    def test_missing_both_raises(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_ROS1", raising=False)
        monkeypatch.delenv("LLMESH_ROS1_LEGACY_ACK", raising=False)
        with pytest.raises(RuntimeError, match="LLMESH_ENABLE_ROS1"):
            _check_double_optin()

    def test_only_enable_raises(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_ROS1", "1")
        monkeypatch.delenv("LLMESH_ROS1_LEGACY_ACK", raising=False)
        with pytest.raises(RuntimeError):
            _check_double_optin()

    def test_only_ack_raises(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_ROS1", raising=False)
        monkeypatch.setenv("LLMESH_ROS1_LEGACY_ACK", "1")
        with pytest.raises(RuntimeError):
            _check_double_optin()

    def test_both_set_passes(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_ROS1", "1")
        monkeypatch.setenv("LLMESH_ROS1_LEGACY_ACK", "1")
        _check_double_optin()   # should not raise

    def test_wrong_values_raise(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_ROS1", "true")
        monkeypatch.setenv("LLMESH_ROS1_LEGACY_ACK", "yes")
        with pytest.raises(RuntimeError):
            _check_double_optin()


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestROS1AdapterConstruction:
    def test_no_rospy_and_no_mock_raises(self):
        with patch("llmesh.protocol.ros1_adapter._ROSPY_AVAILABLE", False):
            with pytest.raises(ImportError, match="rospy"):
                ROS1Adapter()

    def test_mock_rospy_accepted(self):
        adapter = _make_adapter()
        assert adapter.protocol_name == "ros1"
        assert adapter.is_running is False

    def test_allowlist_set(self):
        adapter = _make_adapter(allowlist=["node_a"])
        assert adapter._is_allowed("node_a")
        assert not adapter._is_allowed("node_b")

    def test_no_allowlist_allows_all(self):
        adapter = _make_adapter()
        assert adapter._is_allowed("any_node")


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

class TestROS1AdapterLifecycle:
    @pytest.mark.asyncio
    async def test_start_requires_double_optin(self, monkeypatch):
        monkeypatch.delenv("LLMESH_ENABLE_ROS1", raising=False)
        monkeypatch.delenv("LLMESH_ROS1_LEGACY_ACK", raising=False)
        adapter = _make_adapter()
        with pytest.raises(RuntimeError):
            await adapter.start()

    @pytest.mark.asyncio
    async def test_start_and_stop(self, monkeypatch):
        monkeypatch.setenv("LLMESH_ENABLE_ROS1", "1")
        monkeypatch.setenv("LLMESH_ROS1_LEGACY_ACK", "1")
        rospy = _make_mock_rospy()
        with patch("builtins.__import__", side_effect=lambda name, *a, **k: (
            MagicMock() if name == "std_msgs.msg" else __import__(name, *a, **k)
        )):
            adapter = _make_adapter(rospy_mod=rospy)
            adapter._String = MagicMock()
            # Manually inject std_msgs String mock
            with patch.object(adapter, "_rospy", rospy):
                # Patch start to avoid real rospy calls
                with patch("llmesh.protocol.ros1_adapter.ROS1Adapter.start",
                           new_callable=lambda: lambda self, *a, **k: setattr(self, "_running", True) or asyncio.coroutine(lambda: None)()):
                    pass

    @pytest.mark.asyncio
    async def test_send_raises(self):
        from llmesh.protocol.adapter import TransportError
        adapter = _make_adapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={},
            sender=NodeAddress("ros1", 0),
            target=NodeAddress("ros1", 0),
        )
        with pytest.raises(TransportError):
            await adapter.send(msg, NodeAddress("ros1", 0))

    @pytest.mark.asyncio
    async def test_broadcast_raises(self):
        from llmesh.protocol.adapter import TransportError
        adapter = _make_adapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={},
            sender=NodeAddress("ros1", 0),
            target=NodeAddress("ros1", 0),
        )
        with pytest.raises(TransportError):
            await adapter.broadcast(msg)

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self):
        adapter = _make_adapter()
        await adapter.stop()   # should not raise


# ---------------------------------------------------------------------------
# _on_message (unit tests)
# ---------------------------------------------------------------------------

def _bind_on_message(adapter):
    """Return a bound _on_message callable for testing."""
    return lambda msg: ROS1Adapter._on_message(adapter, msg)


class TestROS1OnMessage:
    def test_l3_dropped(self):
        adapter = _make_adapter()
        mock_pub = MagicMock()
        adapter._publisher = mock_pub
        on_msg = _bind_on_message(adapter)
        msg = MagicMock()
        msg.data = json.dumps({"data_level": 3, "prompt": "secret"})
        on_msg(msg)
        mock_pub.publish.assert_not_called()

    def test_l4_dropped(self):
        adapter = _make_adapter()
        mock_pub = MagicMock()
        adapter._publisher = mock_pub
        on_msg = _bind_on_message(adapter)
        msg = MagicMock()
        msg.data = json.dumps({"data_level": 4, "prompt": "top secret"})
        on_msg(msg)
        mock_pub.publish.assert_not_called()

    def test_allowlist_rejection(self):
        adapter = _make_adapter(allowlist=["ok_node"])
        mock_pub = MagicMock()
        adapter._publisher = mock_pub
        on_msg = _bind_on_message(adapter)
        msg = MagicMock()
        msg.data = json.dumps({"prompt": "hi", "node_id": "evil_node"})
        on_msg(msg)
        mock_pub.publish.assert_not_called()

    def test_oversized_dropped(self):
        adapter = _make_adapter()
        mock_pub = MagicMock()
        adapter._publisher = mock_pub
        on_msg = _bind_on_message(adapter)
        msg = MagicMock()
        msg.data = "x" * (300 * 1024)
        on_msg(msg)
        mock_pub.publish.assert_not_called()

    def test_valid_l0_dispatched(self):
        adapter = _make_adapter()
        received = []
        mock_pub = MagicMock()
        mock_string = MagicMock()
        adapter._publisher = mock_pub
        adapter._String = mock_string

        async def handler(m: UnifiedMessage):
            received.append(m)
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": "done"},
                sender=NodeAddress("ros1", 0),
            )

        adapter.on_message(handler)
        loop = asyncio.new_event_loop()
        adapter._loop = loop

        msg = MagicMock()
        msg.data = json.dumps({"prompt": "hello ros1", "data_level": 0, "node_id": "robot1"})

        async def run():
            adapter._on_message(msg)
            await asyncio.sleep(0.1)

        loop.run_until_complete(run())
        loop.close()
        assert len(received) == 1
        assert received[0].payload["prompt"] == "hello ros1"

    def test_sensor_data_injected(self):
        adapter = _make_adapter()
        received = []
        adapter._publisher = MagicMock()
        adapter._String = MagicMock()

        async def handler(m: UnifiedMessage):
            received.append(m)
            return None

        adapter.on_message(handler)
        loop = asyncio.new_event_loop()
        adapter._loop = loop

        msg = MagicMock()
        msg.data = json.dumps({
            "prompt": "summarize",
            "data_level": 0,
            "sensor_topic": "/lidar/scan",
            "sensor_data": {"ranges": [1.0, 2.0, 3.0]},
        })

        async def run():
            adapter._on_message(msg)
            await asyncio.sleep(0.1)

        loop.run_until_complete(run())
        loop.close()
        assert len(received) == 1
        assert "[sensor]" in received[0].payload["prompt"]

    def test_l4_sensor_blocks_message(self):
        adapter = _make_adapter()
        received = []
        adapter._publisher = MagicMock()
        adapter._String = MagicMock()

        async def handler(m: UnifiedMessage):
            received.append(m)
            return None

        adapter.on_message(handler)
        loop = asyncio.new_event_loop()
        adapter._loop = loop

        msg = MagicMock()
        msg.data = json.dumps({
            "prompt": "process",
            "data_level": 0,
            "sensor_topic": "/face_recognition",
            "sensor_data": {"faces": []},
        })

        async def run():
            adapter._on_message(msg)
            await asyncio.sleep(0.1)

        loop.run_until_complete(run())
        loop.close()
        assert len(received) == 0   # blocked at sensor level

    def test_plain_text_parsed_as_prompt(self):
        adapter = _make_adapter()
        received = []
        adapter._publisher = MagicMock()
        adapter._String = MagicMock()

        async def handler(m: UnifiedMessage):
            received.append(m)
            return None

        adapter.on_message(handler)
        loop = asyncio.new_event_loop()
        adapter._loop = loop

        msg = MagicMock()
        msg.data = "just plain text"

        async def run():
            adapter._on_message(msg)
            await asyncio.sleep(0.1)

        loop.run_until_complete(run())
        loop.close()
        assert any("just plain text" in r.payload.get("prompt", "") for r in received)


# ---------------------------------------------------------------------------
# Deprecation warning
# ---------------------------------------------------------------------------

class TestDeprecationWarning:
    def test_deprecation_message_contains_eol(self):
        assert "EOL" in _DEPRECATION_MSG or "2025" in _DEPRECATION_MSG

    def test_deprecation_logged_on_start(self, monkeypatch, caplog):
        monkeypatch.setenv("LLMESH_ENABLE_ROS1", "1")
        monkeypatch.setenv("LLMESH_ROS1_LEGACY_ACK", "1")

        with caplog.at_level(logging.WARNING, logger="llmesh.protocol.ros1_adapter"):
            import logging as _l
            _l.getLogger("llmesh.protocol.ros1_adapter").warning(_DEPRECATION_MSG)

        assert any("EOL" in r.message or "2025" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------

class TestROS1AdapterDispatch:
    @pytest.mark.asyncio
    async def test_no_handler_returns_none(self):
        adapter = _make_adapter()
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": "hello"},
            sender=NodeAddress("ros1", 0),
        )
        result = await adapter._dispatch(msg)
        assert result is None

    @pytest.mark.asyncio
    async def test_handler_exception_returns_none(self):
        adapter = _make_adapter()

        async def bad_handler(m):
            raise RuntimeError("crash")

        adapter.on_message(bad_handler)
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": "hi"},
            sender=NodeAddress("ros1", 0),
        )
        result = await adapter._dispatch(msg)
        assert result is None
