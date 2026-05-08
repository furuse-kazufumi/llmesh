"""Tests for ReliableStream — high-level send/receive over arbitrary adapters."""
from __future__ import annotations

import base64
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from llmesh.protocol.message import MessageType, NodeAddress, UnifiedMessage
from llmesh.protocol.reliable_stream import ReliableStream, _DTYPE_BYTES, _DTYPE_DICT, _DTYPE_STR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _addr(host: str = "127.0.0.1", port: int = 8000) -> NodeAddress:
    return NodeAddress(host=host, port=port)


def _make_stream(sender: NodeAddress | None = None, **kwargs) -> ReliableStream:
    return ReliableStream(sender or _addr(), watchdog_timeout_s=None, **kwargs)


def _mock_adapter() -> MagicMock:
    adapter = MagicMock()
    adapter.send = AsyncMock()
    return adapter


async def _send_to_receiver(
    sender_stream: ReliableStream,
    receiver_stream: ReliableStream,
    data: Any,
    *,
    sender_addr: NodeAddress,
    receiver_addr: NodeAddress,
) -> list[Any]:
    """Send data through sender_stream; feed all chunks into receiver_stream."""
    sent: list[UnifiedMessage] = []
    cap = MagicMock()
    cap.send = AsyncMock(side_effect=lambda m, t: sent.append(m))
    await sender_stream.send(data, target=receiver_addr, adapter=cap)

    ack_adapter = _mock_adapter()
    results = []
    for chunk in sent:
        results.extend(await receiver_stream.on_message(chunk, adapter=ack_adapter))
    return results


# ---------------------------------------------------------------------------
# Encoding / decoding round-trips
# ---------------------------------------------------------------------------

class TestEncoding:
    def test_bytes_round_trip(self):
        raw, dtype = ReliableStream._encode(b"\x00\xff\xab\xcd")
        assert dtype == _DTYPE_BYTES
        assert ReliableStream._decode(raw, dtype) == b"\x00\xff\xab\xcd"

    def test_dict_round_trip(self):
        d = {"key": "value", "n": 42, "nested": {"a": [1, 2, 3]}}
        raw, dtype = ReliableStream._encode(d)
        assert dtype == _DTYPE_DICT
        assert ReliableStream._decode(raw, dtype) == d

    def test_str_round_trip(self):
        s = "hello, 世界 🌏"
        raw, dtype = ReliableStream._encode(s)
        assert dtype == _DTYPE_STR
        assert ReliableStream._decode(raw, dtype) == s

    def test_empty_bytes(self):
        raw, dtype = ReliableStream._encode(b"")
        assert ReliableStream._decode(raw, dtype) == b""

    def test_unsupported_type_raises(self):
        with pytest.raises(TypeError):
            ReliableStream._encode(12345)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Chunk generation
# ---------------------------------------------------------------------------

class TestMakeChunks:
    def test_small_data_single_chunk(self):
        chunks = _make_stream()._make_chunks(b"hello", _DTYPE_BYTES, "sid-1")
        assert len(chunks) == 1
        assert chunks[0].type == MessageType.STREAM_END
        assert chunks[0].total_chunks == 1
        assert chunks[0].sequence_no == 0

    def test_large_data_multiple_chunks(self):
        chunks = _make_stream(chunk_size=10)._make_chunks(b"A" * 100, _DTYPE_BYTES, "sid-2")
        assert len(chunks) > 1
        assert chunks[-1].type == MessageType.STREAM_END
        assert chunks[-1].total_chunks == len(chunks)
        assert [c.sequence_no for c in chunks] == list(range(len(chunks)))

    def test_chunks_share_stream_id(self):
        chunks = _make_stream(chunk_size=10)._make_chunks(b"X" * 50, _DTYPE_BYTES, "my-stream")
        assert all(c.correlation_id == "my-stream" for c in chunks)

    def test_empty_data_produces_one_chunk(self):
        assert len(_make_stream()._make_chunks(b"", _DTYPE_BYTES, "sid-empty")) == 1

    def test_chunk_payload_contains_type(self):
        chunks = _make_stream()._make_chunks(b"data", _DTYPE_DICT, "sid-3")
        assert all(c.payload["_type"] == _DTYPE_DICT for c in chunks)

    def test_chunk_data_reassembles_correctly(self):
        raw = b"Binary\x00\xff\xfe data here!"
        chunks = _make_stream(chunk_size=10)._make_chunks(raw, _DTYPE_BYTES, "sid-4")
        recovered = base64.b64decode("".join(c.payload["_chunk"] for c in chunks))
        assert recovered == raw


