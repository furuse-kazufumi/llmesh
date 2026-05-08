"""ROS 1 Topic Adapter for LLMesh (v1.1.0) — Opt-in, legacy support only.

Bridges ROS 1 (Noetic) topic communication with the LLM privacy pipeline.

WARNING: ROS 1 reached EOL in May 2025.  This adapter is provided for
         short-term migration support only.  Prefer ROS2Adapter.

Double opt-in required:
    LLMESH_ENABLE_ROS1=1       — explicitly enable ROS 1 support
    LLMESH_ROS1_LEGACY_ACK=1   — acknowledge that ROS 1 is EOL

Security:
    - L3/L4 messages are rejected unconditionally at adapter boundary.
    - Node allowlist via LLMESH_ROS1_ALLOWLIST (comma-separated node names).
    - No shell=True, eval, exec, or pickle.
    - Deprecation warning logged every time the adapter starts.

Dependencies:
    rospy — ROS 1 Python client library (system package, Noetic only)

Install via apt:
    sudo apt install ros-noetic-rospy
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
    import rospy
    _ROSPY_AVAILABLE = True
except ImportError:
    _ROSPY_AVAILABLE = False
    rospy = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_MAX_MSG_BYTES = 256 * 1024
_BLOCKED_LEVELS = {3, 4}
_DEFAULT_REQUEST_TOPIC  = "/llmesh/request"
_DEFAULT_RESPONSE_TOPIC = "/llmesh/response"
_DEFAULT_NODE_NAME      = "llmesh_ros1_node"

_DEPRECATION_MSG = (
    "ROS1Adapter: WARNING — ROS 1 reached EOL May 2025. "
    "Migrate to ROS 2 and use ROS2Adapter. "
    "This adapter will be removed in LLMesh v2.0."
)


def _check_double_optin() -> None:
    """Raise RuntimeError unless both opt-in vars are set to '1'."""
    enable = os.environ.get("LLMESH_ENABLE_ROS1", "")
    ack    = os.environ.get("LLMESH_ROS1_LEGACY_ACK", "")
    if enable != "1" or ack != "1":
        raise RuntimeError(
            "ROS1Adapter requires LLMESH_ENABLE_ROS1=1 AND "
            "LLMESH_ROS1_LEGACY_ACK=1. "
            "ROS 1 is EOL (May 2025). Prefer ROS2Adapter."
        )


class ROS1Adapter(ProtocolAdapter):
    """ROS 1 topic adapter: /llmesh/request → LLM pipeline → /llmesh/response.

    Args:
        node_name:       ROS node name.
        request_topic:   Topic to subscribe for incoming task strings.
        response_topic:  Topic to publish LLM responses.
        node_allowlist:  If not None, only listed node names are accepted.
        _rospy_mod:      Injection point for testing (replaces ``rospy``).
    """

    def __init__(
        self,
        node_name: str = _DEFAULT_NODE_NAME,
        request_topic: str = _DEFAULT_REQUEST_TOPIC,
        response_topic: str = _DEFAULT_RESPONSE_TOPIC,
        node_allowlist: list[str] | None = None,
        _rospy_mod: Any = None,
    ) -> None:
        if not _ROSPY_AVAILABLE and _rospy_mod is None:
            raise ImportError(
                "rospy is required for ROS1Adapter. "
                "Install ROS Noetic: sudo apt install ros-noetic-rospy"
            )
        self._rospy = _rospy_mod or rospy
        self._node_name = node_name
        self._request_topic = request_topic
        self._response_topic = response_topic
        self._node_allowlist = set(node_allowlist) if node_allowlist else None
        self._handler: MessageHandler | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._publisher: Any = None
        self._subscriber: Any = None
        self._spin_thread: threading.Thread | None = None
        self._running = False

    # ------------------------------------------------------------------
    # ProtocolAdapter interface
    # ------------------------------------------------------------------

    @property
    def protocol_name(self) -> str:
        return "ros1"

    @property
    def is_running(self) -> bool:
        return self._running

    def on_message(self, handler: MessageHandler) -> None:
        self._handler = handler

    async def start(self, host: str = "", port: int = 0) -> None:
        _check_double_optin()
        logger.warning(_DEPRECATION_MSG)
        self._loop = asyncio.get_event_loop()

        try:
            from std_msgs.msg import String  # type: ignore[import]
            self._String = String
        except ImportError as exc:
            raise ImportError(
                "std_msgs not found. Install: sudo apt install ros-noetic-std-msgs"
            ) from exc

        self._rospy.init_node(self._node_name, anonymous=False, disable_signals=True)
        self._publisher = self._rospy.Publisher(
            self._response_topic, self._String, queue_size=10
        )
        self._subscriber = self._rospy.Subscriber(
            self._request_topic, self._String, self._on_message
        )
        self._running = True
        self._spin_thread = threading.Thread(target=self._spin, daemon=True)
        self._spin_thread.start()
        logger.info("ROS1Adapter started (node=%r)", self._node_name)

    async def stop(self) -> None:
        self._running = False
        if self._subscriber is not None:
            try:
                self._subscriber.unregister()
            except Exception:
                pass
            self._subscriber = None
        if self._publisher is not None:
            try:
                self._publisher.unregister()
            except Exception:
                pass
            self._publisher = None
        try:
            self._rospy.signal_shutdown("ROS1Adapter.stop()")
        except Exception:
            pass
        if self._spin_thread is not None:
            self._spin_thread.join(timeout=3)
            self._spin_thread = None
        logger.info("ROS1Adapter stopped")

    async def send(self, message: UnifiedMessage, target: NodeAddress) -> None:
        raise TransportError("ROS1Adapter.send() is not supported", protocol="ros1")

    async def broadcast(self, message: UnifiedMessage, targets=None) -> None:
        raise TransportError("ROS1Adapter.broadcast() is not supported", protocol="ros1")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _is_allowed(self, node_id: str) -> bool:
        if self._node_allowlist is None:
            return True
        return node_id in self._node_allowlist

    def _on_message(self, msg: Any) -> None:
        raw = msg.data
        if len(raw.encode("utf-8")) > _MAX_MSG_BYTES:
            logger.warning("ROS1Adapter: oversized message dropped")
            return

        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {"prompt": raw}

        data_level = int(payload.get("data_level", 0))
        if data_level in _BLOCKED_LEVELS:
            logger.warning("ROS1Adapter: L%d message blocked", data_level)
            return

        node_id = str(payload.get("node_id", "ros1_node"))
        if not self._is_allowed(node_id):
            logger.warning("ROS1Adapter: node %r not in allowlist — rejected", node_id)
            return

        # Sensor payload pre-processing
        sensor_topic = payload.get("sensor_topic")
        sensor_data  = payload.get("sensor_data")
        if sensor_topic and sensor_data:
            from ..privacy.sensor_summarizer import SensorSummarizer
            ss = SensorSummarizer()
            result = ss.summarize(topic=sensor_topic, data=sensor_data)
            if result.blocked:
                logger.warning("ROS1Adapter: sensor data blocked: %s", result.block_reason)
                return
            payload["prompt"] = (
                payload.get("prompt", "") + "\n[sensor] " + result.description
            ).strip()
            payload.pop("sensor_data", None)

        nonce   = secrets.token_hex(16)
        task_id = str(payload.get("task_id") or uuid.uuid4())

        unified = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={
                "prompt":       payload.get("prompt", ""),
                "tool_name":    payload.get("tool_name", "generate_code"),
                "caller_nonce": nonce,
                "task_id":      task_id,
                "node_id":      node_id,
                "protocol":     "ros1",
                "data_level":   data_level,
            },
            sender=NodeAddress("ros1", 0, node_id),
            target=NodeAddress("ros1", 0, _DEFAULT_NODE_NAME),
            id=task_id,
        )

        loop = self._loop
        if loop is not None and loop.is_running():
            future = asyncio.run_coroutine_threadsafe(
                self._dispatch(unified), loop
            )
            try:
                response = future.result(timeout=60)
            except Exception as exc:
                logger.error("ROS1Adapter: dispatch error: %s", exc)
                response = None

            if response is not None and self._publisher is not None:
                reply_msg = self._String()
                reply_msg.data = json.dumps(response.payload, ensure_ascii=False)
                self._publisher.publish(reply_msg)

    def _spin(self) -> None:
        try:
            self._rospy.spin()
        except Exception as exc:
            if self._running:
                logger.error("ROS1Adapter spin error: %s", exc)

    async def _dispatch(self, message: UnifiedMessage) -> UnifiedMessage | None:
        if self._handler is None:
            return None
        try:
            return await self._handler(message)
        except Exception as exc:
            logger.error("ROS1Adapter handler error: %s", exc)
            return None
