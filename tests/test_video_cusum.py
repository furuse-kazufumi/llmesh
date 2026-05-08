"""Tests for VideoCUSUM — v3-N15 synchronised dual-stream CUSUM."""
from __future__ import annotations

import pytest

from llmesh.industrial.spc_engine import CUSUMChart
from llmesh.industrial.video_cusum import VideoCUSUM


def _drift_chart() -> CUSUMChart:
    """Build a CUSUM that alarms quickly under a 1.0 mean shift."""
    return CUSUMChart(target=0.0, k=0.1, h=0.5)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_charts_required(self):
        with pytest.raises(ValueError):
            VideoCUSUM(None, _drift_chart())
        with pytest.raises(ValueError):
            VideoCUSUM(_drift_chart(), None)

    def test_negative_sync_rejected(self):
        with pytest.raises(ValueError):
            VideoCUSUM(_drift_chart(), _drift_chart(), sync_window_s=-0.1)

    def test_buffer_size_must_be_positive(self):
        with pytest.raises(ValueError):
            VideoCUSUM(_drift_chart(), _drift_chart(), buffer_size=0)

    def test_properties(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart(), sync_window_s=2.0)
        assert v.sync_window_s == 2.0


# ---------------------------------------------------------------------------
# In-control behaviour
# ---------------------------------------------------------------------------

class TestInControl:
    def test_in_control_yields_no_synced_alarm(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart())
        out = v.ingest_frame(0.0, 0.0)
        assert out.in_control is True
        assert out.synced_alarm is False
        assert out.paired_with is None
        assert v.pending_alarms() == (0, 0)


# ---------------------------------------------------------------------------
# Single-channel alarms
# ---------------------------------------------------------------------------

class TestSingleChannel:
    def test_frame_alarm_buffered_when_no_sensor_event(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart(), sync_window_s=1.0)
        out = None
        for i in range(20):
            out = v.ingest_frame(t := i * 0.1, value=1.0)
        assert out.in_control is False
        assert out.synced_alarm is False
        # Frame buffer holds at least one pending alarm.
        assert v.pending_alarms()[0] >= 1

    def test_sensor_alarm_buffered_when_no_frame_event(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart())
        out = None
        for i in range(20):
            out = v.ingest_sensor(i * 0.1, 1.0)
        assert out.in_control is False
        assert out.synced_alarm is False
        assert v.pending_alarms()[1] >= 1


# ---------------------------------------------------------------------------
# Synced alarms
# ---------------------------------------------------------------------------

class TestSync:
    def test_within_window_pairs(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart(), sync_window_s=1.0)
        # Drive the frame channel out of control. Each tick alarms once
        # the CUSUM trips; the most-recent alarm sits at t=1.9.
        for i in range(20):
            v.ingest_frame(i * 0.1, 1.0)
        # Single sensor alarm at t=2.0 → must pair with t=1.9 frame alarm.
        out = v.ingest_sensor(2.0, 1.0)
        assert out.synced_alarm is True
        assert out.paired_with is not None
        assert out.paired_with[1] == "frame"
        # The matched frame alarm is consumed.
        assert v.pending_alarms()[0] < 20

    def test_outside_window_does_not_pair(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart(), sync_window_s=0.1)
        for i in range(20):
            v.ingest_frame(i * 0.1, 1.0)
        # 10 seconds later the sensor channel goes out of control.
        last = None
        for i in range(20):
            last = v.ingest_sensor(10.0 + i * 0.1, 1.0)
        assert last.synced_alarm is False

    def test_picks_closest_in_window(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart(), sync_window_s=2.0)
        # Two distinct frame alarms in the buffer.
        for i in range(15):
            v.ingest_frame(i * 0.1, 1.0)        # alarms across t=0.0..1.4
        for i in range(15):
            v.ingest_frame(5.0 + i * 0.1, 1.0)  # alarms across t=5.0..6.4
        # Single sensor alarm at t=6.5 → must pair with the closest
        # in-window frame alarm (t≈6.4), not the older t≈1.4 cluster.
        out = v.ingest_sensor(6.5, 1.0)
        assert out.synced_alarm is True
        assert out.paired_with[0] >= 5.0


# ---------------------------------------------------------------------------
# Buffer eviction
# ---------------------------------------------------------------------------

class TestEviction:
    def test_stale_alarms_evicted_after_window(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart(),
                       sync_window_s=0.5, buffer_size=64)
        for i in range(20):
            v.ingest_frame(i * 0.1, 1.0)
        # An out-of-control frame alarm is in the buffer.
        assert v.pending_alarms()[0] >= 1
        # A much later sensor event evicts stale frame alarms when it
        # tries to find a match.
        v.ingest_sensor(100.0, 0.0)
        # The in-control sensor event does not run match logic, so we
        # need an out-of-control sensor event to trigger the sweep.
        for i in range(20):
            v.ingest_sensor(100.0 + i * 0.1, 1.0)
        # After the sweep, no stale frame alarms remain (all are well
        # outside the sync window of any sensor alarm).
        assert v.pending_alarms()[0] == 0

    def test_buffer_size_caps_pending(self):
        v = VideoCUSUM(_drift_chart(), _drift_chart(),
                       sync_window_s=1000.0, buffer_size=2)
        # Generate many frame alarms; only buffer_size are retained.
        for i in range(50):
            for _ in range(5):
                v.ingest_frame(i * 100.0, 1.0)
        # Buffer never grows beyond the cap.
        assert v.pending_alarms()[0] <= 2
