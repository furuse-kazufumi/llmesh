"""Vector store abstraction for the RAG module.

A store holds ``Document`` records and supports k-NN search. Different
backends (numpy, sqlite-vec, ChromaDB, …) implement this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Document:
    """A document stored in the vector index.

    The vector is stored alongside the original text (so the retriever can
    return the source) and a metadata dict for application-specific tags
    (e.g. ``{"source": "wiki", "section": "auth"}``).
    """

    doc_id: str
    text: str
    vector: tuple[float, ...]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RetrievedDocument:
    """A search result — a document plus its similarity score."""

    document: Document
    score: float


class VectorStore(ABC):
    """k-NN vector store interface.

    Implementations must enforce vector dimensionality on insertion and
    must not silently drop rows. The retriever expects L2-normalized
    vectors so that cosine similarity reduces to a dot product.
    """

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Configured vector dimension. All inserted vectors must match."""

    @abstractmethod
    def __len__(self) -> int:
        """Number of indexed documents."""

    @abstractmethod
    def add(self, document: Document) -> None:
        """Insert (or replace) a document by ``doc_id``."""

    @abstractmethod
    def remove(self, doc_id: str) -> bool:
        """Remove the document with ``doc_id``. Returns True if removed."""

    @abstractmethod
    def search(
        self,
        query_vector: list[float] | tuple[float, ...],
        top_k: int = 5,
    ) -> list[RetrievedDocument]:
        """Return up to ``top_k`` nearest documents (highest score first)."""

    @abstractmethod
    def save(self, path) -> None:
        """Persist the store to disk."""

    @classmethod
    @abstractmethod
    def load(cls, path) -> "VectorStore":
        """Load a previously saved store from disk."""
