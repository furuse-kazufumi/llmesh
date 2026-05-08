"""Tests for SqliteVectorStore — F-1.1 stdlib durable backend."""
from __future__ import annotations

import math

import pytest

from llmesh.rag.sqlite_store import SqliteVectorStore
from llmesh.rag.store import Document


def _norm(v):
    n = math.sqrt(sum(x * x for x in v))
    return tuple(x / n for x in v) if n else tuple(v)


def _doc(doc_id: str, vec, text: str = "", **md) -> Document:
    return Document(doc_id=doc_id, text=text or doc_id, vector=vec, metadata=md)


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestConstruct:
    def test_invalid_dimension(self):
        with pytest.raises(ValueError):
            SqliteVectorStore(":memory:", dimension=0)

    def test_in_memory_init(self):
        s = SqliteVectorStore(":memory:", dimension=4)
        assert s.dimension == 4
        assert len(s) == 0

    def test_existing_file_dim_mismatch_rejected(self, tmp_path):
        p = tmp_path / "store.sqlite"
        SqliteVectorStore(p, dimension=4).close()
        with pytest.raises(ValueError):
            SqliteVectorStore(p, dimension=8)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

class TestCrud:
    def test_add_and_len(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.add(_doc("b", _norm((0.0, 1.0))))
        assert len(s) == 2

    def test_dim_mismatch_rejected(self):
        s = SqliteVectorStore(":memory:", dimension=4)
        with pytest.raises(ValueError):
            s.add(_doc("a", (1.0, 0.0, 0.0)))

    def test_replace_same_id(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0)), text="v1"))
        s.add(_doc("a", _norm((0.0, 1.0)), text="v2"))
        assert len(s) == 1
        out = s.search(_norm((0.0, 1.0)), top_k=1)
        assert out[0].document.text == "v2"

    def test_remove(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.add(_doc("b", _norm((0.0, 1.0))))
        assert s.remove("a") is True
        assert s.remove("nope") is False
        out = s.search(_norm((1.0, 0.0)), top_k=2)
        assert len(out) == 1
        assert out[0].document.doc_id == "b"

    def test_add_many(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        s.add_many([
            _doc("a", _norm((1.0, 0.0))),
            _doc("b", _norm((0.0, 1.0))),
            _doc("c", _norm((1.0, 1.0))),
        ])
        assert len(s) == 3


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

class TestSearch:
    def test_top_k_orders_by_score(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        s.add(_doc("east", _norm((1.0, 0.0))))
        s.add(_doc("west", _norm((-1.0, 0.0))))
        s.add(_doc("north", _norm((0.0, 1.0))))
        out = s.search(_norm((1.0, 0.1)), top_k=3)
        ids = [r.document.doc_id for r in out]
        assert ids[0] == "east"
        assert ids[-1] == "west"

    def test_query_dim_mismatch_raises(self):
        s = SqliteVectorStore(":memory:", dimension=3)
        with pytest.raises(ValueError):
            s.search([1.0, 0.0], top_k=1)

    def test_empty_store(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        assert s.search([1.0, 0.0], top_k=5) == []

    def test_top_k_zero(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        assert s.search([1.0, 0.0], top_k=0) == []

    def test_top_k_larger_than_len(self):
        s = SqliteVectorStore(":memory:", dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        out = s.search(_norm((1.0, 0.0)), top_k=10)
        assert len(out) == 1


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_save_and_reload(self, tmp_path):
        path = tmp_path / "v.sqlite"
        s = SqliteVectorStore(path, dimension=3)
        s.add(_doc("a", _norm((1.0, 0.0, 0.0)), text="alpha", source="wiki"))
        s.add(_doc("b", _norm((0.0, 1.0, 0.0)), text="beta"))
        s.close()

        s2 = SqliteVectorStore(path, dimension=3)
        assert len(s2) == 2
        out = s2.search(_norm((1.0, 0.0, 0.0)), top_k=1)
        assert out[0].document.doc_id == "a"
        assert out[0].document.metadata == {"source": "wiki"}

    def test_save_to_alternate_path(self, tmp_path):
        path1 = tmp_path / "live.sqlite"
        path2 = tmp_path / "snapshot.sqlite"
        s = SqliteVectorStore(path1, dimension=2)
        s.add(_doc("a", _norm((1.0, 0.0))))
        s.save(path2)
        s.close()

        snap = SqliteVectorStore.load(path2)
        assert len(snap) == 1
        snap.close()

    def test_load_rejects_non_store(self, tmp_path):
        # A blank sqlite file (no meta_kv row) is not a valid store.
        import sqlite3
        path = tmp_path / "blank.sqlite"
        sqlite3.connect(str(path)).close()
        with pytest.raises(ValueError):
            SqliteVectorStore.load(path)

    def test_context_manager_closes(self, tmp_path):
        path = tmp_path / "cm.sqlite"
        with SqliteVectorStore(path, dimension=2) as s:
            s.add(_doc("a", _norm((1.0, 0.0))))
        # Re-open succeeds (not locked).
        s2 = SqliteVectorStore(path, dimension=2)
        assert len(s2) == 1
        s2.close()
