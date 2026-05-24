"""Concrete egress sinks for predictive-coding push frames.

``InMemorySink`` (in ``transport``) is for tests; these are the real-transport
bridges. Following the LLMesh optional-extras rule, the base sinks need only the
stdlib — heavy transports (MQTT) are optional and fail with a clear message when
their dependency is absent.

- :class:`CallbackSink` — the universal bridge: hands each frame to a callable, so
  a host application can forward it over WebSocket / SSE / its own bus.
- :class:`JsonlSink` — appends each frame as one JSON line to a writable stream
  (file, socket-backed file object, ``io.StringIO``). A genuine, dependency-free
  typed diff-stream that a consumer can tail and replay.
- :class:`SsePushSink` — formats each frame as a Server-Sent Events message
  (``event:`` / ``id:`` / ``data:``) on a writable text stream. The browser-facing
  side-channel from the compat note: a consumer opens an ``EventSource`` and
  applies each diff as it arrives. Pure stdlib — the host owns the HTTP response
  and the ``text/event-stream`` content type; this sink only frames bytes.
- :class:`MqttPushSink` — publishes frames to an MQTT topic (optional, needs
  ``paho-mqtt``). The natural industrial side-channel from the compat note.
"""
from __future__ import annotations

import json
from typing import Any, Callable, TextIO

from .transport import PushFrame, PushSink


class CallbackSink(PushSink):
    """Forward each frame to a user-supplied callable (host wires the transport)."""

    def __init__(self, callback: Callable[[PushFrame], None]) -> None:
        if not callable(callback):
            raise TypeError("callback must be callable")
        self._callback = callback

    def push(self, frame: PushFrame) -> None:
        self._callback(frame)


class JsonlSink(PushSink):
    """Write each frame as one JSON line to a writable text stream."""

    def __init__(self, stream: TextIO) -> None:
        if not hasattr(stream, "write"):
            raise TypeError("stream must be a writable text stream")
        self._stream = stream

    def push(self, frame: PushFrame) -> None:
        self._stream.write(json.dumps(frame.to_payload(), ensure_ascii=False) + "\n")
        flush = getattr(self._stream, "flush", None)
        if callable(flush):
            flush()


def _sse_safe(value: str) -> str:
    """Strip CR/LF from an SSE field value to prevent frame-injection.

    SSE delimits fields by ``\\n`` and messages by a blank line, so a stray
    newline in ``event``/``id`` would let a crafted value forge extra events.
    The JSON ``data`` payload is safe already — :func:`json.dumps` escapes any
    embedded newline — so only the structural fields need sanitising. Mirrors the
    fail-closed posture of :class:`MqttPushSink`'s topic validation.
    """
    return value.replace("\r", "").replace("\n", "")


class SsePushSink(PushSink):
    """Write each frame as a Server-Sent Events message to a text stream.

    SSE is the browser-facing twin of :class:`MqttPushSink`: a consumer opens an
    ``EventSource`` over a long-lived HTTP response and receives each confirmed
    frame as it happens, applying the typed diff against its held speculative
    document. Pure stdlib — the *host* owns the HTTP response, the
    ``text/event-stream`` content type, and connection lifecycle; this sink only
    formats one message per frame.

    Wire format (per the WHATWG SSE spec)::

        event: <kind>
        id: <incident_id>
        data: <single-line JSON payload>
        <blank line>

    The ``data`` payload is the same JSON wire form the other sinks emit
    (:meth:`PushFrame.to_payload`); :func:`json.dumps` keeps it on one line, so a
    single ``data:`` field suffices and the consumer parses it with ``JSON.parse``.
    """

    def __init__(self, stream: TextIO) -> None:
        if not hasattr(stream, "write"):
            raise TypeError("stream must be a writable text stream")
        self._stream = stream

    def push(self, frame: PushFrame) -> None:
        payload = json.dumps(frame.to_payload(), ensure_ascii=False)
        msg = (
            f"event: {_sse_safe(frame.kind)}\n"
            f"id: {_sse_safe(frame.incident_id)}\n"
            f"data: {payload}\n\n"
        )
        self._stream.write(msg)
        flush = getattr(self._stream, "flush", None)
        if callable(flush):
            flush()


class MqttPushSink(PushSink):
    """Publish frames to an MQTT topic (optional — requires ``paho-mqtt``).

    A pre-built client may be injected (for tests / shared connections); otherwise
    a paho client is created and connected to ``host:port``. Mirrors
    :mod:`llmesh.industrial.mqtt_adapter`'s optional-dependency handling: importing
    this module never requires paho — only constructing a *non-injected* sink does.
    """

    def __init__(
        self,
        *,
        topic: str,
        host: str = "localhost",
        port: int = 1883,
        qos: int = 0,
        client: Any = None,
    ) -> None:
        if not topic or "\x00" in topic or len(topic) > 65535:
            raise ValueError("invalid MQTT topic")
        self._topic = topic
        self._qos = int(qos)

        if client is not None:
            self._client = client
            return
        try:
            import paho.mqtt.client as _paho  # noqa: WPS433 (optional dependency)
        except ImportError as exc:  # pragma: no cover - exercised only without paho
            raise ImportError(
                "MqttPushSink requires paho-mqtt. Install it (pip install paho-mqtt) "
                "or inject a client= for testing."
            ) from exc
        self._client = _paho.Client()
        self._client.connect(host, port)

    def push(self, frame: PushFrame) -> None:
        payload = json.dumps(frame.to_payload(), ensure_ascii=False)
        self._client.publish(self._topic, payload, qos=self._qos)


__all__ = ["CallbackSink", "JsonlSink", "MqttPushSink"]
