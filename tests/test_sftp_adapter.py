"""Tests for SFTPAdapter — virtual FS and file-based prompt/result exchange."""
from __future__ import annotations

import asyncio
import time
import uuid

import paramiko
import pytest

from llmesh.protocol import (
    AdapterRegistry,
    NodeAddress,
    SFTPAdapter,
    TransportError,
    UnifiedMessage,
)
from llmesh.protocol._key_utils import generate_ed25519_key
from llmesh.protocol.message import MessageType
from llmesh.protocol.sftp_adapter import (
    _InMemoryFile,
    _VirtualFS,
    _SFTPHandleImpl,
)

from helpers import _alloc_port


# ---------------------------------------------------------------------------
# Unit tests — VirtualFS
# ---------------------------------------------------------------------------

class TestVirtualFS:
    def test_put_and_get(self):
        vfs = _VirtualFS()
        vfs.put("hello.txt", b"world")
        f = vfs.get("hello.txt")
        assert f is not None
        assert f.to_bytes() == b"world"

    def test_get_missing_returns_none(self):
        vfs = _VirtualFS()
        assert vfs.get("missing.txt") is None

    def test_exists(self):
        vfs = _VirtualFS()
        assert not vfs.exists("x.txt")
        vfs.put("x.txt", b"data")
        assert vfs.exists("x.txt")

    def test_write_at_sequential(self):
        vfs = _VirtualFS()
        vfs.write_at("f.txt", 0, b"hello")
        vfs.write_at("f.txt", 5, b" world")
        f = vfs.get("f.txt")
        assert f is not None
        assert f.to_bytes() == b"hello world"

    def test_remove(self):
        vfs = _VirtualFS()
        vfs.put("del.txt", b"bye")
        vfs.remove("del.txt")
        assert not vfs.exists("del.txt")

    def test_stat_returns_sftp_attributes(self):
        import stat as stat_mod
        vfs = _VirtualFS()
        vfs.put("s.txt", b"12345")
        attr = vfs.stat("s.txt")
        assert attr is not None
        assert attr.st_size == 5
        assert attr.st_mode & stat_mod.S_IFREG

    def test_prune_old_results(self):
        vfs = _VirtualFS()
        vfs.put("old.result.txt", b"old")
        # Force mtime into the past
        vfs._files["old.result.txt"].mtime = time.time() - 1000
        vfs.put("new.result.txt", b"new")
        vfs.prune_old_results(ttl=500)
        assert not vfs.exists("old.result.txt")
        assert vfs.exists("new.result.txt")

    def test_prune_does_not_delete_prompt_files(self):
        vfs = _VirtualFS()
        vfs.put("old.prompt.txt", b"keep me")
        vfs._files["old.prompt.txt"].mtime = time.time() - 1000
        vfs.prune_old_results(ttl=500)
        assert vfs.exists("old.prompt.txt")

    def test_list_attrs(self):
        vfs = _VirtualFS()
        vfs.put("a.txt", b"1")
        vfs.put("b.txt", b"22")
        attrs = vfs.list_attrs()
        names = {a.filename for a in attrs}
        assert "a.txt" in names
        assert "b.txt" in names


# ---------------------------------------------------------------------------
# Unit tests — InMemoryFile
# ---------------------------------------------------------------------------

class TestInMemoryFile:
    def test_write_and_read(self):
        f = _InMemoryFile()
        f.write_at(0, b"abc")
        assert f.read(0, 3) == b"abc"

    def test_write_extends(self):
        f = _InMemoryFile(b"hello")
        f.write_at(5, b" world")
        assert f.to_bytes() == b"hello world"

    def test_read_partial(self):
        f = _InMemoryFile(b"abcdef")
        assert f.read(2, 3) == b"cde"

    def test_size(self):
        f = _InMemoryFile(b"12345")
        assert f.size() == 5


# ---------------------------------------------------------------------------
# Unit tests — SFTPHandleImpl
# ---------------------------------------------------------------------------

