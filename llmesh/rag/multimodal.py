"""Multimodal memory — text / image / table / log under one ID space (Phase 5).

The :class:`MultimodalMemory` is a thin layer **on top of** the
existing :mod:`llmesh.rag` stack: each ``modality`` is a labelled
sub-store inside a single namespace, so callers can mix a paper's
prose, its figure captions, its tables, and the run logs that
referenced it under one canonical ``record_id`` set.

Phase 5 ships a dependency-free in-memory implementation
(:class:`InMemoryMultimodalStore`) — cosine similarity is computed
with stdlib math; numpy is **not** required. Real deployments wire
``MultimodalMemory`` to per-modality :class:`VectorStore` instances
from :mod:`llmesh.rag` (Numpy / Sqlite / LSH) via the same API.

The Phase 1 → Phase 5 connection is intentional: literature digests,
hypothesis traces, robot perception frames, and materials predictor
outputs can all be recorded as one of the four modalities and
retrieved later by a single :func:`MultimodalMemory.search` call.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

Modality = Literal["text", "image", "table", "log"]
_VALID_MODALITIES: frozenset[str] = frozenset({"text", "image", "table", "log"})


# ---------------------------------------------------------------------------
# dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MultimodalRecord:
    """One memory entry under a shared ID space.

    ``content`` is modality-specific:

    - ``text``: the plain string (post-parser output).
    - ``image``: a path / URI / opaque handle string (binary blobs stay
      off the dataclass to keep traces JSON-serialisable).
    - ``table``: rows as a 2-D tuple — ``tuple[tuple[str, ...], ...]``.
    - ``log``: the raw log line / JSONL string.

    ``vector`` is the embedding used for similarity search. ``metadata``
    is application-defined (source URL, page number, scenario id ...).
    """

    record_id: str
    modality: Modality
    content: Any
    vector: tuple[float, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MultimodalHit:
    """One search result — a record plus its similarity score."""

    record: MultimodalRecord
    score: float


# ---------------------------------------------------------------------------
# store ABC + in-memory implementation
# ---------------------------------------------------------------------------


class MultimodalStoreBackend(ABC):
    """ABC for the underlying per-modality storage.

    Distinct from :class:`llmesh.rag.store.VectorStore` because the
    multimodal layer needs modality-aware queries and a single ID
    space across modalities — semantics that the existing store ABC
    doesn't natively express.
    """

    @abstractmethod
    def add(self, record: MultimodalRecord) -> None:
        ...

    @abstractmethod
    def get(self, record_id: str) -> MultimodalRecord | None:
        ...

    @abstractmethod
    def remove(self, record_id: str) -> bool:
        ...

    @abstractmethod
    def iter_modality(self, modality: Modality) -> Iterable[MultimodalRecord]:
        ...

    @abstractmethod
    def __len__(self) -> int:
        ...

    @abstractmethod
    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        modalities: tuple[Modality, ...] | None,
        top_k: int,
    ) -> list[MultimodalHit]:
        ...


class InMemoryMultimodalStore(MultimodalStoreBackend):
    """Stdlib-only in-memory backend.

    Uses a single dict keyed by ``record_id`` plus a per-modality
    index for fast modality-scoped iteration. Cosine similarity is
    computed with :mod:`math`; suitable for thousands of records.
    """

    def __init__(self) -> None:
        self._records: dict[str, MultimodalRecord] = {}
        self._by_modality: dict[Modality, set[str]] = {
            "text": set(),
            "image": set(),
            "table": set(),
            "log": set(),
        }

    def add(self, record: MultimodalRecord) -> None:
        if record.modality not in _VALID_MODALITIES:
            raise ValueError(f"unknown modality: {record.modality!r}")
        # If the record already exists under a different modality,
        # remove the stale index entry first so iter_modality stays
        # consistent.
        existing = self._records.get(record.record_id)
        if existing is not None and existing.modality != record.modality:
            self._by_modality[existing.modality].discard(record.record_id)
        self._records[record.record_id] = record
        self._by_modality[record.modality].add(record.record_id)

    def get(self, record_id: str) -> MultimodalRecord | None:
        return self._records.get(record_id)

    def remove(self, record_id: str) -> bool:
        rec = self._records.pop(record_id, None)
        if rec is None:
            return False
        self._by_modality[rec.modality].discard(record_id)
        return True

    def iter_modality(self, modality: Modality) -> Iterable[MultimodalRecord]:
        if modality not in _VALID_MODALITIES:
            raise ValueError(f"unknown modality: {modality!r}")
        for rid in self._by_modality[modality]:
            yield self._records[rid]

    def __len__(self) -> int:
        return len(self._records)

    def search(
        self,
        query_vector: tuple[float, ...],
        *,
        modalities: tuple[Modality, ...] | None,
        top_k: int,
    ) -> list[MultimodalHit]:
        if top_k < 1:
            raise ValueError("top_k must be >= 1")
        if modalities is not None:
            for m in modalities:
                if m not in _VALID_MODALITIES:
                    raise ValueError(f"unknown modality: {m!r}")
            allowed = {self._records[rid] for m in modalities for rid in self._by_modality[m]}
        else:
            allowed = set(self._records.values())
        scored: list[MultimodalHit] = []
        for rec in allowed:
            score = _cosine(query_vector, rec.vector)
            scored.append(MultimodalHit(record=rec, score=score))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """Cosine similarity in stdlib. Returns 0 when either vector is zero."""
    if len(a) != len(b):
        raise ValueError(f"vector dimension mismatch: {len(a)} vs {len(b)}")
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


# ---------------------------------------------------------------------------
# façade
# ---------------------------------------------------------------------------


class MultimodalMemory:
    """User-facing façade with convenience builders for each modality.

    The constructor accepts any :class:`MultimodalStoreBackend` so a
    test can swap in a fake (or a deployment can wire to a real
    backing store). Default backend is :class:`InMemoryMultimodalStore`.
    """

    def __init__(self, backend: MultimodalStoreBackend | None = None) -> None:
        self._backend: MultimodalStoreBackend = backend or InMemoryMultimodalStore()

    # ---- write -------------------------------------------------------

    def add(self, record: MultimodalRecord) -> None:
        self._backend.add(record)

    def add_text(
        self,
        record_id: str,
        *,
        text: str,
        vector: tuple[float, ...] | list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._backend.add(
            MultimodalRecord(
                record_id=record_id,
                modality="text",
                content=text,
                vector=tuple(vector),
                metadata=dict(metadata or {}),
            )
        )

    def add_image(
        self,
        record_id: str,
        *,
        uri: str,
        vector: tuple[float, ...] | list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._backend.add(
            MultimodalRecord(
                record_id=record_id,
                modality="image",
                content=uri,
                vector=tuple(vector),
                metadata=dict(metadata or {}),
            )
        )

    def add_table(
        self,
        record_id: str,
        *,
        rows: Iterable[Iterable[str]],
        vector: tuple[float, ...] | list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        rows_tuple = tuple(tuple(str(c) for c in row) for row in rows)
        self._backend.add(
            MultimodalRecord(
                record_id=record_id,
                modality="table",
                content=rows_tuple,
                vector=tuple(vector),
                metadata=dict(metadata or {}),
            )
        )

    def add_log(
        self,
        record_id: str,
        *,
        line: str,
        vector: tuple[float, ...] | list[float],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._backend.add(
            MultimodalRecord(
                record_id=record_id,
                modality="log",
                content=line,
                vector=tuple(vector),
                metadata=dict(metadata or {}),
            )
        )

    # ---- read --------------------------------------------------------

    def get(self, record_id: str) -> MultimodalRecord | None:
        return self._backend.get(record_id)

    def remove(self, record_id: str) -> bool:
        return self._backend.remove(record_id)

    def iter_modality(self, modality: Modality) -> Iterable[MultimodalRecord]:
        yield from self._backend.iter_modality(modality)

    def __len__(self) -> int:
        return len(self._backend)

    def search(
        self,
        query_vector: tuple[float, ...] | list[float],
        *,
        modalities: tuple[Modality, ...] | None = None,
        top_k: int = 5,
    ) -> list[MultimodalHit]:
        return self._backend.search(
            tuple(query_vector), modalities=modalities, top_k=top_k
        )


__all__ = [
    "InMemoryMultimodalStore",
    "Modality",
    "MultimodalHit",
    "MultimodalMemory",
    "MultimodalRecord",
    "MultimodalStoreBackend",
]
