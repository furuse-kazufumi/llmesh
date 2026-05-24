"""Tests for the predictive-push egress sinks."""
from __future__ import annotations

import io
import json

import pytest

from llmesh.predictive_push import (
    CallbackSink,
    JsonlSink,
    MqttPushSink,
    PushFrame,
    SsePushSink,
)


def _frame() -> PushFrame:
    return PushFrame(
        kind="diff",
        incident_id="inc-0001",
        ops=[{"op": "replace", "path": "/root/children/0/text", "value": "x"}],
        prediction_error=1,
        meta={"speculated": True},
    )


def test_callback_sink_forwards_frame():
    seen: list[PushFrame] = []
    CallbackSink(seen.append).push(_frame())
    assert len(seen) == 1 and seen[0].incident_id == "inc-0001"


def test_callback_sink_rejects_non_callable():
    with pytest.raises(TypeError):
        CallbackSink(123)  # type: ignore[arg-type]


def test_jsonl_sink_writes_one_json_line_per_frame():
    buf = io.StringIO()
    sink = JsonlSink(buf)
    sink.push(_frame())
    sink.push(_frame())
    lines = buf.getvalue().splitlines()
    assert len(lines) == 2
    payload = json.loads(lines[0])
    assert payload["kind"] == "diff"
    assert payload["incident_id"] == "inc-0001"
    assert payload["ops"][0]["op"] == "replace"


def test_jsonl_sink_rejects_non_stream():
    with pytest.raises(TypeError):
        JsonlSink(object())  # type: ignore[arg-type]


class _FakeMqttClient:
    def __init__(self) -> None:
        self.published: list[tuple[str, str, int]] = []

    def publish(self, topic: str, payload: str, qos: int = 0) -> None:
        self.published.append((topic, payload, qos))


def test_mqtt_push_sink_publishes_json_to_topic():
    client = _FakeMqttClient()
    sink = MqttPushSink(topic="factory/alarms", client=client, qos=1)
    sink.push(_frame())
    assert len(client.published) == 1
    topic, payload, qos = client.published[0]
    assert topic == "factory/alarms" and qos == 1
    assert json.loads(payload)["incident_id"] == "inc-0001"


def test_mqtt_push_sink_validates_topic():
    with pytest.raises(ValueError):
        MqttPushSink(topic="", client=_FakeMqttClient())
    with pytest.raises(ValueError):
        MqttPushSink(topic="bad\x00topic", client=_FakeMqttClient())
