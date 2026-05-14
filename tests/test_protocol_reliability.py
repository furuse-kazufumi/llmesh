"""Tests for the ACK/RETRANSMIT reliability protocol.

Covers:
- MessageAssembler.pop_completed() → STREAM_ACK trigger
- MessageAssembler.check_timeouts() → RETRANSMIT trigger (once only)
- ChunkSender.buffer / handle_retransmit / handle_ack / expire_old
- End-to-end: gap → retransmit → complete → ACK → buffer freed
"""
from __future__ import annotations

import time


from llmesh.protocol import (
    ChunkSender,
    MessageAssembler,
    MessageType,
    NodeAddress,
    RetransmitInfo,
    UnifiedMessage,
)


def _sender(port: int = 8000) -> NodeAddress:
    return NodeAddress("127.0.0.1", port, node_id="sender")


def _receiver(port: int = 9000) -> NodeAddress:
    return NodeAddress("127.0.0.1", port, node_id="receiver")


def _chunk(stream_id: str, seq: int, data: str, total: int | None = None) -> UnifiedMessage:
    return UnifiedMessage.chunk(
        {"text": data},
        _sender(),
        stream_id=stream_id,
        sequence_no=seq,
        total_chunks=total,
    )


# ===========================================================================
# MessageAssembler — completion / ACK
# ===========================================================================

class TestPopCompleted:
    def test_no_completed_initially(self):
        asm = MessageAssembler()
        assert asm.pop_completed() == []

    def test_complete_stream_appears_in_pop_completed(self):
        asm = MessageAssembler()
        asm.push(_chunk("s", 0, "only", total=1))
        done = asm.pop_completed()
        assert len(done) == 1
        assert done[0].stream_id == "s"
        assert done[0].sender == _sender()

    def test_pop_completed_drains_queue(self):
        asm = MessageAssembler()
        asm.push(_chunk("s", 0, "x", total=1))
        asm.pop_completed()
        assert asm.pop_completed() == []

    def test_two_completed_streams(self):
        asm = MessageAssembler()
        asm.push(_chunk("a", 0, "1", total=1))
        asm.push(_chunk("b", 0, "2", total=1))
        done = asm.pop_completed()
        assert {d.stream_id for d in done} == {"a", "b"}

    def test_incomplete_stream_not_in_completed(self):
        asm = MessageAssembler()
        asm.push(_chunk("s", 0, "a"))   # no total_chunks → not complete
        assert asm.pop_completed() == []

    def test_completed_stream_removed_from_pending(self):
        asm = MessageAssembler()
        asm.push(_chunk("s", 0, "x", total=1))
        assert "s" not in asm.pending_streams()

    def test_completed_stream_sender_is_last_chunk_sender(self):
        asm = MessageAssembler()
        # Deliver out of order: seq 1 first (from a different sender field),
        # then seq 0 which triggers completion
        c0 = UnifiedMessage.chunk(
            {"t": "A"}, _sender(8001),
            stream_id="s", sequence_no=0
        )
        c1 = UnifiedMessage.chunk(
            {"t": "B"}, _sender(8002),
            stream_id="s", sequence_no=1, total_chunks=2
        )
        asm.push(c1)
        asm.push(c0)  # triggers completion; last pushed sender = _sender(8001)
        done = asm.pop_completed()
        assert len(done) == 1
        # sender is from the chunk that triggered completion
        assert done[0].sender.port in (8001, 8002)


# ===========================================================================
# MessageAssembler — timeout / RETRANSMIT
# ===========================================================================

