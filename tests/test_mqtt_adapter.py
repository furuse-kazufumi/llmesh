"""Tests for MQTTAdapter (v1.6.0) — paho-mqtt mocked throughout."""
from __future__ import annotations

import asyncio
import sys
import threading
from unittest.mock import MagicMock, patch, call
import pytest

from llmesh.industrial.sensor_event import Priority, SensorEvent


# ---------------------------------------------------------------------------
# Helpers — fake paho-mqtt objects
# ---------------------------------------------------------------------------

def _make_fake_paho():
    fake = MagicMock()

    class FakeCallbackAPIVersion:
        VERSION2 = "v2"

    class FakeMessage:
        def __init__(self, topic: str, payload: bytes, qos: int = 0, retain: bool = False):
            self.topic = topic
            self.payload = payload
            self.qos = qos
            self.retain = retain

    class FakeClient:
        def __init__(self, callback_api_version=None, client_id=""):
            self._on_connect = None
            self._on_disconnect = None
            self._on_message = None
            self._subscriptions: list[tuple[str, int]] = []
            self._loop_started = False
            self._connected = False

        @property
        def on_connect(self):
            return self._on_connect

        @on_connect.setter
        def on_connect(self, fn):
            self._on_connect = fn

        @property
        def on_disconnect(self):
            return self._on_disconnect

        @on_disconnect.setter
        def on_disconnect(self, fn):
            self._on_disconnect = fn

        @property
        def on_message(self):
            return self._on_message

        @on_message.setter
        def on_message(self, fn):
            self._on_message = fn

        def tls_set_context(self, ctx):
            pass

        def username_pw_set(self, username, password=None):
            pass

        def connect(self, host, port, keepalive=60):
            self._connected = True

        def disconnect(self):
            self._connected = False

        def loop_start(self):
            self._loop_started = True

        def loop_stop(self):
            self._loop_started = False

        def subscribe(self, topic, qos=0):
            self._subscriptions.append((topic, qos))

        def simulate_connect(self):
            if self._on_connect:
                self._on_connect(self, None, None, None, None)

        def simulate_message(self, topic: str, payload: bytes, qos: int = 0):
            if self._on_message:
                msg = FakeMessage(topic, payload, qos)
                self._on_message(self, None, msg)

        def simulate_disconnect(self):
            if self._on_disconnect:
                self._on_disconnect(self, None, None, None)

    fake.Client = FakeClient
    fake.CallbackAPIVersion = FakeCallbackAPIVersion
    return fake, FakeMessage


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_paho():
    fake, FakeMessage = _make_fake_paho()
    with patch.dict(sys.modules, {"paho": fake, "paho.mqtt": fake.mqtt, "paho.mqtt.client": fake}):
        import llmesh.industrial.mqtt_adapter as mod
        mod._PAHO_AVAILABLE = True
        mod._PAHO_V2 = True
        mod._paho = fake
        mod._CallbackAPIVersion = fake.CallbackAPIVersion
        yield fake, mod, FakeMessage


# ---------------------------------------------------------------------------
# Unit tests — TopicSpec validation
# ---------------------------------------------------------------------------

class TestTopicSpec:
    def test_basic(self):
        from llmesh.industrial.mqtt_adapter import TopicSpec
        spec = TopicSpec(topic="factory/temp", sensor_id="t1")
        assert spec.topic == "factory/temp"
        assert spec.qos == 0

    def test_empty_topic_raises(self):
        from llmesh.industrial.mqtt_adapter import TopicSpec
        with pytest.raises(ValueError, match="non-empty"):
            TopicSpec(topic="", sensor_id="s1")

    def test_null_byte_raises(self):
        from llmesh.industrial.mqtt_adapter import TopicSpec
        with pytest.raises(ValueError, match="null"):
            TopicSpec(topic="bad\x00topic", sensor_id="s1")

    def test_invalid_qos_raises(self):
        from llmesh.industrial.mqtt_adapter import TopicSpec
        with pytest.raises(ValueError, match="qos"):
            TopicSpec(topic="t", sensor_id="s1", qos=3)


# ---------------------------------------------------------------------------
# Unit tests — _mqtt_topic_match
# ---------------------------------------------------------------------------

class TestMqttTopicMatch:
    def test_exact_match(self):
        from llmesh.industrial.mqtt_adapter import _mqtt_topic_match
        assert _mqtt_topic_match("a/b/c", "a/b/c") is True

    def test_no_match(self):
        from llmesh.industrial.mqtt_adapter import _mqtt_topic_match
        assert _mqtt_topic_match("a/b/c", "a/b/d") is False

    def test_single_wildcard(self):
        from llmesh.industrial.mqtt_adapter import _mqtt_topic_match
        assert _mqtt_topic_match("a/+/c", "a/b/c") is True
        assert _mqtt_topic_match("a/+/c", "a/b/d") is False

    def test_multi_wildcard(self):
        from llmesh.industrial.mqtt_adapter import _mqtt_topic_match
        assert _mqtt_topic_match("a/#", "a/b/c/d") is True
        assert _mqtt_topic_match("factory/#", "factory/line1/temp") is True

    def test_multi_wildcard_exact_prefix(self):
        from llmesh.industrial.mqtt_adapter import _mqtt_topic_match
        assert _mqtt_topic_match("a/#", "a") is True

    def test_level_mismatch(self):
        from llmesh.industrial.mqtt_adapter import _mqtt_topic_match
        assert _mqtt_topic_match("a/b", "a/b/c") is False


# ---------------------------------------------------------------------------
# Unit tests — MQTTAdapter construction
# ---------------------------------------------------------------------------

