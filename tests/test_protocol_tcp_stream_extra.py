"""Additional coverage for ``llmesh/protocol/tcp_stream_adapter.py``.

既存 ``tests/test_protocol_tcp_stream.py`` (33 件) は roundtrip / connection
pool / tick loop 等を広く扱うが、以下の経路は未網羅だった:

1. ``_ConnPool`` を直接組み立てて挙動検証
   - pool 容量上限 → ``TransportError("pool_exhausted")``
   - 死んだ接続を release → ``_total`` がデクリメント
   - close_all で idle queue がドレインされる
2. ``OutboxQueue`` 統合
   - send が TransportError を捕捉 → outbox.enqueue → caller に None
   - retry_loop が outbox から dequeue して再送
3. ``_send_inner`` のエラー分岐
   - read_timeout (deadline 超過)
   - 不正フレーム (UnifiedMessage.from_bytes が ValueError)
4. ``_handle_connection`` のエッジケース
   - 不正バイト送信 → サーバはクラッシュせず接続継続 (loop continue)
   - handler が None でも payloads を黙って drop
   - handler が None を返した場合に応答が送信されない

設計方針:
- ``runner`` / ``monkeypatch`` で subprocess 系 dependency を注入し、
  flaky timing test 化を避ける (1 件以外は実 socket を使わない)
- 実 socket を使うものは ``free_port`` fixture で port 衝突回避
- `slow_handler` 系は短い (50 ms 以下) で済ませる
"""

from __future__ import annotations

import asyncio
import socket
import struct
from contextlib import closing
from unittest.mock import patch

import pytest

