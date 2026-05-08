"""Tests for HTTPAdapter — server and client using FastAPI TestClient."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from llmesh.protocol import NodeAddress, TransportError, UnifiedMessage
from llmesh.protocol.http_adapter import HTTPAdapter
from llmesh.protocol.message import MessageType


def _sender() -> NodeAddress:
    return NodeAddress("127.0.0.1", 8000, node_id="sender")


def _target() -> NodeAddress:
    return NodeAddress("127.0.0.1", 8001, node_id="target")


# ------------------------------------------------------------------
# Server-side: /msg endpoint via FastAPI TestClient
# ------------------------------------------------------------------

class TestHTTPAdapterServer:
    def _make_adapter_and_client(self):
        adapter = HTTPAdapter()
        tc = TestClient(adapter._app, raise_server_exceptions=True)
        return adapter, tc

    def test_msg_endpoint_registered(self):
        adapter = HTTPAdapter()
        tc = TestClient(adapter._app, raise_server_exceptions=False)
        msg = UnifiedMessage.request({"x": 1}, _sender(), _target())
        resp = tc.post("/msg", json=msg.to_dict())
        assert resp.status_code == 200

    def test_handler_receives_message(self):
        adapter = HTTPAdapter()
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> None:
            received.append(msg)

        adapter.on_message(handler)
        tc = TestClient(adapter._app)
        msg = UnifiedMessage.request({"tool": "gen"}, _sender(), _target())
        tc.post("/msg", json=msg.to_dict())
        assert len(received) == 1
        assert received[0].payload == {"tool": "gen"}

    def test_handler_response_returned(self):
        adapter = HTTPAdapter()
        src = _sender()
        tgt = _target()

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"result": "ok"}, sender=tgt)

        adapter.on_message(handler)
        tc = TestClient(adapter._app)
        msg = UnifiedMessage.request({"q": "hello"}, src, tgt)
        resp = tc.post("/msg", json=msg.to_dict())
        assert resp.status_code == 200
        data = resp.json()
        assert data["payload"]["result"] == "ok"
        assert data["type"] == "response"
        assert data["correlation_id"] == msg.id

    def test_no_handler_returns_empty(self):
        adapter = HTTPAdapter()
        tc = TestClient(adapter._app)
        msg = UnifiedMessage.request({}, _sender())
        resp = tc.post("/msg", json=msg.to_dict())
        assert resp.status_code == 200
        assert resp.json() == {}

    def test_attaches_to_existing_app(self):
        from fastapi import FastAPI

        existing = FastAPI()

        @existing.get("/custom")
        def custom():
            return {"ok": True}

        adapter = HTTPAdapter(app=existing)
        tc = TestClient(existing)
        # Existing endpoint still works
        assert tc.get("/custom").json() == {"ok": True}
        # /msg endpoint added
        msg = UnifiedMessage.request({}, _sender())
        assert tc.post("/msg", json=msg.to_dict()).status_code == 200

    def test_malformed_json_returns_422(self):
        adapter = HTTPAdapter()
        tc = TestClient(adapter._app, raise_server_exceptions=False)
        resp = tc.post("/msg", content=b"not-json", headers={"Content-Type": "application/json"})
        assert resp.status_code == 422

    def test_protocol_name(self):
        assert HTTPAdapter().protocol_name == "http"

    def test_is_running_false_before_start(self):
        assert not HTTPAdapter().is_running

    def test_error_response_type(self):
        adapter = HTTPAdapter()
        tgt = _target()

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"err": "oops"}, sender=tgt, error=True)

        adapter.on_message(handler)
        tc = TestClient(adapter._app)
        msg = UnifiedMessage.request({}, _sender(), tgt)
        resp = tc.post("/msg", json=msg.to_dict())
        assert resp.json()["type"] == "error"


# ------------------------------------------------------------------
# Client-side: send() using urllib (mocked)
# ------------------------------------------------------------------

class TestHTTPAdapterClient:
    async def test_send_raises_on_connection_error(self):
        adapter = HTTPAdapter()
        msg = UnifiedMessage.request({}, _sender(), _target())
        target = NodeAddress("127.0.0.1", 19999)

        with pytest.raises(TransportError, match="http|url"):
            await adapter.send(msg, target)

    async def test_broadcast_swallows_errors(self):
        """broadcast() should not raise even when sends fail."""
        adapter = HTTPAdapter()
        msg = UnifiedMessage.broadcast({}, _sender())
        targets = [NodeAddress("127.0.0.1", 19990), NodeAddress("127.0.0.1", 19991)]
        await adapter.broadcast(msg, targets)

    async def test_broadcast_no_targets_noop(self):
        adapter = HTTPAdapter()
        msg = UnifiedMessage.broadcast({}, _sender())
        await adapter.broadcast(msg, [])
