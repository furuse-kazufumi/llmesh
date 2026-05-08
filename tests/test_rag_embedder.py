"""Tests for the RAG embedders (F-1 / v3.0+)."""
from __future__ import annotations

import math

import pytest

from llmesh.rag.embedder import (
    EmbeddingError,
    MockEmbedder,
    OllamaEmbedder,
    _l2_normalize,
)


class TestL2Normalize:
    def test_zero_vector_passes_through(self):
        assert _l2_normalize([0.0, 0.0]) == [0.0, 0.0]

    def test_unit_norm_on_arbitrary_input(self):
        v = _l2_normalize([3.0, 4.0])
        norm = math.sqrt(sum(x * x for x in v))
        assert pytest.approx(norm, rel=1e-6) == 1.0


class TestMockEmbedder:
    def test_dimension_property(self):
        e = MockEmbedder(dimension=32)
        assert e.dimension == 32

    def test_invalid_dimension(self):
        with pytest.raises(ValueError):
            MockEmbedder(dimension=0)

    def test_invalid_input_raises(self):
        e = MockEmbedder()
        with pytest.raises(EmbeddingError):
            e.embed(123)  # type: ignore[arg-type]

    def test_deterministic(self):
        e = MockEmbedder()
        a = e.embed("hello world")
        b = e.embed("hello world")
        assert a == b

    def test_l2_normalized(self):
        e = MockEmbedder()
        v = e.embed("the quick brown fox jumps over the lazy dog")
        norm = math.sqrt(sum(x * x for x in v))
        assert pytest.approx(norm, rel=1e-5) == 1.0

    def test_different_text_different_vector(self):
        e = MockEmbedder()
        a = e.embed("hello")
        b = e.embed("goodbye")
        assert a != b

    def test_embed_many(self):
        e = MockEmbedder()
        vs = e.embed_many(["a", "b", "c"])
        assert len(vs) == 3
        assert all(len(v) == e.dimension for v in vs)


class TestOllamaEmbedderClient:
    """Network is mocked — these tests do not contact a real Ollama daemon."""

    def test_dimension_inferred_lazily(self, monkeypatch):
        e = OllamaEmbedder(model="nomic-embed-text")

        captured = {}

        class _FakeResp:
            def __init__(self, body): self._body = body
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, size=-1):
                if size is None or size < 0 or size >= len(self._body):
                    return self._body
                return self._body[:size]

        def _fake_urlopen(req, timeout):
            captured["url"] = req.full_url
            captured["body"] = req.data
            import json as _json
            return _FakeResp(_json.dumps({"embedding": [0.1, 0.2, 0.3, 0.4]}).encode())

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        v = e.embed("hello")
        assert e.dimension == 4
        # L2 normalized → norm == 1
        assert pytest.approx(math.sqrt(sum(x * x for x in v)), rel=1e-5) == 1.0
        assert captured["url"].endswith("/api/embeddings")

    def test_dimension_mismatch_raises(self, monkeypatch):
        e = OllamaEmbedder(model="m", dimension=4)

        class _FakeResp:
            def __init__(self, body): self._body = body
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, size=-1):
                if size is None or size < 0 or size >= len(self._body):
                    return self._body
                return self._body[:size]

        def _fake_urlopen(req, timeout):
            import json as _json
            return _FakeResp(_json.dumps({"embedding": [0.1, 0.2, 0.3]}).encode())

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        with pytest.raises(EmbeddingError):
            e.embed("hello")

    def test_malformed_response_raises(self, monkeypatch):
        e = OllamaEmbedder(model="m")

        class _FakeResp:
            def __init__(self, body): self._body = body
            def __enter__(self): return self
            def __exit__(self, *a): pass
            def read(self, size=-1):
                if size is None or size < 0 or size >= len(self._body):
                    return self._body
                return self._body[:size]

        def _fake_urlopen(req, timeout):
            return _FakeResp(b'{"not_embedding": 1}')

        monkeypatch.setattr("urllib.request.urlopen", _fake_urlopen)
        with pytest.raises(EmbeddingError):
            e.embed("hello")

    def test_network_error_raises(self, monkeypatch):
        e = OllamaEmbedder(model="m")

        def _boom(req, timeout):
            import urllib.error
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr("urllib.request.urlopen", _boom)
        with pytest.raises(EmbeddingError):
            e.embed("hello")
