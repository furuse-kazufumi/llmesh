"""Pure-numpy in-memory vector store with ``.npz`` persistence.

Suitable for tens of thousands of documents — the search is a single
matrix-vector product. For larger corpora, swap in a sharded /
disk-backed store while keeping the ``VectorStore`` interface.

Persistence format
------------------
The store saves to a single ``.npz`` archive containing:

- ``vectors``   — float32 array of shape ``(n, dim)``
- ``doc_ids``   — utf-8 string array, length ``n``
- ``texts``     — utf-8 string array, length ``n``
- ``metadata``  — JSON-encoded list of dicts, length ``n``
- ``dimension`` — int scalar (sanity check on load)

Security notes
--------------
- We use ``numpy.savez``/``load`` with ``allow_pickle=False``; metadata is
  JSON-encoded to keep the file pickle-free.
- Files are written atomically via a ``.tmp`` rename so a crash mid-write
  does not corrupt an existing index.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

import numpy as np

from .store import Document, RetrievedDocument, VectorStore


class NumpyVectorStore(VectorStore):
    """In-memory cosine-similarity store backed by numpy."""

    def __init__(self, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._dim = int(dimension)
        # Parallel arrays keep insertion / search O(1) bookkeeping.
        self._doc_ids: list[str] = []
        self._texts: list[str] = []
        self._metadata: list[dict] = []
        self._matrix = np.zeros((0, self._dim), dtype=np.float32)
        self._index_by_id: dict[str, int] = {}

    # ------------------------------------------------------------------
    # VectorStore interface
    # ------------------------------------------------------------------

    @property
    def dimension(self) -> int:
        return self._dim

    def __len__(self) -> int:
        return len(self._doc_ids)

    def add(self, document: Document) -> None:
        if len(document.vector) != self._dim:
            raise ValueError(
                f"vector dim mismatch: got {len(document.vector)}, expected {self._dim}"
            )
        new_row = np.asarray(document.vector, dtype=np.float32).reshape(1, -1)
        existing = self._index_by_id.get(document.doc_id)
        if existing is not None:
            self._matrix[existing] = new_row
            self._texts[existing] = document.text
            self._metadata[existing] = dict(document.metadata)
            return
        self._index_by_id[document.doc_id] = len(self._doc_ids)
        self._doc_ids.append(document.doc_id)
        self._texts.append(document.text)
        self._metadata.append(dict(document.metadata))
        self._matrix = np.vstack([self._matrix, new_row]) if len(self._matrix) else new_row

    def add_many(self, documents: Iterable[Document]) -> None:
        for d in documents:
            self.add(d)

    def remove(self, doc_id: str) -> bool:
        idx = self._index_by_id.pop(doc_id, None)
        if idx is None:
            return False
        # Drop the row and re-pack indices.
        self._matrix = np.delete(self._matrix, idx, axis=0)
        del self._doc_ids[idx]
        del self._texts[idx]
        del self._metadata[idx]
        # Re-index everything that shifted up.
        for d_id, i in list(self._index_by_id.items()):
            if i > idx:
                self._index_by_id[d_id] = i - 1
        return True

    def search(
        self,
        query_vector,
        top_k: int = 5,
    ) -> list[RetrievedDocument]:
        if top_k <= 0 or len(self._doc_ids) == 0:
            return []
        q = np.asarray(query_vector, dtype=np.float32)
        if q.shape != (self._dim,):
            raise ValueError(
                f"query vector dim mismatch: got {q.shape}, expected ({self._dim},)"
            )
        scores = self._matrix @ q  # (n,) cosine since both sides are L2-normalized
        # argpartition for top-k then a small sort — O(n) for the partition.
        k = min(top_k, len(self._doc_ids))
        top_idx = np.argpartition(-scores, kth=k - 1)[:k]
        top_idx = top_idx[np.argsort(-scores[top_idx])]
        results = []
        for i in top_idx:
            doc = Document(
                doc_id=self._doc_ids[i],
                text=self._texts[i],
                vector=tuple(float(x) for x in self._matrix[i]),
                metadata=dict(self._metadata[i]),
            )
            results.append(RetrievedDocument(document=doc, score=float(scores[i])))
        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save(self, path) -> None:
        # Avoid ``dtype=object`` columns: they require ``allow_pickle=True``
        # at load time, which is a remote-code-execution sink for untrusted
        # files. Instead serialise all string fields into a single UTF-8
        # JSON document and store it as a uint8 buffer.
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Note: ``np.savez`` auto-appends ``.npz`` if missing, so we
        # construct the temp name with the suffix already in place
        # (``store.tmp.npz``) so atomic rename works without surprises.
        tmp = path.parent / f"{path.stem}.tmp{path.suffix or '.npz'}"
        payload = json.dumps({
            "doc_ids": self._doc_ids,
            "texts": self._texts,
            "metadata": self._metadata,
        }, ensure_ascii=False).encode("utf-8")
        np.savez(
            tmp,
            vectors=self._matrix,
            payload=np.frombuffer(payload, dtype=np.uint8),
            dimension=np.int64(self._dim),
        )
        os.replace(tmp, path)

    @classmethod
    def load(cls, path) -> "NumpyVectorStore":
        path = Path(path)
        with np.load(path, allow_pickle=False) as npz:
            dim = int(npz["dimension"])
            store = cls(dimension=dim)
            store._matrix = np.asarray(npz["vectors"], dtype=np.float32).reshape(-1, dim)
            payload = bytes(npz["payload"]).decode("utf-8")
        data = json.loads(payload)
        store._doc_ids = [str(x) for x in data["doc_ids"]]
        store._texts = [str(x) for x in data["texts"]]
        store._metadata = [dict(m) for m in data["metadata"]]
        store._index_by_id = {d: i for i, d in enumerate(store._doc_ids)}
        return store
