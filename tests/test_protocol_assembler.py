"""Tests for MessageAssembler — stream ordering and partial delivery."""
from __future__ import annotations

import pytest

from llmesh.protocol import MessageAssembler, NodeAddress, UnifiedMessage
from llmesh.protocol.message import MessageType


def _sender() -> NodeAddress:
    return NodeAddress("127.0.0.1", 8000)


def _chunk(stream_id: str, seq: int, data: str, total: int | None = None) -> UnifiedMessage:
    return UnifiedMessage.chunk(
        {"text": data},
        _sender(),
        stream_id=stream_id,
        sequence_no=seq,
        total_chunks=total,
    )


def _non_stream(payload: dict) -> UnifiedMessage:
    return UnifiedMessage.request(payload, _sender())


# ------------------------------------------------------------------
# Non-stream pass-through
# ------------------------------------------------------------------

class TestPassThrough:
    def test_non_stream_returned_immediately(self):
        asm = MessageAssembler()
        msg = _non_stream({"x": 1})
        result = asm.push(msg)
        assert result == [msg]

    def test_multiple_non_stream_independent(self):
        asm = MessageAssembler()
        m1 = _non_stream({"a": 1})
        m2 = _non_stream({"b": 2})
        assert asm.push(m1) == [m1]
        assert asm.push(m2) == [m2]
        assert len(asm) == 0


# ------------------------------------------------------------------
# In-order delivery
# ------------------------------------------------------------------

class TestInOrder:
    def test_single_chunk_stream(self):
        asm = MessageAssembler()
        c = _chunk("s1", 0, "only", total=1)
        result = asm.push(c)
        assert len(result) == 1
        assert result[0].payload["text"] == "only"
        assert "s1" not in asm.pending_streams()

    def test_two_chunks_in_order(self):
        asm = MessageAssembler()
        c0 = _chunk("s1", 0, "a")
        c1 = _chunk("s1", 1, "b", total=2)
        assert asm.push(c0)[0].payload["text"] == "a"
        out = asm.push(c1)
        assert len(out) == 1
        assert out[0].payload["text"] == "b"
        assert "s1" not in asm.pending_streams()

    def test_five_chunks_in_order(self):
        asm = MessageAssembler()
        texts = ["a", "b", "c", "d", "e"]
        delivered = []
        for i, t in enumerate(texts):
            total = 5 if i == 4 else None
            delivered.extend(asm.push(_chunk("s", i, t, total=total)))
        assert [m.payload["text"] for m in delivered] == texts
        assert len(asm) == 0


# ------------------------------------------------------------------
# Out-of-order delivery (gaps filled → burst release)
# ------------------------------------------------------------------

class TestOutOfOrder:
    def test_gap_then_fill(self):
        asm = MessageAssembler()
        # seq 1 arrives before seq 0
        assert asm.push(_chunk("s", 1, "B")) == []
        assert len(asm) == 1
        # seq 0 arrives → 0 and 1 both released
        out = asm.push(_chunk("s", 0, "A", total=2))
        assert [m.payload["text"] for m in out] == ["A", "B"]
        assert len(asm) == 0

    def test_reverse_order_three_chunks(self):
        asm = MessageAssembler()
        assert asm.push(_chunk("s", 2, "C", total=3)) == []
        assert asm.push(_chunk("s", 1, "B")) == []
        out = asm.push(_chunk("s", 0, "A"))
        assert [m.payload["text"] for m in out] == ["A", "B", "C"]
        assert len(asm) == 0

    def test_partial_fill(self):
        """seq 0 + seq 2 arrive; seq 1 missing → only seq 0 delivered."""
        asm = MessageAssembler()
        out0 = asm.push(_chunk("s", 0, "A"))
        assert [m.payload["text"] for m in out0] == ["A"]
        out2 = asm.push(_chunk("s", 2, "C"))
        assert out2 == []            # seq 1 still missing
        assert "s" in asm.stalled_streams()
        out1 = asm.push(_chunk("s", 1, "B", total=3))
        assert [m.payload["text"] for m in out1] == ["B", "C"]
        assert len(asm) == 0

    def test_duplicate_sequence_ignored(self):
        """Second push of same seq_no must not double-deliver."""
        asm = MessageAssembler()
        c0a = _chunk("s", 0, "first")
        c0b = _chunk("s", 0, "dupe")
        out1 = asm.push(c0a)
        assert len(out1) == 1
        out2 = asm.push(c0b)    # already consumed; buffer is past seq 0
        assert out2 == []


# ------------------------------------------------------------------
# Multiple independent streams
# ------------------------------------------------------------------

class TestMultipleStreams:
    def test_two_streams_interleaved(self):
        asm = MessageAssembler()
        # Interleave chunks from two streams
        asm.push(_chunk("alpha", 1, "A1"))
        asm.push(_chunk("beta", 0, "B0"))
        out_a = asm.push(_chunk("alpha", 0, "A0", total=2))
        out_b = asm.push(_chunk("beta", 1, "B1", total=2))
        assert [m.payload["text"] for m in out_a] == ["A0", "A1"]
        assert [m.payload["text"] for m in out_b] == ["B1"]
        assert len(asm) == 0

    def test_pending_streams_lists_incomplete(self):
        asm = MessageAssembler()
        asm.push(_chunk("s1", 1, "x"))
        asm.push(_chunk("s2", 0, "y"))
        pending = asm.pending_streams()
        assert "s1" in pending   # gap → still pending
        # s2 seq 0 delivered already; seq 1 not received yet
        assert "s2" in pending


# ------------------------------------------------------------------
# Stream management helpers
# ------------------------------------------------------------------

class TestStreamManagement:
    def test_drop_stream(self):
        asm = MessageAssembler()
        asm.push(_chunk("s", 1, "orphan"))
        dropped = asm.drop_stream("s")
        assert dropped == 1
        assert "s" not in asm.pending_streams()

    def test_drop_nonexistent_stream(self):
        asm = MessageAssembler()
        assert asm.drop_stream("ghost") == 0

    def test_len_counts_buffered_chunks(self):
        asm = MessageAssembler()
        asm.push(_chunk("a", 1, "x"))
        asm.push(_chunk("a", 2, "y"))
        asm.push(_chunk("b", 1, "z"))
        assert len(asm) == 3

    def test_stalled_streams(self):
        asm = MessageAssembler()
        asm.push(_chunk("s1", 0, "delivered"))
        asm.push(_chunk("s1", 2, "buffered"))  # gap at 1
        asm.push(_chunk("s2", 0, "ok"))        # no gap
        stalled = asm.stalled_streams()
        assert "s1" in stalled
        assert "s2" not in stalled


# ------------------------------------------------------------------
# STREAM_CHUNK / STREAM_END type propagation
# ------------------------------------------------------------------

class TestMessageTypes:
    def test_chunk_type_is_stream_chunk(self):
        c = UnifiedMessage.chunk({"d": 1}, _sender(), stream_id="s", sequence_no=0)
        assert c.type == MessageType.STREAM_CHUNK

    def test_final_chunk_type_is_stream_end(self):
        c = UnifiedMessage.chunk({"d": 1}, _sender(), stream_id="s", sequence_no=0, total_chunks=1)
        assert c.type == MessageType.STREAM_END

    def test_stream_end_carries_total_chunks(self):
        c = UnifiedMessage.chunk({}, _sender(), stream_id="s", sequence_no=4, total_chunks=5)
        assert c.total_chunks == 5
        assert c.sequence_no == 4
