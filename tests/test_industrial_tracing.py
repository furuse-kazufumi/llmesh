"""Tests for IndustrialTracer / Span / current_span (v3 — C-13.1)."""
from __future__ import annotations

import asyncio
import json
import pytest

from llmesh.industrial.tracing import (
    IndustrialTracer, Span, current_span,
    SPAN_STATUS_OK, SPAN_STATUS_ERROR,
    _MAX_ATTRIBUTES_PER_SPAN,
    _MAX_SPANS_RETAINED,
    _coerce_attribute,
    _new_trace_id, _new_span_id,
)


class TestIdGeneration:
    def test_trace_id_is_32_hex(self):
        tid = _new_trace_id()
        assert len(tid) == 32
        int(tid, 16)  # parses as hex

    def test_span_id_is_16_hex(self):
        sid = _new_span_id()
        assert len(sid) == 16
        int(sid, 16)

    def test_ids_unique(self):
        ids = {_new_trace_id() for _ in range(100)}
        assert len(ids) == 100  # cryptographically unique


class TestCoerceAttribute:
    def test_str_int_float_bool(self):
        assert _coerce_attribute("x") == "x"
        assert _coerce_attribute(42) == 42
        assert _coerce_attribute(3.14) == 3.14
        assert _coerce_attribute(True) is True
        assert _coerce_attribute(None) is None

    def test_bytes_to_hex(self):
        assert _coerce_attribute(b"\x00\xff") == "00ff"

    def test_list_recursed(self):
        assert _coerce_attribute([1, "x", b"\xab"]) == [1, "x", "ab"]

    def test_object_to_str(self):
        class Foo:
            def __str__(self):
                return "foo!"
        assert _coerce_attribute(Foo()) == "foo!"


class TestBasicSpan:
    def test_span_records_to_tracer(self):
        tr = IndustrialTracer()
        with tr.span("op_a"):
            pass
        spans = tr.collected_spans()
        assert len(spans) == 1
        assert spans[0].name == "op_a"
        assert spans[0].status == SPAN_STATUS_OK

    def test_span_attributes(self):
        tr = IndustrialTracer()
        with tr.span("op", attributes={"k1": "v1"}) as s:
            s.set_attribute("k2", 42)
        recorded = tr.collected_spans()[0]
        assert recorded.attributes == {"k1": "v1", "k2": 42}

    def test_span_duration(self):
        tr = IndustrialTracer()
        with tr.span("op") as s:
            pass
        s = tr.collected_spans()[0]
        assert s.duration_ns >= 0
        assert s.start_ns < s.end_ns or s.start_ns == s.end_ns

    def test_exception_marks_error(self):
        tr = IndustrialTracer()
        with pytest.raises(RuntimeError):
            with tr.span("op"):
                raise RuntimeError("boom")
        recorded = tr.collected_spans()[0]
        assert recorded.status == SPAN_STATUS_ERROR
        assert "boom" in recorded.error_message


class TestParentChild:
    def test_nested_inherits_trace_id(self):
        tr = IndustrialTracer()
        with tr.span("parent") as p:
            with tr.span("child") as c:
                assert c.trace_id == p.trace_id
                assert c.parent_span_id == p.span_id

    def test_three_level_nesting(self):
        tr = IndustrialTracer()
        with tr.span("a") as a:
            with tr.span("b") as b:
                with tr.span("c") as c:
                    assert c.parent_span_id == b.span_id
                    assert b.parent_span_id == a.span_id
                    assert a.parent_span_id == ""

    def test_current_span_tracks_active(self):
        tr = IndustrialTracer()
        assert current_span() is None
        with tr.span("op") as s:
            assert current_span() is s
        assert current_span() is None


class TestAsyncContext:
    @pytest.mark.asyncio
    async def test_contextvar_isolated_per_task(self):
        tr = IndustrialTracer()

        async def task():
            with tr.span("inner"):
                await asyncio.sleep(0)
                return current_span().name

        with tr.span("outer"):
            name = await task()
            assert name == "inner"
            assert current_span().name == "outer"


class TestAttributeCap:
    def test_max_attributes_enforced(self):
        tr = IndustrialTracer()
        with tr.span("op") as s:
            for i in range(_MAX_ATTRIBUTES_PER_SPAN + 10):
                s.set_attribute(f"k_{i}", i)
        recorded = tr.collected_spans()[0]
        assert len(recorded.attributes) == _MAX_ATTRIBUTES_PER_SPAN


class TestSpanCap:
    def test_eviction_when_exceeded(self, monkeypatch):
        # Lower cap for fast test
        import llmesh.industrial.tracing as mod
        monkeypatch.setattr(mod, "_MAX_SPANS_RETAINED", 4)
        tr = mod.IndustrialTracer()
        for i in range(10):
            with tr.span(f"op_{i}"):
                pass
        assert len(tr.collected_spans()) <= 4


class TestExport:
    def test_jsonl_format(self):
        tr = IndustrialTracer()
        with tr.span("op", attributes={"k": "v"}):
            pass
        text = tr.export_jsonl()
        line = json.loads(text)
        assert line["name"] == "op"
        assert line["status"]["code"] == SPAN_STATUS_OK
        assert {"key": "k", "value": "v"} in line["attributes"]

    def test_otlp_payload_shape(self):
        tr = IndustrialTracer()
        with tr.span("op"):
            pass
        payload = tr.export_otlp_payload()
        assert "resourceSpans" in payload
        assert payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]["name"] == "op"

    def test_clear(self):
        tr = IndustrialTracer()
        with tr.span("op"):
            pass
        tr.clear()
        assert tr.collected_spans() == []
