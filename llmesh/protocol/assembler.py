"""MessageAssembler — orders streamed messages by (stream_id, sequence_no).

Design principle:
  The sender may deliver chunks in any order or in parallel bursts.
  The receiver processes messages in sequence-number order, starting from
  the leading contiguous run.  Gaps are buffered; when a gap fills the
  newly contiguous run is yielded immediately without waiting for the
  complete stream to arrive.

Non-stream messages (sequence_no is None) pass through unchanged.

Reliability protocol (opt-in):
  1. Stream completes → pop_completed() returns (stream_id, sender).
     Caller sends STREAM_ACK → sender can drop its chunk buffer.
  2. Gap + timeout → check_timeouts() returns RetransmitInfo once per stream.
     Caller sends RETRANSMIT with missing seq list → sender resends.
     If chunks still don't arrive after that, the stream is silently abandoned.

Usage::

    asm = MessageAssembler()
    for incoming in network_stream:
        for ready in asm.push(incoming):
            process(ready)

    # After each push batch — send ACKs and (if needed) retransmit requests
    for done in asm.pop_completed():
        await adapter.send(UnifiedMessage(...STREAM_ACK...), done.sender)

    for info in asm.check_timeouts(timeout_s=5.0):
        await adapter.send(UnifiedMessage(...RETRANSMIT..., payload={
            "stream_id": info.stream_id, "missing": info.missing
        }), info.sender)
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .message import NodeAddress, UnifiedMessage
from .watchdog import WatchdogTimer


@dataclass
class RetransmitInfo:
    """Describes the chunks the receiver needs re-sent."""
    stream_id: str
    sender: NodeAddress
    missing: list[int]


@dataclass
class CompletedStream:
    """A stream that has been fully delivered; ACK should be sent."""
    stream_id: str
    sender: NodeAddress


@dataclass
class _StreamBuffer:
    """Per-stream_id state: buffered chunks and delivery cursor."""

    stream_id: str
    chunks: dict[int, UnifiedMessage] = field(default_factory=dict)
    next_expected: int = 0
    total: int | None = None
    last_received_at: float = field(default_factory=time.monotonic)
    retransmit_sent: bool = False
    sender: NodeAddress | None = None  # updated on every received chunk

    def push(self, msg: UnifiedMessage) -> list[UnifiedMessage]:
        """Buffer *msg* and return the newly contiguous leading run."""
        seq = msg.sequence_no
        if seq is None:
            return []
        self.chunks[seq] = msg
        self.last_received_at = time.monotonic()
        self.sender = msg.sender
        if msg.total_chunks is not None:
            self.total = msg.total_chunks

        ready: list[UnifiedMessage] = []
        while self.next_expected in self.chunks:
            ready.append(self.chunks.pop(self.next_expected))
            self.next_expected += 1
        return ready

    def is_complete(self) -> bool:
        return (
            self.total is not None
            and self.next_expected >= self.total
            and not self.chunks
        )

    def is_stalled(self) -> bool:
        return bool(self.chunks) and self.next_expected not in self.chunks

    def missing_seqs(self) -> list[int]:
        """Sequence numbers between next_expected and the highest buffered chunk."""
        if not self.chunks:
            return []
        high = max(self.chunks)
        return [s for s in range(self.next_expected, high) if s not in self.chunks]


class MessageAssembler:
    """Protocol-agnostic stream assembler with optional reliability support.

    Thread-unsafe; use one instance per async task / connection.
    """

    def __init__(self, watchdog_timeout_s: float | None = None) -> None:
        self._streams: dict[str, _StreamBuffer] = {}
        self._completed: list[CompletedStream] = []
        self._watchdog: WatchdogTimer | None = (
            WatchdogTimer(watchdog_timeout_s) if watchdog_timeout_s is not None else None
        )

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------

    def push(self, msg: UnifiedMessage) -> list[UnifiedMessage]:
        """Accept *msg* and return all messages that are ready to process.

        Also kicks the watchdog timer (if configured) on every call.
        Non-stream messages are returned immediately (pass-through).
        Stream chunks are returned in ascending sequence_no order as
        soon as the leading contiguous run is available.
        When a stream completes it is moved to the completed queue;
        call pop_completed() to retrieve it and send STREAM_ACK.
        """
        if self._watchdog is not None:
            self._watchdog.kick()

        if msg.sequence_no is None:
            return [msg]

        stream_id = msg.correlation_id or msg.id
        if stream_id not in self._streams:
            self._streams[stream_id] = _StreamBuffer(stream_id=stream_id)

        buf = self._streams[stream_id]
        ready = buf.push(msg)

        if buf.is_complete():
            if buf.sender is not None:
                self._completed.append(
                    CompletedStream(stream_id=stream_id, sender=buf.sender)
                )
            del self._streams[stream_id]

        return ready

    # ------------------------------------------------------------------
    # Reliability helpers
    # ------------------------------------------------------------------

    def pop_completed(self) -> list[CompletedStream]:
        """Return and clear all streams that completed since last call.

        Caller should send STREAM_ACK to each CompletedStream.sender so
        the sender can drop its chunk buffer.
        """
        done, self._completed = self._completed, []
        return done

    def check_timeouts(
        self,
        timeout_s: float,
        now: float | None = None,
    ) -> list[RetransmitInfo]:
        """Return retransmit requests for stalled streams past *timeout_s*.

        A stream qualifies only if:
        - it has a gap (is_stalled)
        - it has not yet had a retransmit request sent (retransmit_sent=False)
        - it has been idle for at least timeout_s seconds

        Matching streams are marked retransmit_sent=True — they will never
        appear again even if chunks still don't arrive.  The caller sends the
        RETRANSMIT message once; if the stream remains incomplete it is simply
        abandoned (caller may call drop_stream() to reclaim memory).
        """
        t = now if now is not None else time.monotonic()
        result: list[RetransmitInfo] = []
        for buf in self._streams.values():
            if (
                not buf.retransmit_sent
                and buf.is_stalled()
                and buf.sender is not None
                and (t - buf.last_received_at) >= timeout_s
            ):
                buf.retransmit_sent = True
                result.append(
                    RetransmitInfo(
                        stream_id=buf.stream_id,
                        sender=buf.sender,
                        missing=buf.missing_seqs(),
                    )
                )
        return result

    # ------------------------------------------------------------------
    # Inspection helpers
    # ------------------------------------------------------------------

    def pending_streams(self) -> list[str]:
        """Stream IDs that have buffered but not-yet-delivered chunks."""
        return list(self._streams.keys())

    def stalled_streams(self) -> list[str]:
        """Stream IDs blocked on a missing chunk (gap detected)."""
        return [sid for sid, buf in self._streams.items() if buf.is_stalled()]

    def drop_stream(self, stream_id: str) -> int:
        """Discard all buffered state for *stream_id*.

        Returns the number of dropped (undelivered) chunks.
        """
        buf = self._streams.pop(stream_id, None)
        return len(buf.chunks) if buf else 0

    def check_watchdog(self, now: float | None = None) -> bool:
        """Return True if the watchdog has expired (sender silent too long).

        Call this periodically; when True the caller should close the
        connection to the sender.  Returns False if no watchdog is configured.

        Typical use::

            if asm.check_watchdog():
                await adapter.stop()   # disconnect
        """
        if self._watchdog is None:
            return False
        return self._watchdog.is_expired(now=now)

    @property
    def watchdog(self) -> WatchdogTimer | None:
        """The underlying WatchdogTimer, or None if not configured."""
        return self._watchdog

    def __len__(self) -> int:
        """Total number of buffered (not-yet-delivered) chunks."""
        return sum(len(b.chunks) for b in self._streams.values())
