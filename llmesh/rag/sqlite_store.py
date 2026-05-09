"""SqliteVectorStore — pure sqlite3 backed vector store (F-1.1 / v2.14+).

Why sqlite over numpy?
----------------------
``NumpyVectorStore`` keeps every vector resident in RAM. For corpora
larger than a few hundred-thousand documents that becomes wasteful;
sqlite gives us *durable, indexable* storage with zero extra
dependencies (sqlite3 is stdlib).

What this is **not**
--------------------
This is **not** an ANN index — it does not use sqlite-vec or any
specialised distance extension. The implementation does an O(n) cosine
scan over the table, decoding vectors on demand. That is fast enough
for ≤10⁶ documents on commodity hardware, and it keeps the file format
trivial (a single ``vectors.sqlite`` you can ``cp`` between hosts).
For larger corpora swap in a sqlite-vec or chromadb backend behind the
same :class:`VectorStore` interface.

File format
-----------
Two tables::

    docs(doc_id TEXT PRIMARY KEY,
         text  TEXT NOT NULL,
         vec   BLOB NOT NULL,    -- raw little-endian float32, length == dim*4
         meta  TEXT NOT NULL)    -- JSON

    meta_kv(key TEXT PRIMARY KEY, value TEXT NOT NULL)
        -- holds: dimension, schema_version, created_at

Cosine similarity assumes vectors are L2-normalised on insert; the
embedders ship vectors that already are.
"""
from __future__ import annotations

import array
import json
import sqlite3
import time
from pathlib import Path
from typing import Iterable

from .store import Document, RetrievedDocument, VectorStore


_SCHEMA_VERSION = 1


def _vec_to_blob(vec) -> bytes:
    """Encode a Python float sequence as little-endian float32 bytes."""
    a = array.array("f")
    a.extend(float(v) for v in vec)
    # array.tobytes() honours machine byte order; force little-endian.
    if not _is_le():
        a.byteswap()
    return a.tobytes()


def _blob_to_vec(blob: bytes, dim: int) -> tuple[float, ...]:
    a = array.array("f")
    a.frombytes(blob)
    if not _is_le():
        a.byteswap()
    if len(a) != dim:
        raise ValueError(f"vec length {len(a)} != dim {dim}")
    return tuple(float(x) for x in a)


def _is_le() -> bool:
    import sys
    return sys.byteorder == "little"