# ---------------------------------------------------------------------------
# End-to-end send / receive (single chunk)
# ---------------------------------------------------------------------------

class TestSendReceiveSingleChunk:
    async def test_bytes_small(self):
        sa, ra = _addr(port=9001), _addr(port=9002)
        results = await _send_to_receiver(
            _make_stream(sa), _make_stream(ra), b"\xde\xad\xbe\xef",
            sender_addr=sa, receiver_addr=ra,
        )
        assert results == [b"\xde\xad\xbe\xef"]

    async def test_dict_small(self):
        sa, ra = _addr(port=9003), _addr(port=9004)
        payload = {"status": "ok", "count": 7}
        assert await _send_to_receiver(
            _make_stream(sa), _make_stream(ra), payload,
            sender_addr=sa, receiver_addr=ra,
        ) == [payload]

    async def test_str_small(self):
        sa, ra = _addr(port=9005), _addr(port=9006)
        assert await _send_to_receiver(
            _make_stream(sa), _make_stream(ra), "hello world",
            sender_addr=sa, receiver_addr=ra,
        ) == ["hello world"]

    async def test_empty_bytes(self):
        sa, ra = _addr(port=9007), _addr(port=9008)
        assert await _send_to_receiver(
            _make_stream(sa), _make_stream(ra), b"",
            sender_addr=sa, receiver_addr=ra,
        ) == [b""]


# ---------------------------------------------------------------------------
# End-to-end send / receive (multi-chunk)
# ---------------------------------------------------------------------------

class TestSendReceiveMultiChunk:
    async def test_large_binary(self):
        sa, ra = _addr(port=9010), _addr(port=9011)
        data = bytes(range(256)) * 40
        assert await _send_to_receiver(
            _make_stream(sa, chunk_size=512), _make_stream(ra, chunk_size=512),
            data, sender_addr=sa, receiver_addr=ra,
        ) == [data]

    async def test_large_dict(self):
        sa, ra = _addr(port=9012), _addr(port=9013)
        payload = {str(i): "v" * 20 for i in range(50)}
        assert await _send_to_receiver(
            _make_stream(sa, chunk_size=64), _make_stream(ra, chunk_size=64),
            payload, sender_addr=sa, receiver_addr=ra,
        ) == [payload]

    async def test_out_of_order_chunks(self):
        sa, ra = _addr(port=9014), _addr(port=9015)
        sent: list[UnifiedMessage] = []
        a = MagicMock()
        a.send = AsyncMock(side_effect=lambda m, t: sent.append(m))
        data = b"ABCDEFGHIJKLMNOPQRSTUVWXYZ" * 4
        await _make_stream(sa, chunk_size=10).send(data, target=ra, adapter=a)

        receiver = _make_stream(ra, chunk_size=10)
        ack_a = _mock_adapter()
        results = []
        for chunk in reversed(sent):
            results.extend(await receiver.on_message(chunk, adapter=ack_a))
        assert results == [data]

    async def test_multiple_streams_interleaved(self):
        sa, ra = _addr(port=9016), _addr(port=9017)
        sent_a: list[UnifiedMessage] = []
        sent_b: list[UnifiedMessage] = []
        a = MagicMock()
        a.send = AsyncMock(side_effect=lambda m, t: sent_a.append(m))
        b = MagicMock()
        b.send = AsyncMock(side_effect=lambda m, t: sent_b.append(m))
        data_a, data_b = b"Stream-A-" * 50, b"Stream-B-" * 50
        await _make_stream(sa, chunk_size=64).send(data_a, target=ra, adapter=a)
        await _make_stream(sa, chunk_size=64).send(data_b, target=ra, adapter=b)

        interleaved = [x for pair in zip(sent_a, sent_b) for x in pair]
        interleaved += sent_a[len(sent_b):]
        interleaved += sent_b[len(sent_a):]

        receiver = _make_stream(ra, chunk_size=64)
        ack_a = _mock_adapter()
        results = []
        for chunk in interleaved:
            results.extend(await receiver.on_message(chunk, adapter=ack_a))
        assert set(results) == {data_a, data_b}


# ---------------------------------------------------------------------------
# ACK handling
# ---------------------------------------------------------------------------

