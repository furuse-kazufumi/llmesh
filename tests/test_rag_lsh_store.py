"""Tests for LSHVectorStore — v2.15+ approximate nearest-neighbour."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("numpy")

import numpy as np  # noqa: E402

from llmesh.rag.lsh_store import LSHVectorStore  # noqa: E402
from llmesh.rag.store import Document  # noqa: E402


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v) if n else tuple(v)


def _doc(doc_id: str, vec, **md) -> Document:
    return Document(doc_id=doc_id, text=doc_id, vector=vec, metadata=md)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_invalid_dimension(self):
        with pytest.raises(ValueError):
            LSHVectorStore(0)

    def test_invalid_n_planes(self):
        with pytest.raises(ValueError):
            LSHVectorStore(8, n_planes=0)
        with pytest.raises(ValueError):
            LSHVectorStore(8, n_planes=33)

    def test_invalid_n_tables(self):
        with pytest.raises(ValueError):
            LSHVectorStore(8, n_tables=0)

    def test_invalid_rerank_factor(self):
        with pytest.raises(ValueError):
            LSHVectorStore(8, rerank_factor=0)

    def test_initial_state(self):
        s = LSHVectorStore(8)
        assert s.dimension == 8
        assert s.n_planes > 0
        assert s.n_tables > 0
        assert len(s) == 0


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestCrud:
    def test_dim_mismatch_rejected(self):
        s = LSHVectorStore(4)
        with pytest.raises(ValueError):
            s.add(_doc("a", (1.0, 0.0, 0.0)))

    def test_add_increments_len(self):
        s = LSHVectorStore(2, seed=42)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.add(_doc("b", _norm((0.0, 1.0))))
        assert len(s) == 2

    def test_replace_same_id(self):
        s = LSHVectorStore(2, seed=42)
        s.add(_doc("a", _norm((1.0, 0.0)), tag="v1"))
        s.add(_doc("a", _norm((0.0, 1.0)), tag="v2"))
        assert len(s) == 1
        out = s.search(_norm((0.0, 1.0)), top_k=1)
        assert out[0].document.metadata == {"tag": "v2"}

    def test_remove(self):
        s = LSHVectorStore(2, seed=42)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.add(_doc("b", _norm((0.0, 1.0))))
        assert s.remove("a") is True
        out = s.search(_norm((1.0, 0.0)), top_k=2)
        assert all(r.document.doc_id != "a" for r in out)
        assert s.remove("a") is False


# ---------------------------------------------------------------------------
# Recall — random unit-vector data
# ---------------------------------------------------------------------------

class TestRecall:
    def _build_corpus(self, dim=32, n=1000, seed=0):
        rng = np.random.default_rng(seed)
        raw = rng.standard_normal((n, dim))
        norms = np.linalg.norm(raw, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return raw / norms

    def test_perfect_query_returns_self(self):
        s = LSHVectorStore(32, seed=7)
        corpus = self._build_corpus(dim=32, n=200)
        for i, v in enumerate(corpus):
            s.add(_doc(f"d{i}", tuple(v)))
        # Query with one of the indexed vectors itself.
        out = s.search(tuple(corpus[42]), top_k=1)
        assert out and out[0].document.doc_id == "d42"

    def test_recall_at_10_above_threshold(self):
        # Recall@10: fraction of queries whose true nearest neighbour
        # appears in the top-10 LSH result.
        n = 500
        dim = 64
        rng = np.random.default_rng(123)
        corpus = self._build_corpus(dim=dim, n=n, seed=99)
        s = LSHVectorStore(dim, n_planes=12, n_tables=8, seed=11)
        for i, v in enumerate(corpus):
            s.add(_doc(f"d{i}", tuple(v)))
        # Build a small probe set that is the corpus itself with light
        # additive noise — the true nearest is usually the original.
        probes = corpus + rng.standard_normal((n, dim)) * 0.05
        # L2-normalize each probe
        probes = probes / np.linalg.norm(probes, axis=1, keepdims=True)
        hits = 0
        for i, q in enumerate(probes[:100]):
            out = s.search(tuple(q), top_k=10)
            ids = {r.document.doc_id for r in out}
            if f"d{i}" in ids:
                hits += 1
        # 0.85 is generous given the noise; well above pure chance.
        assert hits / 100 >= 0.85

    def test_top_k_zero_returns_empty(self):
        s = LSHVectorStore(2, seed=42)
        s.add(_doc("a", _norm((1.0, 0.0))))
        assert s.search(_norm((1.0, 0.0)), top_k=0) == []

    def test_query_dim_mismatch(self):
        s = LSHVectorStore(3, seed=42)
        s.add(_doc("a", (1.0, 0.0, 0.0)))
        with pytest.raises(ValueError):
            s.search([1.0, 0.0], top_k=1)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        s = LSHVectorStore(4, n_planes=8, n_tables=4, seed=7)
        s.add(_doc("a", _norm((1.0, 0.0, 0.0, 0.0)), tag="alpha"))
        s.add(_doc("b", _norm((0.0, 1.0, 0.0, 0.0)), tag="beta"))
        path = tmp_path / "lsh.npz"
        s.save(path)

        s2 = LSHVectorStore.load(path)
        assert s2.dimension == 4
        assert len(s2) == 2
        out = s2.search(_norm((1.0, 0.0, 0.0, 0.0)), top_k=1)
        assert out and out[0].document.doc_id == "a"
        assert out[0].document.metadata == {"tag": "alpha"}

    def test_save_atomic(self, tmp_path):
        s = LSHVectorStore(2, seed=42)
        s.add(_doc("a", _norm((1.0, 0.0))))
        path = tmp_path / "lsh.npz"
        s.save(path)
        assert not (tmp_path / "lsh.tmp.npz").exists()
        assert not (tmp_path / "lsh.npz.tmp").exists()
        assert path.exists()
