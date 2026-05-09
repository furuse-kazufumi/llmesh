"""VideoCUSUM — v3-N15 synchronised CUSUM for video frames + sensor stream.

Two parallel CUSUM channels share a **wall-clock timeline**:

- a *frame* channel ingests one numerical feature per video frame
  (typically derived by ``VLMFeatureExtractor``: an OCR digit, a defect
  count, a per-frame scalar produced by a vision LLM).
- a *sensor* channel ingests a regular sensor measurement (vibration,
  temperature, current, etc.).

Each ``ingest`` call carries a timestamp.  The class buffers events from
each channel and emits a paired alarm only when both channels see a
violation **inside** a configurable synchronisation window.  This
matches the v3-N15 spec: two-stream drift detection where independent
channels often fire at slightly different times yet point to the same
physical event.

The implementation is **pure stdlib** — it composes two
:class:`CUSUMChart` instances and a small bounded-deque event buffer.
Time is supplied by the caller (no internal clock) so unit tests stay
deterministic.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque

from .spc_engine import CUSUMChart, SPCResult


@dataclass(frozen=True)
class _Alarm:
    """Internal per-channel alarm record."""

    timestamp: float
    channel: str           # "frame" | "sensor"
    spc: SPCResult


@dataclass(frozen=True)
class VideoCUSUMResult:
    """Verdict from a single ``ingest`` call."""

    timestamp: float
    channel: str
    spc_result: SPCResult
    synced_alarm: bool = False
    paired_with: tuple[float, str] | None = None

    @property
    def in_control(self) -> bool:
        return self.spc_result.in_control


class VideoCUSUM:
    """Two-channel CUSUM with synchronisation window.

    Parameters
    ----------
    frame_chart, sensor_chart:
        Two pre-configured :class:`CUSUMChart` instances. The class does
        not reset their internal state.
    sync_window_s:
        Maximum |Δt| in seconds between a frame alarm and a sensor alarm
        for them to count as a synchronised pair. Defaults to ``1.0``.
    buffer_size:
        Bounded retention size for unmatched alarms in each channel. The
        oldest entries are dropped first. Defaults to ``128`` per
        channel.
    """

    def __init__(
        self,
        frame_chart: CUSUMChart,
        sensor_chart: CUSUMChart,
        *,
        sync_window_s: float = 1.0,
        buffer_size: int = 128,
    ) -> None:
        if frame_chart is None or sensor_chart is None:
            raise ValueError("both charts are required")
        if sync_window_s < 0:
            raise ValueError("sync_window_s must be non-negative")
        if buffer_size <= 0:
            raise ValueError("buffer_size must be positive")
        self._frame = frame_chart
        self._sensor = sensor_chart
        self._sync = float(sync_window_s)
        self._frame_buf: Deque[_Alarm] = deque(maxlen=buffer_size)
        self._sensor_buf: Deque[_Alarm] = deque(maxlen=buffer_size)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def sync_window_s(self) -> float:
        return self._sync

    @property
    def frame_chart(self) -> CUSUMChart:
        return self._frame

    @property
    def sensor_chart(self) -> CUSUMChart:
        return self._sensor

    # ------------------------------------------------------------------
    # Ingest
    # ------------------------------------------------------------------

    def ingest_frame(self, timestamp: float, value: float) -> VideoCUSUMResult:
        """Push one frame-derived value at ``timestamp`` (seconds)."""
        return self._ingest("frame", float(timestamp), float(value))

    def ingest_sensor(self, timestamp: float, value: float) -> VideoCUSUMResult:
        """Push one sensor measurement at ``timestamp`` (seconds)."""
        return self._ingest("sensor", float(timestamp), float(value))

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ingest(self, channel: str, timestamp: float, value: float) -> VideoCUSUMResult:
        chart = self._frame if channel == "frame" else self._sensor
        spc = chart.update(value)
        if spc.in_control:
            return VideoCUSUMResult(
                timestamp=timestamp, channel=channel, spc_result=spc,
            )
        # Out-of-control — try to pair with the OTHER channel's pending alarms.
        other_buf = self._sensor_buf if channel == "frame" else self._frame_buf
        match = self._find_match(other_buf, timestamp)
        if match is not None:
            other_buf.remove(match)
            return VideoCUSUMResult(
                timestamp=timestamp,
                channel=channel,
                spc_result=spc,
                synced_alarm=True,
                paired_with=(match.timestamp, match.channel),
            )
        # No match — buffer for the other channel to find later.
        own_buf = self._frame_buf if channel == "frame" else self._sensor_buf
        own_buf.append(_Alarm(timestamp=timestamp, channel=channel, spc=spc))
        return VideoCUSUMResult(
            timestamp=timestamp, channel=channel, spc_result=spc,
        )

    def _find_match(self, buf: Deque[_Alarm], timestamp: float) -> _Alarm | None:
        """Return the closest in-window alarm from ``buf``, else None.

        Eviction:
            entries older than ``timestamp - sync_window_s`` are stale
            and are removed in this pass to bound memory.
        """
        cutoff = timestamp - self._sync
        # Drop stale entries from the left (deque is time-ordered as it's
        # appended on each ingest).
        while buf and buf[0].timestamp < cutoff:
            buf.popleft()
        # The nearest in-window candidate is the most recent one
        # (highest timestamp ≤ current). Walk from the right.
        best: _Alarm | None = None
        best_dist = self._sync + 1.0
        for alarm in reversed(buf):
            dt = abs(alarm.timestamp - timestamp)
            if dt <= self._sync and dt < best_dist:
                best = alarm
                best_dist = dt
        return best

    # ------------------------------------------------------------------
    # Inspection
    # ------------------------------------------------------------------

    def pending_alarms(self) -> tuple[int, int]:
        """Return (frame_pending, sensor_pending) counts."""
        return (len(self._frame_buf), len(self._sensor_buf))
