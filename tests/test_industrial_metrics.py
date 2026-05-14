"""Tests for IndustrialMetrics (v3 preview)."""
from __future__ import annotations

import asyncio
import socket
import pytest

from llmesh.industrial.metrics import IndustrialMetrics


class TestCounter:
    def test_basic_increment(self):
        m = IndustrialMetrics()
        m.increment("requests_total")
        m.increment("requests_total")
        assert m.get("requests_total") == 2

    def test_increment_with_labels(self):
        m = IndustrialMetrics()
        m.increment("events", labels={"device": "a"})
        m.increment("events", labels={"device": "b"})
        m.increment("events", labels={"device": "a"})
        assert m.get("events", labels={"device": "a"}) == 2
        assert m.get("events", labels={"device": "b"}) == 1

    def test_negative_amount_rejected(self):
        m = IndustrialMetrics()
        with pytest.raises(ValueError, match="non-negative"):
            m.increment("foo", -1)

    def test_invalid_metric_name(self):
        m = IndustrialMetrics()
        with pytest.raises(ValueError, match="invalid metric name"):
            m.increment("123-bad")

    def test_invalid_label_name(self):
        m = IndustrialMetrics()
        with pytest.raises(ValueError, match="invalid label name"):
            m.increment("ok", labels={"1bad": "x"})

    def test_kind_conflict_rejected(self):
        m = IndustrialMetrics()
        m.set_gauge("temp", 25.0)
        with pytest.raises(ValueError, match="already registered as"):
            m.increment("temp")


class TestGauge:
    def test_set_and_get(self):
        m = IndustrialMetrics()
        m.set_gauge("temp_c", 25.3, labels={"sensor": "s1"})
        assert m.get("temp_c", labels={"sensor": "s1"}) == 25.3

    def test_overwrites(self):
        m = IndustrialMetrics()
        m.set_gauge("temp", 10.0)
        m.set_gauge("temp", 20.0)
        assert m.get("temp") == 20.0


class TestRender:
    def test_empty(self):
        m = IndustrialMetrics()
        assert m.render() == ""

    def test_counter_no_labels(self):
        m = IndustrialMetrics()
        m.increment("foo_total", 5, help_text="number of foos")
        text = m.render()
        assert "# HELP foo_total number of foos" in text
        assert "# TYPE foo_total counter" in text
        assert "foo_total 5" in text

    def test_gauge_with_labels(self):
        m = IndustrialMetrics()
        m.set_gauge("temp_c", 21.5, labels={"sensor": "s1", "loc": "north"})
        text = m.render()
        assert "# TYPE temp_c gauge" in text
        # label order is sorted (loc < sensor alphabetically)
        assert 'temp_c{loc="north",sensor="s1"} 21.5' in text

    def test_label_value_escaping(self):
        m = IndustrialMetrics()
        m.set_gauge("x", 1, labels={"k": 'a"b\\c'})
        text = m.render()
        assert 'k="a\\"b\\\\c"' in text

    def test_integer_value_no_decimal(self):
        m = IndustrialMetrics()
        m.set_gauge("x", 42.0)
        text = m.render()
        assert "x 42\n" in text


class TestCardinalityCap:
    def test_capacity_limit_enforced(self, monkeypatch):
        m = IndustrialMetrics()
        monkeypatch.setattr("llmesh.industrial.metrics._MAX_SERIES", 3)
        m.increment("x", labels={"id": "1"})
        m.increment("x", labels={"id": "2"})
        m.increment("x", labels={"id": "3"})
        with pytest.raises(RuntimeError, match="cardinality limit"):
            m.increment("x", labels={"id": "4"})


class TestReset:
    def test_reset_clears_all(self):
        m = IndustrialMetrics()
        m.increment("a")
        m.set_gauge("b", 1)
        m.reset()
        assert m.get("a") is None
        assert m.get("b") is None


class TestHttpEndpoint:
    @pytest.mark.asyncio
    async def test_serve_metrics(self):
        m = IndustrialMetrics()
        m.increment("requests_total", 7, labels={"path": "/x"})

        # Bind to ephemeral port
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()

        await m.serve_http("127.0.0.1", port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(-1), timeout=2.0)
            writer.close()
            await writer.wait_closed()

            assert b"200 OK" in response
            assert b"text/plain" in response
            assert b'requests_total{path="/x"} 7' in response
        finally:
            await m.stop_http()

    @pytest.mark.asyncio
    async def test_404_for_other_paths(self):
        m = IndustrialMetrics()
        s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()

        await m.serve_http("127.0.0.1", port)
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.write(b"GET /admin HTTP/1.1\r\nHost: localhost\r\n\r\n")
            await writer.drain()
            response = await asyncio.wait_for(reader.read(-1), timeout=2.0)
            writer.close()
            await writer.wait_closed()
            assert b"404" in response
        finally:
            await m.stop_http()
