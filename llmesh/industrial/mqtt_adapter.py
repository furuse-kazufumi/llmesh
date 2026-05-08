"""MQTTAdapter — MQTT broker client for LLMesh Industrial (v1.6.0).

Connects to an MQTT broker (v3.1.1 or v5.0), subscribes to configured topics,
and emits each incoming message as a SensorEvent for the unified industrial
pipeline.

Usage::

    adapter = MQTTAdapter("broker.local", 1883)
    adapter.add_topic(
        topic="factory/smt01/pressure",
        sensor_id="pressure_01",
        sensor_type="pressure",
        unit="Pa",
    )
    adapter.on_event(lambda ev: print(ev))
    await adapter.start()
    # ... messages received until stop() is called
    await adapter.stop()

Wildcard topics are supported::

    adapter.add_topic("factory/+/temperature", sensor_id="temp_any", ...)
    adapter.add_topic("factory/#", sensor_id="all_factory", ...)

TLS::

    import ssl
    ctx = ssl.create_default_context()
    adapter = MQTTAdapter("broker.local", 8883, tls_context=ctx)

Security invariants
-------------------
- No shell=True, eval, exec, pickle anywhere.
- Topic names are validated (no null bytes, length ≤ 65535).
- paho-mqtt is an optional dependency; import errors produce a clear message.
- TLS enabled via ssl.SSLContext — never plain-text credentials over the wire.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from llmesh.industrial.sensor_event import Priority, SensorEvent

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional paho-mqtt import
# ---------------------------------------------------------------------------

try:
    import paho.mqtt.client as _paho
    # paho-mqtt 2.0 requires explicit CallbackAPIVersion
    try:
        from paho.mqtt.client import CallbackAPIVersion as _CallbackAPIVersion
        _PAHO_V2 = True
    except ImportError:
        _CallbackAPIVersion = None  # type: ignore[assignment, misc]
        _PAHO_V2 = False
    _PAHO_AVAILABLE = True
except ImportError:
    _paho = None                   # type: ignore[assignment]
    _CallbackAPIVersion = None     # type: ignore[assignment]
    _PAHO_V2 = False
    _PAHO_AVAILABLE = False


# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

@dataclass
class TopicSpec:
    """Configuration for a single MQTT topic subscription."""

    topic: str            # e.g. "factory/smt01/pressure" or "sensors/+/temp"
    sensor_id: str
    sensor_type: str = ""
    unit: str = ""
    device_id: str = ""
    qos: int = 0          # 0, 1, or 2
    priority: Priority = Priority.NORMAL
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.topic or len(self.topic.encode()) > 65535:
            raise ValueError("topic must be non-empty and ≤ 65535 bytes")
        if "\x00" in self.topic:
            raise ValueError("topic must not contain null bytes")
        if self.qos not in (0, 1, 2):
            raise ValueError(f"qos must be 0, 1, or 2, got {self.qos}")


EventCallback = Callable[[SensorEvent], None]


# ---------------------------------------------------------------------------
# MQTTAdapter
# ---------------------------------------------------------------------------

class MQTTAdapter:
    """Subscribe to MQTT topics and emit SensorEvents.

    Parameters
    ----------
    host:
        Broker hostname or IP address.
    port:
        Broker TCP port (default 1883; 8883 for TLS).
    client_id:
        MQTT client identifier. Auto-generated if empty.
    keepalive_s:
        Keepalive interval in seconds.
    reconnect_delay_s:
        Seconds to wait before retrying after a connection failure.
    tls_context:
        Optional :class:`ssl.SSLContext` for TLS connections.
    username:
        Optional MQTT username.
    password:
        Optional MQTT password.
    """

    _DEFAULT_PORT = 1883
    _DEFAULT_KEEPALIVE = 60
    _DEFAULT_RECONNECT_S = 5.0

    def __init__(
        self,
        host: str,
        port: int = _DEFAULT_PORT,
        *,
        client_id: str = "",
        keepalive_s: int = _DEFAULT_KEEPALIVE,
        reconnect_delay_s: float = _DEFAULT_RECONNECT_S,
        tls_context: ssl.SSLContext | None = None,
        username: str = "",
        password: str = "",
    ) -> None:
        if not _PAHO_AVAILABLE:
            raise RuntimeError(
                "paho-mqtt is not installed — run: pip install llmesh[industrial]"
            )
        self._host = host
        self._port = port
        self._keepalive_s = keepalive_s
        self._reconnect_delay_s = reconnect_delay_s
        self._tls_context = tls_context
        self._username = username
        self._password = password
        self._specs: list[TopicSpec] = []
        self._callbacks: list[EventCallback] = []
        self._client: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._connected = False
        self._connect_event = threading.Event()
        self._task: asyncio.Task | None = None   # type: ignore[type-arg]
        self._client_id = client_id

    # ------------------------------------------------------------------
    # Configuration
    # ------------------------------------------------------------------

    def add_topic(
        self,
        topic: str,
        sensor_id: str,
        *,
        sensor_type: str = "",
        unit: str = "",
        device_id: str = "",
        qos: int = 0,
        priority: Priority = Priority.NORMAL,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Register an MQTT topic pattern to subscribe to."""
        spec = TopicSpec(
            topic=topic,
            sensor_id=sensor_id,
            sensor_type=sensor_type,
            unit=unit,
            device_id=device_id,
            qos=qos,
            priority=priority,
            metadata=dict(metadata) if metadata else {},
        )
        self._specs.append(spec)

    def on_event(self, callback: EventCallback) -> None:
        """Register a callback invoked with each new SensorEvent."""
        self._callbacks.append(callback)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Connect to MQTT broker and begin receiving messages. Non-blocking."""
        if self._running:
            return
        self._running = True
        self._loop = asyncio.get_event_loop()
        self._client = self._build_client()
        self._task = asyncio.create_task(self._connect_loop(), name="mqtt_connect")

    async def stop(self) -> None:
        """Disconnect from broker and stop message processing."""
        self._running = False
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                pass
            self._client = None
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    # ------------------------------------------------------------------
    # Internal — client setup
    # ------------------------------------------------------------------

    def _build_client(self) -> Any:
        if _PAHO_V2:
            client = _paho.Client(
                callback_api_version=_CallbackAPIVersion.VERSION2,
                client_id=self._client_id,
            )
        else:
            client = _paho.Client(client_id=self._client_id)

        if self._tls_context is not None:
            client.tls_set_context(self._tls_context)

        if self._username:
            client.username_pw_set(self._username, self._password or None)

        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        return client

    # ------------------------------------------------------------------
    # Internal — asyncio connect loop
    # ------------------------------------------------------------------

    async def _connect_loop(self) -> None:
        while self._running:
            try:
                self._connect_event.clear()
                self._connected = False
                self._client.connect(self._host, self._port, keepalive=self._keepalive_s)
                self._client.loop_start()
                # Wait for on_connect (up to reconnect_delay_s)
                await asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: self._connect_event.wait(timeout=self._reconnect_delay_s),
                )
                if not self._connected:
                    logger.warning(
                        "MQTTAdapter: no CONNACK from %s:%d — retrying in %ss",
                        self._host, self._port, self._reconnect_delay_s,
                    )
                    try:
                        self._client.loop_stop()
                    except Exception:
                        pass
                    await asyncio.sleep(self._reconnect_delay_s)
                    continue
                # Connection is up; keep alive until disconnected or stop()
                while self._running and self._connected:
                    await asyncio.sleep(1.0)
                if self._running and not self._connected:
                    logger.info(
                        "MQTTAdapter: disconnected — reconnecting in %ss",
                        self._reconnect_delay_s,
                    )
                    try:
                        self._client.loop_stop()
                    except Exception:
                        pass
                    await asyncio.sleep(self._reconnect_delay_s)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("MQTTAdapter connect error: %s — retrying in %ss", exc, self._reconnect_delay_s)
                try:
                    self._client.loop_stop()
                except Exception:
                    pass
                await asyncio.sleep(self._reconnect_delay_s)

    # ------------------------------------------------------------------
    # Internal — paho callbacks (called from paho network thread)
    # ------------------------------------------------------------------

    def _on_connect(self, *args: Any) -> None:
        # paho v2: (client, userdata, connect_flags, reason_code, properties)
        # paho v1: (client, userdata, flags, rc)
        self._connected = True
        self._connect_event.set()
        for spec in self._specs:
            try:
                self._client.subscribe(spec.topic, qos=spec.qos)
            except Exception as exc:
                logger.error("MQTTAdapter subscribe error for %r: %s", spec.topic, exc)
        logger.info(
            "MQTTAdapter: connected to %s:%d, subscribed %d topics",
            self._host, self._port, len(self._specs),
        )

    def _on_disconnect(self, *args: Any) -> None:
        self._connected = False
        logger.info("MQTTAdapter: disconnected from %s:%d", self._host, self._port)

    def _on_message(self, *args: Any) -> None:
        # paho v2: (client, userdata, message)
        # paho v1: (client, userdata, message)
        # message is always the last argument in both versions
        msg = args[-1]
        topic = msg.topic
        payload_bytes: bytes = msg.payload if isinstance(msg.payload, bytes) else str(msg.payload).encode()

        spec = self._best_match(topic)
        if spec is None:
            return

        meta = dict(spec.metadata)
        meta.update({
            "topic": topic,
            "qos": msg.qos,
            "retain": msg.retain,
        })

        event = SensorEvent.create(
            sensor_id=spec.sensor_id,
            protocol="mqtt",
            payload=payload_bytes,
            priority=spec.priority,
            device_id=spec.device_id,
            sensor_type=spec.sensor_type,
            unit=spec.unit,
            metadata=meta,
        )

        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(self._emit, event)
        else:
            self._emit(event)

    def _best_match(self, topic: str) -> TopicSpec | None:
        """Return the first registered TopicSpec whose pattern matches *topic*."""
        for spec in self._specs:
            if _mqtt_topic_match(spec.topic, topic):
                return spec
        return None

    def _emit(self, event: SensorEvent) -> None:
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception as exc:
                logger.error("MQTTAdapter callback error: %s", exc)


# ---------------------------------------------------------------------------
# MQTT topic pattern matching (MQTT §4.7)
# ---------------------------------------------------------------------------

def _mqtt_topic_match(pattern: str, topic: str) -> bool:
    """Return True if *topic* matches MQTT wildcard *pattern*.

    MQTT uses ``+`` (single level) and ``#`` (multi level, must be last).
    """
    if pattern == topic:
        return True
    if "#" in pattern:
        prefix = pattern[: pattern.index("#")]
        return topic.startswith(prefix) or topic == prefix.rstrip("/")
    # Convert MQTT '+' wildcards to fnmatch '?' — but '+' matches a full level
    # so we replace '/' boundaries carefully.
    parts_p = pattern.split("/")
    parts_t = topic.split("/")
    if len(parts_p) != len(parts_t):
        return False
    return all(p == "+" or p == t for p, t in zip(parts_p, parts_t))