class TestAck:
    async def test_ack_clears_sender_buffer(self):
        sa, ra = _addr(port=9020), _addr(port=9021)
        sender = _make_stream(sa)
        sent: list[UnifiedMessage] = []
        a = MagicMock()
        a.send = AsyncMock(side_effect=lambda m, t: sent.append(m))
        stream_id = await sender.send(b"data", target=ra, adapter=a)
        assert sender._sender_buf.chunk_count(stream_id) == 1

        ack = UnifiedMessage(type=MessageType.STREAM_ACK, payload={"stream_id": stream_id}, sender=ra)
        assert await sender.on_message(ack) == []
        assert sender._sender_buf.chunk_count(stream_id) == 0

    async def test_receiver_sends_ack_to_adapter(self):
        sa, ra = _addr(port=9022), _addr(port=9023)
        sender_s = _make_stream(sa)
        sent: list[UnifiedMessage] = []
        a = MagicMock()
        a.send = AsyncMock(side_effect=lambda m, t: sent.append(m))
        await sender_s.send(b"small", target=ra, adapter=a)

        receiver = _make_stream(ra)
        ack_msgs: list[UnifiedMessage] = []
        ack_a = MagicMock()
        ack_a.send = AsyncMock(side_effect=lambda m, t: ack_msgs.append(m))
        for chunk in sent:
            await receiver.on_message(chunk, adapter=ack_a)
        assert any(m.type == MessageType.STREAM_ACK for m in ack_msgs)


# ---------------------------------------------------------------------------
# RETRANSMIT handling
# ---------------------------------------------------------------------------

class TestRetransmit:
    async def test_retransmit_resends_missing_chunks(self):
        sa, ra = _addr(port=9030), _addr(port=9031)
        sender_s = _make_stream(sa, chunk_size=10)
        sent: list[UnifiedMessage] = []
        a = MagicMock()
        a.send = AsyncMock(side_effect=lambda m, t: sent.append(m))
        stream_id = await sender_s.send(b"A" * 80, target=ra, adapter=a)
        assert len(sent) > 2

        retransmit = UnifiedMessage(
            type=MessageType.RETRANSMIT,
            payload={"stream_id": stream_id, "missing": [1]},
            sender=ra,
        )
        resent: list[UnifiedMessage] = []
        rt_a = MagicMock()
        rt_a.send = AsyncMock(side_effect=lambda m, t: resent.append(m))
        await sender_s.on_message(retransmit, adapter=rt_a)
        assert len(resent) == 1
        assert resent[0].sequence_no == 1

    async def test_tick_sends_retransmit_on_timeout(self):
        sa, ra = _addr(port=9032), _addr(port=9033)
        receiver = _make_stream(ra, retransmit_timeout_s=0.0)
        sent: list[UnifiedMessage] = []
        a = MagicMock()
        a.send = AsyncMock(side_effect=lambda m, t: sent.append(m))
        await _make_stream(sa, chunk_size=10).send(b"A" * 50, target=ra, adapter=a)
        assert len(sent) >= 2

        ack_a = _mock_adapter()
        for i, chunk in enumerate(sent):
            if i != 1:
                await receiver.on_message(chunk, adapter=ack_a)

        rt_msgs: list[UnifiedMessage] = []
        rt_a = MagicMock()
        rt_a.send = AsyncMock(side_effect=lambda m, t: rt_msgs.append(m))
        await receiver.tick(adapter=rt_a, now=time.monotonic() + 10.0)
        assert any(m.type == MessageType.RETRANSMIT for m in rt_msgs)


# ---------------------------------------------------------------------------
# Watchdog
# ---------------------------------------------------------------------------

class TestWatchdog:
    def test_peer_silent_when_watchdog_expires(self):
        assert ReliableStream(_addr(), watchdog_timeout_s=1.0).is_peer_silent(
            now=time.monotonic() + 999
        )

    async def test_peer_not_silent_after_message(self):
        stream = ReliableStream(_addr(), watchdog_timeout_s=60.0)
        await stream.on_message(UnifiedMessage(
            type=MessageType.REQUEST, payload={"x": 1}, sender=_addr(port=9999)
        ))
        assert not stream.is_peer_silent(now=time.monotonic() + 0.1)

    def test_no_watchdog_never_silent(self):
        assert not ReliableStream(_addr(), watchdog_timeout_s=None).is_peer_silent(
            now=time.monotonic() + 9999
        )


# ---------------------------------------------------------------------------
# Default chunk size constraint
# ---------------------------------------------------------------------------

class TestChunkSizeConstraint:
    def test_default_chunk_size_under_512kb(self):
        assert ReliableStream.DEFAULT_CHUNK_SIZE < 512 * 1024
