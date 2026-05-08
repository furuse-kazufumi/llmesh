"""WatchdogTimer — idle-connection detector for the protocol layer.

The receiver kicks the timer on every incoming message.  If no message
arrives within *timeout_s* seconds the watchdog expires; the caller should
then close the connection and optionally notify the sender.

Usage (synchronous polling)::

    wd = WatchdogTimer(timeout_s=60.0)

    async def on_message(msg):
        wd.kick()
        process(msg)

    # Background task — check every few seconds
    while True:
        if wd.is_expired():
            await connection.close()
            break
        await asyncio.sleep(wd.remaining() / 2 + 0.1)

Usage with MessageAssembler::

    asm = MessageAssembler(watchdog_timeout_s=60.0)
    for msg in incoming:
        asm.push(msg)                   # auto-kicks the watchdog
    if asm.check_watchdog():
        await adapter.stop()            # disconnect
"""
from __future__ import annotations

import time


class WatchdogTimer:
    """Single-purpose idle timer.

    Not thread-safe; use one instance per async task / connection.

    Args:
        timeout_s: Seconds of inactivity before is_expired() returns True.
    """

    def __init__(self, timeout_s: float = 60.0) -> None:
        if timeout_s <= 0:
            raise ValueError(f"timeout_s must be positive, got {timeout_s}")
        self._timeout = timeout_s
        self._last_kick: float = time.monotonic()

    def kick(self) -> None:
        """Record activity — resets the idle counter."""
        self._last_kick = time.monotonic()

    def is_expired(self, now: float | None = None) -> bool:
        """True if no activity for at least timeout_s seconds."""
        t = now if now is not None else time.monotonic()
        return (t - self._last_kick) >= self._timeout

    def remaining(self, now: float | None = None) -> float:
        """Seconds until expiry (0.0 if already expired)."""
        t = now if now is not None else time.monotonic()
        return max(0.0, self._timeout - (t - self._last_kick))

    def reset(self) -> None:
        """Alias for kick() — explicit reset intent."""
        self._last_kick = time.monotonic()

    @property
    def timeout_s(self) -> float:
        return self._timeout

    @property
    def idle_s(self) -> float:
        """Seconds since last activity."""
        return time.monotonic() - self._last_kick
