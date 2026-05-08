"""Tests for GOOSEAdapter — v3-N7 IEC 61850 subscriber (skeleton)."""
from __future__ import annotations

import struct

import pytest

from llmesh.industrial.goose_adapter import (
    GOOSEAdapter,
    GoosePDU,
    GooseTransport,
    pdu_to_events,
    _encode_value,
    MAX_DATASET_VALUES,
)


def _pdu(values, *, ref="IED1/LLN0$GO$gcb01", dat_set="DataSet1",
         st=1, sq=0) -> GoosePDU:
    return GoosePDU(
        go_cb_ref=ref, dat_set=dat_set,
        st_num=st, sq_num=sq, dataset=tuple(values),
    )


class _FakeTransport(GooseTransport):
    def __init__(self, pdus):
        self._queue = list(pdus)
    def recv(self):
        if not self._queue:
            return None
        return self._queue.pop(0)


# ---------------------------------------------------------------------------
# Encoding
# ---------------------------------------------------------------------------

class TestEncodeValue:
    def test_bool(self):
        assert _encode_value(True) == b"\x01"
        assert _encode_value(False) == b"\x00"

    def test_int(self):
        assert _encode_value(7) == struct.pack("<q", 7)

    def test_float(self):
        assert _encode_value(3.14) == struct.pack("<d", 3.14)

    def test_bytes(self):
        assert _encode_value(b"\xab\xcd") == b"\xab\xcd"

    def test_str_fallback(self):
        assert _encode_value("trip") == b"trip"


# ---------------------------------------------------------------------------
# pdu_to_events
# ---------------------------------------------------------------------------

class TestPduToEvents:
    def test_one_event_per_value(self):
        events = pdu_to_events(_pdu([True, False, 12.5]), device_id="ied1")
        assert len(events) == 3
        assert events[0].sensor_type == "goose_value"
        assert events[0].metadata["index"] == 0
        assert events[1].metadata["index"] == 1
        assert events[2].metadata["index"] == 2

    def test_metadata_preserves_pdu_coordinates(self):
        events = pdu_to_events(_pdu([1], st=42, sq=7), device_id="ied1")
        md = events[0].metadata
        assert md["go_cb_ref"] == "IED1/LLN0$GO$gcb01"
        assert md["st_num"] == 42
        assert md["sq_num"] == 7
        assert md["dat_set"] == "DataSet1"

    def test_protocol_tag(self):
        ev = pdu_to_events(_pdu([1]))[0]
        assert ev.protocol == "iec61850_goose"

    def test_priority_is_high(self):
        from llmesh.industrial.sensor_event import Priority
        ev = pdu_to_events(_pdu([1]))[0]
        assert ev.priority == Priority.HIGH

    def test_oversize_dataset_rejected(self):
        too_big = list(range(MAX_DATASET_VALUES + 1))
        with pytest.raises(ValueError):
            pdu_to_events(_pdu(too_big))


# ---------------------------------------------------------------------------
# Adapter — basic flow
# ---------------------------------------------------------------------------

class TestStep:
    def test_no_transport_yields_empty(self):
        a = GOOSEAdapter()
        assert a.step() == []

    def test_step_emits_events(self):
        captured = []
        t = _FakeTransport([_pdu([True, 1, 2.5])])
        a = GOOSEAdapter(transport=t, allow_iedids=["IED1/LLN0$GO$gcb01"])
        a.on_event(captured.append)
        events = a.step()
        assert len(events) == 3
        assert len(captured) == 3

    def test_disallowed_iedid_dropped(self):
        t = _FakeTransport([_pdu([1], ref="IED2/LLN0$GO$gcb01")])
        a = GOOSEAdapter(transport=t, allow_iedids=["IED1/LLN0$GO$gcb01"])
        assert a.step() == []

    def test_no_allowlist_accepts_any(self):
        t = _FakeTransport([_pdu([1], ref="IED99/LLN0$GO$xxx")])
        a = GOOSEAdapter(transport=t, allow_iedids=None)
        assert len(a.step()) == 1


# ---------------------------------------------------------------------------
# Replay protection
# ---------------------------------------------------------------------------

class TestReplay:
    def test_st_num_must_not_go_backwards(self):
        t = _FakeTransport([
            _pdu([1], st=5),
            _pdu([2], st=4),    # replay — must be dropped
        ])
        a = GOOSEAdapter(transport=t, allow_iedids=["IED1/LLN0$GO$gcb01"])
        first = a.step()
        replay = a.step()
        assert len(first) == 1
        assert replay == []

    def test_equal_st_num_allowed(self):
        # Same state, different sqNum — common during retransmissions.
        t = _FakeTransport([
            _pdu([1], st=5, sq=0),
            _pdu([1], st=5, sq=1),
        ])
        a = GOOSEAdapter(transport=t, allow_iedids=["IED1/LLN0$GO$gcb01"])
        assert len(a.step()) == 1
        assert len(a.step()) == 1

    def test_replay_protection_per_ref(self):
        t = _FakeTransport([
            _pdu([1], ref="A", st=10),
            _pdu([2], ref="B", st=1),     # different ref — independent counter
        ])
        a = GOOSEAdapter(transport=t, allow_iedids=["A", "B"])
        assert len(a.step()) == 1
        assert len(a.step()) == 1


# ---------------------------------------------------------------------------
# drain()
# ---------------------------------------------------------------------------

class TestDrain:
    def test_drain_pulls_until_empty(self):
        t = _FakeTransport([
            _pdu([1], st=1),
            _pdu([2], st=2),
            _pdu([3], st=3),
        ])
        a = GOOSEAdapter(transport=t, allow_iedids=["IED1/LLN0$GO$gcb01"])
        out = a.drain()
        assert len(out) == 3

    def test_drain_max_steps_caps(self):
        t = _FakeTransport([_pdu([i], st=i + 1) for i in range(50)])
        a = GOOSEAdapter(transport=t, allow_iedids=["IED1/LLN0$GO$gcb01"])
        out = a.drain(max_steps=5)
        assert len(out) == 5

    def test_drain_invalid_max_steps(self):
        a = GOOSEAdapter()
        with pytest.raises(ValueError):
            a.drain(max_steps=0)

    def test_callback_exception_isolated(self):
        t = _FakeTransport([_pdu([1, 2])])
        a = GOOSEAdapter(transport=t, allow_iedids=["IED1/LLN0$GO$gcb01"])
        a.on_event(lambda ev: (_ for _ in ()).throw(RuntimeError("boom")))
        out = a.step()
        # Two values produce two events even though the callback raises.
        assert len(out) == 2