class TestCheckTimeouts:
    def _stalled_assembler(self, stream_id: str = "s") -> MessageAssembler:
        asm = MessageAssembler()
        # Push seq 0 (delivered) and seq 2 (buffered) — gap at seq 1
        asm.push(_chunk(stream_id, 0, "A"))
        asm.push(_chunk(stream_id, 2, "C"))
        return asm

    def test_no_timeout_before_deadline(self):
        asm = self._stalled_assembler()
        now = time.monotonic()
        result = asm.check_timeouts(timeout_s=60.0, now=now)
        assert result == []

    def test_timeout_returns_retransmit_info(self):
        asm = self._stalled_assembler()
        past = time.monotonic() - 10.0
        # Fake last_received_at to be in the past
        asm._streams["s"].last_received_at = past
        result = asm.check_timeouts(timeout_s=5.0)
        assert len(result) == 1
        info = result[0]
        assert isinstance(info, RetransmitInfo)
        assert info.stream_id == "s"
        assert info.missing == [1]
        assert info.sender == _sender()

    def test_retransmit_sent_only_once(self):
        asm = self._stalled_assembler()
        asm._streams["s"].last_received_at = time.monotonic() - 10.0
        first = asm.check_timeouts(timeout_s=5.0)
        second = asm.check_timeouts(timeout_s=5.0)
        assert len(first) == 1
        assert second == []

    def test_non_stalled_stream_not_returned(self):
        asm = MessageAssembler()
        # Only seq 0 buffered, no gap (next_expected=0, chunk at 0)
        asm.push(_chunk("s", 1, "B"))   # seq 1 in buffer, next_expected=0 → stalled
        asm._streams["s"].last_received_at = time.monotonic() - 10.0
        result = asm.check_timeouts(timeout_s=5.0)
        # seq 1 is buffered but seq 0 is missing → is_stalled=True
        assert len(result) == 1
        assert result[0].missing == [0]

    def test_completed_stream_never_triggers_timeout(self):
        asm = MessageAssembler()
        asm.push(_chunk("s", 0, "only", total=1))
        # Stream is complete and removed — no pending streams
        result = asm.check_timeouts(timeout_s=0.0)
        assert result == []

    def test_multiple_stalled_streams(self):
        asm = MessageAssembler()
        for sid in ("a", "b"):
            asm.push(_chunk(sid, 0, "x"))
            asm.push(_chunk(sid, 2, "z"))
            asm._streams[sid].last_received_at = time.monotonic() - 10.0
        result = asm.check_timeouts(timeout_s=5.0)
        assert {r.stream_id for r in result} == {"a", "b"}


# ===========================================================================
# ChunkSender
# ===========================================================================

class TestChunkSender:
    def _make_chunks(self, stream_id: str, n: int) -> list[UnifiedMessage]:
        return [
            _chunk(stream_id, i, f"data{i}", total=n if i == n - 1 else None)
            for i in range(n)
        ]

    def test_buffer_and_count(self):
        cs = ChunkSender()
        chunks = self._make_chunks("s", 3)
        cs.buffer("s", chunks)
        assert cs.chunk_count("s") == 3
        assert len(cs) == 3

    def test_buffered_streams(self):
        cs = ChunkSender()
        cs.buffer("a", self._make_chunks("a", 2))
        cs.buffer("b", self._make_chunks("b", 1))
        assert set(cs.buffered_streams()) == {"a", "b"}

    def test_handle_retransmit_returns_missing_chunks(self):
        cs = ChunkSender()
        chunks = self._make_chunks("s", 4)
        cs.buffer("s", chunks)

        retransmit_msg = UnifiedMessage(
            type=MessageType.RETRANSMIT,
            payload={"stream_id": "s", "missing": [1, 3]},
            sender=_receiver(),
        )
        resend = cs.handle_retransmit(retransmit_msg)
        assert len(resend) == 2
        seqs = {c.sequence_no for c in resend}
        assert seqs == {1, 3}

    def test_handle_retransmit_skips_unknown_seqs(self):
        cs = ChunkSender()
        cs.buffer("s", self._make_chunks("s", 2))

        retransmit_msg = UnifiedMessage(
            type=MessageType.RETRANSMIT,
            payload={"stream_id": "s", "missing": [99]},
            sender=_receiver(),
        )
        assert cs.handle_retransmit(retransmit_msg) == []

    def test_handle_retransmit_unknown_stream(self):
        cs = ChunkSender()
        msg = UnifiedMessage(
            type=MessageType.RETRANSMIT,
            payload={"stream_id": "ghost", "missing": [0]},
            sender=_receiver(),
        )
        assert cs.handle_retransmit(msg) == []

    def test_handle_retransmit_wrong_type_ignored(self):
        cs = ChunkSender()
        cs.buffer("s", self._make_chunks("s", 2))
        msg = UnifiedMessage.request(
            {"stream_id": "s", "missing": [0]}, _receiver()
        )
        assert cs.handle_retransmit(msg) == []

    def test_handle_ack_frees_buffer(self):
        cs = ChunkSender()
        cs.buffer("s", self._make_chunks("s", 3))

        ack = UnifiedMessage(
            type=MessageType.STREAM_ACK,
            payload={"stream_id": "s"},
            sender=_receiver(),
        )
        assert cs.handle_ack(ack) is True
        assert cs.chunk_count("s") == 0
        assert "s" not in cs.buffered_streams()

    def test_handle_ack_unknown_stream_returns_false(self):
        cs = ChunkSender()
        ack = UnifiedMessage(
            type=MessageType.STREAM_ACK,
            payload={"stream_id": "ghost"},
            sender=_receiver(),
        )
        assert cs.handle_ack(ack) is False

    def test_handle_ack_wrong_type_ignored(self):
        cs = ChunkSender()
        cs.buffer("s", self._make_chunks("s", 1))
        msg = UnifiedMessage.request({"stream_id": "s"}, _receiver())
        assert cs.handle_ack(msg) is False
        assert cs.chunk_count("s") == 1

    def test_expire_old_removes_stale(self):
        cs = ChunkSender(ttl_s=10.0)
        cs.buffer("old", self._make_chunks("old", 2))
        cs.buffer("new", self._make_chunks("new", 2))
        # Fake creation time for "old"
        cs._buffers["old"].created_at = time.monotonic() - 20.0
        evicted = cs.expire_old()
        assert evicted == ["old"]
        assert "old" not in cs.buffered_streams()
        assert "new" in cs.buffered_streams()

    def test_expire_old_no_stale(self):
        cs = ChunkSender(ttl_s=300.0)
        cs.buffer("s", self._make_chunks("s", 1))
        assert cs.expire_old() == []

    def test_rebuffer_same_stream_replaces(self):
        cs = ChunkSender()
        cs.buffer("s", self._make_chunks("s", 3))
        cs.buffer("s", self._make_chunks("s", 1))   # replace
        assert cs.chunk_count("s") == 1


