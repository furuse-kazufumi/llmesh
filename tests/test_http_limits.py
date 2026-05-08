"""Tests for the shared HTTP response size guard (v2.17+)."""
from __future__ import annotations

from io import BytesIO

import pytest

from llmesh.security.http_limits import (
    DEFAULT_LLM_RESPONSE_BYTES,
    DEFAULT_MAX_RESPONSE_BYTES,
    ResponseTooLargeError,
    read_capped,
)


def _resp(body: bytes):
    """Tiny stand-in for an urlopen() context manager response."""
    class _R:
        def __init__(self, b): self._buf = BytesIO(b)
        def read(self, n=-1): return self._buf.read(n)
    return _R(body)


class TestReadCapped:
    def test_reads_body_under_cap(self):
        body = b"hello world"
        out = read_capped(_resp(body), max_bytes=64)
        assert out == body

    def test_reads_body_exactly_at_cap(self):
        body = b"a" * 64
        out = read_capped(_resp(body), max_bytes=64)
        assert out == body
        assert len(out) == 64

    def test_overflow_raises(self):
        body = b"a" * 65
        with pytest.raises(ResponseTooLargeError) as exc_info:
            read_capped(_resp(body), max_bytes=64)
        assert exc_info.value.cap == 64
        # ResponseTooLargeError is also an IOError so callers that
        # already handle network errors absorb it.
        assert isinstance(exc_info.value, IOError)

    def test_zero_cap_rejected(self):
        with pytest.raises(ValueError):
            read_capped(_resp(b""), max_bytes=0)

    def test_negative_cap_rejected(self):
        with pytest.raises(ValueError):
            read_capped(_resp(b""), max_bytes=-1)

    def test_non_bytes_response_raises(self):
        class _Bad:
            def read(self, n=-1): return "not bytes"
        with pytest.raises(TypeError):
            read_capped(_bad := _Bad(), max_bytes=10)

    def test_default_bounds_are_sane(self):
        assert DEFAULT_MAX_RESPONSE_BYTES > 0
        assert DEFAULT_LLM_RESPONSE_BYTES >= DEFAULT_MAX_RESPONSE_BYTES
