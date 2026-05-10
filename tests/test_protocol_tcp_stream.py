"""Tests for TCPStreamAdapter — persistent connections with ReliableStream chunking."""
from __future__ import annotations

import asyncio
import socket
import time

import pytest

from llmesh.protocol import (
    AdapterRegistry,
    NodeAddress,
    TCPStreamAdapter,
    TransportError,
    UnifiedMessage,
)
from llmesh.protocol.message import MessageType
from llmesh.protocol.reliable_stream import ReliableStream
from llmesh.protocol.tcp_stream_adapter import (
    _TICK_INTERVAL,
    _ConnAdapter,
    _ConnPool,
    _DEFAULT_POOL_SIZE,
    _tick_loop,
)

from helpers import _alloc_port


# ------------------------------------------------------------------
# Unit: basic properties
# ------------------------------------------------------------------

class TestTCPStreamAdapterProperties:
    def test_protocol_name(self):
        assert TCPStreamAdapter().protocol_name == "tcp_stream"

    def test_not_running_before_start(self):
        assert not TCPStreamAdapter().is_running

    def test_registered_in_registry(self):
        assert AdapterRegistry.create("tcp_stream").protocol_name == "tcp_stream"

    def test_timeout_kwarg_accepted(self):
        assert isinstance(AdapterRegistry.create("tcp_stream", timeout=30), TCPStreamAdapter)

    def test_extra_kwargs_ignored(self):
        adapter = TCPStreamAdapter(timeout=10, unknown_param="x")
        assert adapter._read_timeout == 10.0


# ------------------------------------------------------------------
# Unit: _ConnAdapter
# ------------------------------------------------------------------

class TestConnAdapter:
    def test_protocol_name_and_is_running(self):
        class _FakeWriter:
            def is_closing(self): return False
        ca = _ConnAdapter(_FakeWriter())  # type: ignore[arg-type]
        assert ca.protocol_name == "_conn"
        assert ca.is_running is True

    async def test_no_op_methods(self):
        class _FakeWriter:
            def is_closing(self): return False
        ca = _ConnAdapter(_FakeWriter())  # type: ignore[arg-type]
        await ca.start("h", 1)
        await ca.stop()
        await ca.broadcast(UnifiedMessage.request({}, NodeAddress("h", 1)))
        ca.on_message(lambda m: None)  # type: ignore[arg-type]


# ------------------------------------------------------------------
# Integration: start / stop
# ------------------------------------------------------------------

class TestStartStop:
    async def test_start_stop(self, free_port):
        adapter = TCPStreamAdapter()
        await adapter.start("127.0.0.1", free_port)
        assert adapter.is_running
        await adapter.stop()
        assert not adapter.is_running

    async def test_stop_without_start(self):
        await TCPStreamAdapter().stop()


# ------------------------------------------------------------------
# Integration: round-trip (small payload)
# ------------------------------------------------------------------

class TestRoundTripSmall:
    async def test_echo_small_payload(self, free_port, sender):
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def echo(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"echo": msg.payload}, sender=server_addr)

        server.on_message(echo)
        await server.start("127.0.0.1", free_port)
        req = UnifiedMessage.request({"tool": "ping", "body": {"data": "hello"}}, sender, server_addr)
        response = await TCPStreamAdapter().send(req, server_addr)
        await server.stop()

        assert response is not None
        assert response.type == MessageType.RESPONSE
        assert response.payload["echo"]["tool"] == "ping"

    async def test_response_payload_structure_preserved(self, free_port, sender):
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response(
                {"result": {"answer": 42}, "caller_nonce_echo": "abc"}, sender=server_addr
            )

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)
        response = await TCPStreamAdapter().send(
            UnifiedMessage.request({}, sender, server_addr), server_addr
        )
        await server.stop()

        assert response is not None
        assert response.payload["result"] == {"answer": 42}
        assert response.payload["caller_nonce_echo"] == "abc"


# ------------------------------------------------------------------
# Integration: round-trip (large payload)
# ------------------------------------------------------------------

class TestRoundTripLarge:
    async def test_large_request_payload(self, free_port, sender):
        large_data = "x" * (300 * 1024)
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            body_len = len(msg.payload.get("body", {}).get("data", ""))
            return msg.make_response({"body_len": body_len}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)
        response = await TCPStreamAdapter().send(
            UnifiedMessage.request({"tool": "t", "body": {"data": large_data}}, sender, server_addr),
            server_addr,
        )
        await server.stop()
        assert response is not None
        assert response.payload["body_len"] == len(large_data)

    async def test_large_response_payload(self, free_port, sender):
        large_response = "y" * (300 * 1024)
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"data": large_response}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)
        response = await TCPStreamAdapter().send(
            UnifiedMessage.request({}, sender, server_addr), server_addr
        )
        await server.stop()
        assert response is not None
        assert response.payload["data"] == large_response

    async def test_both_directions_large(self, free_port, sender):
        large_req, large_resp = "A" * (350 * 1024), "B" * (350 * 1024)
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            assert msg.payload.get("data") == large_req
            return msg.make_response({"data": large_resp}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)
        response = await TCPStreamAdapter().send(
            UnifiedMessage.request({"data": large_req}, sender, server_addr), server_addr
        )
        await server.stop()
        assert response is not None
        assert response.payload["data"] == large_resp


