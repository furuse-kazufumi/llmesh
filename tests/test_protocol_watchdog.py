"""Tests for WatchdogTimer and MessageAssembler watchdog integration."""
from __future__ import annotations

import time
import pytest
from llmesh.protocol import MessageAssembler, NodeAddress, UnifiedMessage, WatchdogTimer


def _sender() -> NodeAddress:
    return NodeAddress("127.0.0.1", 8000)


def _chunk(stream_id: str, seq: int, data: str, total: int | None = None) -> UnifiedMessage:
    return UnifiedMessage.chunk({"text": data}, _sender(),
                                stream_id=stream_id, sequence_no=seq, total_chunks=total)


# ---------------------------------------------------------------------------
# WatchdogTimer unit tests
# ---------------------------------------------------------------------------

class TestWatchdogTimer:
    def test_not_expired_immediately(self):
        wd = WatchdogTimer(timeout_s=60.0)
        assert not wd.is_expired()

    def test_expired_after_timeout(self):
        wd = WatchdogTimer(timeout_s=5.0)
        past = time.monotonic() - 10.0
        wd._last_kick = past
        assert wd.is_expired()

    def test_kick_resets_timer(self):
        wd = WatchdogTimer(timeout_s=5.0)
        wd._last_kick = time.monotonic() - 10.0
        assert wd.is_expired()
        wd.kick()
        assert not wd.is_expired()

    def test_remaining_positive_before_expiry(self):
        wd = WatchdogTimer(timeout_s=60.0)
        assert wd.remaining() > 0.0

    def test_remaining_zero_after_expiry(self):
        wd = WatchdogTimer(timeout_s=5.0)
        wd._last_kick = time.monotonic() - 10.0
        assert wd.remaining() == 0.0

    def test_idle_s_increases(self):
        wd = WatchdogTimer(timeout_s=60.0)
        wd._last_kick = time.monotonic() - 5.0
        assert wd.idle_s >= 5.0

    def test_reset_alias(self):
        wd = WatchdogTimer(timeout_s=5.0)
        wd._last_kick = time.monotonic() - 10.0
        wd.reset()
        assert not wd.is_expired()

    def test_invalid_timeout_raises(self):
        with pytest.raises(ValueError):
            WatchdogTimer(timeout_s=0.0)
        with pytest.raises(ValueError):
            WatchdogTimer(timeout_s=-1.0)

    def test_custom_now(self):
        wd = WatchdogTimer(timeout_s=10.0)
        future = time.monotonic() + 20.0
        assert wd.is_expired(now=future)

    def test_timeout_property(self):
        wd = WatchdogTimer(timeout_s=42.0)
        assert wd.timeout_s == 42.0


# ---------------------------------------------------------------------------
# MessageAssembler watchdog integration
# ---------------------------------------------------------------------------

class TestAssemblerWatchdog:
    def test_no_watchdog_by_default(self):
        asm = MessageAssembler()
        assert asm.watchdog is None
        assert not asm.check_watchdog()

    def test_watchdog_configured(self):
        asm = MessageAssembler(watchdog_timeout_s=60.0)
        assert asm.watchdog is not None

    def test_push_kicks_watchdog(self):
        asm = MessageAssembler(watchdog_timeout_s=60.0)
        asm.watchdog._last_kick = time.monotonic() - 70.0   # already expired
        assert asm.check_watchdog()
        # push resets it
        asm.push(UnifiedMessage.request({}, _sender()))
        assert not asm.check_watchdog()

    def test_check_watchdog_false_before_timeout(self):
        asm = MessageAssembler(watchdog_timeout_s=60.0)
        assert not asm.check_watchdog()

    def test_check_watchdog_true_after_timeout(self):
        asm = MessageAssembler(watchdog_timeout_s=5.0)
        asm.watchdog._last_kick = time.monotonic() - 10.0
        assert asm.check_watchdog()

    def test_check_watchdog_with_custom_now(self):
        asm = MessageAssembler(watchdog_timeout_s=10.0)
        future = time.monotonic() + 20.0
        assert asm.check_watchdog(now=future)

    def test_watchdog_kicks_on_stream_chunk(self):
        asm = MessageAssembler(watchdog_timeout_s=60.0)
        asm.watchdog._last_kick = time.monotonic() - 70.0
        asm.push(_chunk("s", 0, "A"))
        assert not asm.check_watchdog()

    def test_watchdog_does_not_auto_drop_streams(self):
        """Expiry signals the caller to disconnect; assembler state is unchanged."""
        asm = MessageAssembler(watchdog_timeout_s=5.0)
        asm.push(_chunk("s", 1, "B"))   # gap at 0 → buffered
        asm.watchdog._last_kick = time.monotonic() - 10.0
        assert asm.check_watchdog()
        assert "s" in asm.pending_streams()   # still there — caller must clean up
