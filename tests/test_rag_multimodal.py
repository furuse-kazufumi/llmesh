"""Tests for llmesh.rag.multimodal — Phase 5 memory façade."""

from __future__ import annotations

import pytest

from llmesh.rag.multimodal import (
    InMemoryMultimodalStore,
    MultimodalMemory,
    MultimodalRecord,
    MultimodalStoreBackend,
    _cosine,
)


def _v(x: float, y: float = 0.0, z: float = 0.0) -> tuple[float, ...]:
    return (x, y, z)


# ---------------------------------------------------------------------------
# cosine util
# ---------------------------------------------------------------------------


class TestCosine:
    def test_identical_vectors(self) -> None:
        assert _cosine((1.0, 0.0), (1.0, 0.0)) == pytest.approx(1.0)

    def test_orthogonal_vectors(self) -> None:
        assert _cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)

    def test_opposite_vectors(self) -> None:
        assert _cosine((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)

    def test_zero_vector_returns_zero(self) -> None:
        assert _cosine((0.0, 0.0), (1.0, 1.0)) == 0.0

    def test_dimension_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="dimension"):
            _cosine((1.0,), (1.0, 0.0))


# ---------------------------------------------------------------------------
# ABC + backend basics
# ---------------------------------------------------------------------------


class TestBackend:
    def test_abc_not_instantiable(self) -> None:
        with pytest.raises(TypeError):
            MultimodalStoreBackend()  # type: ignore[abstract]

    def test_add_and_get(self) -> None:
        store = InMemoryMultimodalStore()
        rec = MultimodalRecord(
            record_id="r1", modality="text", content="hi", vector=(1.0, 0.0, 0.0)
        )
        store.add(rec)
        assert store.get("r1") == rec
        assert len(store) == 1

    def test_remove(self) -> None:
        store = InMemoryMultimodalStore()
        store.add(
            MultimodalRecord(
                record_id="r1", modality="text", content="x", vector=(1.0, 0.0, 0.0)
            )
        )
        assert store.remove("r1") is True
        assert store.remove("r1") is False
        assert len(store) == 0

    def test_unknown_modality_rejected_on_add(self) -> None:
        store = InMemoryMultimodalStore()
        bad = MultimodalRecord(
            record_id="r", modality="ultra-violet", content="x", vector=(0.0,)  # type: ignore[arg-type]
        )
        with pytest.raises(ValueError, match="modality"):
            store.add(bad)

    def test_iter_modality(self) -> None:
        store = InMemoryMultimodalStore()
        store.add(
            MultimodalRecord(record_id="t1", modality="text", content="x", vector=_v(1))
        )
        store.add(
            MultimodalRecord(record_id="i1", modality="image", content="i.png", vector=_v(0, 1))
        )
        store.add(
            MultimodalRecord(record_id="t2", modality="text", content="y", vector=_v(1, 1))
        )
        texts = list(store.iter_modality("text"))
        images = list(store.iter_modality("image"))
        assert {r.record_id for r in texts} == {"t1", "t2"}
        assert {r.record_id for r in images} == {"i1"}

    def test_id_collision_across_modalities_updates_index(self) -> None:
        store = InMemoryMultimodalStore()
        store.add(
            MultimodalRecord(record_id="x", modality="text", content="text", vector=_v(1))
        )
        store.add(
            MultimodalRecord(record_id="x", modality="image", content="i.png", vector=_v(1))
        )
        # The text index entry was vacated; image now holds id "x"
        assert list(store.iter_modality("text")) == []
        images = list(store.iter_modality("image"))
        assert [r.record_id for r in images] == ["x"]

    def test_search_ranks_by_cosine(self) -> None:
        store = InMemoryMultimodalStore()
        store.add(
            MultimodalRecord(record_id="far", modality="text", content="far", vector=_v(-1))
        )
        store.add(
            MultimodalRecord(record_id="close", modality="text", content="close", vector=_v(1))
        )
        store.add(
            MultimodalRecord(record_id="middle", modality="text", content="middle", vector=(0.5, 0.5, 0.0))
        )
        hits = store.search(_v(1), modalities=None, top_k=3)
        assert hits[0].record.record_id == "close"
        # cosines are descending
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)

    def test_search_modality_filter(self) -> None:
        store = InMemoryMultimodalStore()
        store.add(
            MultimodalRecord(record_id="t", modality="text", content="t", vector=_v(1))
        )
        store.add(
            MultimodalRecord(record_id="i", modality="image", content="i.png", vector=_v(1))
        )
        hits = store.search(_v(1), modalities=("image",), top_k=5)
        assert {h.record.record_id for h in hits} == {"i"}

    def test_search_top_k_bounds(self) -> None:
        store = InMemoryMultimodalStore()
        for i in range(5):
            store.add(
                MultimodalRecord(
                    record_id=f"r{i}", modality="text", content=f"x{i}", vector=_v(1, i, 0)
                )
            )
        with pytest.raises(ValueError, match="top_k"):
            store.search(_v(1), modalities=None, top_k=0)
        assert len(store.search(_v(1), modalities=None, top_k=2)) == 2