# ------------------------------------------------------------------
# Integration: persistent connection reuse
# ------------------------------------------------------------------

class TestPersistentConnection:
    async def test_connection_reused_across_calls(self, free_port, sender):
        call_count = [0]
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            call_count[0] += 1
            return msg.make_response({"n": call_count[0]}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter()
        r1 = await client.send(UnifiedMessage.request({}, sender, server_addr), server_addr)
        r2 = await client.send(UnifiedMessage.request({}, sender, server_addr), server_addr)
        pool = client._pools.get(("127.0.0.1", free_port))
        await server.stop()

        assert r1 is not None and r1.payload["n"] == 1
        assert r2 is not None and r2.payload["n"] == 2
        assert pool is not None and pool._total == 1

    async def test_multiple_sequential_requests(self, free_port, sender):
        N = 10
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"n": msg.payload["n"]}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter()
        results = []
        for i in range(N):
            r = await client.send(UnifiedMessage.request({"n": i}, sender, server_addr), server_addr)
            assert r is not None
            results.append(r.payload["n"])

        await server.stop()
        assert results == list(range(N))


# ------------------------------------------------------------------
# Integration: no handler → no response
# ------------------------------------------------------------------

class TestNoHandler:
    async def test_server_no_handler_raises(self, free_port, sender):
        server = TCPStreamAdapter()
        await server.start("127.0.0.1", free_port)
        with pytest.raises(TransportError):
            await TCPStreamAdapter(timeout=1.0).send(
                UnifiedMessage.request({}, sender, NodeAddress("127.0.0.1", free_port)),
                NodeAddress("127.0.0.1", free_port),
            )
        await server.stop()


# ------------------------------------------------------------------
# Integration: error handling
# ------------------------------------------------------------------

class TestErrorHandling:
    async def test_send_to_closed_port_raises(self, sender):
        with pytest.raises(TransportError):
            await TCPStreamAdapter().send(
                UnifiedMessage.request({}, sender), NodeAddress("127.0.0.1", 19955)
            )

    async def test_stale_connection_replaced(self, free_port, sender):
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"ok": True}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter()
        r1 = await client.send(UnifiedMessage.request({}, sender, server_addr), server_addr)
        assert r1 is not None

        await server.stop()
        server2 = TCPStreamAdapter()
        server2.on_message(handler)
        await server2.start("127.0.0.1", free_port)

        pool = client._pools.get(("127.0.0.1", free_port))
        if pool:
            try:
                conn = pool._queue.get_nowait()
                conn.writer.close()
            except asyncio.QueueEmpty:
                pass

        r2 = await client.send(UnifiedMessage.request({}, sender, server_addr), server_addr)
        await server2.stop()
        assert r2 is not None and r2.payload["ok"] is True


# ------------------------------------------------------------------
# Integration: broadcast
# ------------------------------------------------------------------

class TestBroadcast:
    async def test_broadcast_to_multiple_targets(self, sender):
        port1, port2 = _alloc_port(), _alloc_port()
        received: list[int] = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            received.append(msg.payload.get("n", 0))
            return msg.make_response({}, sender=NodeAddress("127.0.0.1", 0))

        s1, s2 = TCPStreamAdapter(), TCPStreamAdapter()
        s1.on_message(handler)
        s2.on_message(handler)
        await s1.start("127.0.0.1", port1)
        await s2.start("127.0.0.1", port2)

        await TCPStreamAdapter().broadcast(
            UnifiedMessage.request({"n": 7}, sender),
            [NodeAddress("127.0.0.1", port1), NodeAddress("127.0.0.1", port2)],
        )
        await asyncio.sleep(0.2)
        await s1.stop()
        await s2.stop()
        assert sorted(received) == [7, 7]

    async def test_broadcast_ignores_errors(self, sender):
        await TCPStreamAdapter().broadcast(
            UnifiedMessage.request({}, sender),
            [NodeAddress("127.0.0.1", 19960), NodeAddress("127.0.0.1", 19961)],
        )

    async def test_broadcast_empty_targets(self, sender):
        client = TCPStreamAdapter()
        await client.broadcast(UnifiedMessage.request({}, sender), targets=[])
        await client.broadcast(UnifiedMessage.request({}, sender), targets=None)


