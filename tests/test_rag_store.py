"""Tests for NumpyVectorStore (F-1 / v3.0+)."""
from __future__ import annotations

import math

import pytest

pytest.importorskip("numpy")

from llmesh.rag.numpy_store import NumpyVectorStore  # noqa: E402
from llmesh.rag.store import Document  # noqa: E402


def _doc(doc_id: str, vec: tuple[float, ...], text: str = "", **md) -> Document:
    return Document(doc_id=doc_id, text=text or doc_id, vector=vec, metadata=md)


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v) if n else v


class TestConstruct:
    def test_invalid_dimension(self):
        with pytest.raises(ValueError):
            NumpyVectorStore(dimension=0)

    def test_initial_state(self):
        s = NumpyVectorStore(dimension=4)
        assert s.dimension == 4
        assert len(s) == 0
        assert s.search([0.0, 0.0, 0.0, 1.0], top_k=3) == []


class TestAdd:
    def test_dim_mismatch_rejected(self):
        s = NumpyVectorStore(dimension=4)
        with pytest.raises(ValueError):
            s.add(_doc("a", (1.0, 0.0, 0.0)))

    def test_add_increments_len(self):
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.add(_doc("b", _norm((0.0, 1.0))))
        assert len(s) == 2

    def test_add_replaces_same_id(self):
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0)), text="v1"))
        s.add(_doc("a", _norm((0.0, 1.0)), text="v2"))
        assert len(s) == 1
        results = s.search(_norm((0.0, 1.0)), top_k=1)
        assert results[0].document.text == "v2"


class TestSearch:
    def test_top_k_returns_highest_first(self):
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("east", _norm((1.0, 0.0))))
        s.add(_doc("northeast", _norm((1.0, 1.0))))
        s.add(_doc("west", _norm((-1.0, 0.0))))
        out = s.search(_norm((1.0, 0.1)), top_k=2)
        ids = [r.document.doc_id for r in out]
        assert ids[0] in ("east", "northeast")
        assert "west" not in ids

    def test_top_k_zero_returns_empty(self):
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        assert s.search(_norm((1.0, 0.0)), top_k=0) == []

    def test_query_dim_mismatch_raises(self):
        s = NumpyVectorStore(dimension=3)
        s.add(_doc("a", (1.0, 0.0, 0.0)))
        with pytest.raises(ValueError):
            s.search([1.0, 0.0], top_k=1)

    def test_top_k_larger_than_index_caps(self):
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        out = s.search(_norm((1.0, 0.0)), top_k=10)
        assert len(out) == 1


class TestRemove:
    def test_removed_doc_not_searchable(self):
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.add(_doc("b", _norm((0.0, 1.0))))
        assert s.remove("a") is True
        out = s.search(_norm((1.0, 0.0)), top_k=2)
        assert len(out) == 1
        assert out[0].document.doc_id == "b"

    def test_remove_unknown(self):
        s = NumpyVectorStore(dimension=2)
        assert s.remove("nope") is False

    def test_remove_re_indexes_remaining(self):
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.add(_doc("b", _norm((0.0, 1.0))))
        s.add(_doc("c", _norm((1.0, 1.0))))
        s.remove("a")
        assert len(s) == 2
        # Re-add with same id should replace (not raise an internal index issue)
        s.add(_doc("c", _norm((1.0, 1.0)), text="updated"))
        out = s.search(_norm((1.0, 1.0)), top_k=1)
        assert out[0].document.text == "updated"


class TestPersistence:
    def test_save_load_roundtrip(self, tmp_path):
        s = NumpyVectorStore(dimension=3)
        s.add(_doc("a", _norm((1.0, 0.0, 0.0)), text="alpha", source="wiki"))
        s.add(_doc("b", _norm((0.0, 1.0, 0.0)), text="beta", source="manual"))
        path = tmp_path / "store.npz"
        s.save(path)
        s2 = NumpyVectorStore.load(path)
        assert s2.dimension == 3
        assert len(s2) == 2
        out = s2.search(_norm((1.0, 0.0, 0.0)), top_k=1)
        assert out[0].document.doc_id == "a"
        assert out[0].document.text == "alpha"
        assert out[0].document.metadata == {"source": "wiki"}

    def test_save_atomic(self, tmp_path):
        # If save fails partway, the original file must remain intact.
        s = NumpyVectorStore(dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        path = tmp_path / "store.npz"
        s.save(path)
        # No .tmp residue after successful save.
        assert not (tmp_path / "store.tmp.npz").exists()
        assert not (tmp_path / "store.npz.tmp").exists()
        assert path.exists()
