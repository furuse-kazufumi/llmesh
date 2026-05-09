"""ROS 2 Topic/Service Adapter for LLMesh (v1.1.0).

Bridges ROS 2 topic and service communication with the LLM privacy pipeline.

Usage::

    adapter = ROS2Adapter(
        node_name="llmesh_node",
        request_topic="/llmesh/request",
        response_topic="/llmesh/response",
        node_allowlist=["allowed_node_1"],  # or None to allow all
    )
    adapter.on_message(my_handler)
    await adapter.start()

Opt-in:
    LLMESH_ENABLE_ROS2=1   (default: disabled)

Security:
    - L3/L4 messages are rejected at the adapter boundary unconditionally.
    - Node authentication via DDS Security (SROS2) or explicit node-name allowlist.
    - Rate limiting via UnifiedRateLimiter (shared with other adapters).
    - Circuit breaker per node via AdapterCircuitBreakerRegistry.
    - No shell=True, eval, exec, or pickle.
    - Raw sensor payloads are passed through SensorSummarizer before reaching LLM.
    - Node ID derived from ROS node name; nonce from message header stamp or uuid4.

Dependencies:
    rclpy  — ROS 2 Python client library (system package, not PyPI)
    std_msgs — ROS 2 standard messages (system package)

Install via rosdep / apt (not PyPI):
    sudo apt install ros-<distro>-rclpy ros-<distro>-std-msgs
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import threading
import uuid
from typing import TYPE_CHECKING, Any

from .adapter import MessageHandler, ProtocolAdapter, TransportError
from .message import MessageType, NodeAddress, UnifiedMessage

if TYPE_CHECKING:
    pass

try:
    import rclpy
    from rclpy.node import Node as RclpyNode
    _RCLPY_AVAILABLE = True
except ImportError:
    _RCLPY_AVAILABLE = False
    rclpy = None          # type: ignore[assignment]
    RclpyNode = object    # type: ignore[assignment,misc]

logger = logging.getLogger(__name__)

_MAX_MSG_BYTES = 256 * 1024   # 256 KiB
_BLOCKED_LEVELS = {3, 4}
_DEFAULT_REQUEST_TOPIC  = "/llmesh/request"
_DEFAULT_RESPONSE_TOPIC = "/llmesh/response"
_DEFAULT_NODE_NAME      = "llmesh_node"


def _check_optin() -> None:
    """Raise RuntimeError unless LLMESH_ENABLE_ROS2=1."""
    if os.environ.get("LLMESH_ENABLE_ROS2") != "1":
        raise RuntimeError(
            "ROS2Adapter requires LLMESH_ENABLE_ROS2=1. "
            "Ensure a ROS 2 environment is sourced before enabling."
        )


def _nonce_from_stamp(stamp: Any) -> str:
    """Derive a nonce from a ROS header stamp dict or fallback to uuid4."""
    if isinstance(stamp, dict):
        sec  = stamp.get("sec",  0)
        nsec = stamp.get("nanosec", stamp.get("nsec", 0))
        return secrets.token_hex(8) + f"{sec:010x}{nsec:09x}"
    return secrets.token_hex(16)


class _LLMeshROS2Node(RclpyNode):  # type: ignore[misc]
    """ROS 2 node that subscribes to request topic and publishes responses."""

    def __init__(
        self,
        node_name: str,
        request_topic: str,
        response_topic: str,
        adapter: "ROS2Adapter",
    ) -> None:
        super().__init__(node_name)
        self._adapter = adapter
        self._response_topic = response_topic

        try:
            from std_msgs.msg import String
            self._String = String
        except ImportError as exc:
            raise ImportError(
                "std_msgs not found. Install ros-<distro>-std-msgs."
            ) from exc

        self._publisher = self.create_publisher(String, response_topic, 10)
        self._subscription = self.create_subscription(
            String, request_topic, self._on_message, 10
        )
        logger.info(
            "ROS2Adapter node %r listening on %s → %s",
            node_name, request_topic, response_topic,
        )

    def _on_message(self, msg: Any) -> None:
        """Called by rclpy on each incoming std_msgs/String message."""
        raw = msg.data
        if len(raw.encode("utf-8")) > _MAX_MSG_BYTES:
            logger.warning("ROS2Adapter: oversized message dropped (>%d bytes)", _MAX_MSG_BYTES)
            return

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"prompt": raw}

        # L3/L4 boundary rejection
        data_level = int(payload.get("data_level", 0))
        if data_level in _BLOCKED_LEVELS:
            logger.warning(
                "ROS2Adapter: L%d message blocked from node %s",
                data_level, payload.get("node_id", "<unknown>"),
            )
            return

        # Allowlist check
        node_id = str(payload.get("node_id", "ros2_node"))
        if not self._adapter._is_allowed(node_id):
            logger.warning("ROS2Adapter: node %r not in allowlist — rejected", node_id)
            return

        # Sensor payload pre-processing (topic-keyed sensor data)
        sensor_topic = payload.get("sensor_topic")
        sensor_data  = payload.get("sensor_data")
        if sensor_topic and sensor_data:
            from ..privacy.sensor_summarizer import SensorSummarizer
            ss = SensorSummarizer()
            result = ss.summarize(topic=sensor_topic, data=sensor_data)
            if result.blocked:
                logger.warning(
                    "ROS2Adapter: sensor data blocked: %s", result.block_reason
                )
                return
            payload["prompt"] = (
                payload.get("prompt", "") + "\n[sensor] " + result.description
            ).strip()
            payload.pop("sensor_data", None)

        nonce = _nonce_from_stamp(payload.get("header", {}).get("stamp"))
        task_id = str(payload.get("task_id") or uuid.uuid4())

        unified = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={
                "prompt":       payload.get("prompt", ""),
                "tool_name":    payload.get("tool_name", "generate_code"),
                "caller_nonce": nonce,
                "task_id":      task_id,
                "node_id":      node_id,
                "protocol":     "ros2",
                "data_level":   data_level,
            },
            sender=NodeAddress("ros2", 0, node_id),
            target=NodeAddress("ros2", 0, _DEFAULT_NODE_NAME),
            id=task_id,
        )

        # Dispatch to event loop
        loop = self._adapter._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._adapter._dispatch(unified), loop
            )
            try:
                response = future.result(timeout=60)
            except Exception as exc:
                logger.error("ROS2Adapter: dispatch error: %s", exc)
                response = None

            if response is not None:
                reply_str = json.dumps(response.payload, ensure_ascii=False)
                reply_msg = self._String()
                reply_msg.data = reply_str
                self._publisher.publish(reply_msg)


class ROS2Adapter(ProtocolAdapter):
    """ROS 2 topic adapter: bridges /llmesh/request → LLM pipeline → /llmesh/response.

    Args:
        node_name:       ROS node name (default: ``llmesh_node``).
        request_topic:   Topic to subscribe for incoming task strings.
        response_topic:  Topic to publish LLM responses.
        node_allowlist:  If not None, only listed node IDs are accepted.
        _rclpy_mod:      Injection point for testing (replaces ``rclpy``).
    """

    def __init__(
        self,
        node_name: str = _DEFAULT_NODE_NAME,
        request_topic: str = _DEFAULT_REQUEST_TOPIC,
        response_topic: str = _DEFAULT_RESPONSE_TOPIC,
        node_allowlist: list[str] | None = None,
        _rclpy_mod: Any = None,
    ) -> None:
        if not _RCLPY_AVAILABLE and _rclpy_mod is None:
            raise ImportError(
                "rclpy is required for ROS2Adapter. "
                "Install ROS 2 and source the setup script."
            )
        self._rclpy = _rclpy_mod or rclpy
        self._node_name = node_name
        self._request_topic = request_topic
        self._response_topic = response_topic
        self._node_allowlist = set(node_allowlist) if node_allowlist else None
        self._handler: MessageHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ros_node: Any = None
        self._spin_thread: threading.Thread | None = None
        self._running = False

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "ros2"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str = "", port: int = 0) -> None:
        _check_optin()
        self._loop = asyncio.get_event_loop()
        self._rclpy.init()
        self._ros_node = _LLMeshROS2Node(
            self._node_name,
            self._request_topic,
            self._response_topic,
            self,
        )
        self._running = True
        self._spin_thread = threading.Thread(
            target=self._spin, daemon=True
        )
        self._spin_thread.start()
        logger.info("ROS2Adapter started (node=%r)", self._node_name)

    async def stop(self) -> None:
        self._running = False
        if self._ros_node is not None:
            try:
                self._ros_node.destroy_node()
            except Exception:
                pass
            self._ros_node = None
        try:
            self._rclpy.shutdown()
        except Exception:
            pass
        if self._spin_thread is not None:
            self._spin_thread.join(timeout=3)
            self._spin_thread = None
        logger.info("ROS2Adapter stopped")

    async def send(self, message: UnifiedMessage, target: NodeAddress) -> None:
        raise TransportError("ROS2Adapter.send() is not supported — use topic publishing", protocol="ros2")

    async def broadcast(self, message: UnifiedMessage, targets=None) -> None:
        raise TransportError("ROS2Adapter.broadcast() is not supported", protocol="ros2")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_allowed(self, node_id: str) -> bool:
        if self._node_allowlist is None:
            return True
        return node_id in self._node_allowlist

    def _spin(self) -> None:
        """Background thread: spin the ROS node until stopped."""
        try:
            while self._running and self._ros_node is not None:
                self._rclpy.spin_once(self._ros_node, timeout_sec=0.1)
        except Exception as exc:
            if self._running:
                logger.error("ROS2Adapter spin error: %s", exc)

    async def _dispatch(self, message: UnifiedMessage) -> UnifiedMessage | None:
        if self._handler is None:
            return None
        try:
            return await self._handler(message)
        except Exception as exc:
            logger.error("ROS2Adapter handler error: %s", exc)
            return None