# ------------------------------------------------------------------
# Integration: NodeClient with protocol="tcp_stream"
# ------------------------------------------------------------------

class TestNodeClientIntegration:
    def test_node_client_tcp_stream_roundtrip(self):
        import threading
        from llmesh.orchestrator.node_client import NodeClient

        port = _alloc_port()
        server_addr = NodeAddress("127.0.0.1", port)
        ready = threading.Event()
        stop_ev = threading.Event()
        results: list = []

        def _run_server_thread():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            async def _serve():
                server = TCPStreamAdapter()

                async def handler(msg: UnifiedMessage) -> UnifiedMessage:
                    tool = msg.payload.get("tool", "")
                    body = msg.payload.get("body", {})
                    return msg.make_response(
                        {
                            "result": {"tool_called": tool},
                            "caller_nonce_echo": body.get("caller_nonce", ""),
                            "task_id": body.get("task_id", "t1"),
                            "node_id": "test-node",
                        },
                        sender=server_addr,
                    )

                server.on_message(handler)
                await server.start("127.0.0.1", port)
                ready.set()
                while not stop_ev.is_set():
                    await asyncio.sleep(0.05)
                await server.stop()

            loop.run_until_complete(_serve())
            loop.close()

        t = threading.Thread(target=_run_server_thread, daemon=True)
        t.start()
        ready.wait(timeout=5)

        try:
            result = NodeClient(protocol="tcp_stream", timeout=10).call(
                endpoint=f"127.0.0.1:{port}",
                tool_name="generate_code",
                body={"caller_nonce": "abc123", "task_id": "t1", "prompt": "hello"},
                node_id="test-node",
            )
            results.append(result)
        finally:
            stop_ev.set()
            t.join(timeout=5)

        assert results[0] == {"tool_called": "generate_code"}


# ------------------------------------------------------------------
# Integration: tick loop
# ------------------------------------------------------------------