# ---------------------------------------------------------------------------
# Façade
# ---------------------------------------------------------------------------


class TestMemoryFacade:
    def test_add_helpers_dispatch_to_correct_modality(self) -> None:
        m = MultimodalMemory()
        m.add_text("t1", text="hello", vector=_v(1))
        m.add_image("i1", uri="http://example/img.png", vector=_v(0, 1))
        m.add_table("tab1", rows=[("a", "b"), ("c", "d")], vector=_v(0, 0, 1))
        m.add_log("log1", line="2026-05-11 12:00 INFO ok", vector=_v(1, 1))
        assert len(m) == 4
        text = m.get("t1")
        image = m.get("i1")
        table = m.get("tab1")
        log = m.get("log1")
        assert text is not None and text.modality == "text"
        assert image is not None and image.modality == "image"
        assert table is not None and table.modality == "table"
        assert log is not None and log.modality == "log"
        assert table.content == (("a", "b"), ("c", "d"))

    def test_search_top_k_default(self) -> None:
        m = MultimodalMemory()
        for i in range(3):
            m.add_text(f"r{i}", text=f"x{i}", vector=_v(1, i * 0.1, 0))
        hits = m.search(_v(1))
        assert hits  # default top_k=5

    def test_iter_modality_passthrough(self) -> None:
        m = MultimodalMemory()
        m.add_text("t1", text="x", vector=_v(1))
        m.add_image("i1", uri="i.png", vector=_v(1))
        texts = list(m.iter_modality("text"))
        assert [r.record_id for r in texts] == ["t1"]

    def test_search_modality_filter(self) -> None:
        m = MultimodalMemory()
        m.add_text("t1", text="x", vector=_v(1))
        m.add_log("l1", line="info", vector=_v(1))
        hits = m.search(_v(1), modalities=("log",), top_k=5)
        assert all(h.record.modality == "log" for h in hits)

    def test_remove(self) -> None:
        m = MultimodalMemory()
        m.add_text("t1", text="x", vector=_v(1))
        assert m.remove("t1") is True
        assert m.get("t1") is None

    def test_custom_backend_injected(self) -> None:
        sentinel = InMemoryMultimodalStore()
        m = MultimodalMemory(backend=sentinel)
        m.add_text("t", text="x", vector=_v(1))
        assert sentinel.get("t") is not None


# ---------------------------------------------------------------------------
# Phase 1/4 → Phase 5 integration: store research artefacts in memory
# ---------------------------------------------------------------------------


class TestResearchArtefactsIntoMemory:
    def test_can_store_literature_digest_as_text_record(self) -> None:
        from llmesh.research import LiteratureResponse

        digest = LiteratureResponse(
            research_question="Does X affect Y?",
            constraints=("c1",),
            metrics=("m1",),
            open_problems=("op1",),
        )
        m = MultimodalMemory()
        # Store the digest's research_question as a text memory entry.
        m.add_text(
            "paper-1#rq",
            text=digest.research_question,
            vector=_v(0.7, 0.3, 0.1),
            metadata={"paper_id": "paper-1", "field": "research_question"},
        )
        rec = m.get("paper-1#rq")
        assert rec is not None
        assert rec.content == "Does X affect Y?"
        assert rec.metadata["field"] == "research_question"