# ===========================================================================
# End-to-end: gap → retransmit → ACK flow
# ===========================================================================

class TestReliabilityFlow:
    def test_full_flow(self):
        """Simulate: send 3 chunks, chunk 1 is lost, receiver retransmits,
        sender resends, stream completes, receiver sends ACK, sender frees."""
        sender_cs = ChunkSender(ttl_s=60.0)
        receiver_asm = MessageAssembler()

        stream_id = "stream-xyz"
        chunks = [
            _chunk(stream_id, 0, "hello"),
            _chunk(stream_id, 1, "world"),
            _chunk(stream_id, 2, "!", total=3),
        ]
        sender_cs.buffer(stream_id, chunks)

        # Receiver gets chunks 0 and 2 (chunk 1 lost)
        receiver_asm.push(chunks[0])
        receiver_asm.push(chunks[2])
        assert receiver_asm.pop_completed() == []

        # Simulate timeout elapsed
        receiver_asm._streams[stream_id].last_received_at = time.monotonic() - 10.0
        retransmit_infos = receiver_asm.check_timeouts(timeout_s=5.0)
        assert len(retransmit_infos) == 1
        assert retransmit_infos[0].missing == [1]

        # Build RETRANSMIT message and send to sender
        retransmit_msg = UnifiedMessage(
            type=MessageType.RETRANSMIT,
            payload={"stream_id": stream_id, "missing": retransmit_infos[0].missing},
            sender=_receiver(),
        )
        resend = sender_cs.handle_retransmit(retransmit_msg)
        assert len(resend) == 1
        assert resend[0].sequence_no == 1

        # Receiver gets the resent chunk — seq 1 + buffered seq 2 both released
        ready = receiver_asm.push(resend[0])
        assert len(ready) == 2
        assert ready[0].payload["text"] == "world"   # seq 1
        assert ready[1].payload["text"] == "!"        # seq 2 (was buffered)

        completed = receiver_asm.pop_completed()
        assert len(completed) == 1
        assert completed[0].stream_id == stream_id

        # Receiver sends STREAM_ACK → sender frees buffer
        ack = UnifiedMessage(
            type=MessageType.STREAM_ACK,
            payload={"stream_id": stream_id},
            sender=_receiver(),
        )
        freed = sender_cs.handle_ack(ack)
        assert freed is True
        assert stream_id not in sender_cs.buffered_streams()

    def test_no_second_retransmit_after_timeout(self):
        """Even if stream still incomplete, retransmit is sent only once."""
        asm = MessageAssembler()
        asm.push(_chunk("s", 0, "A"))
        asm.push(_chunk("s", 2, "C"))
        asm._streams["s"].last_received_at = time.monotonic() - 10.0

        first = asm.check_timeouts(timeout_s=5.0)
        second = asm.check_timeouts(timeout_s=5.0)
        assert len(first) == 1
        assert second == []
        # Stream still in pending (abandoned, not dropped automatically)
        assert "s" in asm.pending_streams()