class TestTickLoop:
    async def test_tick_loop_runs_and_exits_on_cancel(self):
        tick_count = [0]
        addr = NodeAddress("127.0.0.1", 0)
        stream = ReliableStream(sender=addr)
        original_tick = stream.tick

        async def counting_tick(*, adapter=None, now=None):
            tick_count[0] += 1
            await original_tick(adapter=adapter, now=now)

        stream.tick = counting_tick  # type: ignore[method-assign]

        class _FakeAdapter:
            protocol_name = "_fake"
            is_running = True
            def on_message(self, h): pass
            async def start(self, h, p): pass
            async def stop(self): pass
            async def send(self, m, t=None): pass
            async def broadcast(self, m, t=None): pass

        task = asyncio.create_task(_tick_loop(stream, _FakeAdapter()))  # type: ignore[arg-type]
        await asyncio.sleep(_TICK_INTERVAL * 2.5)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        count_at_cancel = tick_count[0]
        assert count_at_cancel >= 2
        await asyncio.sleep(_TICK_INTERVAL * 1.2)
        assert tick_count[0] == count_at_cancel

    async def test_tick_called_during_server_connection(self, free_port, sender):
        from unittest.mock import patch
        tick_count = [0]
        original_tick = ReliableStream.tick

        async def counting_tick(self_stream, *, adapter=None, now=None):
            tick_count[0] += 1
            await original_tick(self_stream, adapter=adapter, now=now)

        with patch.object(ReliableStream, "tick", counting_tick):
            server = TCPStreamAdapter()
            server_addr = NodeAddress("127.0.0.1", free_port)

            async def echo(msg):
                return msg.make_response({"ok": True}, sender=server_addr)

            server.on_message(echo)
            await server.start("127.0.0.1", free_port)
            # Keep a reference to the client so its connection pool stays alive;
            # an ephemeral TCPStreamAdapter() would be GC'd immediately, closing
            # the TCP socket and causing the server's tick_task to be cancelled
            # before the first tick fires.
            client = TCPStreamAdapter()
            r = await client.send(
                UnifiedMessage.request({}, sender, server_addr), server_addr
            )
            assert r is not None
            # Poll for at least 2 ticks; CI runners are slower than dev machines.
            deadline = time.monotonic() + _TICK_INTERVAL * 8
            while tick_count[0] < 2 and time.monotonic() < deadline:
                await asyncio.sleep(_TICK_INTERVAL * 0.5)
            await server.stop()

        assert tick_count[0] >= 2

    async def test_roundtrip_unaffected_by_tick(self, free_port, sender):
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg):
            return msg.make_response({"seq": msg.payload["seq"]}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter()
        r1 = await client.send(UnifiedMessage.request({"seq": 1}, sender, server_addr), server_addr)
        await asyncio.sleep(_TICK_INTERVAL * 1.2)
        r2 = await client.send(UnifiedMessage.request({"seq": 2}, sender, server_addr), server_addr)
        await server.stop()

        assert r1 is not None and r1.payload["seq"] == 1
        assert r2 is not None and r2.payload["seq"] == 2


# ------------------------------------------------------------------
# Integration: stop() cancels idle handler tasks
# ------------------------------------------------------------------

class TestStopCancelsHandlers:
    async def test_stop_cancels_idle_handler_quickly(self, free_port, sender):
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def echo(msg):
            return msg.make_response({"ok": True}, sender=server_addr)

        server.on_message(echo)
        await server.start("127.0.0.1", free_port)

        r = await TCPStreamAdapter().send(
            UnifiedMessage.request({}, sender, server_addr), server_addr
        )
        assert r is not None
        # Poll for the handler task to appear; CI runners may schedule slowly.
        deadline = time.monotonic() + 2.0
        while len(server._handler_tasks) < 1 and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert len(server._handler_tasks) >= 1

        t0 = time.monotonic()
        await server.stop()
        assert time.monotonic() - t0 < 5.0
        assert not server.is_running
        assert len(server._handler_tasks) == 0

    async def test_handler_tasks_cleaned_up_after_natural_disconnect(self, free_port, sender):
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def echo(msg):
            return msg.make_response({"ok": True}, sender=server_addr)

        server.on_message(echo)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter()
        r = await client.send(UnifiedMessage.request({}, sender, server_addr), server_addr)
        assert r is not None

        for pool in client._pools.values():
            await pool.close_all()
        await asyncio.sleep(0.2)
        await server.stop()
        assert len(server._handler_tasks) == 0


# ------------------------------------------------------------------
# Integration: connection pool
# ------------------------------------------------------------------

class TestConnectionPool:
    def test_pool_size_kwarg_accepted(self):
        assert TCPStreamAdapter(pool_size=2)._pool_size == 2

    def test_default_pool_size(self):
        assert TCPStreamAdapter()._pool_size == _DEFAULT_POOL_SIZE

    async def test_concurrent_requests_create_multiple_connections(self, free_port, sender):
        N = 3
        in_flight = [0]
        max_in_flight = [0]
        server = TCPStreamAdapter(pool_size=N)
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def slow_handler(msg: UnifiedMessage) -> UnifiedMessage:
            in_flight[0] += 1
            max_in_flight[0] = max(max_in_flight[0], in_flight[0])
            await asyncio.sleep(0.1)
            in_flight[0] -= 1
            return msg.make_response({"n": msg.payload["n"]}, sender=server_addr)

        server.on_message(slow_handler)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter(pool_size=N)
        results = await asyncio.gather(*[
            client.send(UnifiedMessage.request({"n": i}, sender, server_addr), server_addr)
            for i in range(N)
        ])
        await server.stop()

        assert all(r is not None for r in results)
        assert {r.payload["n"] for r in results} == set(range(N))
        assert max_in_flight[0] == N
        pool = client._pools.get(("127.0.0.1", free_port))
        assert pool is not None and pool._total == N

    async def test_pool_connections_reused_after_concurrent_burst(self, free_port, sender):
        N = 3
        server = TCPStreamAdapter(pool_size=N)
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def echo(msg: UnifiedMessage) -> UnifiedMessage:
            return msg.make_response({"ok": True}, sender=server_addr)

        server.on_message(echo)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter(pool_size=N)
        await asyncio.gather(*[
            client.send(UnifiedMessage.request({}, sender, server_addr), server_addr)
            for _ in range(N)
        ])
        pool = client._pools.get(("127.0.0.1", free_port))
        assert pool is not None
        total_after_burst = pool._total
        assert total_after_burst == N

        for _ in range(N):
            r = await client.send(UnifiedMessage.request({}, sender, server_addr), server_addr)
            assert r is not None

        await server.stop()
        assert pool._total == total_after_burst

    async def test_pool_size_one_behaves_like_original(self, free_port, sender):
        call_order: list[int] = []
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port)

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            call_order.append(msg.payload["n"])
            return msg.make_response({}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)

        client = TCPStreamAdapter(pool_size=1)
        for i in range(5):
            await client.send(UnifiedMessage.request({"n": i}, sender, server_addr), server_addr)

        pool = client._pools.get(("127.0.0.1", free_port))
        await server.stop()
        assert call_order == list(range(5))
        assert pool is not None and pool._total == 1