class SqliteVectorStore(VectorStore):
    """Durable vector store backed by a single sqlite file.

    Parameters
    ----------
    path:
        Path to the sqlite file. Use ``":memory:"`` for an in-memory
        instance (handy in tests).
    dimension:
        Required for new files. When opening an existing file the value
        stored in ``meta_kv`` wins; if the caller passes a mismatched
        dimension we raise.
    """

    def __init__(self, path, dimension: int) -> None:
        if dimension <= 0:
            raise ValueError("dimension must be positive")
        self._path = str(path)
        self._dim_requested = int(dimension)
        # Auto-create parent directory for non-memory paths so callers do
        # not have to mkdir before opening the store.
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._init_schema()
        self._dim = self._read_dimension()
        if self._dim != self._dim_requested:
            self._conn.close()
            raise ValueError(
                f"existing store has dimension {self._dim}, "
                f"caller requested {self._dim_requested}"
            )

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS docs ("
                " doc_id TEXT PRIMARY KEY,"
                " text   TEXT NOT NULL,"
                " vec    BLOB NOT NULL,"
                " meta   TEXT NOT NULL"
                ")"
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS meta_kv ("
                " key TEXT PRIMARY KEY,"
                " value TEXT NOT NULL"
                ")"
            )
            cur = self._conn.execute(
                "SELECT value FROM meta_kv WHERE key = ?",
                ("dimension",),
            )
            row = cur.fetchone()
            if row is None:
                self._conn.executemany(
                    "INSERT INTO meta_kv(key, value) VALUES(?, ?)",
                    [
                        ("dimension", str(self._dim_requested)),
                        ("schema_version", str(_SCHEMA_VERSION)),
                        ("created_at", str(int(time.time()))),
                    ],
                )

    def _read_dimension(self) -> int:
        cur = self._conn.execute(
            "SELECT value FROM meta_kv WHERE key = ?", ("dimension",),
        )
        row = cur.fetchone()
        return int(row[0]) if row else self._dim_requested

    # ------------------------------------------------------------------
    # VectorStore interface
    # ------------------------------------------------------------------

    @property
    def dimension(self) -> int:
        return self._dim

    def __len__(self) -> int:
        cur = self._conn.execute("SELECT COUNT(*) FROM docs")
        return int(cur.fetchone()[0])

    def add(self, document: Document) -> None:
        if len(document.vector) != self._dim:
            raise ValueError(
                f"vector dim mismatch: got {len(document.vector)}, expected {self._dim}"
            )
        blob = _vec_to_blob(document.vector)
        meta_json = json.dumps(document.metadata, ensure_ascii=False)
        with self._conn:
            self._conn.execute(
                "INSERT INTO docs(doc_id, text, vec, meta) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET "
                " text = excluded.text, vec = excluded.vec, meta = excluded.meta",
                (document.doc_id, document.text, blob, meta_json),
            )

    def add_many(self, documents: Iterable[Document]) -> None:
        rows = []
        for d in documents:
            if len(d.vector) != self._dim:
                raise ValueError(
                    f"vector dim mismatch: got {len(d.vector)}, expected {self._dim}"
                )
            rows.append((
                d.doc_id, d.text, _vec_to_blob(d.vector),
                json.dumps(d.metadata, ensure_ascii=False),
            ))
        with self._conn:
            self._conn.executemany(
                "INSERT INTO docs(doc_id, text, vec, meta) VALUES(?, ?, ?, ?) "
                "ON CONFLICT(doc_id) DO UPDATE SET "
                " text = excluded.text, vec = excluded.vec, meta = excluded.meta",
                rows,
            )

    def remove(self, doc_id: str) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "DELETE FROM docs WHERE doc_id = ?", (doc_id,),
            )
            return cur.rowcount > 0

    def search(
        self,
        query_vector,
        top_k: int = 5,
    ) -> list[RetrievedDocument]:
        if top_k <= 0:
            return []
        q = tuple(float(x) for x in query_vector)
        if len(q) != self._dim:
            raise ValueError(
                f"query vector dim mismatch: got {len(q)}, expected {self._dim}"
            )
        # Pull every row; for ≤10⁶ rows this is acceptable. Larger
        # deployments should swap in an ANN backend.
        cur = self._conn.execute(
            "SELECT doc_id, text, vec, meta FROM docs"
        )
        scored: list[tuple[float, Document]] = []
        for doc_id, text, blob, meta_json in cur:
            vec = _blob_to_vec(blob, self._dim)
            score = sum(a * b for a, b in zip(q, vec))
            scored.append((score, Document(
                doc_id=doc_id,
                text=text,
                vector=vec,
                metadata=json.loads(meta_json),
            )))
        if not scored:
            return []
        scored.sort(key=lambda x: -x[0])
        return [
            RetrievedDocument(document=d, score=s)
            for s, d in scored[: min(top_k, len(scored))]
        ]

    def save(self, path) -> None:
        """Copy the live database to ``path`` (sqlite native backup API)."""
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        dst = sqlite3.connect(str(path))
        try:
            self._conn.backup(dst)
        finally:
            dst.close()

    @classmethod
    def load(cls, path) -> "SqliteVectorStore":
        # Re-open by reading the dimension stored in meta_kv. A file
        # without the schema is not a SqliteVectorStore — turn the
        # underlying OperationalError into a clean ValueError.
        conn = sqlite3.connect(str(path))
        try:
            try:
                cur = conn.execute(
                    "SELECT value FROM meta_kv WHERE key = ?", ("dimension",),
                )
                row = cur.fetchone()
            except sqlite3.OperationalError as exc:
                raise ValueError(f"{path} is not a SqliteVectorStore") from exc
            if row is None:
                raise ValueError(f"{path} is not a SqliteVectorStore")
            dim = int(row[0])
        finally:
            conn.close()
        return cls(path, dimension=dim)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:
            pass

    def __enter__(self) -> "SqliteVectorStore":
        return self

    def __exit__(self, *exc):
        self.close()