class TestMQTTAdapterConstruct:
    def test_requires_paho(self):
        import llmesh.industrial.mqtt_adapter as mod
        old = mod._PAHO_AVAILABLE
        mod._PAHO_AVAILABLE = False
        try:
            with pytest.raises(RuntimeError, match="paho-mqtt"):
                mod.MQTTAdapter("localhost")
        finally:
            mod._PAHO_AVAILABLE = old

    def test_defaults(self, fake_paho):
        _, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost")
        assert adapter._host == "localhost"
        assert adapter._port == 1883

    def test_custom_port(self, fake_paho):
        _, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("broker.local", 8883)
        assert adapter._port == 8883


# ---------------------------------------------------------------------------
# Unit tests — add_topic / on_event
# ---------------------------------------------------------------------------

class TestMQTTAdapterConfig:
    def test_add_topic(self, fake_paho):
        _, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost")
        adapter.add_topic("factory/temp", "temp_01", sensor_type="temperature", unit="°C")
        assert len(adapter._specs) == 1

    def test_add_multiple_topics(self, fake_paho):
        _, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost")
        adapter.add_topic("t/a", "s1")
        adapter.add_topic("t/b", "s2")
        assert len(adapter._specs) == 2

    def test_on_event_registers(self, fake_paho):
        _, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost")
        cb = MagicMock()
        adapter.on_event(cb)
        assert cb in adapter._callbacks


# ---------------------------------------------------------------------------
# Unit tests — message processing
# ---------------------------------------------------------------------------

class TestMQTTAdapterMessages:
    def _make_adapter(self, mod):
        adapter = mod.MQTTAdapter("localhost", reconnect_delay_s=0.01)
        adapter.add_topic("factory/smt01/pressure", "pressure_01",
                          sensor_type="pressure", unit="Pa", device_id="smt01")
        return adapter

    def test_message_fires_callback(self, fake_paho):
        fake, mod, FakeMessage = fake_paho
        adapter = self._make_adapter(mod)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter._loop = asyncio.new_event_loop()

        msg = FakeMessage("factory/smt01/pressure", b"101325", qos=1)
        adapter._on_message(None, None, msg)

        assert len(events) == 1
        ev = events[0]
        assert ev.sensor_id == "pressure_01"
        assert ev.protocol == "mqtt"
        assert ev.payload == b"101325"
        assert ev.metadata["topic"] == "factory/smt01/pressure"
        assert ev.metadata["qos"] == 1

    def test_unregistered_topic_ignored(self, fake_paho):
        fake, mod, FakeMessage = fake_paho
        adapter = self._make_adapter(mod)
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter._loop = asyncio.new_event_loop()

        msg = FakeMessage("other/topic", b"data")
        adapter._on_message(None, None, msg)
        assert events == []

    def test_wildcard_topic_matches(self, fake_paho):
        fake, mod, FakeMessage = fake_paho
        adapter = mod.MQTTAdapter("localhost")
        adapter.add_topic("factory/+/temp", "temp_any", sensor_type="temperature")
        events: list[SensorEvent] = []
        adapter.on_event(events.append)
        adapter._loop = asyncio.new_event_loop()

        msg = FakeMessage("factory/smt01/temp", b"25.3")
        adapter._on_message(None, None, msg)
        assert events[0].sensor_id == "temp_any"

    def test_callback_exception_does_not_crash(self, fake_paho):
        fake, mod, FakeMessage = fake_paho
        adapter = self._make_adapter(mod)
        adapter.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
        adapter._loop = asyncio.new_event_loop()

        msg = FakeMessage("factory/smt01/pressure", b"0")
        adapter._on_message(None, None, msg)  # must not raise

    def test_on_connect_subscribes_topics(self, fake_paho):
        fake, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost")
        adapter.add_topic("t/a", "s1", qos=1)
        adapter.add_topic("t/b", "s2", qos=2)

        fake_client = fake.Client()
        adapter._client = fake_client
        adapter._on_connect(fake_client, None, None, None, None)

        assert ("t/a", 1) in fake_client._subscriptions
        assert ("t/b", 2) in fake_client._subscriptions

    def test_on_disconnect_clears_connected(self, fake_paho):
        fake, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost")
        adapter._connected = True
        adapter._on_disconnect(None, None, None, None)
        assert adapter._connected is False


# ---------------------------------------------------------------------------
# Async tests — lifecycle
# ---------------------------------------------------------------------------

class TestMQTTAdapterLifecycle:
    @pytest.mark.asyncio
    async def test_start_creates_task(self, fake_paho):
        fake, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost", reconnect_delay_s=0.01)
        adapter.add_topic("t/a", "s1")

        # Patch connect_loop so it returns immediately
        async def _noop():
            pass

        adapter._connect_loop = _noop
        await adapter.start()
        assert adapter._running is True
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_double_start_idempotent(self, fake_paho):
        fake, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost", reconnect_delay_s=0.01)

        async def _noop():
            pass

        adapter._connect_loop = _noop
        await adapter.start()
        t1 = adapter._task
        await adapter.start()
        assert adapter._task is t1
        await adapter.stop()

    @pytest.mark.asyncio
    async def test_stop_clears_running(self, fake_paho):
        fake, mod, _ = fake_paho
        adapter = mod.MQTTAdapter("localhost", reconnect_delay_s=0.01)

        async def _noop():
            pass

        adapter._connect_loop = _noop
        await adapter.start()
        await adapter.stop()
        assert adapter._running is False