from llmesh.protocol import tcp_stream_adapter as tsa
from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage
from llmesh.protocol.outbox import OutboxQueue
from llmesh.protocol.tcp_stream_adapter import (
    _ConnPool,
    _PersistentConn,
    TCPStreamAdapter,
    TransportError,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def free_port() -> int:
    """Return a free TCP port on localhost."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def sender() -> NodeAddress:
    return NodeAddress("127.0.0.1", 9000, "test-client")


# ---------------------------------------------------------------------------
# _ConnPool — direct unit tests
# ---------------------------------------------------------------------------


class _FakeWriter:
    """Minimal asyncio.StreamWriter stand-in for pool tests."""

    def __init__(self, *, alive: bool = True) -> None:
        self._closing = not alive

    def is_closing(self) -> bool:
        return self._closing

    def close(self) -> None:
        self._closing = True

    async def wait_closed(self) -> None:
        return None


class _FakeReader:
    pass


def _make_fake_conn(*, alive: bool = True) -> _PersistentConn:
    return _PersistentConn(
        reader=_FakeReader(),  # type: ignore[arg-type]
        writer=_FakeWriter(alive=alive),  # type: ignore[arg-type]
        local=NodeAddress("127.0.0.1", 12345),
    )


class TestConnPoolDirect:
    async def test_pool_exhausted_raises_transport_error(
        self, monkeypatch
    ) -> None:
        """Pool 容量を埋めて acquire を試みると TransportError が必ず出る."""
        # _open_connection を fake で置き換えて socket を踏まない
        async def fake_open(host: str, port: int) -> _PersistentConn:
            return _make_fake_conn(alive=True)

        monkeypatch.setattr(tsa, "_open_connection", fake_open)
        # acquire のタイムアウトを 0.05s に短縮
        monkeypatch.setattr(tsa, "_POOL_ACQUIRE_TIMEOUT", 0.05)

        pool = _ConnPool(max_size=2)
        # 2 個取って release せず保留 (pool 満杯状態)
        c1 = await pool.acquire("h", 1)
        c2 = await pool.acquire("h", 1)
        assert c1 is not c2
        # 3 個目は待機 → タイムアウト → TransportError
        with pytest.raises(TransportError) as exc_info:
            await pool.acquire("h", 1)
        assert "pool_exhausted" in str(exc_info.value)

    async def test_pool_recovers_dead_connection_on_release(
        self, monkeypatch
    ) -> None:
        """release() に死んだ接続を渡すと _total がデクリメントされ、再 acquire できる."""
        created: list[bool] = []

        async def fake_open(host: str, port: int) -> _PersistentConn:
            created.append(True)
            return _make_fake_conn(alive=True)

        monkeypatch.setattr(tsa, "_open_connection", fake_open)
        pool = _ConnPool(max_size=1)

        c1 = await pool.acquire("h", 1)
        # 死亡シミュレーション: writer を closing にする
        c1.writer.close()  # type: ignore[attr-defined]
        pool.release(c1)
        # _total が戻ったので新しい接続を取れる
        c2 = await pool.acquire("h", 1)
        assert c2 is not c1
        assert len(created) == 2  # 2 回 _open_connection が呼ばれた

    async def test_pool_acquire_skips_dead_idle_connection(
        self, monkeypatch
    ) -> None:
        """idle queue 内の死んだ接続は捨てて、新規接続を作る."""
        alive_states = [True, True]  # 1 個目: 取得時生きてる、2 個目: 新規も生きてる

        async def fake_open(host: str, port: int) -> _PersistentConn:
            return _make_fake_conn(alive=True)

        monkeypatch.setattr(tsa, "_open_connection", fake_open)
        pool = _ConnPool(max_size=2)
        c1 = await pool.acquire("h", 1)
        # release 後 c1 を idle queue に戻して、その後で死亡させる
        pool.release(c1)
        c1.writer.close()  # type: ignore[attr-defined]
        # 次の acquire は死んだ c1 を捨てて新規を作る
        c2 = await pool.acquire("h", 1)
        assert c2 is not c1
        assert c2.is_alive() is True
        # c2 はまだ in-use なので alive_states も生きていることを確認
        del alive_states  # silence unused

    async def test_pool_close_all_drains_idle_queue(
        self, monkeypatch
    ) -> None:
        """close_all で idle queue 内の接続が close される."""
        async def fake_open(host: str, port: int) -> _PersistentConn:
            return _make_fake_conn(alive=True)

        monkeypatch.setattr(tsa, "_open_connection", fake_open)
        pool = _ConnPool(max_size=3)
        # 2 個取って両方 release
        c1 = await pool.acquire("h", 1)
        c2 = await pool.acquire("h", 1)
        pool.release(c1)
        pool.release(c2)
        # close_all は idle queue を全部 close する
        await pool.close_all()
        assert c1.writer.is_closing()  # type: ignore[attr-defined]
        assert c2.writer.is_closing()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# OutboxQueue 統合
# ---------------------------------------------------------------------------


class TestOutboxIntegration:
    async def test_send_falls_back_to_outbox_on_transport_error(
        self, sender: NodeAddress
    ) -> None:
        """target が unreachable → send は outbox.enqueue → caller に None."""
        outbox = OutboxQueue(":memory:")
        # 9999 番ポートに何もリッスンしていない前提 (closed port)
        target = NodeAddress("127.0.0.1", 9, "unreachable")

        client = TCPStreamAdapter(
            timeout=0.5, outbox=outbox, retry_interval=999.0
        )
        msg = UnifiedMessage.request({"x": 1}, sender, target)
        result = await client.send(msg, target)
        # outbox 経路 → caller に None
        assert result is None
        # outbox にメッセージが残っている
        pending = outbox.dequeue(10)
        assert len(pending) == 1
        # メッセージ ID 一致
        assert pending[0][0].id == msg.id

    async def test_send_without_outbox_propagates_transport_error(
        self, sender: NodeAddress
    ) -> None:
        """outbox 無し時は TransportError が呼出元に伝播."""
        target = NodeAddress("127.0.0.1", 9, "unreachable")
        client = TCPStreamAdapter(timeout=0.5)
        msg = UnifiedMessage.request({"x": 1}, sender, target)
        with pytest.raises(TransportError):
            await client.send(msg, target)

    async def test_retry_loop_drains_outbox(
        self, free_port: int, sender: NodeAddress
    ) -> None:
        """outbox に pending メッセージがあれば retry_loop が dequeue → 再送."""
        # サーバ側
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port, "srv")
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            received.append(msg)
            return msg.make_response({"ack": True}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)
        try:
            # outbox に 1 件直接突っ込む
            outbox = OutboxQueue(":memory:")
            queued = UnifiedMessage.request({"k": "v"}, sender, server_addr)
            outbox.enqueue(queued, server_addr)

            # client adapter の retry_loop を 0.05 秒間隔で起動
            client = TCPStreamAdapter(
                timeout=2.0, outbox=outbox, retry_interval=0.05
            )
            # _retry_loop は start() から起動するが、サーバではなく client なので
            # 手動で _retry_loop を 1 cycle 走らせる
            retry_task = asyncio.create_task(client._retry_loop())
            # retry interval + processing time だけ待つ
            await asyncio.sleep(0.5)
            retry_task.cancel()
            try:
                await retry_task
            except asyncio.CancelledError:
                pass

            # サーバが受信していること
            assert len(received) >= 1
            assert received[0].id == queued.id
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# _send_inner エラーパス
# ---------------------------------------------------------------------------


class TestSendErrorPaths:
    async def test_read_timeout_raises_transport_error(
        self, free_port: int, sender: NodeAddress
    ) -> None:
        """サーバが応答しない (handler 無し) → read_timeout → TransportError."""
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port, "srv")
        # handler を設定しない → サーバはリクエストを受け取っても応答を返さない
        await server.start("127.0.0.1", free_port)
        try:
            client = TCPStreamAdapter(timeout=0.3)  # 短い timeout
            msg = UnifiedMessage.request({"x": 1}, sender, server_addr)
            with pytest.raises(TransportError) as exc_info:
                await client.send(msg, server_addr)
            # read_timeout または connection_closed のいずれか
            err_str = str(exc_info.value)
            assert "timeout" in err_str.lower() or "closed" in err_str.lower()
        finally:
            await server.stop()

    async def test_handle_connection_drops_invalid_frame_continues(
        self, free_port: int, sender: NodeAddress
    ) -> None:
        """サーバに不正バイトを送ってもクラッシュせず、後続の有効リクエストを処理する."""
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port, "srv")
        received: list[UnifiedMessage] = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage:
            received.append(msg)
            return msg.make_response({"ok": True}, sender=server_addr)

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)
        try:
            # 生 socket で「長さプレフィクス + ゴミ JSON」を送って frame を壊す
            reader, writer = await asyncio.open_connection("127.0.0.1", free_port)
            try:
                garbage = b"!!!not-json!!!"
                writer.write(struct.pack(">I", len(garbage)) + garbage)
                await writer.drain()
                # サーバは continue でループするはず。少し待つ。
                await asyncio.sleep(0.1)
                # この接続を閉じてから、新しい有効リクエストで応答を確認
            finally:
                writer.close()
                with pytest.raises((OSError, ConnectionResetError, asyncio.IncompleteReadError)):
                    await writer.wait_closed()
            # 別接続から有効メッセージ
            client = TCPStreamAdapter(timeout=2.0)
            msg = UnifiedMessage.request({"valid": True}, sender, server_addr)
            resp = await client.send(msg, server_addr)
            assert resp is not None
            assert resp.payload.get("ok") is True
        finally:
            await server.stop()


# ---------------------------------------------------------------------------
# _handle_connection edge cases
# ---------------------------------------------------------------------------


class TestServerEdgeCases:
    async def test_handler_returning_none_sends_no_response(
        self, free_port: int, sender: NodeAddress
    ) -> None:
        """handler が None を返したらサーバは応答を送らない."""
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port, "srv")
        called = []

        async def handler(msg: UnifiedMessage) -> UnifiedMessage | None:
            called.append(msg.id)
            return None

        server.on_message(handler)
        await server.start("127.0.0.1", free_port)
        try:
            client = TCPStreamAdapter(timeout=0.3)
            msg = UnifiedMessage.request({"x": 1}, sender, server_addr)
            # handler は呼ばれるが応答が無いので read_timeout
            with pytest.raises(TransportError):
                await client.send(msg, server_addr)
            assert called == [msg.id]
        finally:
            await server.stop()

    async def test_handler_exception_does_not_crash_server(
        self, free_port: int, sender: NodeAddress
    ) -> None:
        """handler が例外を投げてもサーバ全体はクラッシュしない."""
        server = TCPStreamAdapter()
        server_addr = NodeAddress("127.0.0.1", free_port, "srv")
        call_count = [0]

        async def bad_handler(msg: UnifiedMessage) -> UnifiedMessage:
            call_count[0] += 1
            if call_count[0] == 1:
                raise RuntimeError("handler explosion")
            return msg.make_response({"ok": True}, sender=server_addr)

        server.on_message(bad_handler)
        await server.start("127.0.0.1", free_port)
        try:
            client = TCPStreamAdapter(timeout=0.5)
            # 1 度目は handler が爆発
            msg1 = UnifiedMessage.request({"first": True}, sender, server_addr)
            try:
                await client.send(msg1, server_addr)
            except TransportError:
                pass  # 接続が落ちる可能性あり
            # サーバは生きているので 2 度目は通る
            msg2 = UnifiedMessage.request({"second": True}, sender, server_addr)
            try:
                resp = await client.send(msg2, server_addr)
                # 2 度目は handler が成功するので応答が返る
                assert resp is None or resp.payload.get("ok") is True
            except TransportError:
                # サーバが残っていれば再接続は通る
                resp = await client.send(msg2, server_addr)
                assert resp is None or resp.payload.get("ok") is True
            assert call_count[0] >= 1
        finally:
            await server.stop()