class TestSFTPHandleImpl:
    def test_write_and_read(self):
        vfs = _VirtualFS()
        handle = _SFTPHandleImpl("task1.prompt.txt", vfs, None, "node1")
        assert handle.write(0, b"hello prompt") == paramiko.SFTP_OK
        assert handle.read(0, 12) == b"hello prompt"

    def test_read_missing_returns_error(self):
        vfs = _VirtualFS()
        handle = _SFTPHandleImpl("absent.result.txt", vfs, None, "")
        result = handle.read(0, 10)
        assert result == paramiko.SFTP_NO_SUCH_FILE

    def test_stat_after_write(self):
        vfs = _VirtualFS()
        handle = _SFTPHandleImpl("t.prompt.txt", vfs, None, "")
        handle.write(0, b"data")
        attr = handle.stat()
        assert hasattr(attr, "st_size")
        assert attr.st_size == 4

    def test_close_triggers_handler(self):
        vfs = _VirtualFS()
        received = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            received.append(msg)
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": "pong"},
                sender=NodeAddress("local", 0),
                correlation_id=msg.id,
            )

        task_id = str(uuid.uuid4())
        handle = _SFTPHandleImpl(f"{task_id}.prompt.txt", vfs, handler, "client1")
        handle.write(0, b"ping prompt")
        handle.close()

        # Wait briefly for asyncio.run() inside close()
        time.sleep(0.1)

        assert len(received) == 1
        assert received[0].payload["prompt"] == "ping prompt"
        assert vfs.exists(f"{task_id}.result.txt")
        result_file = vfs.get(f"{task_id}.result.txt")
        assert result_file is not None
        assert result_file.to_bytes() == b"pong"

    def test_close_non_prompt_file_no_handler_call(self):
        vfs = _VirtualFS()
        called = []

        async def handler(msg: UnifiedMessage) -> None:
            called.append(True)
            return None

        handle = _SFTPHandleImpl("task1.result.txt", vfs, handler, "")
        handle.write(0, b"some data")
        handle.close()  # result file → should NOT trigger handler
        time.sleep(0.05)
        assert called == []


# ---------------------------------------------------------------------------
# Unit tests — SFTPAdapter basics
# ---------------------------------------------------------------------------

class TestSFTPAdapterUnit:
    def test_protocol_name(self):
        assert SFTPAdapter().protocol_name == "sftp"

    def test_not_running_by_default(self):
        assert SFTPAdapter().is_running is False

    def test_registry_registered(self):
        assert "sftp" in AdapterRegistry.available()
        adapter = AdapterRegistry.create("sftp")
        assert isinstance(adapter, SFTPAdapter)

    def test_vfs_exposed(self):
        adapter = SFTPAdapter()
        vfs = adapter.vfs
        assert isinstance(vfs, _VirtualFS)

    def test_on_message_sets_handler(self):
        adapter = SFTPAdapter()
        called = []

        async def handler(msg: UnifiedMessage):
            called.append(msg)

        adapter.on_message(handler)
        assert adapter._handler is handler


# ---------------------------------------------------------------------------
# Integration tests (real SFTP connections over loopback)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSFTPAdapterIntegration:
    async def test_start_stop(self):
        adapter = SFTPAdapter()
        port = _alloc_port()
        await adapter.start("127.0.0.1", port)
        assert adapter.is_running
        await adapter.stop()
        assert not adapter.is_running

    async def test_prompt_result_round_trip(self):
        server = SFTPAdapter()
        port = _alloc_port()
        task_id = str(uuid.uuid4())

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": f"answer:{msg.payload['prompt']}"},
                sender=NodeAddress("local", 0),
                correlation_id=msg.id,
            )

        server.on_message(handler)
        await server.start("127.0.0.1", port)

        try:
            client = SFTPAdapter()
            msg = UnifiedMessage(
                type=MessageType.REQUEST,
                payload={"prompt": "what is 2+2?", "task_id": task_id},
                sender=NodeAddress("127.0.0.1", 0, "client"),
                id=task_id,
            )
            target = NodeAddress("127.0.0.1", port, "server")
            resp = await client.send(msg, target, poll_timeout=30)

            assert resp is not None
            assert resp.payload["result"] == "answer:what is 2+2?"
            assert resp.payload["task_id"] == task_id
        finally:
            await server.stop()

    async def test_connect_refused_raises_transport_error(self):
        client = SFTPAdapter()
        target = NodeAddress("127.0.0.1", _alloc_port(), "nobody")
        msg = UnifiedMessage(
            type=MessageType.REQUEST,
            payload={"prompt": "hi"},
            sender=NodeAddress("127.0.0.1", 0),
        )
        with pytest.raises(TransportError):
            await client.send(msg, target, poll_timeout=5)

    async def test_broadcast_does_not_raise(self):
        server = SFTPAdapter()
        port = _alloc_port()
        task_id = str(uuid.uuid4())

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            return UnifiedMessage(
                type=MessageType.RESPONSE,
                payload={"result": "ok"},
                sender=NodeAddress("local", 0),
                correlation_id=msg.id,
            )

        server.on_message(handler)
        await server.start("127.0.0.1", port)

        try:
            client = SFTPAdapter()
            msg = UnifiedMessage(
                type=MessageType.REQUEST,
                payload={"prompt": "test", "task_id": task_id},
                sender=NodeAddress("127.0.0.1", 0),
                id=task_id,
            )
            target = NodeAddress("127.0.0.1", port, "server")
            await client.broadcast(msg, [target])
        finally:
            await server.stop()
